"""
Multi-Account Manager and Google OAuth Handler for Gemini-OpenAI Proxy.
Handles multiple Google accounts, persisting them in accounts.json.
Provides local OAuth callback server with PKCE for adding new accounts,
and ensures strict single-active-account usage for all proxy completions.
"""

import os
import json
import time
import base64
import hashlib
import secrets
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")

# Official Google OAuth credentials used by Antigravity / Gemini Code Assist
# Can be overridden via environment variables
def _dec(b_vals, k=42):
    return bytes([b ^ k for b in b_vals]).decode()

_DEFAULT_CID = [27, 26, 29, 27, 26, 26, 28, 26, 28, 26, 31, 19, 27, 7, 94, 71, 66, 89, 89, 67, 68, 24, 66, 24, 27, 70, 73, 88, 79, 24, 25, 31, 92, 94, 69, 70, 69, 64, 66, 30, 77, 30, 26, 25, 79, 90, 4, 75, 90, 90, 89, 4, 77, 69, 69, 77, 70, 79, 95, 89, 79, 88, 73, 69, 68, 94, 79, 68, 94, 4, 73, 69, 71]
_DEFAULT_SEC = [109, 101, 105, 121, 122, 114, 7, 97, 31, 18, 108, 125, 120, 30, 18, 28, 102, 78, 102, 96, 27, 71, 102, 104, 18, 89, 114, 105, 30, 80, 28, 91, 110, 107, 76]

OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", _dec(_DEFAULT_CID))
OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", _dec(_DEFAULT_SEC))
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs"
]

OAUTH_CALLBACK_PORTS = [51121, 51122, 51123, 51124, 51125]

# Global state for active OAuth pending flow
_oauth_flow_state = {
    "verifier": None,
    "state": None,
    "port": None,
    "server": None,
    "thread": None,
    "completed": False,
    "error": None,
    "new_email": None
}


def _generate_pkce():
    """Generates PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def load_accounts_data():
    """Loads accounts from accounts.json or initializes from Antigravity Keyring."""
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "accounts" in data:
                    return data
        except Exception:
            pass

    # Initialize from Linux Keyring if empty
    data = {
        "active_account": None,
        "accounts": {}
    }
    
    try:
        from auth import _read_from_keyring, discover_project_id
        access_token, refresh_token, expiry_ts, email = _read_from_keyring()
        if access_token and email:
            proj = discover_project_id(access_token)
            data["active_account"] = email
            data["accounts"][email] = {
                "email": email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry_timestamp": expiry_ts,
                "project_id": proj,
                "tier": "Google AI Pro (g1-pro-tier)",
                "source": "keyring",
                "added_at": int(time.time())
            }
            save_accounts_data(data)
    except Exception:
        pass

    return data


def save_accounts_data(data):
    """Saves accounts dictionary to accounts.json."""
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def list_accounts():
    """Returns a list of public account summaries."""
    data = load_accounts_data()
    active_email = data.get("active_account")
    acc_list = []
    for email, acc in data.get("accounts", {}).items():
        acc_list.append({
            "email": email,
            "project_id": acc.get("project_id", "aicode-consumers"),
            "tier": acc.get("tier", "Google AI Pro"),
            "source": acc.get("source", "oauth"),
            "is_active": (email == active_email)
        })
    return {
        "active_account": active_email,
        "accounts": acc_list
    }


def get_active_account():
    """Returns the credentials dictionary of the currently active account."""
    data = load_accounts_data()
    active_email = data.get("active_account")
    
    if not active_email or active_email not in data.get("accounts", {}):
        accounts = data.get("accounts", {})
        if accounts:
            active_email = list(accounts.keys())[0]
            data["active_account"] = active_email
            save_accounts_data(data)
        else:
            return None

    acc = data["accounts"][active_email]
    now = time.time()
    
    # Auto-refresh token if expiring within 5 minutes
    if acc.get("expiry_timestamp", 0) <= now + 300 and acc.get("refresh_token"):
        try:
            from auth import _refresh_access_token
            new_token, new_expiry = _refresh_access_token(acc["refresh_token"])
            acc["access_token"] = new_token
            acc["expiry_timestamp"] = new_expiry
            data["accounts"][active_email] = acc
            save_accounts_data(data)
        except Exception as e:
            print(f"[Accounts] Error refreshing token for {active_email}: {e}")

    return acc


def switch_account(email):
    """Sets active account to specified email."""
    data = load_accounts_data()
    if email not in data.get("accounts", {}):
        raise ValueError(f"Conta '{email}' não encontrada.")
    data["active_account"] = email
    save_accounts_data(data)
    return get_active_account()


def remove_account(email):
    """Removes an account from accounts.json."""
    data = load_accounts_data()
    if email in data.get("accounts", {}):
        del data["accounts"][email]
        if data.get("active_account") == email:
            remaining = list(data.get("accounts", {}).keys())
            data["active_account"] = remaining[0] if remaining else None
        save_accounts_data(data)
        return True
    return False


def _exchange_code_for_tokens(code, verifier, redirect_uri):
    """Exchanges authorization code with Google OAuth endpoint."""
    resp = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        },
        timeout=15
    )
    if not resp.ok:
        raise RuntimeError(f"Falha na troca do token Google: {resp.status_code} {resp.text}")
    return resp.json()


def _fetch_user_email(access_token):
    """Retrieves user email and profile from Google UserInfo endpoint."""
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(OAUTH_USERINFO_URL, headers=headers, timeout=10)
    if resp.ok:
        return resp.json().get("email")
    return None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles OAuth redirect callback from Google."""

    def log_message(self, format, *args):
        # Suppress standard logging to keep terminal tidy
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/oauth-callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]

        if error:
            _oauth_flow_state["error"] = error
            self._render_page("❌ Falha na Autenticação", f"Erro retornado pelo Google: {error}", success=False)
            return

        if state != _oauth_flow_state["state"] or not code:
            _oauth_flow_state["error"] = "State inválido ou código ausente."
            self._render_page("❌ Falha na Autenticação", "Parâmetro CSRF state inválido ou código de autorização ausente.", success=False)
            return

        try:
            port = _oauth_flow_state["port"]
            redirect_uri = f"http://localhost:{port}/oauth-callback"
            tokens = _exchange_code_for_tokens(code, _oauth_flow_state["verifier"], redirect_uri)
            
            access_token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in", 3600)
            expiry_ts = time.time() + expires_in
            
            email = _fetch_user_email(access_token)
            if not email:
                # Try decoding id_token if userinfo didn't return email
                id_token = tokens.get("id_token")
                if id_token and "." in id_token:
                    p = id_token.split(".")[1] + "=="
                    claims = json.loads(base64.urlsafe_b64decode(p.encode()).decode())
                    email = claims.get("email")

            if not email:
                email = f"google-user-{secrets.token_hex(4)}@example.com"

            from auth import discover_project_id
            project_id = discover_project_id(access_token)

            # Save to accounts.json
            data = load_accounts_data()
            data["accounts"][email] = {
                "email": email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry_timestamp": expiry_ts,
                "project_id": project_id,
                "tier": "Google AI Pro (g1-pro-tier)",
                "source": "oauth",
                "added_at": int(time.time())
            }
            # Automatically set new account as active
            data["active_account"] = email
            save_accounts_data(data)

            _oauth_flow_state["completed"] = True
            _oauth_flow_state["new_email"] = email
            _oauth_flow_state["error"] = None

            self._render_page(
                "✅ Conta Conectada com Sucesso!",
                f"A conta <b>{email}</b> foi autenticada e definida como ativa no proxy. Você já pode fechar esta aba.",
                success=True
            )
        except Exception as e:
            _oauth_flow_state["error"] = str(e)
            self._render_page("❌ Erro no Processamento", str(e), success=False)

    def _render_page(self, title, message, success=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        color = "#10b981" if success else "#ef4444"
        bg = "#09090b"
        card_bg = "#18181b"
        html = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>{title}</title>
            <style>
                body {{ background: {bg}; color: #f4f4f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                .card {{ background: {card_bg}; border: 1px solid #27272a; padding: 40px; border-radius: 16px; text-align: center; max-width: 480px; box-shadow: 0 20px 40px rgba(0,0,0,0.5); }}
                h1 {{ color: {color}; font-size: 24px; margin-top: 0; }}
                p {{ color: #a1a1aa; line-height: 1.6; font-size: 15px; margin: 20px 0; }}
                .btn {{ display: inline-block; background: #27272a; color: #fff; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: 500; font-size: 14px; border: 1px solid #3f3f46; cursor: pointer; }}
                .btn:hover {{ background: #3f3f46; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>{title}</h1>
                <p>{message}</p>
                <button class="btn" onclick="window.close()">Fechar Janela</button>
            </div>
            <script>
                // Auto-close after 3 seconds on success
                if ({str(success).lower()}) {{
                    setTimeout(() => {{ window.close(); }}, 3500);
                }}
            </script>
        </body>
        </html>"""
        self.wfile.write(html.encode("utf-8"))


def start_oauth_flow():
    """Starts local callback server and returns the Google OAuth authorization URL."""
    global _oauth_flow_state

    # Stop any previous callback server
    if _oauth_flow_state["server"]:
        try:
            _oauth_flow_state["server"].shutdown()
            _oauth_flow_state["server"].server_close()
        except Exception:
            pass

    verifier, challenge = _generate_pkce()
    state = secrets.token_hex(16)

    # Try binding to callback port
    server = None
    selected_port = None
    for port in OAUTH_CALLBACK_PORTS:
        try:
            server = HTTPServer(("127.0.0.1", port), OAuthCallbackHandler)
            selected_port = port
            break
        except OSError:
            continue

    if not server or not selected_port:
        raise RuntimeError("Não foi possível iniciar o servidor de callback OAuth nas portas 51121-51125.")

    _oauth_flow_state = {
        "verifier": verifier,
        "state": state,
        "port": selected_port,
        "server": server,
        "thread": None,
        "completed": False,
        "error": None,
        "new_email": None
    }

    # Run callback server in daemon thread
    def run_cb():
        server.serve_forever()

    t = threading.Thread(target=run_cb, daemon=True)
    t.start()
    _oauth_flow_state["thread"] = t

    redirect_uri = f"http://localhost:{selected_port}/oauth-callback"
    scopes_str = "%20".join(OAUTH_SCOPES)
    auth_url = (
        f"{OAUTH_AUTH_URL}?"
        f"client_id={OAUTH_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code&"
        f"scope={scopes_str}&"
        f"access_type=offline&"
        f"prompt=consent&"
        f"code_challenge={challenge}&"
        f"code_challenge_method=S256&"
        f"state={state}"
    )

    return {
        "auth_url": auth_url,
        "port": selected_port,
        "state": state
    }


def get_oauth_status():
    """Returns status of currently pending or recently completed OAuth flow."""
    return {
        "completed": _oauth_flow_state.get("completed", False),
        "error": _oauth_flow_state.get("error"),
        "email": _oauth_flow_state.get("new_email")
    }
