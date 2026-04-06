"""PCE Core – OpenAI Chat Completions normalizer.

Handles:
- POST /v1/chat/completions  (request: messages array, response: choices array)
- Streaming responses (assembled from SSE chunks) — best-effort

Request body example:
{
  "model": "gpt-4",
  "messages": [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "Hello"}
  ]
}

Response body example:
{
  "choices": [
    {"message": {"role": "assistant", "content": "Hi there!"}}
  ],
  "model": "gpt-4",
  "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
}
"""

import json
import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.openai")

_CHAT_PATHS = {
    "/v1/chat/completions",
    "/chat/completions",
}


class OpenAIChatNormalizer(BaseNormalizer):
    """Normalizes OpenAI-compatible chat completions (also works for DeepSeek, Groq, Together, etc.)."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        # Match any provider that uses OpenAI-compatible chat completions
        if path in _CHAT_PATHS:
            return True
        # Also match known OpenAI-compatible hosts
        compatible_hosts = {
            "api.openai.com",
            "api.deepseek.com",
            "api.groq.com",
            "api.together.xyz",
            "api.fireworks.ai",
            "openrouter.ai",
            "localhost",
            "127.0.0.1",
        }
        if host in compatible_hosts and "chat" in path:
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

        # --- Parse request messages ---
        req_data = _safe_json(request_body)
        if req_data is None:
            return None

        req_messages = req_data.get("messages", [])
        if not isinstance(req_messages, list):
            return None

        model = model_name or req_data.get("model")

        for msg in req_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = _extract_content(msg)
            messages.append(NormalizedMessage(
                role=role,
                content_text=content,
                model_name=model if role == "assistant" else None,
                ts=created_at,
            ))

        # --- Parse response ---
        resp_data = _safe_json(response_body)
        if resp_data and isinstance(resp_data, dict):
            resp_model = resp_data.get("model", model)
            usage = resp_data.get("usage", {})
            completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None

            choices = resp_data.get("choices", [])
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    msg = choice.get("message") or choice.get("delta", {})
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role", "assistant")
                    content = _extract_content(msg)
                    if content:
                        messages.append(NormalizedMessage(
                            role=role,
                            content_text=content,
                            model_name=resp_model,
                            token_estimate=completion_tokens,
                            ts=created_at,
                        ))

        if not messages:
            return None

        # Derive tool_family from host
        tool_family = _tool_family(host, provider)

        # Session key: use conversation_id if present, else model+timestamp bucket
        session_key = req_data.get("conversation_id") or req_data.get("session_id")

        # Title hint: first user message (truncated)
        title_hint = None
        for m in messages:
            if m.role == "user" and m.content_text:
                title_hint = m.content_text[:100]
                break

        return NormalizedResult(
            provider=provider or "openai",
            tool_family=tool_family,
            model_name=model,
            session_key=session_key,
            title_hint=title_hint,
            messages=messages,
        )


def _safe_json(text: str) -> Optional[dict]:
    """Parse JSON, returning None on failure."""
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_content(msg: dict) -> Optional[str]:
    """Extract text content from a message dict.

    Handles both simple string content and the array-of-parts format:
    [{"type": "text", "text": "..."}, {"type": "image_url", ...}]
    """
    content = msg.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts) if parts else None
    return str(content)


def _tool_family(host: str, provider: str) -> str:
    """Derive tool_family from host."""
    if host in ("localhost", "127.0.0.1"):
        return "local-model"
    if "openai" in host:
        return "api-direct"
    if "deepseek" in host:
        return "api-direct"
    if "groq" in host:
        return "api-direct"
    if "together" in host:
        return "api-direct"
    if "fireworks" in host:
        return "api-direct"
    if "openrouter" in host:
        return "openrouter"
    return f"api-{provider}"
