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

    Anthropic streaming format:
        event: message_start
        data: {"type":"message_start","message":{"model":"claude-...","role":"assistant",...}}

        event: content_block_delta
        data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

        event: message_delta
        data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{...}}

    Returns assembled dict like Anthropic non-streaming response:
        {"content": [{"type": "text", "text": "..."}], "model": "...", "role": "assistant", "usage": {...}}
    """
    if not sse_text:
        return None

    model: Optional[str] = None
    role = "assistant"
    content_parts: list[str] = []
    usage: Optional[dict] = None

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

        # content_block_delta: accumulate text
        elif chunk_type == "content_block_delta":
            delta = chunk.get("delta", {})
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    content_parts.append(text)

        # message_delta: extract final usage
        elif chunk_type == "message_delta":
            if chunk.get("usage") and isinstance(chunk["usage"], dict):
                usage = chunk["usage"]

    if not content_parts:
        return None

    content = "".join(content_parts)

    result: dict = {
        "content": [{"type": "text", "text": content}],
        "role": role,
    }
    if model:
        result["model"] = model
    if usage:
        result["usage"] = usage

    logger.debug(
        "Assembled Anthropic SSE: %d chars content, model=%s",
        len(content), model,
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
