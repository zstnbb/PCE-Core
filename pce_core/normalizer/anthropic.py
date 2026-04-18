# SPDX-License-Identifier: Apache-2.0
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

from pce_core.rich_content import build_content_json

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
        # Browser extension captures from web UI (conversation direction)
        if host == "claude.ai" or provider == "anthropic":
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
            text, attachments = _extract_rich_blocks(content)
            if text is not None:
                cj = build_content_json(attachments, plain_text=text)
                messages.append(NormalizedMessage(
                    role=role,
                    content_text=text,
                    content_json=cj,
                    model_name=model if role == "assistant" else None,
                    ts=created_at,
                ))

        # --- Parse response ---
        resp_data = _safe_json(response_body)

        # SSE fallback: if response isn't valid JSON, try assembling SSE chunks
        if resp_data is None and response_body:
            from .sse import is_sse_text, assemble_any_sse
            if is_sse_text(response_body):
                resp_data = assemble_any_sse(response_body)

        if resp_data and isinstance(resp_data, dict):
            resp_model = resp_data.get("model", model)
            resp_role = resp_data.get("role", "assistant")
            usage = resp_data.get("usage", {})
            output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None

            content_blocks = resp_data.get("content", [])
            text, attachments = _extract_rich_blocks(content_blocks)
            if text:
                cj = build_content_json(attachments, plain_text=text)
                messages.append(NormalizedMessage(
                    role=resp_role,
                    content_text=text,
                    content_json=cj,
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

        # Confidence scoring
        confidence = _compute_confidence(
            req_data=req_data,
            resp_data=resp_data,
            messages=messages,
            host=host,
            path=path,
        )

        return NormalizedResult(
            provider=provider or "anthropic",
            tool_family="api-direct",
            model_name=model,
            session_key=session_key,
            title_hint=title_hint,
            messages=messages,
            confidence=confidence,
        )


def _compute_confidence(
    req_data: Optional[dict],
    resp_data: Optional[dict],
    messages: list,
    host: str,
    path: str,
) -> float:
    """Score how confidently this normalizer parsed the data.

    Signals:
    - Request has ``messages`` array with content blocks     → +0.25
    - Response has ``content`` blocks array                  → +0.20
    - ``model`` field starts with 'claude'                   → +0.15
    - ``usage`` with input/output tokens                     → +0.10
    - Both user and assistant messages extracted              → +0.15
    - Host is anthropic/claude                               → +0.10
    - Path matches /v1/messages                              → +0.05
    """
    score = 0.0

    if isinstance(req_data, dict):
        req_msgs = req_data.get("messages")
        if isinstance(req_msgs, list) and len(req_msgs) > 0:
            score += 0.25

        model = req_data.get("model", "")
        if isinstance(model, str) and "claude" in model.lower():
            score += 0.15
        elif model:
            score += 0.05

    if isinstance(resp_data, dict):
        content = resp_data.get("content")
        if isinstance(content, list) and len(content) > 0:
            score += 0.20
        usage = resp_data.get("usage")
        if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
            score += 0.10

    roles = {m.role for m in messages} if messages else set()
    if "user" in roles and "assistant" in roles:
        score += 0.15
    elif messages:
        score += 0.05

    if "anthropic" in host or host == "claude.ai":
        score += 0.10
    if path in _MESSAGES_PATHS:
        score += 0.05

    return min(score, 1.0)


def _safe_json(text: str) -> Optional[dict]:
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_rich_blocks(content) -> tuple[Optional[str], list[dict]]:
    """Extract text and rich-content attachments from Anthropic content.

    Returns ``(text, attachments)`` where *attachments* captures non-text
    content: tool_use, tool_result, images, thinking blocks, etc.
    """
    if content is None:
        return None, []
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []

    text_parts: list[str] = []
    attachments: list[dict] = []

    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue

        btype = block.get("type", "")

        if btype == "text":
            text_parts.append(block.get("text", ""))
            # Citations in annotations
            for cite in block.get("citations", []):
                if isinstance(cite, dict):
                    attachments.append({
                        "type": "citation",
                        "url": cite.get("url", ""),
                        "title": cite.get("title", ""),
                        "text": cite.get("cited_text", cite.get("text", "")),
                    })

        elif btype == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            attachments.append({
                "type": "tool_call",
                "id": block.get("id", ""),
                "name": name,
                "arguments": _truncate(json.dumps(inp, ensure_ascii=False) if inp else "", 4000),
            })
            text_parts.append(f"[Tool call: {name}]")

        elif btype == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                inner_text, inner_att = _extract_rich_blocks(result_content)
                result_str = inner_text or ""
                attachments.extend(inner_att)
            elif isinstance(result_content, str):
                result_str = result_content
            else:
                result_str = json.dumps(result_content, ensure_ascii=False) if result_content else ""
            attachments.append({
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id", ""),
                "content": _truncate(result_str, 8000),
                "is_error": block.get("is_error", False),
            })
            text_parts.append(f"[Tool result]")

        elif btype == "image":
            source = block.get("source", {})
            if isinstance(source, dict):
                stype = source.get("type", "")
                if stype == "url":
                    attachments.append({
                        "type": "image_url",
                        "url": source.get("url", ""),
                        "media_type": source.get("media_type", ""),
                    })
                else:
                    # base64 — store media type only, not the data
                    attachments.append({
                        "type": "image_url",
                        "media_type": source.get("media_type", ""),
                        "source_type": stype,
                    })
            text_parts.append("[Image]")

        elif btype == "thinking":
            thinking_text = block.get("thinking", "")
            if thinking_text:
                text_parts.append(f"<thinking>\n{thinking_text}\n</thinking>")

        elif btype == "document":
            # PDF / document block
            source = block.get("source", {})
            attachments.append({
                "type": "file",
                "source_type": source.get("type", "") if isinstance(source, dict) else "",
                "media_type": source.get("media_type", "") if isinstance(source, dict) else "",
                "title": block.get("title", ""),
            })
            text_parts.append(f"[Document: {block.get('title', '?')}]")

        else:
            # Unknown block type — preserve metadata
            attachments.append({"type": btype, "raw": _truncate(json.dumps(block, ensure_ascii=False), 2000)})
            text_parts.append(f"[{btype}]")

    text = "\n".join(text_parts) if text_parts else None
    return text, attachments


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + "...[truncated]"
