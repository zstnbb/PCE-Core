# SPDX-License-Identifier: Apache-2.0
"""PCE Core – SSE (Server-Sent Events) parsing utilities.

Streaming AI responses (ChatGPT, DeepSeek, Claude, etc.) are returned as SSE:

    data: {"choices":[{"delta":{"role":"assistant"}}]}
    data: {"choices":[{"delta":{"content":"Hello"}}]}
    data: {"choices":[{"delta":{"content":" world"}}]}
    data: [DONE]

This module detects and assembles SSE text into a single JSON response that
the normalizers can parse like a non-streaming response.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("pce.normalizer.sse")


def is_sse_text(text: str) -> bool:
    """Return True if *text* looks like Server-Sent Events data."""
    if not text or len(text) < 6:
        return False
    stripped = text.lstrip()
    # SSE streams start with "data:" or have it early on
    return stripped[:5] == "data:" or "\ndata:" in text[:500]


def assemble_sse_response(sse_text: str) -> Optional[dict]:
    """Parse SSE chunks and assemble into a single OpenAI-compatible response.

    Input:  raw SSE text (multiple ``data: {...}`` lines)
    Output: ``{"choices": [{"message": {"role": "assistant", "content": "..."}}],
              "model": "...", "usage": {...}}``

    Returns *None* if the SSE cannot be meaningfully assembled.
    """
    if not sse_text:
        return None

    model: Optional[str] = None
    role = "assistant"
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: Optional[dict] = None
    chunk_count = 0

    for line in sse_text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue

        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            continue

        try:
            chunk = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(chunk, dict):
            continue

        chunk_count += 1

        # Extract model (take first non-empty)
        if not model and chunk.get("model"):
            model = chunk["model"]

        # Extract usage (take last, as final chunk often has cumulative usage)
        if chunk.get("usage") and isinstance(chunk["usage"], dict):
            usage = chunk["usage"]

        # Process choices / delta
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            continue

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue

            if delta.get("role"):
                role = delta["role"]

            # Standard content
            if delta.get("content"):
                content_parts.append(delta["content"])

            # Reasoning / thinking content (DeepSeek, OpenAI o1, etc.)
            reasoning = delta.get("reasoning_content") or delta.get("thinking")
            if reasoning:
                reasoning_parts.append(reasoning)

    if not content_parts and not reasoning_parts:
        # Some APIs put the full response in a single non-delta chunk
        # Try to extract from non-streaming format within SSE
        for line in sse_text.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                chunk = json.loads(data_str)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(chunk, dict):
                continue
            # Check for non-delta message format
            for choice in chunk.get("choices", []):
                msg = choice.get("message", {})
                if isinstance(msg, dict) and msg.get("content"):
                    content_parts.append(msg["content"])
                    if msg.get("role"):
                        role = msg["role"]
                    break
            if content_parts:
                break

    if not content_parts and not reasoning_parts:
        return None

    content = "".join(content_parts)

    result: dict = {
        "choices": [{"message": {"role": role, "content": content}}],
    }

    # Include reasoning as a separate field if present
    if reasoning_parts:
        reasoning_text = "".join(reasoning_parts)
        result["choices"][0]["message"]["reasoning_content"] = reasoning_text

    if model:
        result["model"] = model
    if usage:
        result["usage"] = usage

    logger.debug(
        "Assembled SSE: %d chunks → %d chars content, model=%s",
        chunk_count, len(content), model,
    )
    return result


def assemble_anthropic_sse(sse_text: str) -> Optional[dict]:
    """Parse Anthropic-style SSE and assemble into a single response dict.

    Anthropic streaming format with the four delta types we care about:
        event: message_start
        data: {"type":"message_start","message":{"model":"claude-...","role":"assistant",...}}

        event: content_block_start
        data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

        event: content_block_start
        data: {"type":"content_block_start","index":1,"content_block":{"type":"thinking","thinking":""}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":1,"delta":{"type":"thinking_delta","thinking":"Let me ..."}}

        event: content_block_start
        data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu_x","name":"create_file","input":{}}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"file_path\":..."}}

        event: content_block_stop
        data: {"type":"content_block_stop","index":2}

        event: message_delta
        data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{...}}

    Returns assembled dict matching Anthropic non-streaming response:
        {"content": [<block>, ...], "model": "...", "role": "assistant", "usage": {...}}

    Handles ``text_delta``, ``thinking_delta``, ``signature_delta`` and
    ``input_json_delta`` so artifact bodies streamed via Claude's
    ``create_file`` / ``str_replace_editor`` tool_use end up reachable
    through the structured ``content`` array (resolves ``fu_recon_join``
    items 1 + 4).
    """
    if not sse_text:
        return None

    model: Optional[str] = None
    role = "assistant"
    usage: Optional[dict] = None
    blocks: dict[int, dict] = {}
    block_order: list[int] = []

    def _ensure_block(idx: int, default_type: str = "text") -> dict:
        if idx not in blocks:
            blocks[idx] = {"type": default_type}
            block_order.append(idx)
        return blocks[idx]

    for line in sse_text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue

        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            continue

        try:
            chunk = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(chunk, dict):
            continue

        chunk_type = chunk.get("type", "")

        # message_start: extract model and role
        if chunk_type == "message_start":
            msg = chunk.get("message", {})
            if isinstance(msg, dict):
                if msg.get("model"):
                    model = msg["model"]
                if msg.get("role"):
                    role = msg["role"]

        elif chunk_type == "content_block_start":
            idx = chunk.get("index", 0)
            block_seed = chunk.get("content_block", {})
            if not isinstance(block_seed, dict):
                continue
            block = _ensure_block(idx, block_seed.get("type") or "text")
            for k, v in block_seed.items():
                # Preserve incoming metadata (id, name, type, etc.)
                # but don't clobber any deltas already accumulated.
                if k not in block or block[k] in (None, ""):
                    block[k] = v
            btype = block.get("type", "")
            if btype == "text":
                block.setdefault("text", "")
            elif btype == "thinking":
                block.setdefault("thinking", "")
            elif btype == "tool_use":
                block.setdefault("__partial_json", "")

        elif chunk_type == "content_block_delta":
            idx = chunk.get("index", 0)
            delta = chunk.get("delta", {})
            if not isinstance(delta, dict):
                continue
            block = _ensure_block(idx)
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                block["type"] = block.get("type") or "text"
                block["text"] = (block.get("text") or "") + (delta.get("text", "") or "")
            elif dtype == "thinking_delta":
                block["type"] = "thinking"
                block["thinking"] = (block.get("thinking") or "") + (delta.get("thinking", "") or "")
            elif dtype == "signature_delta":
                # Anthropic ships a signature alongside thinking for verification.
                block["signature"] = (block.get("signature") or "") + (delta.get("signature", "") or "")
            elif dtype == "input_json_delta":
                block["type"] = "tool_use"
                block["__partial_json"] = (block.get("__partial_json") or "") + (delta.get("partial_json", "") or "")

        elif chunk_type == "content_block_stop":
            idx = chunk.get("index", 0)
            block = blocks.get(idx)
            if isinstance(block, dict) and "__partial_json" in block:
                pj = block.pop("__partial_json", "") or ""
                if pj:
                    try:
                        parsed = json.loads(pj)
                        if isinstance(parsed, dict):
                            block["input"] = parsed
                        else:
                            block["input"] = {"_value": parsed}
                    except (json.JSONDecodeError, TypeError):
                        # Stream may end before the JSON is well-formed
                        # (rare, e.g. truncated capture). Preserve raw.
                        block["input"] = {"_partial_json": pj}
                else:
                    block.setdefault("input", {})

        elif chunk_type == "message_delta":
            if chunk.get("usage") and isinstance(chunk["usage"], dict):
                usage = chunk["usage"]

    # Finalise any tool_use blocks that never received an explicit stop
    # (truncated SSE / partial capture).
    for block in blocks.values():
        if isinstance(block, dict) and "__partial_json" in block:
            pj = block.pop("__partial_json", "") or ""
            if pj:
                try:
                    parsed = json.loads(pj)
                    block["input"] = parsed if isinstance(parsed, dict) else {"_value": parsed}
                except (json.JSONDecodeError, TypeError):
                    block["input"] = {"_partial_json": pj}
            else:
                block.setdefault("input", {})

    content_blocks: list[dict] = []
    for idx in block_order:
        block = blocks.get(idx)
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        # Drop empty placeholder blocks
        if btype == "text" and not (block.get("text") or "").strip():
            continue
        if btype == "thinking" and not (block.get("thinking") or "").strip():
            continue
        content_blocks.append(block)

    if not content_blocks:
        return None

    result: dict = {
        "content": content_blocks,
        "role": role,
    }
    if model:
        result["model"] = model
    if usage:
        result["usage"] = usage

    text_chars = sum(len(b.get("text", "")) for b in content_blocks if b.get("type") == "text")
    logger.debug(
        "Assembled Anthropic SSE: %d blocks (%d text chars), model=%s",
        len(content_blocks), text_chars, model,
    )
    return result


def assemble_any_sse(sse_text: str) -> Optional[dict]:
    """Try both OpenAI and Anthropic SSE formats, return whichever works."""
    if not sse_text:
        return None

    # Detect format: Anthropic uses "type": "content_block_delta"
    if "content_block_delta" in sse_text or "message_start" in sse_text:
        result = assemble_anthropic_sse(sse_text)
        if result:
            return result

    # Default: try OpenAI format
    return assemble_sse_response(sse_text)
