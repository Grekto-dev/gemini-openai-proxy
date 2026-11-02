"""
Structured Automatic Logging, Error Tracking & Self-Diagnostics for Gemini-OpenAI Proxy.

Provides:
- Real-time colorized terminal output (stdout/stderr)
- Persistent file logging (`server.log`) with automatic credential redaction
- Thread-safe in-memory ring buffer for live WebUI streaming & error notifications
- Built-in self-diagnostics runner (`/api/diagnostics/run`)
"""

import os
import re
import sys
import json
import time
import threading
import traceback
from datetime import datetime
from collections import deque

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE_PATH = os.path.join(BASE_DIR, "server.log")

MAX_LOG_ENTRIES = 500
MAX_ERROR_ENTRIES = 100

_lock = threading.Lock()
_log_counter = 0
_logs_buffer = deque(maxlen=MAX_LOG_ENTRIES)
_errors_buffer = deque(maxlen=MAX_ERROR_ENTRIES)

# ANSI escape sequence stripper
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Credential redaction patterns to guarantee tokens/keys never leak into logs
_REDACT_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(ya29\.[A-Za-z0-9\-._~+/]+)"), "[REDACTED_GOOGLE_TOKEN]"),
    (re.compile(r"(1//[A-Za-z0-9\-._~+/]+)"), "[REDACTED_REFRESH_TOKEN]"),
    (re.compile(r"(sk-[A-Za-z0-9]{8,})"), "[REDACTED_API_KEY]"),
    (re.compile(r'("access_token"\s*:\s*")[^"]+(")', re.I), r"\1[REDACTED]\2"),
    (re.compile(r'("refresh_token"\s*:\s*")[^"]+(")', re.I), r"\1[REDACTED]\2"),
]

# ANSI colors
GRAY = "\033[90m"
BLUE = "\033[94m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"


def redact_sensitive(text: str) -> str:
    """Strips OAuth tokens, refresh tokens, and API keys from any log string."""
    if not text:
        return ""
    out = str(text)
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def strip_ansi(text: str) -> str:
    if not text:
        return ""
    return _ANSI_RE.sub("", str(text))


def record_log(
    level: str,
    category: str,
    message: str,
    details=None,
    print_to_terminal: bool = True,
):
    """
    Records a structured log entry in memory, writes to server.log,
    and optionally prints to the active terminal.
    """
    global _log_counter
    now = datetime.now()
    ts_full = now.strftime("%Y-%m-%d %H:%M:%S")
    ts_short = now.strftime("%H:%M:%S")

    level_up = (level or "INFO").upper()
    cat_up = (category or "PROXY").upper()

    safe_msg_ansi = redact_sensitive(message)
    clean_msg = strip_ansi(safe_msg_ansi)

    safe_details = None
    if details is not None:
        if isinstance(details, (dict, list)):
            try:
                safe_details = json.loads(redact_sensitive(json.dumps(details, ensure_ascii=False)))
            except Exception:
                safe_details = redact_sensitive(str(details))
        else:
            safe_details = redact_sensitive(str(details))

    with _lock:
        _log_counter += 1
        entry = {
            "id": _log_counter,
            "timestamp": ts_full,
            "time": ts_short,
            "level": level_up,
            "category": cat_up,
            "message": clean_msg,
            "details": safe_details,
        }
        _logs_buffer.append(entry)
        if level_up in ("ERROR", "CRITICAL"):
            _errors_buffer.append(entry)

        # Write to server.log (avoid duplicate lines if stdout is already redirected to server.log in --silent mode)
        is_stdout_log_file = os.environ.get("PROXY_SILENT_MODE") == "1"
        if not is_stdout_log_file:
            try:
                with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
                    line = f"[{ts_full}] [{level_up}] [{cat_up}] {clean_msg}\n"
                    if safe_details and level_up in ("ERROR", "WARN"):
                        detail_str = (
                            json.dumps(safe_details, ensure_ascii=False)
                            if isinstance(safe_details, (dict, list))
                            else str(safe_details)
                        )
                        line += f"    ↳ Details: {detail_str}\n"
                    f.write(line)
            except Exception:
                pass

    if print_to_terminal:
        level_badge = {
            "INFO": f"{GREEN}INFO{RESET}",
            "WARN": f"{YELLOW}WARN{RESET}",
            "ERROR": f"{RED}{BOLD}ERRO{RESET}",
            "DEBUG": f"{MAGENTA}DBUG{RESET}",
        }.get(level_up, level_up)
        stream = sys.stderr if level_up == "ERROR" else sys.stdout
        print(f"{GRAY}[{ts_short}]{RESET} [{level_badge}] {safe_msg_ansi}", file=stream, flush=True)
        if safe_details and level_up == "ERROR":
            detail_preview = (
                json.dumps(safe_details, ensure_ascii=False)
                if isinstance(safe_details, (dict, list))
                else str(safe_details)
            )
            first_line = detail_preview.splitlines()[0][:240] if detail_preview else ""
            if first_line:
                print(f"           {RED}↳ {first_line}{RESET}", file=stream, flush=True)

    return entry


def log_error(category: str, message: str, exc: Exception = None, extra_details: dict = None):
    """Helper to record an error with optional exception & stack trace."""
    details = {}
    if extra_details and isinstance(extra_details, dict):
        details.update(extra_details)
    if exc is not None:
        details["exception_type"] = type(exc).__name__
        details["exception_message"] = str(exc)
        details["traceback"] = traceback.format_exc()
    return record_log("ERROR", category, message, details=details if details else None)


def get_logs(since_id: int = 0, level: str = None, limit: int = 200) -> dict:
    """Returns logs newer than `since_id` along with summary statistics."""
    with _lock:
        items = list(_logs_buffer)
        err_count = len(_errors_buffer)
        last_err = _errors_buffer[-1] if _errors_buffer else None

    if since_id > 0:
        items = [x for x in items if x["id"] > since_id]

    if level and level.upper() not in ("ALL", "TODOS"):
        lvl = level.upper()
        if lvl == "ERROR":
            items = [x for x in items if x["level"] in ("ERROR", "CRITICAL")]
        elif lvl == "WARN":
            items = [x for x in items if x["level"] in ("WARN", "ERROR", "CRITICAL")]
        else:
            items = [x for x in items if x["level"] == lvl]

    if limit and len(items) > limit:
        items = items[-limit:]

    return {
        "logs": items,
        "total_buffered": len(_logs_buffer),
        "error_count": err_count,
        "last_error": last_err,
        "latest_id": _log_counter,
    }


def clear_logs() -> dict:
    """Clears in-memory log buffers and truncates server.log."""
    with _lock:
        _logs_buffer.clear()
        _errors_buffer.clear()
        try:
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass
    record_log("INFO", "DIAG", "Histórico de logs e erros limpo pelo usuário.")
    return {"success": True}


def run_self_diagnostics() -> dict:
    """
    Executes an automated diagnostic battery across all proxy subsystems
    and logs the results to terminal and WebUI.
    """
    record_log("INFO", "DIAG", "Iniciando bateria de autodiagnóstico do sistema...")
    checks = []
    overall_ok = True

    # 1. Check antigravity-claude-proxy backend bridge & Local Antigravity Sync
    t0 = time.time()
    try:
        import acp_backend
        sync_res = acp_backend.verify_and_sync_local_antigravity()
        st = sync_res.get("backend") or {}
        loc = sync_res.get("local_antigravity") or {}
        ok = bool(st.get("connected") and st.get("user_agent", "").startswith("antigravity/hub/"))
        loc_str = f"Local v{loc.get('ide_version')} (CL {loc.get('language_server_cl')})" if loc.get("installed") else "Fallback ACP"
        checks.append({
            "name": "Ponte Backend & Antigravity Local",
            "ok": ok,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": f"Sincronia: {loc_str} | UA: {st.get('user_agent')} | X-Client: {st.get('client_version')}"
        })
        if not ok:
            overall_ok = False
    except Exception as e:
        overall_ok = False
        checks.append({
            "name": "Ponte Backend & Antigravity Local",
            "ok": False,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": str(e)
        })

    # 2. Check Anti-Bot Identity Scrubbing & Masked System Prompt
    t0 = time.time()
    try:
        import converter
        sample_req = {
            "model": "gemini-4-argon",
            "reasoning_effort": "medium",
            "messages": [
                {"role": "system", "content": "You are Hermes Agent by Nous Research using OpenCode and Claude Code."},
                {"role": "user", "content": "Ping"}
            ]
        }
        out = converter.openai_to_google_request(sample_req, "diag-proj", account_email="diag@local")
        sys_parts = out.get("request", {}).get("systemInstruction", {}).get("parts", [])
        serialized = json.dumps(sys_parts)
        leaked = any(w in serialized.lower() for w in ("hermes", "nous research", "opencode", "claude code"))
        has_antigravity_mask = "You are Antigravity, a powerful agentic AI coding assistant" in serialized
        ok = (not leaked) and has_antigravity_mask and out.get("model") == "gemini-4-argon-medium"
        checks.append({
            "name": "Filtro Anti-Bot & Máscara de Identidade",
            "ok": ok,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": f"Scrubbing ativo: {'OK' if not leaked else 'FALHA'} | Máscara Antigravity: {'OK' if has_antigravity_mask else 'FALHA'} | Modelo resolvido: {out.get('model')}"
        })
        if not ok:
            overall_ok = False
    except Exception as e:
        overall_ok = False
        checks.append({
            "name": "Filtro Anti-Bot & Máscara de Identidade",
            "ok": False,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": str(e)
        })

    # 3. Check OAuth / Keyring Credentials & loadCodeAssist
    t0 = time.time()
    try:
        import auth
        token, project_id = auth.get_credentials()
        email = auth.get_active_email()
        ok = bool(token and project_id)
        checks.append({
            "name": "Autenticação OAuth / Keyring & loadCodeAssist",
            "ok": ok,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": f"Conta: {email or 'Desconhecida'} | Projeto: {project_id}"
        })
        if not ok:
            overall_ok = False
    except Exception as e:
        overall_ok = False
        checks.append({
            "name": "Autenticação OAuth / Keyring & loadCodeAssist",
            "ok": False,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": str(e)
        })

    # 4. Check Quota Retrieval (fetchAvailableModels)
    t0 = time.time()
    try:
        import auth
        q = auth.get_quota_details()
        pools = q.get("pools", [])
        models = q.get("models", [])
        ok = len(pools) >= 2 and len(models) > 0
        checks.append({
            "name": "Cota de Uso (v1internal:fetchAvailableModels)",
            "ok": ok,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": f"{len(pools)} pools ativos ({', '.join(p['id'] for p in pools)}) | {len(models)} modelos monitorados"
        })
        if not ok:
            overall_ok = False
    except Exception as e:
        overall_ok = False
        checks.append({
            "name": "Cota de Uso (v1internal:fetchAvailableModels)",
            "ok": False,
            "latency_ms": int((time.time() - t0) * 1000),
            "detail": str(e)
        })

    summary_msg = (
        f"Autodiagnóstico concluído: {sum(1 for c in checks if c['ok'])}/{len(checks)} verificações aprovadas."
    )
    record_log("INFO" if overall_ok else "WARN", "DIAG", summary_msg, details={"checks": checks})

    return {
        "success": overall_ok,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "checks": checks,
        "summary": summary_msg,
    }
