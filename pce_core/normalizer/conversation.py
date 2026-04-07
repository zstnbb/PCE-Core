"""PCE Core – Browser Extension Conversation normalizer.

Handles conversation-direction captures from browser extension DOM extractors.
These captures contain a JSON body with a `messages` array of {role, content}
pairs, produced by site-specific extractors (deepseek.js, generic.js, etc.)
and the universal extractor.

Body format:
{
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there!"}
  ],
  "conversation_id": "...",   // optional
  "url": "https://...",       // optional
  "title": "Chat Title"       // optional
}

This normalizer is registered AFTER OpenAI and Anthropic so that API-level
captures still route to the more specific normalizer first.
"""

import json
import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.conversation")

# Providers whose browser extension conversation captures should be handled here.
# OpenAI and Anthropic are excluded because they have dedicated normalizers.
_CONVERSATION_PROVIDERS = {
    "deepseek", "google", "perplexity", "xai", "poe",
    "microsoft", "huggingface", "mistral", "kimi", "universal",
    "grok", "copilot",
}

_CONVERSATION_HOSTS = {
    "chat.deepseek.com",
    "gemini.google.com",
    "www.perplexity.ai",
    "grok.com",
    "poe.com",
    "copilot.microsoft.com",
    "huggingface.co",
    "chat.mistral.ai",
    "kimi.moonshot.cn",
}


class ConversationNormalizer(BaseNormalizer):
    """Normalizes browser extension conversation captures (DOM-extracted messages).

    Acts as a catch-all for any provider that isn't handled by the OpenAI or
    Anthropic normalizer, as long as the body contains a messages array.
    """

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        if provider in _CONVERSATION_PROVIDERS:
            return True
        if host in _CONVERSATION_HOSTS:
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
        # For conversation captures, request_body == response_body (same row).
        data = _safe_json(request_body)
        if data is None:
            return None

        messages_raw = data.get("messages", [])
        if not isinstance(messages_raw, list) or len(messages_raw) == 0:
            return None

        messages: list[NormalizedMessage] = []

        for msg in messages_raw:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            # Skip very short content (likely UI artifacts)
            if len(content.strip()) < 2:
                continue

            messages.append(NormalizedMessage(
                role=role,
                content_text=content,
                model_name=model_name if role == "assistant" else None,
                ts=created_at,
            ))

        if not messages:
            return None

        # Session key: conversation_id or session_id if present
        session_key = (
            data.get("conversation_id")
            or data.get("session_id")
        )

        # Title: prefer explicit title, else first user message
        title_hint = data.get("title")
        if not title_hint:
            for m in messages:
                if m.role == "user" and m.content_text:
                    title_hint = m.content_text[:100]
                    break

        tool_family = f"{provider}-web" if provider else "web"

        return NormalizedResult(
            provider=provider or "unknown",
            tool_family=tool_family,
            model_name=model_name,
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
