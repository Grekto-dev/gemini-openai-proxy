"""
OpenAI-Compatible Micro-Server and Web Dashboard for Hermes.
Backed by Google AI Pro via Antigravity.

Features:
- Web Dashboard SPA served at http://localhost:<port>/
- OpenAI API compatibility: /v1/models and /v1/chat/completions
- Real-time quota consumption API: /v1/quota
- Bi-directional i18n terminal logging and UI (pt-BR / en)
- Hermes function calling & multi-turn tool calling
- Real-time streaming SSE with reasoning_content support
"""

import sys
import os
import json
import time
import uuid
import requests
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import auth
import converter
import config
import accounts
import api_keys
from i18n import log_terminal, get_ui_translations

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# State for pause/resume toggle
SERVER_STATE = {"running": True}

AVAILABLE_MODELS = [
    # Dynamic-effort base models (Hermes controls effort dynamically)
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash (Effort Dinâmico)", "owned_by": "google"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5 (Effort Dinâmico)", "owned_by": "anthropic"},
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5 (Effort Dinâmico)", "owned_by": "anthropic"},
    {"id": "gemini", "name": "Gemini (Alias Dinâmico)", "owned_by": "google"},
    {"id": "claude", "name": "Claude (Alias Dinâmico)", "owned_by": "anthropic"},

    # Explicit tier models (Fixed Effort)
    {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)", "owned_by": "google"},
    {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)", "owned_by": "google"},
    {"id": "gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)", "owned_by": "google"},
    {"id": "claude-sonnet-5-5-high", "name": "Claude Sonnet 5.5 (High)", "owned_by": "anthropic"},
    {"id": "claude-sonnet-5-5-medium", "name": "Claude Sonnet 5.5 (Medium)", "owned_by": "anthropic"},
    {"id": "claude-sonnet-5-5-low", "name": "Claude Sonnet 5.5 (Low)", "owned_by": "anthropic"},
    {"id": "claude-opus-5-5-high", "name": "Claude Opus 5.5 (High)", "owned_by": "anthropic"},
    {"id": "claude-opus-5-5-medium", "name": "Claude Opus 5.5 (Medium)", "owned_by": "anthropic"},
    {"id": "claude-opus-5-5-low", "name": "Claude Opus 5.5 (Low)", "owned_by": "anthropic"},
    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "owned_by": "google"},
    {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)", "owned_by": "openai"}
]


class ProxyHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, api-key, x-api-key")

    def _check_api_key(self):
        """
        Validates API key if keys are defined in api_keys.
        If no keys are defined, access is completely open/optional.
        """
        if not api_keys.has_keys():
            return True

        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "api-key" in self.headers:
            token = self.headers.get("api-key", "").strip()
        elif "x-api-key" in self.headers:
            token = self.headers.get("x-api-key", "").strip()

        if api_keys.is_valid_key(token):
            return True

        self.send_response(401)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        err = {
            "error": {
                "message": "Chave de API inválida ou ausente. Chaves de API estão ativas e protegendo este servidor.",
                "type": "invalid_request_error",
                "param": None,
                "code": "invalid_api_key"
            }
        }
        self.wfile.write(json.dumps(err).encode("utf-8"))
        return False

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        # 1. Serve WebUI Frontend (HTML)
        if self.path in ("/", "/index.html", "/dashboard"):
            index_path = os.path.join(STATIC_DIR, "index.html")
            if os.path.exists(index_path):
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(index_path, "rb") as f:
                    self.wfile.write(f.read())
                return

        # 2. Serve Static Assets
        if self.path.startswith("/static/"):
            rel_path = self.path[8:].split("?")[0]
            file_path = os.path.join(STATIC_DIR, rel_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                self.send_response(200)
                self._send_cors_headers()
                if file_path.endswith(".css"):
                    self.send_header("Content-Type", "text/css")
                elif file_path.endswith(".js"):
                    self.send_header("Content-Type", "application/javascript")
                elif file_path.endswith(".json"):
                    self.send_header("Content-Type", "application/json")
                else:
                    self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
                return

        # 3. Models List & Detail (OpenAI compatible)
        if self.path in ("/v1/models", "/models", "/api/v1/models"):
            if not self._check_api_key():
                return
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            model_data = [
                {
                    "id": m["id"],
                    "object": "model",
                    "created": 1720000000,
                    "owned_by": m["owned_by"],
                    "permission": [],
                    "root": m["id"],
                    "parent": None
                }
                for m in AVAILABLE_MODELS
            ]
            self.wfile.write(json.dumps({"object": "list", "data": model_data}).encode("utf-8"))
            return

        if self.path.startswith("/v1/models/") or self.path.startswith("/models/"):
            if not self._check_api_key():
                return
            model_id = self.path.split("/models/")[-1].split("?")[0]
            found = next((m for m in AVAILABLE_MODELS if m["id"] == model_id), None)
            if not found:
                found = {"id": model_id, "owned_by": "google"}
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "id": found["id"],
                "object": "model",
                "created": 1720000000,
                "owned_by": found.get("owned_by", "google"),
                "permission": [],
                "root": found["id"],
                "parent": None
            }).encode("utf-8"))
            return

        # 4. Real-time Quota Consumption (Instant refresh without caching)
        if self.path.startswith("/v1/quota") or self.path.startswith("/quota"):
            try:
                quota_info = auth.get_quota_details()
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(json.dumps(quota_info, indent=2).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Failed to retrieve quota: {str(e)}"}).encode("utf-8"))
            return

        # 5. Configuration API
        if self.path == "/api/config":
            cfg = config.load_config()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(cfg).encode("utf-8"))
            return

        # 6. Auth Status API
        if self.path in ("/api/auth/status", "/api/status"):
            status = auth.get_auth_status()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        # 7. Accounts List API
        if self.path == "/api/accounts":
            accs = accounts.list_accounts()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(accs).encode("utf-8"))
            return

        # 8. Start Google OAuth API
        if self.path == "/api/oauth/start":
            try:
                flow = accounts.start_oauth_flow()
                log_terminal("oauth_started", port=flow["port"])
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, **flow}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 9. Google OAuth Status API
        if self.path == "/api/oauth/status":
            st = accounts.get_oauth_status()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(st).encode("utf-8"))
            return

        # 10. Server Status API
        if self.path == "/api/server/status":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SERVER_STATE).encode("utf-8"))
            return

        # 11. Health Endpoint
        if self.path in ("/health", "/v1/health"):
            try:
                token, project_id = auth.get_credentials()
                status = {
                    "status": "healthy",
                    "provider": "Google Cloud Code (Antigravity)",
                    "tier": "Google AI Pro (g1-pro-tier)",
                    "project_id": project_id,
                    "token_active": bool(token),
                    "quota_endpoint": "/v1/quota"
                }
            except Exception as e:
                status = {"status": "degraded", "error": str(e)}

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status, indent=2).encode("utf-8"))
        # 12. Hermes Agent Sync API
        if self.path == "/api/hermes/sync":
            try:
                import sync_hermes
                res = sync_hermes.sync_to_hermes()
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
        # 13. API Keys List API
        if self.path == "/api/keys":
            keys = api_keys.list_keys()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"keys": keys}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        # 1. Update Config API
        if self.path == "/api/config":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                new_cfg = config.update_config(
                    host=data.get("host"),
                    port=data.get("port"),
                    language=data.get("language")
                )
                log_terminal("config_updated", lang=new_cfg["language"], host=new_cfg["host"], port=new_cfg["port"])
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "config": new_cfg}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 2. Auth Logout API
        if self.path == "/api/auth/logout":
            auth.clear_auth_cache()
            log_terminal("auth_cleared")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "message": "Session cache cleared"}).encode("utf-8"))
            return

        # 3. Auth Refresh API
        if self.path == "/api/auth/refresh":
            try:
                token, project_id = auth.reload_credentials()
                auth_st = auth.get_auth_status()
                log_terminal("auth_ok", email=auth_st.get("email") or "Google Account")
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "project_id": project_id}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 4. Server State Toggle / Start / Stop APIs
        if self.path == "/api/server/toggle":
            SERVER_STATE["running"] = not SERVER_STATE["running"]
            if SERVER_STATE["running"]:
                log_terminal("server_started_toggle")
            else:
                log_terminal("server_stopped")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SERVER_STATE).encode("utf-8"))
            return

        if self.path == "/api/server/start":
            SERVER_STATE["running"] = True
            log_terminal("server_started_toggle")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SERVER_STATE).encode("utf-8"))
            return

        if self.path == "/api/server/stop":
            SERVER_STATE["running"] = False
            log_terminal("server_stopped")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(SERVER_STATE).encode("utf-8"))
            return

        # 5. Switch Account API
        if self.path == "/api/accounts/switch":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                email = data.get("email")
                active = accounts.switch_account(email)
                log_terminal("account_switched", email=email)
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "active_account": email, "account": active}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 6. Remove Account API
        if self.path == "/api/accounts/remove":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                email = data.get("email")
                removed = accounts.remove_account(email)
                log_terminal("account_removed", email=email)
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": removed}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 7. Hermes Agent Sync API
        if self.path == "/api/hermes/sync":
            try:
                import sync_hermes
                res = sync_hermes.sync_to_hermes()
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 8. API Keys Create API
        if self.path == "/api/keys":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                new_key = api_keys.generate_key(name=data.get("name"))
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "key": new_key}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 9. API Keys Remove API
        if self.path == "/api/keys/remove":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                key_id = data.get("id")
                removed = api_keys.remove_key(key_id)
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": removed}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 10. OpenAI Chat Completions API
        if not self.path.startswith("/v1/chat/completions") and not self.path.startswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return

        # Check API Key Authentication (enforced only if keys exist)
        if not self._check_api_key():
            return

        # Check if proxy is currently paused
        if not SERVER_STATE.get("running", True):
            self.send_response(503)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": {
                    "message": "Servidor proxy pausado/parado pelo usuário no painel de controle.",
                    "type": "server_stopped",
                    "code": 503
                }
            }).encode("utf-8"))
            return

        req_start_time = time.time()
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)
        try:
            req_data = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON body: {str(e)}"}).encode("utf-8"))
            return

        is_stream = req_data.get("stream", False)
        requested_model = req_data.get("model", "gemini-3.8-flash")
        reasoning_effort = req_data.get("reasoning_effort")
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        resolved_model, resolved_effort = converter.resolve_model_and_effort(requested_model, reasoning_effort)
        log_model_str = f"{resolved_model} [effort: {resolved_effort}]" if resolved_effort else resolved_model
        log_terminal("request_recv", model=log_model_str, stream="True" if is_stream else "False")

        # Check if this request includes tool results
        for msg in req_data.get("messages", []):
            if msg.get("role") == "tool":
                log_terminal("tool_result", name=msg.get("name", "tool"), bytes_len=len(str(msg.get("content", ""))))

        try:
            token, project_id = auth.get_credentials()
            payload = converter.openai_to_google_request(req_data, project_id)
        except Exception as e:
            self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Failed to prepare request: {str(e)}"}).encode("utf-8"))
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity/2.15.0 linux/x64",
            "X-Client-Name": "antigravity",
            "X-Client-Version": "2.15.0"
        }

        url = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"

        try:
            upstream_resp = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=120
            )

            if not upstream_resp.ok:
                err_text = upstream_resp.text
                log_terminal("upstream_err", err=f"{upstream_resp.status_code}: {err_text[:200]}")
                with open("server.log", "a", encoding="utf-8") as f:
                    f.write(f"\n[ERROR 400 DEBUG]\nResp: {err_text}\nPayload: {json.dumps(payload, indent=2)}\n\n")
                self.send_response(upstream_resp.status_code)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Upstream error ({upstream_resp.status_code}): {err_text}"}).encode("utf-8"))
                return

        except Exception as e:
            log_terminal("upstream_err", err=str(e))
            self.send_response(502)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Failed to connect to Google Cloud Code: {str(e)}"}).encode("utf-8"))
            return

        # Handle Streaming Response
        if is_stream:
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            initial_chunk = converter.format_openai_stream_chunk(
                completion_id, requested_model, {"role": "assistant"}
            )
            self.wfile.write(initial_chunk.encode("utf-8"))
            self.wfile.flush()

            tool_call_index = 0
            has_tool_calls = False
            last_usage = None
            stream_finished = False

            for line in upstream_resp.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8")
                if not decoded.startswith("data:"):
                    continue

                chunk_payload = decoded[5:].strip()
                try:
                    chunk_json = json.loads(chunk_payload)
                except Exception:
                    continue

                resp_obj = chunk_json.get("response", {})
                usage_meta = resp_obj.get("usageMetadata")
                if usage_meta:
                    last_usage = {
                        "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                        "completion_tokens": usage_meta.get("candidatesTokenCount", 0) + usage_meta.get("thoughtsTokenCount", 0),
                        "total_tokens": usage_meta.get("totalTokenCount", 0)
                    }

                candidates = resp_obj.get("candidates", [])
                if not candidates:
                    continue

                candidate = candidates[0]
                finish_reason = candidate.get("finishReason")
                parts = candidate.get("content", {}).get("parts", [])

                for p in parts:
                    is_thought = p.get("thought")
                    thought_content = None

                    if isinstance(is_thought, str) and is_thought:
                        thought_content = is_thought
                    elif is_thought is True:
                        thought_content = p.get("text", "")

                    if thought_content:
                        thought_chunk = converter.format_openai_stream_chunk(
                            completion_id, requested_model, {"reasoning_content": thought_content}
                        )
                        self.wfile.write(thought_chunk.encode("utf-8"))
                        self.wfile.flush()
                    elif "text" in p and p["text"]:
                        text_chunk = converter.format_openai_stream_chunk(
                            completion_id, requested_model, {"content": p["text"]}
                        )
                        self.wfile.write(text_chunk.encode("utf-8"))
                        self.wfile.flush()

                    if "functionCall" in p:
                        has_tool_calls = True
                        fc = p["functionCall"]
                        log_terminal("tool_call", name=fc.get("name", "unknown"))
                        call_id = f"call_{uuid.uuid4().hex[:12]}"
                        tc_delta = {
                            "tool_calls": [
                                {
                                    "index": tool_call_index,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": fc.get("name", ""),
                                        "arguments": json.dumps(fc.get("args", {}))
                                    }
                                }
                            ]
                        }
                        tool_call_index += 1
                        fc_chunk = converter.format_openai_stream_chunk(
                            completion_id, requested_model, tc_delta
                        )
                        self.wfile.write(fc_chunk.encode("utf-8"))
                        self.wfile.flush()

                if finish_reason:
                    mapped_reason = "tool_calls" if has_tool_calls else (
                        "length" if finish_reason == "MAX_TOKENS" else "stop"
                    )
                    final_chunk = converter.format_openai_stream_chunk(
                        completion_id, requested_model, {}, finish_reason=mapped_reason, usage=last_usage
                    )
                    self.wfile.write(final_chunk.encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    stream_finished = True
                    break

            if not stream_finished:
                final_reason = "tool_calls" if has_tool_calls else "stop"
                final_chunk = converter.format_openai_stream_chunk(
                    completion_id, requested_model, {}, finish_reason=final_reason, usage=last_usage
                )
                self.wfile.write(final_chunk.encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            duration = time.time() - req_start_time
            log_terminal("completed", duration=duration)
            self.close_connection = True

        # Handle Non-Streaming Response
        else:
            accumulated_text = []
            accumulated_thoughts = []
            accumulated_tool_calls = []
            last_usage = None

            for line in upstream_resp.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8")
                if not decoded.startswith("data:"):
                    continue

                try:
                    chunk_json = json.loads(decoded[5:].strip())
                except Exception:
                    continue

                resp_obj = chunk_json.get("response", {})
                usage_meta = resp_obj.get("usageMetadata")
                if usage_meta:
                    last_usage = {
                        "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                        "completion_tokens": usage_meta.get("candidatesTokenCount", 0) + usage_meta.get("thoughtsTokenCount", 0),
                        "total_tokens": usage_meta.get("totalTokenCount", 0)
                    }

                candidates = resp_obj.get("candidates", [])
                if not candidates:
                    continue

                candidate = candidates[0]
                finish_reason = candidate.get("finishReason")
                parts = candidate.get("content", {}).get("parts", [])

                for p in parts:
                    is_thought = p.get("thought")
                    thought_content = None

                    if isinstance(is_thought, str) and is_thought:
                        thought_content = is_thought
                    elif is_thought is True:
                        thought_content = p.get("text", "")

                    if thought_content:
                        accumulated_thoughts.append(thought_content)
                    elif "text" in p and p["text"]:
                        accumulated_text.append(p["text"])
                    if "functionCall" in p:
                        fc = p["functionCall"]
                        log_terminal("tool_call", name=fc.get("name", "unknown"))
                        call_id = f"call_{uuid.uuid4().hex[:12]}"
                        accumulated_tool_calls.append({
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": fc.get("name", ""),
                                "arguments": json.dumps(fc.get("args", {}))
                            }
                        })

                if finish_reason:
                    break

            full_text = "".join(accumulated_text)
            full_thoughts = "".join(accumulated_thoughts)

            message_payload = {"role": "assistant"}
            if full_text:
                message_payload["content"] = full_text
            else:
                message_payload["content"] = None

            if full_thoughts:
                message_payload["reasoning_content"] = full_thoughts

            if accumulated_tool_calls:
                message_payload["tool_calls"] = accumulated_tool_calls

            response_json = {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": requested_model,
                "choices": [
                    {
                        "index": 0,
                        "message": message_payload,
                        "finish_reason": "tool_calls" if accumulated_tool_calls else "stop"
                    }
                ],
                "usage": last_usage or {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }
            }

            try:
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_json).encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass

            duration = time.time() - req_start_time
            log_terminal("completed", duration=duration)


def run_server():
    cfg = config.load_config()
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8000)

    # Allow CLI overrides
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass

    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, ProxyHandler)

    log_terminal("server_started", host=host, port=port)
    auth_status = auth.get_auth_status()
    if auth_status.get("authenticated"):
        log_terminal("tier_info", tier=auth_status.get("tier", "Google AI Pro"), project=auth_status.get("project_id", "aicode-consumers"))
        log_terminal("auth_ok", email=auth_status.get("email", "Conectado"))
    else:
        log_terminal("auth_error", error=auth_status.get("error", "Não autenticado"))

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log_terminal("shutdown")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
