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
import re
from typing import Optional

from pce_core.rich_content import build_content_json

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


# File extension pattern for detecting file upload chips in content text
_FILE_EXT_RE = re.compile(
    r'^(.+\.(?:pdf|docx?|txt|md|py|js|ts|csv|json|xml|html?|xlsx?|pptx?|zip|rar|'
    r'png|jpe?g|gif|svg|mp[34]|wav|webp|heic|c|cpp|h|rb|go|rs|java|kt|swift|sh|'
    r'bat|ps1|r|sql|ya?ml|toml|ini|cfg|log|ipynb))\s*$',
    re.IGNORECASE,
)


def _dedupe_attachments(attachments: list[dict]) -> list[dict]:
    """Return attachments without exact duplicates, preserving order."""
    out: list[dict] = []
    seen: set[str] = set()
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        key = json.dumps(att, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(att)
    return out


def _detect_file_upload(content: str) -> tuple[str, list[dict]]:
    """Detect file upload patterns in user message content text.

    Chat UIs render file upload chips as part of message innerText:
        "filename.pdf\nPDF\nPlease analyze this file"
    Detects the filename, extracts it as a file attachment, and returns
    cleaned content without the filename/type-label lines.
    """
    lines = content.strip().split('\n')
    if len(lines) < 2:
        return content, []

    first_line = lines[0].strip()
    m = _FILE_EXT_RE.match(first_line)
    if not m:
        return content, []

    filename = m.group(1)

    # Second line is usually a short type label (e.g. "PDF", "文件", "Image")
    # Skip it if it's short and doesn't look like real user content
    rest_start = 1
    if len(lines) > 1:
        second = lines[1].strip()
        if len(second) <= 30 and not any(c in second for c in '。，！？.!?,'):
            rest_start = 2

    cleaned = '\n'.join(lines[rest_start:]).strip()
    if not cleaned:
        cleaned = f"[File: {filename}]"

    attachment = {"type": "file", "name": filename}
    return cleaned, [attachment]


def _clean_content(content: str) -> tuple[str, list[dict]]:
    """Clean raw platform-internal JSON that may appear as content_text.

    Some captures (especially from network intercept + WS) produce content
    like: {'content_type': 'text', 'parts': ['actual text here']}
    This function detects that format and extracts the real text.

    Returns (cleaned_text, extracted_attachments).
    """
    attachments = []
    stripped = content.strip()

    # Fast path: most content is not raw JSON
    if not stripped.startswith("{") and not stripped.startswith("["):
        return content, attachments

    # Try parsing as JSON (handles both standard and Python-repr formats)
    parsed = None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        # Try Python repr format: {'key': 'value'} → {"key": "value"}
        try:
            import ast
            parsed = ast.literal_eval(stripped)
        except Exception:
            pass

    if not isinstance(parsed, dict):
        return content, attachments

    # ChatGPT internal format: {'content_type': '...', 'parts': [...]}
    parts = parsed.get("parts")
    if isinstance(parts, list):
        text_parts = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                ct = part.get("content_type", "")
                if ct == "image_asset_pointer" or "asset_pointer" in part:
                    asset_pointer = part.get("asset_pointer", part.get("url", ""))
                    file_id = ""
                    if isinstance(asset_pointer, str) and asset_pointer.startswith("sediment://file_"):
                        file_id = asset_pointer.split("sediment://", 1)[-1]
                    attachments.append({
                        "type": "image_url",
                        "url": asset_pointer,
                        "file_id": file_id,
                        "size": part.get("size_bytes", 0),
                    })
                    text_parts.append("[Image]")
                elif "text" in part:
                    text_parts.append(part["text"])
                else:
                    text_parts.append(f"[{ct or 'unknown'}]")
        cleaned = "\n".join(text_parts).strip()
        if cleaned:
            return cleaned, attachments

    # Generic: if there's a 'text' or 'content' key, extract it
    for key in ("text", "content", "message", "result"):
        val = parsed.get(key)
        if isinstance(val, str) and len(val) > 2:
            return val, attachments

    # Couldn't clean — return original
    return content, attachments


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
        # Catch-all: this normalizer is registered LAST in the registry.
        # If no other normalizer matched, accept any provider — the normalize()
        # method will still validate that the body contains a messages array.
        # This ensures new platforms work without code changes.
        return True

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

            # Clean raw platform JSON (e.g. ChatGPT WS {'content_type':'text','parts':[...]})
            content, extra_atts = _clean_content(content)
            if len(content.strip()) < 2:
                continue

            # Detect file uploads from content text patterns (user messages)
            if role == "user":
                content, file_atts = _detect_file_upload(content)
                extra_atts.extend(file_atts)

            # Merge: DOM-provided attachments + any extracted from raw JSON cleaning
            attachments = _dedupe_attachments(list(msg.get("attachments") or []) + extra_atts)
            cj = build_content_json(attachments, plain_text=content)

            messages.append(NormalizedMessage(
                role=role,
                content_text=content,
                content_json=cj,
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
