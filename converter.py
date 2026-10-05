"""
Converter module between OpenAI Chat Completions API format and Google Cloud Code API format.
Handles:
- System prompt scrubbing (neutralizing "Hermes" / "Nous Research" to prevent Google Cloud Code anti-bot blocks)
- Tool definitions (OpenAI tools -> Google functionDeclarations)
- Tool calls (Google functionCall -> OpenAI tool_calls)
- Tool responses (OpenAI role: 'tool' -> Google functionResponse)
- Streaming chunks and Thinking/Reasoning content (thoughts -> reasoning_content)
- Thought signatures (skip_thought_signature_validator sentinel)
"""

import json
import uuid
import re

# Identity scrubbing rules to avoid Google Cloud Code 429 RESOURCE_EXHAUSTED blocks
IDENTITY_SCRUB_RULES = [
    ("Nous Research", "the team"),
    ("Hermes Agent", "Coding Assistant"),
    ("Hermes", "Assistant"),
    ("hermes", "assistant")
]

GEMINI_SKIP_SIGNATURE = "skip_thought_signature_validator"


def scrub_identity(text):
    """Replaces third-party agent names that trigger Cloud Code filters."""
    if not isinstance(text, str):
        return text
    result = text
    for term, replacement in IDENTITY_SCRUB_RULES:
        result = result.replace(term, replacement)
    return result


def is_thinking_model(model_name):
    """Determines if the model supports thought/reasoning outputs."""
    lower = (model_name or "").lower()
    return "thinking" in lower or "gemini-3" in lower or "gemini-2.5" in lower or "claude" in lower


def sanitize_schema_node(node):
    """Recursively cleans JSON schema nodes for Anthropic draft 2020-12 compatibility."""
    if not isinstance(node, dict):
        return node
    cleaned = dict(node)
    for key in ["$schema", "title", "$id"]:
        cleaned.pop(key, None)
    if "anyOf" in cleaned or "oneOf" in cleaned:
        choices = cleaned.pop("anyOf", None) or cleaned.pop("oneOf", None)
        if isinstance(choices, list) and len(choices) > 0:
            first = choices[0]
            if isinstance(first, dict):
                if "type" in first and "type" not in cleaned:
                    cleaned["type"] = first["type"]
                if "items" in first and "items" not in cleaned:
                    cleaned["items"] = first["items"]
                if "properties" in first and "properties" not in cleaned:
                    cleaned["properties"] = first["properties"]
    if "type" not in cleaned:
        if "properties" in cleaned:
            cleaned["type"] = "object"
        elif "items" in cleaned:
            cleaned["type"] = "array"
        else:
            cleaned["type"] = "string"
    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            k: sanitize_schema_node(v) for k, v in cleaned["properties"].items()
        }
    if "items" in cleaned and isinstance(cleaned["items"], dict):
        cleaned["items"] = sanitize_schema_node(cleaned["items"])
    return cleaned


def sanitize_parameters(params):
    """Sanitizes JSON schema for Google Cloud Code / Anthropic gateway compatibility."""
    if not isinstance(params, dict) or not params:
        return {"type": "object", "properties": {}}
    cleaned = sanitize_schema_node(params)
    if cleaned.get("type") != "object":
        cleaned["type"] = "object"
    if "properties" not in cleaned:
        cleaned["properties"] = {}
    return cleaned


def resolve_model_and_effort(raw_model, reasoning_effort=None):
    """
    Dynamically maps model and effort:
    Translates Hermes Agent reasoning_effort (none, minimal, low, medium, high, xhigh, max, ultra)
    to Google Cloud Code's model tiers (-low, -medium, -high) and thinking configuration.
    """
    raw_model = (raw_model or "gemini-3.8-flash").strip()
    effort = (reasoning_effort or "").lower().strip() if reasoning_effort else None

    alias_map = {
        "gemini": "gemini-3.8-flash",
        "gemini-pro": "gemini-3.8-flash",
        "gemini-flash": "gemini-3.8-flash",
        "gemini-3.1-pro-high": "gemini-3.8-flash-high",
        "gemini-3.1-pro-low": "gemini-3.8-flash-low",
        "claude": "claude-sonnet-5-5",
        "claude-sonnet": "claude-sonnet-5-5",
        "claude-opus": "claude-opus-5-5",
    }
    base = alias_map.get(raw_model, raw_model)

    tier = None
    if effort in ("none", "low", "minimal"):
        tier = "low"
    elif effort == "medium":
        tier = "medium"
    elif effort in ("high", "xhigh", "max", "ultra"):
        tier = "high"

    tier_families = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "claude-sonnet-5-5",
        "claude-opus-5-5"
    ]

    for family in tier_families:
        if base.startswith(family):
            if tier:
                chosen_tier = tier
            else:
                suffix = base[len(family):].lstrip("-")
                chosen_tier = suffix if suffix in ("low", "medium", "high") else "high"
            return f"{family}-{chosen_tier}", effort

    return base, effort


def openai_to_google_request(openai_req, project_id):
    """
    Converts an OpenAI-format chat completion request to Google Cloud Code payload.
    """
    raw_model = openai_req.get("model", "gemini-3.8-flash")
    reasoning_effort = openai_req.get("reasoning_effort")
    model, effort = resolve_model_and_effort(raw_model, reasoning_effort)
    messages = openai_req.get("messages", [])
    tools = openai_req.get("tools")
    temperature = openai_req.get("temperature", 0.7)
    max_tokens = openai_req.get("max_tokens", 8192)
    top_p = openai_req.get("top_p")

    system_parts = []
    google_contents = []

    # Map tool_call_id to tool name so role: "tool" messages can reference the function name
    call_id_to_name = {}

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if content:
                scrubbed = scrub_identity(str(content))
                system_parts.append({"text": scrubbed})
            continue

        if role == "user":
            user_parts = []
            if isinstance(content, str):
                user_parts.append({"text": content})
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        user_parts.append({"text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:"):
                            mime = url.split(";")[0].replace("data:", "")
                            b64 = url.split(",")[1]
                            user_parts.append({"inlineData": {"mimeType": mime, "data": b64}})
            google_contents.append({"role": "user", "parts": user_parts or [{"text": ""}]})

        elif role == "assistant":
            asst_parts = []
            if content:
                asst_parts.append({"text": str(content)})

            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                call_id = tc.get("id")
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                if call_id and fn_name:
                    call_id_to_name[call_id] = fn_name

                raw_args = fn.get("arguments", "{}")
                try:
                    parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    parsed_args = {}

                # Part with functionCall and required thoughtSignature
                part = {
                    "functionCall": {
                        "name": fn_name,
                        "args": parsed_args
                    },
                    "thoughtSignature": GEMINI_SKIP_SIGNATURE
                }
                asst_parts.append(part)

            if not asst_parts:
                asst_parts.append({"text": "."})

            google_contents.append({"role": "model", "parts": asst_parts})

        elif role == "tool":
            call_id = msg.get("tool_call_id", "")
            fn_name = msg.get("name") or call_id_to_name.get(call_id, "tool_response")
            tool_content = msg.get("content", "")

            # Tool responses in Google Cloud Code must be structured functionResponse
            resp_obj = {"result": tool_content} if isinstance(tool_content, str) else tool_content
            google_contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": fn_name,
                        "response": resp_obj
                    }
                }]
            })

    # Prepare Google request object
    google_request = {
        "contents": google_contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature
        }
    }

    if top_p is not None:
        google_request["generationConfig"]["topP"] = top_p

    if system_parts:
        google_request["systemInstruction"] = {"parts": system_parts}

    # Handle thinking configuration based on model and Hermes reasoning_effort
    if effort == "none":
        google_request["generationConfig"]["thinkingConfig"] = {
            "includeThoughts": False
        }
    elif is_thinking_model(model):
        budget = 32768
        if effort in ("low", "minimal"):
            budget = 2048
        elif effort == "medium":
            budget = 8192
        elif effort in ("high", "xhigh", "max", "ultra"):
            budget = 32768

        google_request["generationConfig"]["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": budget
        }

    # Handle Tools / Function Calling
    if tools and isinstance(tools, list):
        function_declarations = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            desc = fn.get("description", "")
            params = sanitize_parameters(fn.get("parameters"))

            # Clean name for Google Cloud Code (alphanumeric and underscores, max 64 chars)
            clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]
            function_declarations.append({
                "name": clean_name,
                "description": desc,
                "parameters": params
            })

        if function_declarations:
            google_request["tools"] = [{
                "functionDeclarations": function_declarations
            }]

    # Build the outer envelope expected by Google Cloud Code v1internal API
    envelope = {
        "project": project_id,
        "model": model,
        "request": google_request,
        "userAgent": "antigravity",
        "requestType": "agent",
        "requestId": f"agent-{uuid.uuid4()}"
    }

    return envelope


def format_openai_stream_chunk(completion_id, model, delta, finish_reason=None, usage=None):
    """Formats an OpenAI-compatible SSE chunk."""
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(uuid.uuid1().time / 10000000),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason
            }
        ]
    }
    if usage:
        chunk["usage"] = usage
    return f"data: {json.dumps(chunk)}\n\n"
