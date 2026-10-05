"""
Configuration manager for Gemini-OpenAI Proxy.
Handles loading and persisting settings in config.json.
"""

import os
import json

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8000,
    "language": "pt-BR"  # Options: "pt-BR", "en"
}


def load_config():
    """Loads settings from config.json or initializes with defaults."""
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            # Ensure all default keys exist
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_config(cfg):
    """Saves settings dictionary to config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def update_config(host=None, port=None, language=None):
    """Updates specific settings and persists them."""
    cfg = load_config()
    if host is not None:
        cfg["host"] = str(host).strip()
    if port is not None:
        cfg["port"] = int(port)
    if language is not None and language in ("pt-BR", "en"):
        cfg["language"] = language
    save_config(cfg)
    return cfg
