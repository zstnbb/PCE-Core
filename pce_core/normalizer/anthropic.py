"""PCE Core – Anthropic Messages API normalizer.

Handles:
- POST /v1/messages  (request: messages array, response: content blocks)

Request body example:
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "system": "You are helpful.",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}

Response body example:
{
  "content": [
    {"type": "text", "text": "Hi there!"}
  ],
  "model": "claude-sonnet-4-20250514",
  "role": "assistant",
  "usage": {"input_tokens": 10, "output_tokens": 25}
}
"""

import json
import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.anthropic")

_MESSAGES_PATHS = {
    "/v1/messages",
    "/messages",
}


class AnthropicMessagesNormalizer(BaseNormalizer):
    """Normalizes Anthropic Messages API responses."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        if path in _MESSAGES_PATHS:
            return True
        if "anthropic" in host and "messages" in path:
            return True
        return False

    def normalize(
        self,
        request_body: str,
        response_body: str,
        *,
        provider: str,
        host: str,
        path: str,
        model_name: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Optional[NormalizedResult]:
        messages: list[NormalizedMessage] = []

        # --- Parse request ---
        req_data = _safe_json(request_body)
        if req_data is None:
            return None

        model = model_name or req_data.get("model")

        # System message (Anthropic puts it at top level, not in messages array)
        system = req_data.get("system")
        if system:
            if isinstance(system, str):
                messages.append(NormalizedMessage(
                    role="system", content_text=system, ts=created_at,
                ))
            elif isinstance(system, list):
                # Array of content blocks
                text = _blocks_to_text(system)
                if text:
                    messages.append(NormalizedMessage(
                        role="system", content_text=text, ts=created_at,
                    ))

        # User/assistant messages
        req_messages = req_data.get("messages", [])
        if not isinstance(req_messages, list):
            return None

        for msg in req_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = msg.get("content")
            text = _extract_text(content)
            if text is not None:
                messages.append(NormalizedMessage(
                    role=role,
                    content_text=text,
                    model_name=model if role == "assistant" else None,
                    ts=created_at,
                ))

        # --- Parse response ---
        resp_data = _safe_json(response_body)
        if resp_data and isinstance(resp_data, dict):
            resp_model = resp_data.get("model", model)
            resp_role = resp_data.get("role", "assistant")
            usage = resp_data.get("usage", {})
            output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None

            content_blocks = resp_data.get("content", [])
            text = _blocks_to_text(content_blocks)
            if text:
                messages.append(NormalizedMessage(
                    role=resp_role,
                    content_text=text,
                    model_name=resp_model,
                    token_estimate=output_tokens,
                    ts=created_at,
                ))

        if not messages:
            return None

        # Session key
        session_key = req_data.get("conversation_id") or req_data.get("session_id")

        # Title hint
        title_hint = None
        for m in messages:
            if m.role == "user" and m.content_text:
                title_hint = m.content_text[:100]
                break

        return NormalizedResult(
            provider=provider or "anthropic",
            tool_family="api-direct",
            model_name=model,
            session_key=session_key,
            title_hint=title_hint,
            messages=messages,
        )


def _safe_json(text: str) -> Optional[dict]:
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_text(content) -> Optional[str]:
    """Extract text from Anthropic message content (string or content blocks)."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _blocks_to_text(content)
    return str(content)


def _blocks_to_text(blocks) -> Optional[str]:
    """Convert an array of content blocks to plain text."""
    if not isinstance(blocks, list):
        return None
    parts = []
    for block in blocks:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                parts.append(f"[tool_use: {block.get('name', '?')}]")
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts) if parts else None
