"""
Authentication and Quota module for Antigravity Google AI Pro proxy.
Backed by `antigravity-claude-proxy` (`acp_backend.py`) for dynamic call masking,
headers (`ANTIGRAVITY_HEADERS`, `User-Agent: antigravity/hub/...`), `CLIENT_METADATA`,
and quota/model retrieval via `loadCodeAssist` + `fetchAvailableModels`.
"""

import os
import json
import time
import base64
import requests
import dbus
from datetime import datetime, timezone

import acp_backend

# Official Google OAuth credentials used by Antigravity / Gemini Code Assist
def _dec(b_vals, k=42):
    return bytes([b ^ k for b in b_vals]).decode()

_DEFAULT_CID = [27, 26, 29, 27, 26, 26, 28, 26, 28, 26, 31, 19, 27, 7, 94, 71, 66, 89, 89, 67, 68, 24, 66, 24, 27, 70, 73, 88, 79, 24, 25, 31, 92, 94, 69, 70, 69, 64, 66, 30, 77, 30, 26, 25, 79, 90, 4, 75, 90, 90, 89, 4, 77, 69, 69, 77, 70, 79, 95, 89, 79, 88, 73, 69, 68, 94, 79, 68, 94, 4, 73, 69, 71]
_DEFAULT_SEC = [109, 101, 105, 121, 122, 114, 7, 97, 31, 18, 108, 125, 120, 30, 18, 28, 102, 78, 102, 96, 27, 71, 102, 104, 18, 89, 114, 105, 30, 80, 28, 91, 110, 107, 76]

OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", _dec(_DEFAULT_CID))
OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", _dec(_DEFAULT_SEC))
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# In-memory cache for token and project info
_token_cache = {
    "access_token": None,
    "refresh_token": None,
    "expiry_timestamp": 0,
    "project_id": None,
    "email": None,
    "tier": None,
    "tier_id": None,
    "credits": None,
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
    expiry_str = token_data.get("expiry")
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
    Returns valid access_token and active project_id.
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
        sub_info = get_subscription_and_project(access_token)
        _token_cache["project_id"] = sub_info["project_id"]
        _token_cache["tier"] = sub_info["tier_display"]
        _token_cache["tier_id"] = sub_info["tier_id"]
        _token_cache["credits"] = sub_info["credits"]

    return _token_cache["access_token"], _token_cache["project_id"]


def get_active_email():
    """Returns the email of the currently active account."""
    try:
        import accounts
        acc = accounts.get_active_account()
        if acc and acc.get("email"):
            return acc["email"]
    except Exception:
        pass
    return _token_cache.get("email") or ""


def parse_tier_id(tier_id):
    """Matches antigravity-claude-proxy/src/cloudcode/model-api.js parseTierId."""
    if not tier_id:
        return "unknown"
    lower = tier_id.lower()
    if "ultra" in lower:
        return "ultra"
    if lower == "standard-tier" or "pro" in lower or "premium" in lower:
        return "pro"
    if lower == "free-tier" or "free" in lower:
        return "free"
    return "unknown"


def get_subscription_and_project(access_token):
    """
    Calls `v1internal:loadCodeAssist` using the exact headers (`LOAD_CODE_ASSIST_HEADERS`),
    endpoint order (`LOAD_CODE_ASSIST_ENDPOINTS`), and `CLIENT_METADATA` from
    `antigravity-claude-proxy/src/cloudcode/model-api.js` (`getSubscriptionTier`).
    """
    spec = acp_backend.get_backend_spec()
    endpoints = spec.get("loadCodeAssistEndpoints") or [
        "https://cloudcode-pa.googleapis.com",
        "https://daily-cloudcode-pa.googleapis.com",
    ]
    base_headers = dict(spec.get("loadCodeAssistHeaders") or spec.get("antigravityHeaders") or {})
    session_id = acp_backend.derive_session_id(get_active_email())
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        **base_headers,
    }
    if session_id:
        headers["X-Machine-Session-Id"] = session_id
    metadata = spec.get("clientMetadata") or {"ideType": 9, "platform": 3, "pluginType": 2}
    payload = {"metadata": metadata, "mode": 1}

    for endpoint in endpoints:
        try:
            resp = requests.post(
                f"{endpoint}/v1internal:loadCodeAssist",
                headers=headers,
                json=payload,
                timeout=10,
            )
            if not resp.ok:
                continue

            data = resp.json()
            project_id = None
            companion = data.get("cloudaicompanionProject")
            if isinstance(companion, str) and companion:
                project_id = companion
            elif isinstance(companion, dict) and companion.get("id"):
                project_id = companion["id"]

            tier = "unknown"
            tier_id = None
            tier_name = None
            credits = None

            paid_tier = data.get("paidTier") or {}
            current_tier = data.get("currentTier") or {}
            allowed_tiers = data.get("allowedTiers") or []

            if paid_tier.get("id"):
                tier_id = paid_tier["id"]
                tier_name = paid_tier.get("name") or "Google AI Pro"
                tier = parse_tier_id(tier_id)
                avail_credits = paid_tier.get("availableCredits") or []
                if avail_credits and isinstance(avail_credits, list):
                    credits = avail_credits[0].get("creditAmount")

            if tier == "unknown" and current_tier.get("id"):
                tier_id = current_tier["id"]
                tier_name = current_tier.get("name") or tier_id
                tier = parse_tier_id(tier_id)

            if tier == "unknown" and allowed_tiers:
                default_t = next((t for t in allowed_tiers if isinstance(t, dict) and t.get("isDefault")), allowed_tiers[0])
                if isinstance(default_t, dict) and default_t.get("id"):
                    tier_id = default_t["id"]
                    tier_name = default_t.get("name") or tier_id
                    tier = parse_tier_id(tier_id)

            tier_display = f"{tier_name or 'Google AI Pro'} ({tier_id or 'g1-pro-tier'})"
            return {
                "tier": tier if tier != "unknown" else "pro",
                "tier_id": tier_id or "g1-pro-tier",
                "tier_name": tier_name or "Google AI Pro",
                "tier_display": tier_display,
                "project_id": project_id or "aicode-consumers",
                "credits": credits,
            }
        except Exception:
            continue

    return {
        "tier": "pro",
        "tier_id": "g1-pro-tier",
        "tier_name": "Google AI Pro",
        "tier_display": "Google AI Pro (g1-pro-tier)",
        "project_id": "aicode-consumers",
        "credits": None,
    }


def discover_project_id(access_token):
    """Discovers the managed project ID associated with the Google AI Pro subscription."""
    return get_subscription_and_project(access_token)["project_id"]


def fetch_available_models_raw(access_token, project_id=None):
    """
    Calls `v1internal:fetchAvailableModels` using `ANTIGRAVITY_HEADERS` and
    `ANTIGRAVITY_ENDPOINT_FALLBACKS` from `antigravity-claude-proxy/src/cloudcode/model-api.js`.
    """
    spec = acp_backend.get_backend_spec()
    endpoints = spec.get("endpointFallbacks") or [
        "https://daily-cloudcode-pa.googleapis.com",
        "https://cloudcode-pa.googleapis.com",
    ]
    session_id = acp_backend.derive_session_id(get_active_email())
    headers = acp_backend.build_antigravity_headers(access_token, session_id=session_id)
    body = {"project": project_id} if project_id else {}

    for endpoint in endpoints:
        try:
            resp = requests.post(
                f"{endpoint}/v1internal:fetchAvailableModels",
                headers=headers,
                json=body,
                timeout=10,
            )
            if resp.ok:
                return resp.json()
        except Exception:
            continue
    return {}


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


def _classify_model_family(model_id, model_data):
    """Classifies a Cloud Code model ID into its family."""
    lower = (model_id or "").lower()
    provider_raw = (model_data.get("modelProvider") or "").upper()
    if "claude" in lower or "ANTHROPIC" in provider_raw:
        return "claude", "anthropic"
    if "gpt" in lower or "OPENAI" in provider_raw:
        return "openai", "openai"
    if "gemini" in lower or "GOOGLE" in provider_raw:
        if lower.startswith("gemini-4") or "argon" in lower:
            return "gemini-4-argon", "google"
        return "gemini", "google"
    return "other", "google"


def get_quota_details():
    """
    Fetches real-time quota and model availability using `antigravity-claude-proxy`'s
    `loadCodeAssist` + `fetchAvailableModels` protocol (`model-api.js`), supplemented
    by `retrieveUserQuotaSummary` when available.

    Returns:
    - `pools`: Aggregated quota pools (Gemini 4 Argon, Gemini 3.8/3.5/2.5, Claude 5.5 & GPT-OSS)
    - `models`: Full per-model quota list from `fetchAvailableModels`
    - `account_limits`: `antigravity-claude-proxy` compatible `/account-limits` structure
    - `backend`: Active `antigravity-claude-proxy` backend & masking metadata
    """
    access_token, project_id = get_credentials()
    now = datetime.now(timezone.utc)

    # 1. Fetch subscription & project info via loadCodeAssist (antigravity-claude-proxy method)
    sub_info = get_subscription_and_project(access_token)
    if sub_info.get("project_id"):
        project_id = sub_info["project_id"]

    # 2. Fetch per-model quotas & metadata via fetchAvailableModels (antigravity-claude-proxy method)
    avail_data = fetch_available_models_raw(access_token, project_id)
    raw_models = avail_data.get("models") or {}

    # 3. Also fetch retrieveUserQuotaSummary for 5h vs weekly window granularity if available
    summary_groups = {}
    try:
        session_id = acp_backend.derive_session_id(get_active_email())
        headers = acp_backend.build_antigravity_headers(access_token, session_id=session_id)
        spec = acp_backend.get_backend_spec()
        ep = (spec.get("endpointFallbacks") or ["https://daily-cloudcode-pa.googleapis.com"])[0]
        s_resp = requests.post(
            f"{ep}/v1internal:retrieveUserQuotaSummary",
            headers=headers,
            json={"project": project_id},
            timeout=6,
        )
        if s_resp.ok:
            for g in s_resp.json().get("groups", []):
                gname = g.get("displayName", "")
                key = "gemini" if "Gemini" in gname else "claude"
                summary_groups[key] = g.get("buckets", [])
    except Exception:
        pass

    # Monthly billing cycle helper
    if now.month == 12:
        next_month_dt = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        next_month_dt = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    days_until_monthly_reset = (next_month_dt.date() - now.date()).days

    models_list = []
    acp_quotas_map = {}

    # Group trackers for the 3 pools from fetchAvailableModels
    pool_trackers = {
        "gemini-4-argon": {"rem": None, "reset": None, "models": []},
        "gemini": {"rem": None, "reset": None, "models": []},
        "claude": {"rem": None, "reset": None, "models": []},
    }

    for model_id, mdata in raw_models.items():
        # Skip internal chat/tab completion placeholders
        if model_id.startswith("chat_") or model_id.startswith("tab_") or model_id.endswith("-disabled"):
            continue

        family, provider = _classify_model_family(model_id, mdata)
        if family == "other":
            continue

        qinfo = mdata.get("quotaInfo") or {}
        rem_frac = qinfo.get("remainingFraction")
        reset_time = qinfo.get("resetTime")
        if rem_frac is None:
            rem_frac = 0.0 if reset_time else 1.0

        rem_pct = round(rem_frac * 100, 1)
        used_pct = round((1.0 - rem_frac) * 100, 1)
        fmt_reset, sec_reset = format_reset_delta(reset_time, now)

        display_name = mdata.get("displayName") or model_id

        acp_quotas_map[model_id] = {
            "remainingFraction": rem_frac,
            "resetTime": reset_time,
            "displayName": display_name,
            "family": family,
            "provider": provider,
        }

        model_entry = {
            "model": model_id,
            "display_name": display_name,
            "family": family,
            "provider": provider,
            "remaining_fraction": rem_frac,
            "remaining_percent": rem_pct,
            "used_percent": used_pct,
            "reset_time": reset_time,
            "time_until_reset": fmt_reset,
            "seconds_until_reset": sec_reset,
            "supports_thinking": bool(mdata.get("supportsThinking")),
            "supports_images": bool(mdata.get("supportsImages")),
            "max_tokens": mdata.get("maxTokens"),
            "max_output_tokens": mdata.get("maxOutputTokens"),
        }
        models_list.append(model_entry)

        pool_key = "gemini-4-argon" if family == "gemini-4-argon" else ("gemini" if family == "gemini" else "claude")
        tracker = pool_trackers[pool_key]
        tracker["models"].append(model_entry)
        if tracker["rem"] is None or rem_frac < tracker["rem"]:
            tracker["rem"] = rem_frac
            tracker["reset"] = reset_time

    # Sort models_list nicely: Gemini 4 Argon -> Gemini 3.8/3.5/2.5 -> Claude 5.5 -> GPT-OSS
    def _sort_key(item):
        fam_order = {"gemini-4-argon": 0, "gemini": 1, "claude": 2, "openai": 3}
        return (fam_order.get(item["family"], 9), item["model"])

    models_list.sort(key=_sort_key)

    # Build the 3 unified pools
    pools = []

    # Pool 1: Gemini 4 Argon (Unlocked by Antigravity Hub masking!)
    argon_t = pool_trackers["gemini-4-argon"]
    if argon_t["rem"] is not None:
        rem_a = argon_t["rem"]
        used_a = round((1.0 - rem_a) * 100, 1)
        rem_pct_a = round(rem_a * 100, 1)
        fmt_a, sec_a = format_reset_delta(argon_t["reset"], now)
        pools.append({
            "id": "gemini-4-argon",
            "name": "Gemini 4 Argon (Next-Gen Pool)",
            "subtitle": "Gemini 4 Argon High, Medium & Low (Desbloqueado via Antigravity Hub)",
            "provider": "google",
            "badge": "Antigravity Hub 2.25+",
            "quota_source": "fetchAvailableModels",
            "used_percent": used_a,
            "remaining_percent": rem_pct_a,
            "reset_time": argon_t["reset"],
            "time_until_reset": fmt_a,
            "seconds_until_reset": sec_a,
            # Also populate 5h/weekly fields for UI compatibility
            "used_percent_5h": used_a,
            "remaining_percent_5h": rem_pct_a,
            "reset_time_5h": argon_t["reset"],
            "time_until_reset_5h": fmt_a,
            "seconds_until_reset_5h": sec_a,
            "used_percent_weekly": used_a,
            "remaining_percent_weekly": rem_pct_a,
            "reset_time_weekly": argon_t["reset"],
            "time_until_reset_weekly": fmt_a,
            "seconds_until_reset_weekly": sec_a,
            "window_type": "weekly" if sec_a > 21600 else "5h",
            "monthly_reset_date": next_month_dt.strftime("%d/%m/%Y"),
            "days_until_monthly_reset": days_until_monthly_reset,
            "models": [
                {"id": "gemini-4-argon", "name": "Gemini 4 Argon", "desc": "Esforço controlado pelo menu do Hermes (Low / Med / High)"},
            ],
        })

    # Pool 2: Gemini 3.8 / 3.5 / 2.5 Flash & Pro
    gem_t = pool_trackers["gemini"]
    gem_buckets = summary_groups.get("gemini", [])
    b_5h_gem = next((b for b in gem_buckets if b.get("window") == "5h"), {})
    b_w_gem = next((b for b in gem_buckets if b.get("window") == "weekly"), {})

    rem_gem_5h = gem_t["rem"] if gem_t["rem"] is not None else b_5h_gem.get("remainingFraction", 1.0)
    reset_gem_5h = gem_t["reset"] or b_5h_gem.get("resetTime")
    used_gem_5h = round((1.0 - rem_gem_5h) * 100, 1)
    fmt_gem_5h, sec_gem_5h = format_reset_delta(reset_gem_5h, now)

    rem_gem_w = b_w_gem.get("remainingFraction", rem_gem_5h)
    reset_gem_w = b_w_gem.get("resetTime", reset_gem_5h)
    used_gem_w = round((1.0 - rem_gem_w) * 100, 1)
    fmt_gem_w, sec_gem_w = format_reset_delta(reset_gem_w, now)

    pools.append({
        "id": "gemini",
        "name": "Gemini 3.8 & 2.5 (Flash / Pro Pool)",
        "subtitle": "Gemini 3.8 Flash, Gemini 3.5 Flash Lite & Gemini 2.5 Pro",
        "provider": "google",
        "badge": "Google AI Pro",
        "quota_source": "fetchAvailableModels",
        "used_percent": used_gem_5h,
        "remaining_percent": round(rem_gem_5h * 100, 1),
        "reset_time": reset_gem_5h,
        "time_until_reset": fmt_gem_5h,
        "seconds_until_reset": sec_gem_5h,
        "used_percent_5h": used_gem_5h,
        "remaining_percent_5h": round(rem_gem_5h * 100, 1),
        "reset_time_5h": reset_gem_5h,
        "time_until_reset_5h": fmt_gem_5h,
        "seconds_until_reset_5h": sec_gem_5h,
        "used_percent_weekly": used_gem_w,
        "remaining_percent_weekly": round(rem_gem_w * 100, 1),
        "reset_time_weekly": reset_gem_w,
        "time_until_reset_weekly": fmt_gem_w,
        "seconds_until_reset_weekly": sec_gem_w,
        "window_type": "dual",
        "monthly_reset_date": next_month_dt.strftime("%d/%m/%Y"),
        "days_until_monthly_reset": days_until_monthly_reset,
        "models": [
            {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash", "desc": "Esforço controlado pelo menu do Hermes (Low / Med / High)"},
            {"id": "gemini-3.5-flash-lite", "name": "Gemini 3.5 Flash Lite", "desc": "Ultra leve e rápido"},
            {"id": "gemini-3-flash", "name": "Gemini 3 Flash", "desc": "Rápido com raciocínio"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "desc": "Modelo Pro clássico"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "Baixa latência"},
        ],
    })

    # Pool 3: Claude 5.5 (Sonnet & Opus) & GPT-OSS
    cl_t = pool_trackers["claude"]
    cl_buckets = summary_groups.get("claude", [])
    b_5h_cl = next((b for b in cl_buckets if b.get("window") == "5h"), {})
    b_w_cl = next((b for b in cl_buckets if b.get("window") == "weekly"), {})

    rem_cl_main = cl_t["rem"] if cl_t["rem"] is not None else b_w_cl.get("remainingFraction", 1.0)
    reset_cl_main = cl_t["reset"] or b_w_cl.get("resetTime")
    used_cl_main = round((1.0 - rem_cl_main) * 100, 1)
    fmt_cl_main, sec_cl_main = format_reset_delta(reset_cl_main, now)

    rem_cl_5h = b_5h_cl.get("remainingFraction", rem_cl_main)
    reset_cl_5h = b_5h_cl.get("resetTime", reset_cl_main)
    used_cl_5h = round((1.0 - rem_cl_5h) * 100, 1)
    fmt_cl_5h, sec_cl_5h = format_reset_delta(reset_cl_5h, now)

    pools.append({
        "id": "claude",
        "name": "Claude 5.5 & GPT-OSS (Anthropic / OpenAI)",
        "subtitle": "Claude Sonnet 5.5, Claude Opus 5.5 & GPT-OSS 120B",
        "provider": "anthropic",
        "badge": "Pro Tier",
        "quota_source": "fetchAvailableModels",
        "used_percent": used_cl_main,
        "remaining_percent": round(rem_cl_main * 100, 1),
        "reset_time": reset_cl_main,
        "time_until_reset": fmt_cl_main,
        "seconds_until_reset": sec_cl_main,
        "used_percent_5h": used_cl_5h,
        "remaining_percent_5h": round(rem_cl_5h * 100, 1),
        "reset_time_5h": reset_cl_5h,
        "time_until_reset_5h": fmt_cl_5h,
        "seconds_until_reset_5h": sec_cl_5h,
        "used_percent_weekly": used_cl_main,
        "remaining_percent_weekly": round(rem_cl_main * 100, 1),
        "reset_time_weekly": reset_cl_main,
        "time_until_reset_weekly": fmt_cl_main,
        "seconds_until_reset_weekly": sec_cl_main,
        "window_type": "weekly",
        "monthly_reset_date": next_month_dt.strftime("%d/%m/%Y"),
        "days_until_monthly_reset": days_until_monthly_reset,
        "models": [
            {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "desc": "Esforço controlado pelo menu do Hermes (Low / Med / High)"},
            {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "desc": "Esforço controlado pelo menu do Hermes (Low / Med / High)"},
            {"id": "gpt-oss-120b", "name": "GPT-OSS 120B", "desc": "OpenAI Open-Weights"},
        ],
    })

    email = get_active_email()
    backend_summary = acp_backend.get_backend_status_summary()

    return {
        "tier": sub_info.get("tier_display", "Google AI Pro (g1-pro-tier)"),
        "tier_level": sub_info.get("tier", "pro"),
        "tier_id": sub_info.get("tier_id", "g1-pro-tier"),
        "credits": sub_info.get("credits"),
        "project": project_id,
        "email": email,
        "has_monthly_quota": False,
        "quota_architecture": "antigravity_claude_proxy_fetchAvailableModels",
        "backend": backend_summary,
        "pools": pools,
        "models": models_list,
        "account_limits": {
            "timestamp": now.isoformat(),
            "totalAccounts": 1,
            "models": [m["model"] for m in models_list],
            "accounts": [
                {
                    "email": email,
                    "status": "ok",
                    "subscription": {
                        "tier": sub_info.get("tier", "pro"),
                        "tierId": sub_info.get("tier_id", "g1-pro-tier"),
                        "projectId": project_id,
                        "credits": sub_info.get("credits"),
                    },
                    "limits": acp_quotas_map,
                }
            ],
        },
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
    """Returns current auth state, active account email, and backend status."""
    backend_info = acp_backend.get_backend_status_summary()
    try:
        import accounts
        acc = accounts.get_active_account()
        if acc:
            return {
                "authenticated": True,
                "email": acc.get("email"),
                "tier": acc.get("tier", "Google AI Pro (g1-pro-tier)"),
                "project_id": acc.get("project_id", "aicode-consumers"),
                "backend": backend_info,
            }
        token, project_id = get_credentials()
        return {
            "authenticated": bool(token),
            "email": _token_cache.get("email") or "",
            "tier": _token_cache.get("tier") or "Google AI Pro (g1-pro-tier)",
            "project_id": project_id,
            "credits": _token_cache.get("credits"),
            "backend": backend_info,
        }
    except Exception as e:
        return {
            "authenticated": False,
            "email": None,
            "tier": None,
            "error": str(e),
            "backend": backend_info,
        }
