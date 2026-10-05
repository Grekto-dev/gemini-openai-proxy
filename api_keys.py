"""
API Key Management for Gemini OpenAI Proxy.
Keys are optional. When no keys are defined, the proxy allows open/blank access.
When 1 or more keys are defined, requests to /v1/* must provide a valid key.
"""

import json
import os
import secrets
from datetime import datetime, timezone

KEYS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.json")


def _read_data():
    if not os.path.exists(KEYS_FILE):
        return []
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("keys", [])
    except Exception as e:
        print(f"[WARN] Error reading {KEYS_FILE}: {e}")
    return []


def _save_data(keys):
    try:
        temp_file = KEYS_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({"keys": keys}, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, KEYS_FILE)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save {KEYS_FILE}: {e}")
        return False


def list_keys():
    """Returns the list of all defined API keys."""
    return _read_data()


def has_keys():
    """Returns True if at least one API key is defined (auth enforced)."""
    return len(_read_data()) > 0


def generate_key(name=None):
    """
    Generates a new secure random API key with 'gai-' prefix.
    Saves to keys.json and returns the created key object.
    """
    keys = _read_data()
    key_id = f"key_{secrets.token_hex(6)}"
    raw_token = f"gai-{secrets.token_hex(20)}"
    now_iso = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
    
    label = (name or "").strip()
    if not label:
        label = f"Chave #{len(keys) + 1}"

    new_entry = {
        "id": key_id,
        "name": label,
        "key": raw_token,
        "created_at": now_iso
    }
    keys.append(new_entry)
    _save_data(keys)
    return new_entry


def remove_key(key_id):
    """Removes a key by ID. Returns True if removed, False otherwise."""
    keys = _read_data()
    original_len = len(keys)
    keys = [k for k in keys if k.get("id") != key_id]
    if len(keys) != original_len:
        _save_data(keys)
        return True
    return False


def is_valid_key(token):
    """
    Validates token against configured keys.
    If no keys are configured, all keys (including blank/None) are considered valid.
    """
    keys = _read_data()
    if not keys:
        return True
    if not token:
        return False
    clean_token = token.strip()
    return any(k.get("key") == clean_token for k in keys)
