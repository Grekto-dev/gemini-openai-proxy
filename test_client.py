"""
Test Client for Gemini-OpenAI-Proxy.
Tests:
1. GET /v1/models
2. POST /v1/chat/completions (Non-streaming)
3. POST /v1/chat/completions (Streaming with Thinking/Reasoning)
4. POST /v1/chat/completions (Hermes Function Calling & Multi-turn Tool Return)
"""

import sys
import json
import requests

BASE_URL = "http://127.0.0.1:8000"


def test_models():
    print("--- 1. Testing GET /v1/models ---")
    resp = requests.get(f"{BASE_URL}/v1/models")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    model_ids = [m["id"] for m in data.get("data", [])]
    print(f"✅ Success! Models available ({len(model_ids)}): {model_ids[:4]}...")
    return model_ids


def test_non_streaming():
    print("\n--- 2. Testing POST /v1/chat/completions (Non-streaming) ---")
    payload = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Diga em português exatamente: 'Proxy funcionando perfeitamente!'"}
        ],
        "temperature": 0.1,
        "stream": False
    }
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=20)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    msg = data["choices"][0]["message"]
    print(f"✅ Success! Response:\n{msg.get('content')}")
    if msg.get("reasoning_content"):
        print(f"🧠 Reasoning length: {len(msg['reasoning_content'])} chars")


def test_streaming():
    print("\n--- 3. Testing POST /v1/chat/completions (Streaming SSE) ---")
    payload = {
        "model": "gemini-3.8-flash-high",
        "messages": [
            {"role": "user", "content": "Conte rapidamente de 1 a 5 separando por hífen."}
        ],
        "stream": True
    }
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, stream=True, timeout=20)
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
    print("\n--- 4. Testing Hermes Function Calling & Tool Response ---")
    # Step A: Define Hermes Tool
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

    # Step B: User asks something that requires the tool
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
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=20)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    
    data = resp.json()
    msg = data["choices"][0]["message"]
    tool_calls = msg.get("tool_calls")
    assert tool_calls, f"Expected model to emit tool_calls, got: {msg}"
    
    tc = tool_calls[0]
    call_id = tc["id"]
    fn_name = tc["function"]["name"]
    fn_args = tc["function"]["arguments"]
    print(f"✅ Step 1 Success! Model emitted Tool Call:")
    print(f"   Function: {fn_name}")
    print(f"   Arguments: {fn_args}")
    print(f"   ID: {call_id}")

    # Step C: Hermes executes the tool and appends tool response
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

    print("\nStep 2: Sending simulated Tool Result back to Proxy...")
    payload_turn2 = {
        "model": "gemini-3.8-flash-high",
        "messages": messages,
        "tools": tools,
        "stream": False
    }

    resp2 = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload_turn2, timeout=20)
    assert resp2.status_code == 200, f"Expected 200, got {resp2.status_code}: {resp2.text}"
    
    data2 = resp2.json()
    final_content = data2["choices"][0]["message"].get("content")
    print("✅ Step 2 Success! Final assistant message after tool result:")
    print(final_content)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        BASE_URL = sys.argv[1].rstrip("/")
    print(f"Connecting to Proxy at: {BASE_URL}")
    test_models()
    test_non_streaming()
    test_streaming()
    test_hermes_tool_calling()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY! 🎉")
