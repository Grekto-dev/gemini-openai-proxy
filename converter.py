"""
Converter module between OpenAI Chat Completions API format and Google Cloud Code API format.
Backed by `antigravity-claude-proxy` (`acp_backend.py`) for dynamic call masking,
session ID derivation, `[ignore]` system instruction wrapping, and anti-bot scrubbing.

Handles:
- Antigravity call masking (`ANTIGRAVITY_SYSTEM_INSTRUCTION` + `[ignore]` wrapper, `sessionId`, `requestType`)
- System prompt & tool description scrubbing (neutralizing "Hermes" / "Nous Research" to prevent Cloud Code 429 blocks)
- Tool definitions (OpenAI tools -> Google functionDeclarations)
- Tool calls (Google functionCall -> OpenAI tool_calls)
- Tool responses (OpenAI role: 'tool' -> Google functionResponse)
- Streaming chunks and Thinking/Reasoning content (thoughts -> reasoning_content)
- Thought signatures (skip_thought_signature_validator sentinel)
"""

import json
import uuid
import re
import acp_backend

# Fallback identity scrubbing rules (dynamically extended by acp_backend from antigravity-claude-proxy)
FALLBACK_IDENTITY_SCRUB_RULES = [
    ("Nous Research", "the assistant team"),
    ("Hermes Agent", "the assistant"),
    ("Hermes", "the assistant"),
    ("OpenClaw", "the assistant"),
    ("ClawdBot", "the assistant"),
    ("OpenCode", "the coding environment"),
    ("Claude Code", "the coding environment"),
    ("Cursor", "the IDE"),
    ("Cline", "the coding assistant"),
    ("Roo Code", "the coding assistant"),
]

GEMINI_SKIP_SIGNATURE = "skip_thought_signature_validator"


_BILLING_HEADER_RE = re.compile(r"^x-anthropic-billing-header:[^\n]*\n?", re.IGNORECASE | re.MULTILINE)


def scrub_identity(text):
    """
    Replaces third-party agent names that trigger Cloud Code filters (case-insensitive)
    and strips `x-anthropic-billing-header` pseudo-headers (matching `antigravity-claude-proxy`).
    Always pulls the latest rules from `antigravity-claude-proxy` via `acp_backend`.
    """
    if not isinstance(text, str):
        return text
    try:
        spec = acp_backend.get_backend_spec()
        rules = spec.get("identityScrubRules") or FALLBACK_IDENTITY_SCRUB_RULES
    except Exception:
        rules = FALLBACK_IDENTITY_SCRUB_RULES

    result = _BILLING_HEADER_RE.sub("", text)
    for term, replacement in rules:
        if term:
            result = re.sub(re.escape(term), replacement, result, flags=re.IGNORECASE)
    return result


def is_thinking_model(model_name):
    """Determines if the model supports thought/reasoning outputs."""
    lower = (model_name or "").lower()
    return (
        "thinking" in lower
        or "gemini-4" in lower
        or "gemini-3" in lower
        or "gemini-2.5" in lower
        or "claude" in lower
        or "gpt-oss" in lower
    )


def sanitize_schema_node(node):
    """Recursively cleans JSON schema nodes for Cloud Code / Anthropic draft 2020-12 compatibility."""
    if not isinstance(node, dict):
        return node
    cleaned = dict(node)
    for key in [
        "$schema", "title", "$id", "additionalProperties", "default",
        "examples", "minLength", "maxLength", "pattern", "format",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "minItems", "maxItems", "uniqueItems"
    ]:
        cleaned.pop(key, None)
    if "anyOf" in cleaned or "oneOf" in cleaned:
        choices = cleaned.pop("anyOf", None) or cleaned.pop("oneOf", None)
        if isinstance(choices, list) and len(choices) > 0:
            # Prefer non-null branch in Optional[T] unions
            non_null = [
                c for c in choices
                if isinstance(c, dict) and c.get("type") != "null"
            ]
            first = non_null[0] if non_null else choices[0]
            if isinstance(first, dict):
                if "type" in first and "type" not in cleaned:
                    cleaned["type"] = first["type"]
                if "items" in first and "items" not in cleaned:
                    cleaned["items"] = first["items"]
                if "properties" in first and "properties" not in cleaned:
                    cleaned["properties"] = first["properties"]
    if "type" in cleaned and isinstance(cleaned["type"], list):
        non_null_types = [t for t in cleaned["type"] if t != "null"]
        cleaned["type"] = non_null_types[0] if non_null_types else "string"
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
        "gemini-pro": "gemini-4-argon",
        "gemini-4": "gemini-4-argon",
        "gemini-argon": "gemini-4-argon",
        "argon": "gemini-4-argon",
        "gemini-flash": "gemini-3.8-flash",
        "gemini-3.1-pro-high": "gemini-4-argon-high",
        "gemini-3.1-pro-low": "gemini-4-argon-low",
        "claude": "claude-sonnet-5-5",
        "claude-sonnet": "claude-sonnet-5-5",
        "claude-sonnet-4-6": "claude-sonnet-5-5-high",
        "claude-opus": "claude-opus-5-5",
        "claude-opus-4-6-thinking": "claude-opus-5-5-high",
    }
    base = alias_map.get(raw_model, raw_model)

    # Preserve special non-tiered variants
    if base == "gemini-3.8-flash-tiered":
        return base, effort

    tier = None
    if effort in ("none", "low", "minimal"):
        tier = "low"
    elif effort == "medium":
        tier = "medium"
    elif effort in ("high", "xhigh", "max", "ultra"):
        tier = "high"

    tier_families = [
        "gemini-4-argon",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    ]

    for family in tier_families:
        if base == family or base.startswith(family + "-"):
            if tier:
                chosen_tier = tier
            else:
                suffix = base[len(family):].lstrip("-")
                chosen_tier = suffix if suffix in ("low", "medium", "high") else "high"
            return f"{family}-{chosen_tier}", effort

    return base, effort


def openai_to_google_request(openai_req, project_id, account_email=None):
    """
    Takes the request structure that `antigravity-claude-proxy` would send
    (`buildCloudCodeRequest` via `acp_backend`), converts the incoming OpenAI
    payload into Google Cloud Code format, applies the anti-bot identity filter,
    and returns the masked Cloud Code envelope ready for Google.
    """
    spec = acp_backend.get_backend_spec()
    skip_sig = spec.get("geminiSkipSignature", GEMINI_SKIP_SIGNATURE)
    envelope_spec = spec.get("sampleEnvelope", {})

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

        if role == "system" or role == "developer":
            if content:
                if isinstance(content, list):
                    text_val = "\n".join(
                        item.get("text", "") for item in content if isinstance(item, dict) and item.get("text")
                    )
                else:
                    text_val = str(content)
                scrubbed = scrub_identity(text_val)
                if scrubbed:
                    system_parts.append({"text": scrubbed})
            continue

        if role == "user":
            user_parts = []
            if isinstance(content, str):
                user_parts.append({"text": scrub_identity(content)})
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        user_parts.append({"text": scrub_identity(item.get("text", ""))})
                    elif item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        if url.startswith("data:") and "," in url:
                            mime = url.split(";")[0].replace("data:", "")
                            b64 = url.split(",", 1)[1]
                            user_parts.append({"inlineData": {"mimeType": mime, "data": b64}})
            google_contents.append({"role": "user", "parts": user_parts or [{"text": "."}]})

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
                    "thoughtSignature": skip_sig
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
            if isinstance(tool_content, str):
                resp_obj = {"result": scrub_identity(tool_content)}
            else:
                resp_obj = tool_content
            google_contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": fn_name,
                        "response": resp_obj
                    }
                }]
            })

    if not google_contents:
        google_contents.append({"role": "user", "parts": [{"text": "."}]})

    # Derive stable per-account sessionId using antigravity-claude-proxy's session-manager logic
    session_id = acp_backend.derive_session_id(account_email)
    is_claude_model = "claude" in (model or "").lower()
    max_output_cap = int(spec.get("geminiMaxOutputTokens", 65536))

    # Prepare Google request object
    google_request = {
        "contents": google_contents,
        "sessionId": session_id,
        "generationConfig": {
            "maxOutputTokens": min(max_tokens, max_output_cap),
            "temperature": temperature
        }
    }

    if top_p is not None:
        google_request["generationConfig"]["topP"] = top_p

    # Inject masked Antigravity systemInstruction with [ignore] tags + scrubbed user system parts
    google_request["systemInstruction"] = acp_backend.get_masked_system_instruction_parts(
        user_system_parts=system_parts,
        scrub_fn=scrub_identity
    )

    # Handle thinking configuration matching antigravity-claude-proxy/src/format/request-converter.js
    if effort == "none":
        if is_claude_model:
            google_request["generationConfig"]["thinkingConfig"] = {"include_thoughts": False}
        else:
            google_request["generationConfig"]["thinkingConfig"] = {"includeThoughts": False}
    elif is_thinking_model(model):
        budget = 32000 if is_claude_model else 32768
        if effort in ("low", "minimal"):
            budget = 2048
        elif effort == "medium":
            budget = 8192
        elif effort in ("high", "xhigh", "max", "ultra"):
            budget = 32000 if is_claude_model else 32768

        current_max = google_request["generationConfig"]["maxOutputTokens"]
        if current_max <= budget:
            google_request["generationConfig"]["maxOutputTokens"] = min(
                budget + max(current_max, 8192), max_output_cap
            )

        if is_claude_model:
            google_request["generationConfig"]["thinkingConfig"] = {
                "include_thoughts": True,
                "thinking_budget": budget
            }
        else:
            google_request["generationConfig"]["thinkingConfig"] = {
                "includeThoughts": True,
                "thinkingBudget": budget
            }

    # Handle Tools / Function Calling (with anti-bot scrubbing on descriptions)
    if tools and isinstance(tools, list):
        function_declarations = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "")
            desc = scrub_identity(fn.get("description", ""))
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
            if is_claude_model:
                google_request["toolConfig"] = {
                    "functionCallingConfig": {
                        "mode": "VALIDATED"
                    }
                }

    # Build the outer envelope matching antigravity-claude-proxy's buildCloudCodeRequest
    req_prefix = envelope_spec.get("requestIdPrefix", "agent-")
    envelope = {
        "project": project_id,
        "model": model,
        "request": google_request,
        "userAgent": envelope_spec.get("userAgent", "antigravity"),
        "requestType": envelope_spec.get("requestType", "agent"),
        "requestId": f"{req_prefix}{uuid.uuid4()}"
    }

    return envelope


# Alias for backward/external compatibility
openai_to_antigravity = openai_to_google_request


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
