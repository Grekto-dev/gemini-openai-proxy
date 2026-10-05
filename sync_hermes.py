"""
Hermes Agent Model Exporter & Synchronizer.
Exports all Google AI Pro models from the proxy directly into Hermes Agent,
registering the proxy as a custom provider in ~/.hermes/config.yaml and
populating ~/.hermes/provider_models_cache.json so that all models appear
automatically in Hermes (CLI, TUI, and Web picker), just like OpenRouter!
"""

import os
import json
import time
import subprocess
import requests

HERMES_DIR = os.path.expanduser("~/.hermes")
HERMES_CONFIG_PATH = os.path.join(HERMES_DIR, "config.yaml")
HERMES_CACHE_PATH = os.path.join(HERMES_DIR, "provider_models_cache.json")


def _find_hermes_python():
    tools_dir = os.path.join(HERMES_DIR, "tools")
    if os.path.isdir(tools_dir):
        for entry in os.listdir(tools_dir):
            py_bin = os.path.join(tools_dir, entry, "bin", "python3")
            if os.path.isfile(py_bin) and os.access(py_bin, os.X_OK):
                return py_bin
    import sys
    return sys.executable


HERMES_PYTHON = _find_hermes_python()

DEFAULT_PROXY_URL = "http://127.0.0.1:8000/v1"
PROVIDER_NAME = "google-ai-pro"
PROVIDER_DISPLAY_NAME = "Google AI Pro (Proxy)"
DEFAULT_MODEL = "gemini-3.8-flash"


def get_proxy_models(proxy_url=DEFAULT_PROXY_URL):
    """Fetches live model IDs from proxy /v1/models endpoint."""
    try:
        url = f"{proxy_url.rstrip('/')}/models"
        resp = requests.get(url, timeout=5)
        if resp.ok:
            data = resp.json()
            models = [m["id"] for m in data.get("data", []) if m.get("id")]
            if models:
                return models
    except Exception:
        pass

    # Fallback to known Google AI Pro models list
    return [
        "gemini-3.8-flash",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "gemini",
        "claude",
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
        "claude-sonnet-5-5-high",
        "claude-sonnet-5-5-medium",
        "claude-sonnet-5-5-low",
        "claude-opus-5-5-high",
        "claude-opus-5-5-medium",
        "claude-opus-5-5-low",
        "gemini-2.5-flash",
        "gpt-oss-120b-medium"
    ]


def sync_to_hermes(proxy_url=DEFAULT_PROXY_URL, set_as_default=False):
    """
    Exports models to Hermes Agent:
    1. Updates ~/.hermes/config.yaml using ruamel.yaml via Hermes python.
    2. Updates ~/.hermes/provider_models_cache.json.
    """
    if not os.path.exists(HERMES_DIR):
        raise FileNotFoundError(f"Diretório do Hermes não encontrado em {HERMES_DIR}")

    models = get_proxy_models(proxy_url)

    hermes_agent_dir = os.path.join(HERMES_DIR, "hermes-agent")

    # 1. Update config.yaml via Hermes's own ruamel.yaml engine to preserve comments
    script = f"""
import sys
sys.path.insert(0, '{hermes_agent_dir}')
import hermes_bootstrap
from hermes_yaml import YAML

yaml = YAML()
config_path = '{HERMES_CONFIG_PATH}'

with open(config_path, 'r', encoding='utf-8') as f:
    cfg = yaml.load(f)

custom_providers = cfg.get('custom_providers')
if custom_providers is None or not isinstance(custom_providers, list):
    custom_providers = []
    cfg['custom_providers'] = custom_providers

# Find existing entry or create new
entry = None
for p in custom_providers:
    if isinstance(p, dict) and (p.get('name') in ('{PROVIDER_NAME}', '{PROVIDER_DISPLAY_NAME}') or p.get('base_url') == '{proxy_url}'):
        entry = p
        break

models_list = {json.dumps(models)}

if entry is None:
    entry = {{
        'name': '{PROVIDER_NAME}',
        'base_url': '{proxy_url}',
        'api_key': 'google-ai-pro-proxy',
        'api_mode': 'chat_completions',
        'model': '{DEFAULT_MODEL}',
        'models': models_list
    }}
    custom_providers.append(entry)
else:
    entry['base_url'] = '{proxy_url}'
    entry['api_key'] = 'google-ai-pro-proxy'
    entry['api_mode'] = 'chat_completions'
    entry['model'] = '{DEFAULT_MODEL}'
    entry['models'] = models_list

if {str(set_as_default)}:
    if 'model' not in cfg or not isinstance(cfg['model'], dict):
        cfg['model'] = {{}}
    cfg['model']['default'] = '{DEFAULT_MODEL}'
    cfg['model']['provider'] = 'custom'
    cfg['model']['base_url'] = '{proxy_url}'
    cfg['model']['api_key'] = 'google-ai-pro-proxy'
    cfg['model']['api_mode'] = 'chat_completions'

with open(config_path, 'w', encoding='utf-8') as f:
    yaml.dump(cfg, f)

print("CONFIG_UPDATED_OK")
"""

    res = subprocess.run([HERMES_PYTHON, "-c", script], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao atualizar config.yaml do Hermes: {res.stderr}")

    # 2. Update provider_models_cache.json
    cache_data = {}
    if os.path.exists(HERMES_CACHE_PATH):
        try:
            with open(HERMES_CACHE_PATH, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
        except Exception:
            cache_data = {}

    now = time.time()
    cache_data[PROVIDER_NAME] = {
        "fp": "proxy",
        "at": now,
        "models": models
    }
    cache_data[f"custom:{proxy_url.rstrip('/').lower()}"] = {
        "fp": "proxy",
        "at": now,
        "models": models
    }

    with open(HERMES_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "provider": PROVIDER_NAME,
        "base_url": proxy_url,
        "default_model": DEFAULT_MODEL,
        "models_count": len(models),
        "models": models,
        "set_as_default": set_as_default
    }


if __name__ == "__main__":
    print("[*] Sincronizando modelos do Google AI Pro com o Hermes Agent...")
    res = sync_to_hermes()
    print(f"[✓] Sucesso! {res['models_count']} modelos exportados para o Hermes Agent:")
    for m in res["models"]:
        print(f"    - {m}")
    print("\nNo Hermes você pode rodar:")
    print(f"    hermes --provider custom:{PROVIDER_NAME} -m {DEFAULT_MODEL}")
