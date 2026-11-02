# 🚀 Gemini Google AI Pro -> OpenAI Proxy Server (v2.0 • Antigravity Hub Backend)

A high-fidelity, OpenAI-compatible proxy server (`/v1/chat/completions` and `/v1/models`) that bridges your **Google AI Pro** subscription directly to autonomous agent harnesses such as **Hermes Agent**, **Cursor**, **Cline**, **OpenCode**, and Python OpenAI SDK scripts.

Powered by a dynamic specification bridge ([`acp_backend.py`](acp_backend.py)) over **[`antigravity-claude-proxy`](https://github.com/badrisnarayanan/antigravity-claude-proxy)**, it automatically adopts the latest official Antigravity Hub call masking, headers, system prompt wrappers, and quota retrieval protocol.

---

## 🌟 Key Features (v2.0)

- **Dynamic `antigravity-claude-proxy` Backend Bridge (`acp_backend.py`)**:
  - Dynamically extracts `ANTIGRAVITY_HEADERS`, `LOAD_CODE_ASSIST_HEADERS`, `CLIENT_METADATA`, `ANTIGRAVITY_SYSTEM_INSTRUCTION`, and `endpointFallbacks` (`daily-cloudcode-pa.googleapis.com` -> `cloudcode-pa.googleapis.com`) directly from the `antigravity-claude-proxy` ESM modules via Node.js (with a pure-Python parser fallback).
  - **Future-Proof Auto-Reload**: Monitors file modification timestamps (`mtime`) on `antigravity-claude-proxy` and exposes a 1-click **`↻ git pull`** button in the WebUI (`/api/backend/update`), immediately applying any future upstream masking updates without code changes.
- **Multi-Layer Call Masking & Anti-Bot Identity Filter**:
  1. Converts incoming OpenAI requests (`messages`, `tools`, `reasoning_effort`) into Google Cloud Code format.
  2. Applies the **Anti-Bot Identity Scrubbing Filter** (`scrub_identity`) across all system prompts and tool descriptions to strip third-party harness signatures (`Hermes Agent`, `Nous Research`, `OpenCode`, `Claude Code`, `Cursor`, etc.).
  3. Injects the official `ANTIGRAVITY_SYSTEM_INSTRUCTION` identity preamble with the `Please ignore the following [ignore]...[/ignore]` wrapper and official `antigravity/hub/<version>` headers (`X-Client-Name: antigravity`, `X-Client-Version`, `x-goog-api-client`, deterministic `X-Machine-Session-Id`).
- **Unlocked `gemini-4-argon` Model Family**:
  - Full support for the **Gemini 4 Argon** pool (`gemini-4-argon` with adaptive effort, `gemini-4-argon-high`, `gemini-4-argon-medium`, `gemini-4-argon-low`), alongside **Gemini 3.8 Flash**, **Claude Sonnet 5.5**, and **Claude Opus 5.5**.
- **Updated Quota & Account Limits Protocol (`/v1/quota` & `/account-limits`)**:
  - Queries `v1internal:loadCodeAssist` (discovering subscription tier, Google One AI credits, and managed `cloudaicompanionProject`) and `v1internal:fetchAvailableModels` using `ANTIGRAVITY_HEADERS`.
  - Displays 3 unified quota pools (**Gemini 4 Argon**, **Gemini 3.8 / 2.5**, **Claude 5.5**) plus a 22-model individual breakdown with family filters in the Web Dashboard.
- **Terminal-Bound Default & `--silent` Background Mode (`start.sh`)**:
  - Running `./start.sh` keeps both the proxy server and WebUI bound to the active terminal window — closing the terminal or pressing `Ctrl+C` automatically shuts down the server.
  - Running `./start.sh --silent` (or `./start.sh silent` / `./start.sh -s`) launches the server detached in the background, independent of the terminal.
- **Automated Logging, Real-Time Error Alerts & Self-Diagnostics (`logger.py`)**:
  - Live structured logs in the terminal, `server.log`, and the WebUI **Logs & Diagnóstico** tab (`/api/logs`), with automatic OAuth/API key redaction.
  - Automatic error toast banner in the WebUI whenever an upstream or request error occurs, plus a 1-click **Self-Diagnostics** suite (`/api/diagnostics/run`).
- **Hermes Agent 1-Click Sync (`sync_hermes.py`)**:
  - Exports all 24 dynamic and fixed-tier models directly into `~/.hermes/config.yaml` and `~/.hermes/provider_models_cache.json`.

---

## 📁 Project Structure

```text
gemini-openai-proxy/
├── start.sh              # Launcher script (Foreground terminal-bound default, or --silent background mode)
├── stop.sh               # Graceful shutdown script
├── server.py             # Multi-threaded HTTP server (OpenAI endpoints, Quota, Logs, Diagnostics & WebUI)
├── acp_backend.py        # Dynamic Node ESM / Python bridge to antigravity-claude-proxy masking & specs
├── converter.py          # OpenAI <-> Cloud Code converter + Anti-Bot scrub_identity filter
├── auth.py               # Keyring / OAuth integration + loadCodeAssist & fetchAvailableModels quota protocol
├── logger.py             # Structured ring-buffer logger, credential redactor & self-diagnostics runner
├── accounts.py           # Multi-account Google OAuth manager with instant switching
├── api_keys.py           # Local API key manager (optional authorization mode)
├── sync_hermes.py        # Automatic 24-model synchronizer for Hermes Agent
├── config.py             # Settings persistence manager (host, port, language)
├── i18n.py               # Internationalization dictionary (PT-BR / EN) and terminal logger
├── static/
│   └── index.html        # Responsive Web Dashboard (Pools, /account-limits, Backend status, Live Logs)
├── test_client.py        # End-to-end integration & debugging test suite
└── README.md             # Project documentation
```

---

## ⚡ Quick Start

### 1. Start the Server

#### Option A: Terminal-Bound Mode (Default)
Keeps the proxy and WebUI active **only while the terminal window remains open**. Closing the terminal or pressing `Ctrl+C` cleanly terminates the server:
```bash
./start.sh
```

#### Option B: Silent Background Mode (`--silent` / `silent` / `-s`)
Runs the proxy and WebUI detached in the background, independent of the terminal window:
```bash
./start.sh --silent
# or: ./start.sh silent
```

To stop a background instance at any time:
```bash
./stop.sh
```

### 2. Open the Web Dashboard
Open your browser at:
👉 **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

From the WebUI you can:
- Monitor **3 Unified Quota Pools** (`Gemini 4 Argon`, `Gemini 3.8 / 2.5`, `Claude 5.5`) and **22 Per-Model Quotas** (`/account-limits`).
- View active **Backend & Masking Status** (`User-Agent: antigravity/hub/2.25.0`, `X-Client-Version`, active endpoints) and update `antigravity-claude-proxy` via **`↻ git pull`**.
- Inspect real-time requests, stack traces, and run the **Self-Diagnostics Battery** in the **Logs & Diagnóstico** tab.
- Switch Google accounts, manage optional local API keys, and sync models to **Hermes Agent** with 1 click.

---

## 🤖 Connecting to Hermes Agent

### Automatic Sync (Recommended)
Export all 24 models (including `gemini-4-argon` and dynamic-effort aliases) directly to Hermes Agent:
```bash
python3 sync_hermes.py
```
Then launch Hermes:
```bash
hermes --provider custom:google-ai-pro -m gemini-4-argon
# or:
hermes --provider custom:google-ai-pro -m gemini-3.8-flash
```

---

## 📊 API Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | Interactive Web Dashboard (Pools, Models, Guide, Settings, Live Logs) |
| `/v1/models` | `GET` | List all 24 available Gemini 4 Argon, Gemini 3.8/2.5 & Claude 5.5 models |
| `/v1/chat/completions` | `POST` | OpenAI-compatible completions & SSE streaming with anti-bot scrubbing |
| `/v1/quota` | `GET` | Unified 3-pool quota metrics, per-model breakdown, credits & backend status |
| `/account-limits` | `GET` | `antigravity-claude-proxy` compatible account limits & model quota matrix |
| `/api/backend/status` | `GET` | Active `antigravity-claude-proxy` version, masking headers, and endpoints |
| `/api/backend/update` | `POST` | Executes `git pull --ff-only` on `antigravity-claude-proxy` and reloads specs |
| `/api/logs` | `GET` | Live structured log and error stream (`?since_id=&level=&limit=`) |
| `/api/logs/clear` | `POST` | Clear in-memory log buffer and truncate `server.log` |
| `/api/diagnostics/run` | `POST` | Run automated 4-stage self-diagnostics battery |
| `/api/hermes/sync` | `POST` | Export all models to `~/.hermes/config.yaml` |
| `/api/server/status` | `GET` | Returns proxy operational status (`running: true/false`) |
| `/api/server/toggle` | `POST` | Toggle proxy state between Operational and Paused |
| `/api/accounts` | `GET` | List registered Google accounts and active account |
| `/api/keys` | `GET/POST` | List or generate optional local API keys |

---

## 🧪 Running the Test & Debugging Battery

Run the automated test suite to verify backend extraction, anti-bot scrubbing, quota retrieval, streaming/non-streaming completions, and error logging:
```bash
python3 test_client.py
```

---

## 🔒 Security & Credential Safety

- All local credentials (`accounts.json`, `keys.json`, `config.json`, `.env`, `*.log`, `*.pid`) are strictly git-ignored.
- `logger.py` automatically redacts `Bearer` tokens, Google OAuth `ya29.*` / `1//*` tokens, and `sk-*` keys before writing to `server.log` or exposing logs in the WebUI.
