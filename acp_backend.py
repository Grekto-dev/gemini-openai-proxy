"""
Antigravity Claude Proxy Backend Bridge (acp_backend.py).

Dynamically integrates with the `antigravity-claude-proxy` repository so that
`gemini-openai-proxy` always uses the most up-to-date:
1. Call identifier masking (`ANTIGRAVITY_HEADERS`, `User-Agent: antigravity/hub/...`,
   `X-Client-Version`, `x-goog-api-client`, `X-Machine-Session-Id`, `CLIENT_METADATA`).
2. System instruction masking (`ANTIGRAVITY_SYSTEM_INSTRUCTION` with `[ignore]...[/ignore]` wrapper
   and `role: "user"`).
3. Request envelope format (`userAgent: "antigravity"`, `requestType: "agent"`,
   `requestId: "agent-<uuid>"`, per-account `sessionId`).
4. Anti-bot client identity scrubbing (`IDENTITY_SCRUB_RULES`).
5. Quota and model discovery via `loadCodeAssist` and `fetchAvailableModels`
   (matching `antigravity-claude-proxy/src/cloudcode/model-api.js`).

Even if `antigravity-claude-proxy` is updated in the future (e.g., via `git pull`),
this bridge dynamically executes/parses its modules so the latest masking and endpoints
are always used automatically.
"""

import os
import re
import json
import time
import uuid
import glob
import struct
import platform
import subprocess
import requests

# Default locations to search for the antigravity-claude-proxy repository
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ACP_PATHS = [
    os.environ.get("ANTIGRAVITY_CLAUDE_PROXY_PATH", ""),
    os.path.abspath(os.path.join(_THIS_DIR, "..", "antigravity-claude-proxy")),
    os.path.abspath(os.path.join(_THIS_DIR, "antigravity-claude-proxy")),
    os.path.expanduser("~/Documentos/antigravity-claude-proxy"),
    os.path.expanduser("~/Documents/antigravity-claude-proxy"),
    os.path.expanduser("~/.gemini/antigravity/scratch/antigravity-claude-proxy"),
]

# Session ID store per account email (matches session-manager.js: randomUUID() + Date.now())
_RUNTIME_SESSION_STORE = {}

# Cached backend spec extracted from antigravity-claude-proxy
_BACKEND_CACHE = {
    "spec": None,
    "mtime": 0,
    "loaded_at": 0,
    "acp_path": None,
    "node_bin": None,
    "extraction_mode": None,
}


def find_acp_repo_path():
    """Finds the local path to the antigravity-claude-proxy repository."""
    for candidate in _DEFAULT_ACP_PATHS:
        if candidate and os.path.isdir(candidate):
            constants_js = os.path.join(candidate, "src", "constants.js")
            if os.path.isfile(constants_js):
                return candidate
    return None


def find_node_binary():
    """Finds a working Node.js binary (>= 18) on the system."""
    # 1. Check PATH
    for cmd in ("node", "/usr/local/bin/node", "/usr/bin/node"):
        try:
            res = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip().startswith("v"):
                return cmd
        except Exception:
            pass

    # 2. Check Hermes tools & NVM & FNM directories
    search_patterns = [
        os.path.expanduser("~/.hermes/tools/node-*/bin/node"),
        os.path.expanduser("~/.nvm/versions/node/*/bin/node"),
        os.path.expanduser("~/.local/share/fnm/node-versions/*/installation/bin/node"),
        os.path.expanduser("~/.volta/tools/image/node/*/bin/node"),
    ]
    for pattern in search_patterns:
        matches = sorted(glob.glob(pattern), reverse=True)
        for m in matches:
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m

    return None


def _get_repo_mtime(acp_path):
    """Returns the highest modification timestamp among key files in antigravity-claude-proxy and local Antigravity."""
    tracked_files = [
        "/opt/Antigravity/resources/app.asar",
        "/opt/antigravity/resources/app.asar",
        "/usr/share/antigravity/resources/app.asar",
        "/opt/Antigravity/resources/bin/language_server",
        "/opt/Antigravity/resources/app/product.json",
        "/usr/share/antigravity/resources/app/product.json",
    ]
    if acp_path:
        tracked_files.extend([
            os.path.join(acp_path, "package.json"),
            os.path.join(acp_path, "src", "constants.js"),
            os.path.join(acp_path, "src", "utils", "version-detector.js"),
            os.path.join(acp_path, "src", "cloudcode", "request-builder.js"),
            os.path.join(acp_path, "src", "cloudcode", "model-api.js"),
            os.path.join(acp_path, "src", "cloudcode", "session-manager.js"),
        ])
    max_mtime = 0
    for fpath in tracked_files:
        try:
            if os.path.exists(fpath):
                max_mtime = max(max_mtime, os.path.getmtime(fpath))
        except Exception:
            pass
    return max_mtime


def _is_version_higher(v1, v2):
    """Compares two semver strings (X.Y.Z). Returns True if v1 > v2."""
    try:
        p1 = [int(x) for x in re.findall(r"\d+", str(v1 or ""))]
        p2 = [int(x) for x in re.findall(r"\d+", str(v2 or ""))]
        for i in range(max(len(p1), len(p2))):
            a = p1[i] if i < len(p1) else 0
            b = p2[i] if i < len(p2) else 0
            if a > b:
                return True
            if a < b:
                return False
    except Exception:
        pass
    return False


def _read_asar_file(asar_path, file_parts):
    """Reads a specific file from an Electron ASAR archive in pure Python without external dependencies."""
    with open(asar_path, "rb") as f:
        header_bytes = f.read(16)
        if len(header_bytes) < 16:
            return None
        _, h_size, _, str_size = struct.unpack("<IIII", header_bytes)
        header_json = f.read(str_size).decode("utf-8", errors="ignore")
        header = json.loads(header_json)
        base_offset = 8 + h_size

        node = header
        for part in file_parts:
            files_map = node.get("files")
            if not isinstance(files_map, dict) or part not in files_map:
                return None
            node = files_map[part]

        if "offset" not in node or "size" not in node:
            return None
        f.seek(base_offset + int(node["offset"]))
        return f.read(int(node["size"])).decode("utf-8", errors="ignore")


def inspect_local_antigravity_installation():
    """
    Inspects the host machine's locally installed Antigravity application to extract
    its real version, User-Agent format, IDE flags, Language Server changelist, and CLI version.

    Supports both:
    - Antigravity 2.x Hub packaged in Electron ASAR (`/opt/Antigravity/resources/app.asar`)
    - Unpacked `product.json` / `package.json` installations
    - Local `language_server` (`--stamp`) and `agy` CLI (`--version`)
    """
    sys_os = platform.system().lower()
    os_name = "darwin" if sys_os == "darwin" else ("win32" if sys_os == "windows" else "linux")
    arch_raw = platform.machine().lower()
    arch_name = "arm64" if arch_raw in ("aarch64", "arm64") else "x64"

    info = {
        "installed": False,
        "source": None,
        "source_type": None,
        "ide_version": None,
        "product_client_version": None,
        "ide_name": "antigravity",
        "subclient_type": "hub",
        "user_agent_name": "antigravity",
        "computed_user_agent": None,
        "cloud_code_endpoint": None,
        "language_server_path": None,
        "language_server_cl": None,
        "language_server_built_on": None,
        "agy_cli_path": None,
        "agy_cli_version": None,
        "os_arch": f"{os_name}/{arch_name}",
        "checked_at": int(time.time()),
    }

    # 1. Check ASAR archives (Antigravity 2.x Hub on Linux/macOS/Windows)
    asar_candidates = [
        "/opt/Antigravity/resources/app.asar",
        "/opt/antigravity/resources/app.asar",
        "/usr/share/antigravity/resources/app.asar",
        os.path.expanduser("~/.local/share/antigravity/resources/app.asar"),
        "/snap/antigravity/current/resources/app.asar",
        "/Applications/Antigravity.app/Contents/Resources/app.asar",
        os.path.expanduser("~/Applications/Antigravity.app/Contents/Resources/app.asar"),
    ]
    for asar_path in asar_candidates:
        if os.path.isfile(asar_path):
            try:
                pkg_raw = _read_asar_file(asar_path, ["package.json"])
                if pkg_raw:
                    pkg_data = json.loads(pkg_raw)
                    ver = pkg_data.get("ideVersion") or pkg_data.get("version")
                    if ver:
                        info["installed"] = True
                        info["source"] = asar_path
                        info["source_type"] = "app.asar"
                        info["ide_version"] = str(ver).strip()

                prod_raw = _read_asar_file(asar_path, ["product.json"])
                if prod_raw:
                    prod_data = json.loads(prod_raw)
                    if prod_data.get("ideVersion"):
                        info["ide_version"] = str(prod_data["ideVersion"]).strip()
                    if prod_data.get("version"):
                        info["product_client_version"] = str(prod_data["version"]).strip()

                ls_js = _read_asar_file(asar_path, ["dist", "languageServer.js"])
                if ls_js:
                    m_ide = re.search(r"['\"]--override_ide_name['\"]\s*,\s*['\"]([^'\"]+)['\"]", ls_js)
                    if m_ide:
                        info["ide_name"] = m_ide.group(1)
                    m_sub = re.search(r"['\"]--subclient_type['\"]\s*,\s*['\"]([^'\"]+)['\"]", ls_js)
                    if m_sub:
                        info["subclient_type"] = m_sub.group(1)
                    m_ua = re.search(r"['\"]--override_user_agent_name['\"]\s*,\s*['\"]([^'\"]+)['\"]", ls_js)
                    if m_ua:
                        info["user_agent_name"] = m_ua.group(1)
                    m_ep = re.search(r"['\"]--cloud_code_endpoint['\"]\s*,\s*['\"]([^'\"]+)['\"]", ls_js)
                    if m_ep:
                        info["cloud_code_endpoint"] = m_ep.group(1)
                if info["installed"]:
                    break
            except Exception:
                pass

    # 2. Check unpacked product.json if ASAR wasn't found or to supplement product_client_version
    product_candidates = [
        "/opt/Antigravity/resources/app/product.json",
        "/opt/antigravity/resources/app/product.json",
        "/usr/share/antigravity/resources/app/product.json",
        os.path.expanduser("~/.local/share/antigravity/resources/app/product.json"),
        "/snap/antigravity/current/resources/app/product.json",
        "/Applications/Antigravity.app/Contents/Resources/app/product.json",
    ]
    for prod_path in product_candidates:
        if os.path.isfile(prod_path):
            try:
                with open(prod_path, "r", encoding="utf-8") as pf:
                    pdata = json.load(pf)
                if pdata.get("ideVersion") and not info["ide_version"]:
                    info["ide_version"] = str(pdata["ideVersion"]).strip()
                    info["installed"] = True
                    info["source"] = prod_path
                    info["source_type"] = "product.json"
                if pdata.get("version") and not info["product_client_version"]:
                    info["product_client_version"] = str(pdata["version"]).strip()
                    if not info["installed"]:
                        info["installed"] = True
                        info["source"] = prod_path
                        info["source_type"] = "product.json"
                break
            except Exception:
                pass

    # 3. Check local Language Server binary (--stamp)
    ls_candidates = [
        "/opt/Antigravity/resources/bin/language_server",
        "/opt/antigravity/resources/bin/language_server",
        "/usr/share/antigravity/resources/bin/language_server",
        os.path.expanduser("~/.local/share/antigravity/resources/bin/language_server"),
    ]
    for ls_bin in ls_candidates:
        if os.path.isfile(ls_bin) and os.access(ls_bin, os.X_OK):
            info["language_server_path"] = ls_bin
            try:
                res = subprocess.run([ls_bin, "--stamp"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout:
                    m_cl = re.search(r"Built at CL:\s*(\d+)", res.stdout)
                    if m_cl:
                        info["language_server_cl"] = m_cl.group(1)
                    m_on = re.search(r"Built on:\s*([^\n]+)", res.stdout)
                    if m_on:
                        info["language_server_built_on"] = m_on.group(1).strip()
            except Exception:
                pass
            break

    # 4. Check local `agy` CLI
    agy_candidates = [
        os.path.expanduser("~/.local/bin/agy"),
        "/usr/local/bin/agy",
        "/usr/bin/agy",
    ]
    for agy_bin in agy_candidates:
        if os.path.isfile(agy_bin) and os.access(agy_bin, os.X_OK):
            info["agy_cli_path"] = agy_bin
            try:
                res = subprocess.run([agy_bin, "--version"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout.strip():
                    info["agy_cli_version"] = res.stdout.strip().splitlines()[-1].strip()
            except Exception:
                pass
            break

    # 5. Compute exact User-Agent matching the installed Antigravity binary
    if info["ide_version"]:
        ua_prefix = info["user_agent_name"] or "antigravity"
        sub = info["subclient_type"]
        if sub:
            info["computed_user_agent"] = f"{ua_prefix}/{sub}/{info['ide_version']} {os_name}/{arch_name}"
        else:
            info["computed_user_agent"] = f"{ua_prefix}/{info['ide_version']} {os_name}/{arch_name}"

    return info


def _get_git_info(acp_path):
    """Returns git commit hash, branch, and last commit subject of antigravity-claude-proxy."""
    if not acp_path or not os.path.isdir(os.path.join(acp_path, ".git")):
        return {"commit": "unknown", "branch": "unknown", "subject": ""}
    try:
        commit = subprocess.run(
            ["git", "-C", acp_path, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", acp_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3
        ).stdout.strip()
        subject = subprocess.run(
            ["git", "-C", acp_path, "log", "-1", "--pretty=%s"],
            capture_output=True, text=True, timeout=3
        ).stdout.strip()
        return {"commit": commit, "branch": branch, "subject": subject}
    except Exception:
        return {"commit": "unknown", "branch": "unknown", "subject": ""}


def _extract_spec_via_node(acp_path, node_bin):
    """
    Executes antigravity-claude-proxy's actual ESM modules via Node.js to extract:
    - ANTIGRAVITY_HEADERS (with live User-Agent & X-Client-Version)
    - LOAD_CODE_ASSIST_HEADERS
    - CLIENT_METADATA
    - ANTIGRAVITY_SYSTEM_INSTRUCTION
    - ANTIGRAVITY_ENDPOINT_FALLBACKS & LOAD_CODE_ASSIST_ENDPOINTS
    - Sample output of buildCloudCodeRequest & buildHeaders to capture exact structure
    """
    constants_path = os.path.join(acp_path, "src", "constants.js")
    req_builder_path = os.path.join(acp_path, "src", "cloudcode", "request-builder.js")
    pkg_path = os.path.join(acp_path, "package.json")

    js_code = f"""
import * as constants from {json.dumps(constants_path)};
import {{ buildCloudCodeRequest, buildHeaders }} from {json.dumps(req_builder_path)};
import {{ readFileSync }} from 'fs';

let pkgVersion = 'unknown';
try {{
  const pkg = JSON.parse(readFileSync({json.dumps(pkg_path)}, 'utf8'));
  pkgVersion = pkg.version || 'unknown';
}} catch (e) {{}}

const sampleReq = buildCloudCodeRequest({{
  model: 'gemini-3.8-flash-high',
  messages: [{{ role: 'user', content: 'ping' }}],
  system: 'Hermes Agent by Nous Research',
  max_tokens: 1024
}}, 'sample-project', 'sample@example.com');

const sampleHeaders = buildHeaders('SAMPLE_TOKEN', 'claude-sonnet-5-5-high', 'text/event-stream', 'SAMPLE_SESSION');

const out = {{
  packageVersion: pkgVersion,
  antigravityHeaders: constants.ANTIGRAVITY_HEADERS,
  loadCodeAssistHeaders: constants.LOAD_CODE_ASSIST_HEADERS,
  clientMetadata: constants.CLIENT_METADATA,
  systemInstruction: constants.ANTIGRAVITY_SYSTEM_INSTRUCTION,
  endpointFallbacks: constants.ANTIGRAVITY_ENDPOINT_FALLBACKS,
  loadCodeAssistEndpoints: constants.LOAD_CODE_ASSIST_ENDPOINTS,
  geminiSkipSignature: constants.GEMINI_SKIP_SIGNATURE || 'skip_thought_signature_validator',
  geminiMaxOutputTokens: constants.GEMINI_MAX_OUTPUT_TOKENS || 65536,
  oauthConfig: constants.OAUTH_CONFIG || null,
  sampleEnvelope: {{
    userAgent: sampleReq.userAgent,
    requestType: sampleReq.requestType,
    requestIdPrefix: (sampleReq.requestId || 'agent-').split('-')[0] + '-',
    systemInstructionRole: sampleReq.request?.systemInstruction?.role || 'user',
    systemInstructionParts: sampleReq.request?.systemInstruction?.parts || []
  }},
  sampleHeaders
}};

process.stdout.write('__ACP_JSON_START__' + JSON.stringify(out) + '__ACP_JSON_END__');
"""
    res = subprocess.run(
        [node_bin, "--input-type=module", "-e", js_code],
        capture_output=True, text=True, timeout=8, cwd=acp_path
    )
    if res.returncode != 0:
        raise RuntimeError(f"Node bridge failed: {res.stderr}")

    stdout = res.stdout
    start_tag = "__ACP_JSON_START__"
    end_tag = "__ACP_JSON_END__"
    if start_tag not in stdout or end_tag not in stdout:
        raise RuntimeError("Node bridge did not return expected JSON markers")

    raw_json = stdout.split(start_tag, 1)[1].split(end_tag, 1)[0]
    return json.loads(raw_json)


def _extract_spec_via_source_parser(acp_path):
    """
    Fallback parser that reads antigravity-claude-proxy's JS source files directly
    in pure Python if Node.js is not available on the host.
    """
    constants_file = os.path.join(acp_path, "src", "constants.js")
    vd_file = os.path.join(acp_path, "src", "utils", "version-detector.js")
    rb_file = os.path.join(acp_path, "src", "cloudcode", "request-builder.js")
    pkg_file = os.path.join(acp_path, "package.json")

    pkg_version = "unknown"
    if os.path.exists(pkg_file):
        try:
            with open(pkg_file, "r", encoding="utf-8") as f:
                pkg_version = json.load(f).get("version", "unknown")
        except Exception:
            pass

    vd_text = ""
    if os.path.exists(vd_file):
        with open(vd_file, "r", encoding="utf-8") as f:
            vd_text = f.read()

    c_text = ""
    if os.path.exists(constants_file):
        with open(constants_file, "r", encoding="utf-8") as f:
            c_text = f.read()

    rb_text = ""
    if os.path.exists(rb_file):
        with open(rb_file, "r", encoding="utf-8") as f:
            rb_text = f.read()

    # Extract fallback versions from version-detector.js
    ua_ver_m = re.search(r"FALLBACK_USER_AGENT_VERSION\s*=.*?['\"]([^'\"]+)['\"]", vd_text)
    ua_version = os.environ.get("FALLBACK_ANTIGRAVITY_VERSION") or (ua_ver_m.group(1) if ua_ver_m else "2.25.0")

    cli_ver_m = re.search(r"FALLBACK_CLIENT_VERSION\s*=.*?['\"]([^'\"]+)['\"]", vd_text)
    client_version = os.environ.get("ANTIGRAVITY_CLIENT_VERSION") or (cli_ver_m.group(1) if cli_ver_m else "1.110.0")

    # Check local Antigravity product.json if present (same priority as version-detector.js)
    product_paths = [
        "/usr/share/antigravity/resources/app/product.json",
        "/opt/antigravity/resources/app/product.json",
        "/opt/Antigravity/resources/app/product.json",
        os.path.expanduser("~/.local/share/antigravity/resources/app/product.json"),
        "/snap/antigravity/current/resources/app/product.json",
    ]
    for pp in product_paths:
        if os.path.isfile(pp):
            try:
                with open(pp, "r", encoding="utf-8") as pf:
                    pdata = json.load(pf)
                    if pdata.get("ideVersion"):
                        ua_version = pdata["ideVersion"]
                    if pdata.get("version"):
                        client_version = pdata["version"]
                break
            except Exception:
                pass

    sys_os = platform.system().lower()
    os_name = "darwin" if sys_os == "darwin" else ("win32" if sys_os == "windows" else "linux")
    arch_raw = platform.machine().lower()
    arch_name = "arm64" if arch_raw in ("aarch64", "arm64") else "x64"

    # Extract User-Agent template from version-detector.js
    if "antigravity/hub/" in vd_text or not vd_text:
        user_agent = f"antigravity/hub/{ua_version} {os_name}/{arch_name}"
    else:
        user_agent = f"antigravity/{ua_version} {os_name}/{arch_name}"

    # Extract x-goog-api-client from constants.js
    goog_client_m = re.search(r"['\"]x-goog-api-client['\"]\s*:\s*['\"]([^'\"]+)['\"]", c_text)
    goog_client = goog_client_m.group(1) if goog_client_m else "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x"

    # Extract ANTIGRAVITY_SYSTEM_INSTRUCTION from constants.js
    sys_inst_m = re.search(r"ANTIGRAVITY_SYSTEM_INSTRUCTION\s*=\s*`([^`]+)`", c_text)
    sys_inst = sys_inst_m.group(1) if sys_inst_m else (
        "You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team "
        "working on Advanced Agentic Coding.You are pair programming with a USER to solve their coding task. "
        "The task may require creating a new codebase, modifying or debugging an existing codebase, or simply "
        "answering a question.**Absolute paths only****Proactiveness**"
    )

    antigravity_headers = {
        "User-Agent": user_agent,
        "Content-Type": "application/json",
        "X-Client-Name": "antigravity",
        "X-Client-Version": client_version,
        "x-goog-api-client": goog_client,
    }

    platform_enum = 4 if arch_name == "arm64" else 3
    if os_name == "darwin":
        platform_enum = 2 if arch_name == "arm64" else 1
    elif os_name == "win32":
        platform_enum = 5

    return {
        "packageVersion": pkg_version,
        "antigravityHeaders": antigravity_headers,
        "loadCodeAssistHeaders": antigravity_headers,
        "clientMetadata": {
            "ideType": 9,
            "platform": platform_enum,
            "pluginType": 2,
        },
        "systemInstruction": sys_inst,
        "endpointFallbacks": [
            "https://daily-cloudcode-pa.googleapis.com",
            "https://cloudcode-pa.googleapis.com",
        ],
        "loadCodeAssistEndpoints": [
            "https://cloudcode-pa.googleapis.com",
            "https://daily-cloudcode-pa.googleapis.com",
        ],
        "geminiSkipSignature": "skip_thought_signature_validator",
        "geminiMaxOutputTokens": 65536,
        "oauthConfig": None,
        "sampleEnvelope": {
            "userAgent": "antigravity",
            "requestType": "agent",
            "requestIdPrefix": "agent-",
            "systemInstructionRole": "user",
            "systemInstructionParts": [
                {"text": sys_inst},
                {"text": f"Please ignore the following [ignore]{sys_inst}[/ignore]"},
            ],
        },
        "sampleHeaders": {
            **antigravity_headers,
            "X-Machine-Session-Id": "SAMPLE_SESSION",
            "anthropic-beta": "interleaved-thinking-2025-05-14",
            "Accept": "text/event-stream",
        },
    }


def _extract_scrub_rules_from_acp(acp_path):
    """Reads DEFAULT_IDENTITY_SCRUB from antigravity-claude-proxy/src/cloudcode/request-builder.js."""
    rules = []
    if acp_path:
        rb_file = os.path.join(acp_path, "src", "cloudcode", "request-builder.js")
        if os.path.isfile(rb_file):
            try:
                with open(rb_file, "r", encoding="utf-8") as f:
                    text = f.read()
                for m in re.finditer(r"\[\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\]", text):
                    rules.append((m.group(1), m.group(2)))
            except Exception:
                pass
    return rules


def get_backend_spec(force_reload=False):
    """
    Returns the current masking & API specification from antigravity-claude-proxy.
    Automatically reloads if any file in the antigravity-claude-proxy repo was modified.
    """
    acp_path = find_acp_repo_path()
    current_mtime = _get_repo_mtime(acp_path)

    if (
        not force_reload
        and _BACKEND_CACHE["spec"] is not None
        and _BACKEND_CACHE["acp_path"] == acp_path
        and _BACKEND_CACHE["mtime"] == current_mtime
    ):
        return _BACKEND_CACHE["spec"]

    node_bin = find_node_binary()
    spec = None
    mode = "source-parser"

    if acp_path and node_bin:
        try:
            spec = _extract_spec_via_node(acp_path, node_bin)
            mode = "node-esm-bridge"
        except Exception as e:
            print(f"[ACP Backend] Node bridge fallback to source parser: {e}")

    if spec is None:
        spec = _extract_spec_via_source_parser(acp_path or "")

    # Attach scrub rules and git metadata
    acp_scrub = _extract_scrub_rules_from_acp(acp_path)
    default_scrub = [
        ("Nous Research", "the assistant team"),
        ("Hermes Agent", "the assistant"),
        ("Hermes", "the assistant"),
        ("OpenClaw", "the assistant"),
        ("ClawdBot", "the assistant"),
        ("OpenCode", "the coding environment"),
        ("Claude Code", "the coding environment"),
        ("Cursor", "the IDE"),
        ("Cline", "the coding assistant"),
        ("Roo Code", "the coding assistant"),
    ]
    seen_terms = set()
    merged_scrub = []
    for term, repl in acp_scrub + default_scrub:
        if term not in seen_terms:
            seen_terms.add(term)
            merged_scrub.append((term, repl))

    # Also support ANTIGRAVITY_SCRUB_IDENTITY env var (same format as antigravity-claude-proxy)
    raw_env_scrub = os.environ.get("ANTIGRAVITY_SCRUB_IDENTITY", "")
    if raw_env_scrub:
        for pair in raw_env_scrub.split(","):
            if "=>" in pair:
                k, v = pair.split("=>", 1)
                if k.strip():
                    merged_scrub.append((k.strip(), v.strip()))

    spec["identityScrubRules"] = merged_scrub
    spec["gitInfo"] = _get_git_info(acp_path)
    spec["acpPath"] = acp_path
    spec["extractionMode"] = mode
    spec["loadedAt"] = int(time.time())

    # Inspect locally installed Antigravity binary/ASAR and synchronize identifiers
    local_ag = inspect_local_antigravity_installation()
    acp_default_ua = (spec.get("antigravityHeaders") or {}).get("User-Agent", "")
    acp_default_cver = (spec.get("antigravityHeaders") or {}).get("X-Client-Version", "1.110.0")

    # Read MIN_DETECTED_USER_AGENT_VERSION from antigravity-claude-proxy/src/utils/version-detector.js
    min_supported_ua_ver = "2.0.3"
    if acp_path:
        vd_file = os.path.join(acp_path, "src", "utils", "version-detector.js")
        if os.path.isfile(vd_file):
            try:
                with open(vd_file, "r", encoding="utf-8") as vf:
                    m_min = re.search(r"MIN_DETECTED_USER_AGENT_VERSION\s*=\s*['\"]([^'\"]+)['\"]", vf.read())
                    if m_min:
                        min_supported_ua_ver = m_min.group(1)
            except Exception:
                pass

    synced_with_local = False
    sync_reason = "Usando fallback do antigravity-claude-proxy (Antigravity local não encontrado)"

    if local_ag.get("installed") and local_ag.get("ide_version"):
        local_ver = local_ag["ide_version"]
        if _is_version_higher(local_ver, min_supported_ua_ver) or local_ver == min_supported_ua_ver:
            computed_ua = local_ag.get("computed_user_agent")
            if computed_ua:
                if "antigravityHeaders" in spec and isinstance(spec["antigravityHeaders"], dict):
                    spec["antigravityHeaders"]["User-Agent"] = computed_ua
                    spec["antigravityHeaders"]["X-Client-Name"] = local_ag.get("ide_name") or "antigravity"
                if "loadCodeAssistHeaders" in spec and isinstance(spec["loadCodeAssistHeaders"], dict):
                    spec["loadCodeAssistHeaders"]["User-Agent"] = computed_ua
                    spec["loadCodeAssistHeaders"]["X-Client-Name"] = local_ag.get("ide_name") or "antigravity"
                synced_with_local = True
                sync_reason = (
                    f"Sincronizado com o Antigravity instalado na máquina "
                    f"(v{local_ver} via {local_ag.get('source_type')})"
                )
            if local_ag.get("product_client_version"):
                spec["antigravityHeaders"]["X-Client-Version"] = local_ag["product_client_version"]
                spec["loadCodeAssistHeaders"]["X-Client-Version"] = local_ag["product_client_version"]
        else:
            sync_reason = (
                f"Antigravity local (v{local_ver}) é anterior ao mínimo exigido "
                f"(v{min_supported_ua_ver}); mantendo máscara v{acp_default_ua}"
            )

    spec["localAntigravity"] = local_ag
    spec["identifierSync"] = {
        "synced_with_local": synced_with_local,
        "reason": sync_reason,
        "min_supported_version": min_supported_ua_ver,
        "local_ide_version": local_ag.get("ide_version"),
        "local_user_agent": local_ag.get("computed_user_agent"),
        "acp_fallback_user_agent": acp_default_ua,
        "active_user_agent": (spec.get("antigravityHeaders") or {}).get("User-Agent", ""),
        "active_client_version": (spec.get("antigravityHeaders") or {}).get("X-Client-Version", acp_default_cver),
        "checked_at": int(time.time()),
    }

    _BACKEND_CACHE["spec"] = spec
    _BACKEND_CACHE["mtime"] = current_mtime
    _BACKEND_CACHE["loaded_at"] = time.time()
    _BACKEND_CACHE["acp_path"] = acp_path
    _BACKEND_CACHE["node_bin"] = node_bin
    _BACKEND_CACHE["extraction_mode"] = mode

    return spec


def derive_session_id(account_email=None):
    """
    Matches antigravity-claude-proxy/src/cloudcode/session-manager.js:
    Generates `randomUUID() + Date.now()` per account for the lifetime of the process.
    """
    key = account_email or "__default__"
    if key in _RUNTIME_SESSION_STORE:
        return _RUNTIME_SESSION_STORE[key]
    session_id = f"{uuid.uuid4()}{int(time.time() * 1000)}"
    _RUNTIME_SESSION_STORE[key] = session_id
    return session_id


def build_antigravity_headers(token, model="", accept="application/json", session_id=None):
    """
    Builds HTTP headers for Google Cloud Code requests using the latest
    masking from antigravity-claude-proxy (`buildHeaders` in request-builder.js).
    """
    spec = get_backend_spec()
    base_headers = dict(spec.get("antigravityHeaders") or {})
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        **base_headers,
    }

    if session_id:
        headers["X-Machine-Session-Id"] = session_id

    lower_model = (model or "").lower()
    if "claude" in lower_model:
        headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

    if accept and accept != "application/json":
        headers["Accept"] = accept

    return headers


def get_masked_system_instruction_parts(user_system_parts=None, scrub_fn=None):
    """
    Builds the `systemInstruction` object exactly as `antigravity-claude-proxy` does
    in `buildCloudCodeRequest`:
    1. Prepends `ANTIGRAVITY_SYSTEM_INSTRUCTION`
    2. Prepends `Please ignore the following [ignore]${ANTIGRAVITY_SYSTEM_INSTRUCTION}[/ignore]`
    3. Appends caller's system instruction parts after passing through anti-bot scrubbing.
    """
    spec = get_backend_spec()
    sys_inst = spec.get("systemInstruction", "")
    envelope_info = spec.get("sampleEnvelope", {})
    role = envelope_info.get("systemInstructionRole", "user")

    parts = [
        {"text": sys_inst},
        {"text": f"Please ignore the following [ignore]{sys_inst}[/ignore]"},
    ]

    if user_system_parts:
        for p in user_system_parts:
            if isinstance(p, dict) and p.get("text"):
                txt = scrub_fn(p["text"]) if scrub_fn else p["text"]
                parts.append({"text": txt})
            elif isinstance(p, str) and p:
                txt = scrub_fn(p) if scrub_fn else p
                parts.append({"text": txt})

    return {
        "role": role,
        "parts": parts,
    }


def update_backend_repo():
    """
    Runs `git pull` on the local `antigravity-claude-proxy` repository and reloads
    the backend specification so any new masking or API changes take effect immediately.
    """
    acp_path = find_acp_repo_path()
    if not acp_path:
        return {"success": False, "error": "Repositório antigravity-claude-proxy não encontrado."}

    try:
        pull_res = subprocess.run(
            ["git", "-C", acp_path, "pull", "--ff-only"],
            capture_output=True, text=True, timeout=20
        )
        spec = get_backend_spec(force_reload=True)
        summary = get_backend_status_summary(spec)
        out_str = (pull_res.stdout or pull_res.stderr).strip()
        return {
            "success": pull_res.returncode == 0,
            "output": out_str,
            "git_output": out_str,
            "backend": summary,
            "status": summary,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def verify_and_sync_local_antigravity():
    """
    Forces a fresh inspection of the locally installed Antigravity binary/ASAR on the host machine
    and synchronizes the proxy's outgoing identifiers (`User-Agent`, `X-Client-Name`, `X-Client-Version`)
    with the local installation and `antigravity-claude-proxy`.
    """
    spec = get_backend_spec(force_reload=True)
    summary = get_backend_status_summary(spec)
    local_ag = spec.get("localAntigravity") or {}
    sync_info = spec.get("identifierSync") or {}

    return {
        "success": True,
        "synced_with_local": bool(sync_info.get("synced_with_local")),
        "reason": sync_info.get("reason", ""),
        "active_user_agent": summary.get("user_agent", ""),
        "active_client_version": summary.get("client_version", ""),
        "active_client_name": summary.get("client_name", "antigravity"),
        "goog_api_client": summary.get("goog_api_client", ""),
        "local_antigravity": local_ag,
        "identifier_sync": sync_info,
        "backend": summary,
    }


def get_backend_status_summary(spec=None):
    """Returns a summary dict of the active antigravity-claude-proxy backend and local Antigravity sync for the Web UI."""
    if spec is None:
        spec = get_backend_spec()
    ag_headers = spec.get("antigravityHeaders", {})
    git_info = spec.get("gitInfo", {})
    local_ag = spec.get("localAntigravity", {})
    sync_info = spec.get("identifierSync", {})
    return {
        "connected": bool(spec.get("acpPath")),
        "acp_path": spec.get("acpPath"),
        "package_version": spec.get("packageVersion", "unknown"),
        "extraction_mode": spec.get("extractionMode", "source-parser"),
        "git_commit": git_info.get("commit", "unknown"),
        "git_branch": git_info.get("branch", "main"),
        "git_subject": git_info.get("subject", ""),
        "user_agent": ag_headers.get("User-Agent", ""),
        "client_version": ag_headers.get("X-Client-Version", ""),
        "client_name": ag_headers.get("X-Client-Name", "antigravity"),
        "goog_api_client": ag_headers.get("x-goog-api-client", ""),
        "client_metadata": spec.get("clientMetadata", {}),
        "endpoint_fallbacks": spec.get("endpointFallbacks", []),
        "system_instruction_masked": True,
        "anti_bot_scrub_rules_count": len(spec.get("identityScrubRules", [])),
        "local_antigravity": local_ag,
        "identifier_sync": sync_info,
        "loaded_at": spec.get("loadedAt", 0),
    }
