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
import signal
import urllib.parse
import requests
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import auth
import converter
import config
import accounts
import api_keys
import acp_backend
import logger
from i18n import log_terminal, get_ui_translations

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PID_FILE = os.path.join(BASE_DIR, "proxy.pid")

# State for pause/resume toggle
SERVER_STATE = {"running": True}

AVAILABLE_MODELS = [
    {"id": "gemini-4-argon", "name": "Gemini 4 Argon", "owned_by": "google"},
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash", "owned_by": "google"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "owned_by": "anthropic"},
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "owned_by": "anthropic"},
    {"id": "gemini-3.5-flash-lite", "name": "Gemini 3.5 Flash Lite", "owned_by": "google"},
    {"id": "gemini-3-flash", "name": "Gemini 3 Flash", "owned_by": "google"},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "owned_by": "google"},
    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "owned_by": "google"},
    {"id": "gpt-oss-120b", "name": "GPT-OSS 120B", "owned_by": "openai"},
]


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """
        Suppress noisy BaseHTTPRequestHandler stderr lines for routine dashboard polling,
        while automatically capturing HTTP 4xx/5xx responses in the structured logger.
        """
        try:
            status_code = int(args[1]) if len(args) > 1 else 200
        except Exception:
            status_code = 200

        req_line = str(args[0]) if args else ""
        if status_code >= 400:
            logger.record_log(
                "WARN" if status_code < 500 else "ERROR",
                "HTTP",
                f"HTTP {status_code} em {req_line}",
                details={"status": status_code, "request": req_line, "client": self.client_address[0]},
                print_to_terminal=True
            )

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

        # 4. Real-time Quota Consumption & Antigravity Claude Proxy Account Limits
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

        if self.path.startswith("/account-limits"):
            try:
                quota_info = auth.get_quota_details()
                limits_payload = dict(quota_info.get("account_limits") or {})
                limits_payload["pools"] = quota_info.get("pools", [])
                limits_payload["backend"] = quota_info.get("backend", {})
                limits_payload["detailedModels"] = quota_info.get("models", [])
                self.send_response(200)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(json.dumps(limits_payload, indent=2).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if self.path == "/api/backend/status":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(acp_backend.get_backend_status_summary(), indent=2).encode("utf-8"))
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
                logger.log_error("AUTH", f"Healthcheck degradado: {e}", exc=e)
                status = {"status": "degraded", "error": str(e)}

            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status, indent=2).encode("utf-8"))
            return

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
                logger.log_error("PROXY", f"Erro ao sincronizar com Hermes: {e}", exc=e)
                self.send_response(500)
                self._send_cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        # 13. API Keys List API
        if self.path.startswith("/api/keys"):
            keys = api_keys.list_keys()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"keys": keys}).encode("utf-8"))
            return

        # 14. Structured Logs & Error Stream API
        if self.path.startswith("/api/logs"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                since_id = int(qs.get("since_id", ["0"])[0])
            except ValueError:
                since_id = 0
            level = qs.get("level", [None])[0]
            try:
                limit = int(qs.get("limit", ["200"])[0])
            except ValueError:
                limit = 200
            payload = logger.get_logs(since_id=since_id, level=level, limit=limit)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        self.send_response(404)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": f"Endpoint not found: {self.path}"}).encode("utf-8"))

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

        # 9b. Update / Sync antigravity-claude-proxy Backend Repository
        if self.path == "/api/backend/update":
            res = acp_backend.update_backend_repo()
            logger.record_log(
                "INFO" if res.get("success") else "ERROR",
                "BACKEND",
                f"Atualização git pull do backend antigravity-claude-proxy: {res.get('git_output', '')}",
                details=res
            )
            self.send_response(200 if res.get("success") else 500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # 9b-2. Verify & Sync Local Antigravity Installation Identifiers
        if self.path == "/api/backend/verify-local":
            res = acp_backend.verify_and_sync_local_antigravity()
            local_ag = res.get("local_antigravity") or {}
            logger.record_log(
                "INFO" if res.get("synced_with_local") else "WARN",
                "BACKEND",
                f"Verificação do Antigravity local: {res.get('reason')} | UA Ativo: {res.get('active_user_agent')} | CL: {local_ag.get('language_server_cl', 'N/A')}",
                details=res
            )
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # 9c. Clear Logs API
        if self.path == "/api/logs/clear":
            res = logger.clear_logs()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # 9d. Self-Diagnostics API
        if self.path == "/api/diagnostics/run":
            res = logger.run_self_diagnostics()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))
            return

        # 10. OpenAI Chat Completions API
        if not self.path.startswith("/v1/chat/completions") and not self.path.startswith("/chat/completions"):
            self.send_response(404)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Endpoint not found: {self.path}"}).encode("utf-8"))
            return

        # Check API Key Authentication (enforced only if keys exist)
        if not self._check_api_key():
            return

        # Check if proxy is currently paused
        if not SERVER_STATE.get("running", True):
            logger.record_log("WARN", "PROXY", "Requisição bloqueada: servidor proxy está pausado.")
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
            logger.log_error("REQ", f"JSON inválido no corpo da requisição: {e}", exc=e)
            self.send_response(400)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Invalid JSON body: {str(e)}"}).encode("utf-8"))
            return

        is_stream = req_data.get("stream", False)
        requested_model = req_data.get("model", "gemini-3.8-flash")
        reasoning_effort = converter.extract_reasoning_effort(req_data)
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        resolved_model, resolved_effort = converter.resolve_model_and_effort(requested_model, reasoning_effort)
        log_model_str = f"{requested_model} -> {resolved_model} [effort: {resolved_effort}]" if resolved_effort else f"{requested_model} -> {resolved_model}"
        log_terminal("request_recv", model=log_model_str, stream="True" if is_stream else "False")

        # Check if this request includes tool results
        for msg in req_data.get("messages", []):
            if msg.get("role") == "tool":
                log_terminal("tool_result", name=msg.get("name", "tool"), bytes_len=len(str(msg.get("content", ""))))

        try:
            token, project_id = auth.get_credentials()
            active_email = auth.get_active_email()
            payload = converter.openai_to_google_request(req_data, project_id, account_email=active_email)
        except Exception as e:
            logger.log_error("AUTH", f"Falha ao preparar credenciais/requisição: {e}", exc=e, extra_details={"model": requested_model})
            self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Failed to prepare request: {str(e)}"}).encode("utf-8"))
            return

        session_id = payload.get("request", {}).get("sessionId")
        headers = acp_backend.build_antigravity_headers(
            token,
            model=resolved_model,
            accept="text/event-stream",
            session_id=session_id
        )

        spec = acp_backend.get_backend_spec()
        endpoints = spec.get("endpointFallbacks") or [
            "https://daily-cloudcode-pa.googleapis.com",
            "https://cloudcode-pa.googleapis.com"
        ]

        upstream_resp = None
        last_err = None
        tried_endpoints = []
        for endpoint in endpoints:
            url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"
            tried_endpoints.append(endpoint)
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=120
                )
                if resp.ok:
                    upstream_resp = resp
                    break
                else:
                    last_err = (resp.status_code, resp.text)
                    # Only try next fallback endpoint on 5xx / 404 / connection errors
                    if resp.status_code not in (404, 408, 500, 502, 503, 504):
                        upstream_resp = resp
                        break
                    logger.record_log(
                        "WARN",
                        "UPSTREAM",
                        f"Endpoint {endpoint} retornou HTTP {resp.status_code}, tentando próximo fallback...",
                        details={"endpoint": endpoint, "status": resp.status_code}
                    )
            except Exception as e:
                last_err = (502, str(e))
                logger.record_log(
                    "WARN",
                    "UPSTREAM",
                    f"Falha de conexão em {endpoint}: {e}",
                    details={"endpoint": endpoint, "error": str(e)}
                )
                continue

        if upstream_resp is None:
            code, msg_txt = last_err or (502, "All Cloud Code endpoints failed")
            log_terminal(
                "upstream_err",
                err=f"{code}: {str(msg_txt)[:200]}",
                details={"status": code, "response": str(msg_txt)[:1000], "tried_endpoints": tried_endpoints, "model": resolved_model}
            )
            self.send_response(code if isinstance(code, int) else 502)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Failed to connect to Google Cloud Code: {msg_txt}"}).encode("utf-8"))
            return

        if not upstream_resp.ok:
            err_text = upstream_resp.text
            log_terminal(
                "upstream_err",
                err=f"{upstream_resp.status_code}: {err_text[:200]}",
                details={
                    "status": upstream_resp.status_code,
                    "model": resolved_model,
                    "upstream_response": err_text[:1500],
                    "user_agent": headers.get("User-Agent"),
                }
            )
            self.send_response(upstream_resp.status_code)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Upstream error ({upstream_resp.status_code}): {err_text}"}).encode("utf-8"))
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


def _cleanup_pid_file():
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r", encoding="utf-8") as f:
                recorded_pid = f.read().strip()
            if not recorded_pid or recorded_pid == str(os.getpid()):
                os.remove(PID_FILE)
    except Exception:
        pass


def run_server():
    cfg = config.load_config()
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8000)

    silent_mode = os.environ.get("PROXY_SILENT_MODE") == "1"
    for arg in sys.argv[1:]:
        if arg in ("--silent", "-s", "silent"):
            silent_mode = True
            os.environ["PROXY_SILENT_MODE"] = "1"
        else:
            try:
                port = int(arg)
            except ValueError:
                pass

    ThreadingHTTPServer.allow_reuse_address = True
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, ProxyHandler)

    # Write current PID
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    def _graceful_shutdown(signum, frame):
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.record_log("WARN", "PROXY", f"Sinal {sig_name} recebido. Encerrando servidor proxy e WebUI...")
        log_terminal("shutdown")
        _cleanup_pid_file()
        os._exit(0)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    if silent_mode:
        # In silent mode, ignore SIGHUP so closing the terminal keeps the server alive
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
    else:
        # In default terminal-bound mode, closing the terminal (SIGHUP) or parent exit terminates the server immediately
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, _graceful_shutdown)

        parent_pid_env = os.environ.get("PROXY_TERMINAL_PID")
        initial_ppid = int(parent_pid_env) if (parent_pid_env and parent_pid_env.isdigit()) else os.getppid()

        def _watch_parent_terminal():
            while True:
                time.sleep(1.0)
                try:
                    # If reparented to init/systemd (PID 1) or tracked terminal PID no longer exists, shut down
                    if os.getppid() == 1:
                        _graceful_shutdown(signal.SIGHUP, None)
                    if initial_ppid > 1:
                        os.kill(initial_ppid, 0)
                except ProcessLookupError:
                    _graceful_shutdown(signal.SIGHUP, None)
                except PermissionError:
                    pass

        import threading
        watcher = threading.Thread(target=_watch_parent_terminal, daemon=True)
        watcher.start()

    mode_str = "Background Silencioso (--silent)" if silent_mode else "Vinculado ao Terminal (Foreground)"
    log_terminal("server_started", host=host, port=port)
    logger.record_log("INFO", "PROXY", f"Modo de execução: {mode_str} (PID: {os.getpid()})")

    st_backend = acp_backend.verify_and_sync_local_antigravity()
    b_summary = st_backend.get("backend") or {}
    local_ag = st_backend.get("local_antigravity") or {}
    logger.record_log(
        "INFO",
        "BACKEND",
        f"Backend antigravity-claude-proxy v{b_summary.get('package_version')} ativo | UA: {b_summary.get('user_agent')}"
    )
    if local_ag.get("installed"):
        logger.record_log(
            "INFO",
            "BACKEND",
            f"Antigravity Local detectado e sincronizado: v{local_ag.get('ide_version')} ({local_ag.get('source')}) | LS CL: {local_ag.get('language_server_cl', 'N/A')} | agy CLI: v{local_ag.get('agy_cli_version', 'N/A')}",
            details=st_backend.get("identifier_sync")
        )
    else:
        logger.record_log(
            "WARN",
            "BACKEND",
            f"Antigravity local não encontrado em disco; usando máscara fallback do antigravity-claude-proxy ({b_summary.get('user_agent')})"
        )

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
    finally:
        _cleanup_pid_file()
        httpd.server_close()


if __name__ == "__main__":
    run_server()
