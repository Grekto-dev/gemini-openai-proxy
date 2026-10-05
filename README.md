# 🚀 Gemini Google AI Pro -> OpenAI Proxy Server

A high-fidelity, OpenAI-compatible proxy server (`/v1/chat/completions` and `/v1/models`) designed specifically to bridge your **Google AI Pro** subscription (via official Antigravity session and Google OAuth) directly to autonomous agent harnesses such as **Hermes Agent**, **Claude Code**, **Cursor**, **Cline**, and Python OpenAI SDK scripts.

---

## 🌟 Key Features

- **Full OpenAI Compatibility**: Seamless bidirectional translation between the OpenAI specification (`chat.completion`, streaming SSE, `tool_calls`) and Google Cloud Code protocol.
- **Hermes Agent Auto-Sync**: Built-in synchronizer (`sync_hermes.py`) that exports all Gemini and Claude models directly into `~/.hermes/config.yaml` and model cache, just like OpenRouter.
- **Function Calling & Tool Handling**: Full multi-turn support for tool execution, automatically managing `thoughtSignature` (`skip_thought_signature_validator`) so local agent harnesses execute tools without conflicts.
- **Secure Keyring & Multi-Account Support**: Reads official Antigravity OAuth tokens via Linux Secret Service (DBus / GNOME Keyring / KWallet), with support for managing multiple Google accounts with instant account switching.
- **Real-Time Quota Tracking & Reset Timers**: Live tracking of 5-hour rolling limits and weekly/monthly reset windows aggregated by model family (`gemini-2.5-pro`, `gemini-2.5-flash`, etc.).
- **Optional API Key Manager**: Generate, list, mask/unmask, copy, and revoke custom local API keys with optional toggle behavior (free/open mode when list is empty, bearer auth when keys are registered).
- **Modern Web Dashboard**: Real-time dark-themed interface with start/pause controls, account selector, copyable model list, and interactive connection guides.
- **Dynamic Internationalization (i18n)**: Switch between **Português (Brasil)** and **English** on the fly for both the Web UI and colorized terminal logs.
- **One-Command Service Controls**: Run detached in background using `./start.sh` and cleanly terminate with `./stop.sh`.

---

## 📁 Project Structure

```
gemini-openai-proxy/
├── start.sh              # Background launcher script
├── stop.sh               # Graceful shutdown script
├── server.py             # Multi-threaded HTTP server (OpenAI endpoints, API & Dashboard)
├── auth.py               # DBus Secret Service integration and quota retrieval
├── accounts.py           # Multi-account Google OAuth manager with instant switching
├── api_keys.py           # Local API key manager (optional authorization mode)
├── sync_hermes.py        # Automatic model synchronizer for Hermes Agent
├── converter.py          # Payload converter: OpenAI <-> Cloud Code Proto
├── config.py             # Settings persistence manager (host, port, language)
├── i18n.py               # Internationalization dictionary (PT-BR / EN) and logger
├── static/
│   └── index.html        # Responsive web dashboard
├── test_client.py        # End-to-end integration test client
└── README.md             # Project documentation
```

---

## ⚡ Quick Start

### 1. Start the Server
Ensure you have logged into Antigravity or authenticate via Google OAuth in the dashboard, then run:
```bash
./start.sh
```
The terminal will display the process PID and access URLs:
```text
========================================================
       Gemini Google AI Pro -> OpenAI Proxy Server      
========================================================
[*] Starting server in background...
[✓] Server successfully started! (PID: 12345)

  🌐 Web Dashboard:      http://127.0.0.1:8000/
  🔌 OpenAI Base URL:    http://127.0.0.1:8000/v1
  📊 Quota & Status:     http://127.0.0.1:8000/v1/quota

  📋 Useful commands:
     - Follow real-time logs: tail -f server.log
     - Stop proxy server:     ./stop.sh
========================================================
```

### 2. Open the Web Dashboard
Open your browser at:
👉 **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

From the dashboard you can:
- View active account status and subscription tier (`Google AI Pro`).
- Switch accounts or add new Google accounts via built-in OAuth flow.
- Monitor live quota usage bars and countdown timers for 5h and weekly resets.
- Manage local API keys (optional).
- Change UI and terminal log language (PT-BR / EN).
- Copy model IDs with a single click.

### 3. Monitor Terminal Logs
To observe incoming requests, response times, and token usage in real time:
```bash
tail -f server.log
```

### 4. Stop the Server
To shut down the background proxy cleanly:
```bash
./stop.sh
```

---

## 🤖 Connecting to Hermes Agent

### Automatic Sync (Recommended)
Sync all proxy models directly to Hermes Agent config and provider cache:
```bash
python3 sync_hermes.py
```
This registers `google-ai-pro` as a custom provider in `~/.hermes/config.yaml` and populates `~/.hermes/provider_models_cache.json`.

Then invoke Hermes using:
```bash
hermes --provider custom:google-ai-pro -m gemini-3.8-flash
```

### Manual Configuration via Environment Variables
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
export OPENAI_API_KEY="google-ai-pro-proxy"  # Any non-empty string or registered API key
export MODEL="gemini-3.8-flash"

hermes run "Review current directory files and summarize key refactorings"
```

> **Note on Tool Calling**: The proxy forwards function schemas to Gemini and maps responses back into standard `tool_calls`. Hermes receives tool calls, executes them locally in its own harness, and sends back results seamlessly across multi-turn interactions.

---

## 🛠️ Other Integrations

### Claude Code
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
export OPENAI_API_KEY="google-ai-pro-proxy"
claude --model gemini-2.5-pro
```

### Cursor / Cline
In editor settings:
- **Provider**: OpenAI Compatible
- **Base URL**: `http://127.0.0.1:8000/v1`
- **API Key**: `google-ai-pro-proxy`
- **Model ID**: `gemini-3.8-flash` (or `gemini-2.5-pro`)

### Python (OpenAI SDK)
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="google-ai-pro-proxy"
)

response = client.chat.completions.create(
    model="gemini-3.8-flash",
    messages=[
        {"role": "user", "content": "Explain briefly how this proxy operates."}
    ],
    stream=True
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
print()
```

---

## 📊 API Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | Interactive Web Dashboard |
| `/v1/models` | `GET` | List available Gemini & Claude models in OpenAI format |
| `/v1/chat/completions` | `POST` | OpenAI-compatible completions and streaming |
| `/v1/quota` | `GET` | Real-time quota consumption and 5h/weekly reset countdowns |
| `/api/server/status` | `GET` | Returns proxy operational status (Active / Paused) |
| `/api/server/toggle` | `POST` | Toggle proxy state between Active and Paused |
| `/api/accounts` | `GET` | List registered accounts and identify active account |
| `/api/accounts/switch` | `POST` | Switch active Google account |
| `/api/accounts/remove` | `POST` | Sign out / remove an account |
| `/api/oauth/start` | `GET` | Initiate local callback server and generate Google OAuth URL |
| `/api/oauth/status` | `GET` | Query ongoing OAuth flow status |
| `/api/keys` | `GET` | List registered API keys (masked) and auth mode |
| `/api/keys/create` | `POST` | Generate a new custom API key |
| `/api/keys/delete` | `POST` | Delete an existing API key |
| `/api/config` | `GET/POST` | Read or update host, port, and active language |

---

## 🔒 Compliance and Personal Use

This proxy is designed for personal development use in accordance with Google AI terms:
1. **Legitimate Authentication**: Interacts with official endpoints using authentic user-authorized sessions on localhost.
2. **Strictly Personal**: Does not function as a SaaS, does not redistribute credentials, and stores all session information locally.
3. **Quota Conformance**: Fully respects plan rate limits and quotas without attempting to bypass capacity controls.
