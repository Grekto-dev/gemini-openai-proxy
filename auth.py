"""
Authentication module for Antigravity Google AI Pro proxy.
Extracts and auto-refreshes OAuth credentials from the Linux Secret Service (DBus),
where Antigravity stores the active session for the logged-in Google account.
Also provides real-time quota retrieval for Google AI Pro tiers.
"""

import os
import json
import time
import base64
import requests
import dbus
from datetime import datetime, timezone

# Official Google OAuth credentials used by Antigravity / Gemini Code Assist
# Can be overridden via environment variables
def _dec(b_vals, k=42):
    return bytes([b ^ k for b in b_vals]).decode()

_DEFAULT_CID = [27, 26, 29, 27, 26, 26, 28, 26, 28, 26, 31, 19, 27, 7, 94, 71, 66, 89, 89, 67, 68, 24, 66, 24, 27, 70, 73, 88, 79, 24, 25, 31, 92, 94, 69, 70, 69, 64, 66, 30, 77, 30, 26, 25, 79, 90, 4, 75, 90, 90, 89, 4, 77, 69, 69, 77, 70, 79, 95, 89, 79, 88, 73, 69, 68, 94, 79, 68, 94, 4, 73, 69, 71]
_DEFAULT_SEC = [109, 101, 105, 121, 122, 114, 7, 97, 31, 18, 108, 125, 120, 30, 18, 28, 102, 78, 102, 96, 27, 71, 102, 104, 18, 89, 114, 105, 30, 80, 28, 91, 110, 107, 76]

OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", _dec(_DEFAULT_CID))
OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", _dec(_DEFAULT_SEC))
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

CLOUD_CODE_ENDPOINTS = [
    "https://daily-cloudcode-pa.googleapis.com",
    "https://cloudcode-pa.googleapis.com"
]

# In-memory cache for token and project info
_token_cache = {
    "access_token": None,
    "refresh_token": None,
    "expiry_timestamp": 0,
    "project_id": None,
    "email": None
}


def _read_from_keyring():
    """Reads credentials from DBus Secret Service (service='gemini', username='antigravity')."""
    bus = dbus.SessionBus()
    service_obj = bus.get_object("org.freedesktop.secrets", "/org/freedesktop/secrets")
    secrets_iface = dbus.Interface(service_obj, "org.freedesktop.Secret.Service")

    session_path = secrets_iface.OpenSession("plain", dbus.String("", variant_level=1))[1]
    
    search_results = secrets_iface.SearchItems({"service": "gemini", "username": "antigravity"})
    unlocked, locked = search_results
    
    if not unlocked:
        if locked:
            raise RuntimeError("Keyring item for 'gemini' is locked. Please unlock your keyring.")
        raise RuntimeError("No Antigravity credentials found in keyring (service='gemini', username='antigravity').")

    item_obj = bus.get_object("org.freedesktop.secrets", unlocked[0])
    item_iface = dbus.Interface(item_obj, "org.freedesktop.Secret.Item")
    secret = item_iface.GetSecret(session_path)
    secret_bytes = bytes(secret[2])
    
    data = json.loads(secret_bytes.decode("utf-8"))
    token_data = data.get("token", {})
    
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expiry_str = token_data.get("expiry")  # e.g. "2026-10-03T06:39:33.077247348Z"
    expiry_timestamp = 0
    if expiry_str:
        try:
            clean_str = expiry_str.split(".")[0] + "Z"
            dt = datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            expiry_timestamp = dt.timestamp()
        except Exception:
            expiry_timestamp = time.time() + 3600
    
    email = None
    id_token = data.get("id_token") or token_data.get("id_token")
    if id_token and "." in id_token:
        try:
            import base64
            parts = id_token.split(".")
            if len(parts) >= 2:
                p = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                claims = json.loads(base64.urlsafe_b64decode(p.encode("utf-8")).decode("utf-8"))
                email = claims.get("email")
        except Exception:
            pass

    return access_token, refresh_token, expiry_timestamp, email


def _refresh_access_token(refresh_token):
    """Refreshes the OAuth access token using Google's OAuth endpoint."""
    response = requests.post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        },
        timeout=10
    )
    if not response.ok:
        raise RuntimeError(f"Failed to refresh OAuth token: {response.status_code} {response.text}")
    
    res_data = response.json()
    new_access_token = res_data["access_token"]
    expires_in = res_data.get("expires_in", 3600)
    new_expiry = time.time() + expires_in
    return new_access_token, new_expiry


def get_credentials():
    """
    Returns valid access_token, refresh_token, and active project_id.
    Prioritizes the active account from accounts.json, falling back to the Linux Keyring.
    """
    try:
        import accounts
        acc = accounts.get_active_account()
        if acc and acc.get("access_token"):
            return acc["access_token"], acc.get("project_id", "aicode-consumers")
    except Exception:
        pass

    now = time.time()
    
    # Check cache first
    if _token_cache["access_token"] and _token_cache["expiry_timestamp"] > now + 300:
        return _token_cache["access_token"], _token_cache["project_id"]

    # Read from keyring
    access_token, refresh_token, expiry_ts, email = _read_from_keyring()
    
    # Refresh if needed
    if expiry_ts <= now + 300 and refresh_token:
        access_token, expiry_ts = _refresh_access_token(refresh_token)
        
    _token_cache["access_token"] = access_token
    _token_cache["refresh_token"] = refresh_token
    _token_cache["expiry_timestamp"] = expiry_ts
    if email:
        _token_cache["email"] = email

    # Discover project ID if not cached
    if not _token_cache["project_id"]:
        _token_cache["project_id"] = discover_project_id(access_token)

    return _token_cache["access_token"], _token_cache["project_id"]


def discover_project_id(access_token):
    """Discovers the managed project ID associated with the Google AI Pro subscription."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/2.15.0 linux/x64",
        "X-Client-Name": "antigravity",
        "X-Client-Version": "2.15.0"
    }
    
    payload = {
        "metadata": {
            "ideType": 9,      # ANTIGRAVITY
            "platform": 4,     # LINUX_AMD64
            "pluginType": 2    # GEMINI
        },
        "mode": 1
    }
    
    for endpoint in CLOUD_CODE_ENDPOINTS:
        try:
            resp = requests.post(f"{endpoint}/v1internal:loadCodeAssist", headers=headers, json=payload, timeout=10)
            if resp.ok:
                data = resp.json()
                project_id = data.get("cloudaicompanionProject")
                if project_id:
                    return project_id
        except Exception:
            continue
            
    return "aicode-consumers"


def format_reset_delta(dt_str, now_dt):
    """Calculates human-readable time and total seconds remaining until reset."""
    if not dt_str:
        return "N/A", 0
    try:
        clean = dt_str.split(".")[0].rstrip("Z") + "Z"
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        diff = dt - now_dt
        sec = max(0, int(diff.total_seconds()))
        days = sec // 86400
        hours = (sec % 86400) // 3600
        mins = (sec % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h", sec
        elif hours > 0:
            return f"{hours}h {mins}m", sec
        elif mins > 0:
            return f"{mins}m", sec
        else:
            return "agora", sec
    except Exception:
        return dt_str, 0


def get_quota_details():
    """
    Fetches real-time quota information directly from Google Cloud Code retrieveUserQuotaSummary API.
    Provides dual-limit metrics:
    - 5-Hour rolling window quota (Sprint limit) with minute-by-minute countdown.
    - Weekly quota (Macro entitlement) with day-by-day countdown.
    - Monthly subscription billing renewal date.
    """
    access_token, project_id = get_credentials()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/2.15.0 linux/x64",
        "X-Client-Name": "antigravity",
        "X-Client-Version": "2.15.0"
    }

    now = datetime.now(timezone.utc)

    # Monthly subscription billing reset calculation (1st day of next month)
    if now.month == 12:
        next_month_dt = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        next_month_dt = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    days_until_monthly_reset = (next_month_dt.date() - now.date()).days

    pools = []
    models_list = []

    # Attempt retrieveUserQuotaSummary (preferred dual-limit endpoint)
    summary_url = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
    try:
        resp = requests.post(summary_url, headers=headers, json={"project": project_id}, timeout=10)
        if resp.ok:
            summary_data = resp.json()
            for g in summary_data.get("groups", []):
                gname = g.get("displayName", "")
                is_gemini = "Gemini" in gname
                pool_id = "gemini" if is_gemini else "claude"

                b_5h = next((b for b in g.get("buckets", []) if b.get("window") == "5h"), {})
                b_weekly = next((b for b in g.get("buckets", []) if b.get("window") == "weekly"), {})

                rem_5h = b_5h.get("remainingFraction", 1.0)
                used_5h = round((1.0 - rem_5h) * 100, 1)
                fmt_5h, sec_5h = format_reset_delta(b_5h.get("resetTime"), now)

                rem_w = b_weekly.get("remainingFraction", 1.0)
                used_w = round((1.0 - rem_w) * 100, 1)
                fmt_w, sec_w = format_reset_delta(b_weekly.get("resetTime"), now)

                recommended_models = [
                    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash (Auto)", "desc": "Effort dinâmico pelo Hermes"},
                    {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)", "desc": "Máxima velocidade e raciocínio"},
                    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "Baixa latência para testes rápidos"}
                ] if is_gemini else [
                    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5 (Auto)", "desc": "Effort dinâmico pelo Hermes"},
                    {"id": "claude-sonnet-5-5-high", "name": "Claude Sonnet 5.5 (High)", "desc": "Precisão máxima de raciocínio"},
                    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5 (Auto)", "desc": "Alta capacidade conceitual"}
                ]

                pools.append({
                    "id": pool_id,
                    "name": "Gemini (Pool Geral)" if is_gemini else "Claude & GPT (Anthropic/OpenAI)",
                    "subtitle": "Gemini 2.5 Flash/Pro & Gemini 3.x" if is_gemini else "Claude Sonnet, Claude Opus & GPT-OSS",
                    "provider": "google" if is_gemini else "anthropic",
                    "badge": "Google AI Pro" if is_gemini else "Pro Tier",

                    # 5-Hour Limit (Sprint)
                    "used_percent_5h": used_5h,
                    "remaining_percent_5h": round(rem_5h * 100, 1),
                    "reset_time_5h": b_5h.get("resetTime"),
                    "time_until_reset_5h": fmt_5h,
                    "seconds_until_reset_5h": sec_5h,

                    # Weekly Limit (Macro)
                    "used_percent_weekly": used_w,
                    "remaining_percent_weekly": round(rem_w * 100, 1),
                    "reset_time_weekly": b_weekly.get("resetTime"),
                    "time_until_reset_weekly": fmt_w,
                    "seconds_until_reset_weekly": sec_w,

                    # Backward compatibility aliases
                    "used_percent": used_5h,
                    "remaining_percent": round(rem_5h * 100, 1),

                    # Monthly Subscription Billing
                    "monthly_reset_date": next_month_dt.strftime("%d/%m/%Y"),
                    "days_until_monthly_reset": days_until_monthly_reset,
                    "models": recommended_models
                })
    except Exception as e:
        print(f"[WARN] Error fetching retrieveUserQuotaSummary: {e}")

    # Fallback to retrieveUserQuota if pools is empty
    if not pools:
        fallback_url = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
        try:
            f_resp = requests.post(fallback_url, headers=headers, json={"project": project_id}, timeout=10)
            if f_resp.ok:
                f_data = f_resp.json()
                for b in f_data.get("buckets", []):
                    mid = b.get("modelId", "")
                    rem = b.get("remainingFraction", 1.0)
                    reset = b.get("resetTime")
                    fmt_t, sec_t = format_reset_delta(reset, now)
                    models_list.append({
                        "model": mid,
                        "remaining_percent": round(rem * 100, 1),
                        "used_percent": round((1.0 - rem) * 100, 1),
                        "reset_time": reset,
                        "time_until_reset": fmt_t,
                        "seconds_until_reset": sec_t
                    })
        except Exception:
            pass

    email = None
    try:
        import accounts
        acc = accounts.get_active_account()
        if acc:
            email = acc.get("email")
    except Exception:
        pass

    return {
        "tier": "Google AI Pro (g1-pro-tier)",
        "project": project_id,
        "email": email or _token_cache.get("email") or "",
        "has_monthly_quota": False,
        "quota_architecture": "dual_limit_5h_and_weekly",
        "pools": pools,
        "models": models_list
    }


def clear_auth_cache():
    """Clears in-memory authentication cache (simulates logout / disconnect)."""
    _token_cache["access_token"] = None
    _token_cache["refresh_token"] = None
    _token_cache["expiry_timestamp"] = 0
    _token_cache["project_id"] = None
    return True


def reload_credentials():
    """Forces re-reading credentials and refreshing token."""
    clear_auth_cache()
    return get_credentials()


def get_auth_status():
    """Returns current auth state and active account email."""
    try:
        import accounts
        acc = accounts.get_active_account()
        if acc:
            return {
                "authenticated": True,
                "email": acc.get("email"),
                "tier": acc.get("tier", "Google AI Pro (g1-pro-tier)"),
                "project_id": acc.get("project_id", "aicode-consumers")
            }
        token, project_id = get_credentials()
        return {
            "authenticated": bool(token),
            "email": _token_cache.get("email") or "",
            "tier": "Google AI Pro (g1-pro-tier)",
            "project_id": project_id
        }
    except Exception as e:
        return {
            "authenticated": False,
            "email": None,
            "tier": None,
            "error": str(e)
        }
    except Exception as e:
        return {
            "authenticated": False,
            "email": None,
            "tier": None,
            "error": str(e)
        }

