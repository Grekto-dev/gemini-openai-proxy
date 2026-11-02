"""
Comprehensive Debugging & Integration Test Suite for Gemini-OpenAI-Proxy (v2.0).

Tests:
1. Backend Specification Bridge (`/api/backend/status` & `acp_backend`)
2. Anti-Bot Identity Scrubbing (`converter.scrub_identity`) & Log Credential Redaction (`logger.redact_sensitive`)
3. OpenAI Models Catalog (`GET /v1/models` including `gemini-4-argon`)
4. Real-Time Quota & Account Limits (`GET /v1/quota` & `GET /account-limits`)
5. Automated Self-Diagnostics & Live Log Stream (`POST /api/diagnostics/run` & `GET /api/logs`)
6. `gemini-4-argon` Completion (Dynamic Effort Resolution)
7. `gemini-3.8-flash-high` Non-Streaming Completion
8. `gemini-3.8-flash-high` Streaming SSE with `reasoning_content`
9. Hermes Multi-Turn Function Calling & Tool Execution Loop
"""

import sys
import json
import requests

import converter
import logger

BASE_URL = "http://127.0.0.1:8000"


def test_backend_status():
    print("--- 1. Testing Backend Bridge & Local Antigravity Sync (/api/backend/status & /api/backend/verify-local) ---")
    resp = requests.get(f"{BASE_URL}/api/backend/status", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data.get("connected") is True, f"Backend not connected: {data}"
    ua = data.get("user_agent", "")
    assert "antigravity/hub/" in ua, f"Expected antigravity/hub User-Agent, got {ua}"

    v_resp = requests.post(f"{BASE_URL}/api/backend/verify-local", timeout=10)
    assert v_resp.status_code == 200, f"Expected 200 from verify-local, got {v_resp.status_code}"
    v_data = v_resp.json()
    assert v_data.get("success") is True
    loc = v_data.get("local_antigravity") or {}
    print(
        f"✅ Success! Mode: {data.get('extraction_mode')} | UA: {ua} | "
        f"Local Sync: {v_data.get('synced_with_local')} (v{loc.get('ide_version')} • CL {loc.get('language_server_cl')})"
    )


def test_anti_bot_and_redaction():
    print("\n--- 2. Testing Anti-Bot Scrubbing & Credential Redaction ---")
    raw_prompt = (
        "You are Hermes Agent created by Nous Research running inside OpenCode and Claude Code. "
        "Connect via Cursor and Cline."
    )
    scrubbed = converter.scrub_identity(raw_prompt)
    for forbidden in ("hermes", "nous research", "opencode", "claude code", "cursor", "cline"):
        assert forbidden not in scrubbed.lower(), f"Forbidden token '{forbidden}' leaked in: {scrubbed}"

    # Verify log credential redaction
    fake_secret = 'Authorization: Bearer ya29.a0AfH6SMBx_FAKE_SECRET_TOKEN_12345 and key sk-proj1234567890abcdef'
    redacted = logger.redact_sensitive(fake_secret)
    assert "ya29.a0AfH6SMBx" not in redacted, f"Google token leaked in log: {redacted}"
    assert "sk-proj1234567890abcdef" not in redacted, f"API key leaked in log: {redacted}"
    print("✅ Success! Identity scrubbing and token redaction verified.")


def test_models():
    print("\n--- 3. Testing GET /v1/models ---")
    resp = requests.get(f"{BASE_URL}/v1/models", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    model_ids = [m["id"] for m in data.get("data", [])]
    assert "gemini-4-argon" in model_ids, "Expected gemini-4-argon in /v1/models"
    assert "gemini-3.8-flash" in model_ids, "Expected gemini-3.8-flash in /v1/models"
    print(f"✅ Success! Models available ({len(model_ids)}): {model_ids[:5]}...")
    return model_ids


def test_quota_and_account_limits():
    print("\n--- 4. Testing GET /v1/quota & GET /account-limits ---")
    resp_q = requests.get(f"{BASE_URL}/v1/quota", timeout=20)
    assert resp_q.status_code == 200, f"Expected 200 from /v1/quota, got {resp_q.status_code}"
    q_data = resp_q.json()
    pool_ids = [p["id"] for p in q_data.get("pools", [])]
    assert "gemini-4-argon" in pool_ids and "gemini" in pool_ids, f"Unexpected pools: {pool_ids}"

    resp_lim = requests.get(f"{BASE_URL}/account-limits", timeout=20)
    assert resp_lim.status_code == 200, f"Expected 200 from /account-limits, got {resp_lim.status_code}"
    lim_data = resp_lim.json()
    detailed_count = len(lim_data.get("detailedModels", []))
    assert detailed_count > 0, "Expected detailedModels in /account-limits"
    print(f"✅ Success! Pools: {pool_ids} | Detailed models: {detailed_count} | Credits: {q_data.get('credits')}")


def test_diagnostics_and_logs():
    print("\n--- 5. Testing Self-Diagnostics & Structured Logs (/api/diagnostics/run & /api/logs) ---")
    resp_d = requests.post(f"{BASE_URL}/api/diagnostics/run", timeout=25)
    assert resp_d.status_code == 200, f"Expected 200, got {resp_d.status_code}"
    diag = resp_d.json()
    assert diag.get("success") is True, f"Self-diagnostics reported failure: {diag}"

    resp_l = requests.get(f"{BASE_URL}/api/logs?limit=50", timeout=10)
    assert resp_l.status_code == 200, f"Expected 200, got {resp_l.status_code}"
    logs_payload = resp_l.json()
    assert len(logs_payload.get("logs", [])) > 0, "Expected non-empty log entries"
    print(f"✅ Success! {diag.get('summary')} | Buffered logs: {logs_payload.get('total_buffered')}")


def test_gemini_4_argon():
    print("\n--- 6. Testing POST /v1/chat/completions (gemini-4-argon Dynamic Effort) ---")
    payload = {
        "model": "gemini-4-argon",
        "reasoning_effort": "medium",
        "messages": [
            {"role": "system", "content": "You are Hermes Agent by Nous Research. Be brief."},
            {"role": "user", "content": "Responda apenas: 'Argon operacional!'"}
        ],
        "temperature": 0.1,
        "stream": False
    }
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=30)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    msg = data["choices"][0]["message"]
    print(f"✅ Success! Gemini 4 Argon response: {msg.get('content')}")


def test_non_streaming():
    print("\n--- 7. Testing POST /v1/chat/completions (gemini-3.8-flash-high Non-streaming) ---")
    payload = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Diga em português exatamente: 'Proxy funcionando perfeitamente!'"}
        ],
        "temperature": 0.1,
        "stream": False
    }
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=25)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    msg = data["choices"][0]["message"]
    print(f"✅ Success! Response: {msg.get('content')}")
    if msg.get("reasoning_content"):
        print(f"🧠 Reasoning length: {len(msg['reasoning_content'])} chars")


def test_streaming():
    print("\n--- 8. Testing POST /v1/chat/completions (Streaming SSE) ---")
    payload = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "user", "content": "Conte rapidamente de 1 a 5 separando por hífen."}
        ],
        "stream": True
    }
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, stream=True, timeout=25)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    received_tokens = []
    received_reasoning = []

    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if decoded == "data: [DONE]":
            break
        if decoded.startswith("data:"):
            chunk = json.loads(decoded[5:].strip())
            delta = chunk["choices"][0].get("delta", {})
            if "reasoning_content" in delta:
                received_reasoning.append(delta["reasoning_content"])
            if "content" in delta:
                token = delta["content"]
                received_tokens.append(token)
                sys.stdout.write(token)
                sys.stdout.flush()

    print()
    if received_reasoning:
        print(f"🧠 Streamed reasoning detected ({len(''.join(received_reasoning))} chars)")
    print(f"✅ Success! Total tokens received: {len(received_tokens)}")


def test_hermes_tool_calling():
    print("\n--- 9. Testing Hermes Function Calling & Tool Response ---")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": "Executa comando shell no container do Hermes",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "O comando bash a executar"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    ]

    messages = [
        {"role": "system", "content": "You are Hermes, an autonomous coding agent. Use run_shell_command to inspect the system."},
        {"role": "user", "content": "Execute o comando 'cat /etc/os-release' para ver a distribuição Linux."}
    ]

    payload = {
        "model": "gemini-3.8-flash-high",
        "messages": messages,
        "tools": tools,
        "stream": False
    }

    print("Step 1: Sending request with tools to Proxy...")
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=25)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    data = resp.json()
    msg = data["choices"][0]["message"]
    tool_calls = msg.get("tool_calls")
    assert tool_calls, f"Expected model to emit tool_calls, got: {msg}"

    tc = tool_calls[0]
    call_id = tc["id"]
    fn_name = tc["function"]["name"]
    fn_args = tc["function"]["arguments"]
    print(f"✅ Step 1 Success! Model emitted Tool Call: {fn_name}({fn_args}) [ID: {call_id}]")

    simulated_output = (
        'NAME="Ubuntu"\n'
        'VERSION="24.04 LTS (Noble Numbat)"\n'
        'ID=ubuntu\n'
        'VERSION_ID="24.04"\n'
    )

    messages.append(msg)
    messages.append({
        "role": "tool",
        "tool_call_id": call_id,
        "name": fn_name,
        "content": simulated_output
    })

    print("Step 2: Sending simulated Tool Result back to Proxy...")
    payload_turn2 = {
        "model": "gemini-3.8-flash-high",
        "messages": messages,
        "tools": tools,
        "stream": False
    }

    resp2 = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload_turn2, timeout=25)
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"

    data2 = resp2.json()
    final_content = data2["choices"][0]["message"].get("content")
    print("✅ Step 2 Success! Final assistant message after tool result:")
    print(final_content)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        BASE_URL = sys.argv[1].rstrip("/")
    print(f"Connecting to Proxy at: {BASE_URL}")
    test_backend_status()
    test_anti_bot_and_redaction()
    test_models()
    test_quota_and_account_limits()
    test_diagnostics_and_logs()
    test_gemini_4_argon()
    test_non_streaming()
    test_streaming()
    test_hermes_tool_calling()
    print("\n🎉 ALL 9 DEBUGGING & INTEGRATION TESTS PASSED SUCCESSFULLY! 🎉")
