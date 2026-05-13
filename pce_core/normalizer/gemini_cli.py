# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Gemini CLI L3g normalizer.

Handles captures from Gemini CLI's local JSONL chat sessions.
The IDE scanner reads session data from
``~/.gemini/tmp/<project>/chats/session-*.jsonl`` and feeds each
session as a JSON body with::

    host = "local-gemini-cli"
    path = "/<session_id>"
    direction = "conversation"
    body_text_or_json = <session JSON with metadata + messages>

Expected body shape::

    {
      "session_id": "<uuid>",
      "project_hash": "<sha256>",
      "start_time": "<ISO timestamp>",
      "kind": "main",
      "model": "gemini-3-flash-preview",
      "messages": [
        {
          "id": "<uuid>",
          "timestamp": "<ISO>",
          "type": "user"|"gemini",
          "content": "..." or [{text:"..."}],
          "model": "...",
          "thoughts": [...],
          "tokens": {input, output, cached, thoughts, tool, total}
        },
        ...
      ]
    }
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.gemini_cli")

_HOST = "local-gemini-cli"
_PROVIDER = "google"
_TOOL_FAMILY = "gemini-cli-l3g"


def _extract_text(content) -> str:
    """Extract text from Gemini content (string or list of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content) if content else ""


class GeminiCliNormalizer(BaseNormalizer):
    """Normalise a Gemini CLI JSONL session dump into messages."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        return host == _HOST

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
        body = request_body or response_body
        if not body:
            return None

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            return None

        session_id = data.get("session_id", "")
        if not session_id:
            return None

        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            return None

        resolved_model = model_name or data.get("model")
        title = None

        messages: list[NormalizedMessage] = []

        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type", "")
            if msg_type == "user":
                content = _extract_text(msg.get("content", ""))
                if not content:
                    continue
                if not title:
                    title = content[:60]
                messages.append(NormalizedMessage(
                    role="user",
                    content_text=content,
                    model_name=resolved_model,
                    ts=created_at,
                ))
            elif msg_type == "gemini":
                content = _extract_text(msg.get("content", ""))
                if not content:
                    continue
                msg_model = msg.get("model") or resolved_model
                if msg_model:
                    resolved_model = msg_model

                messages.append(NormalizedMessage(
                    role="assistant",
                    content_text=content,
                    model_name=msg_model,
                    ts=created_at,
                    token_estimate=msg.get("tokens", {}).get("output") if isinstance(msg.get("tokens"), dict) else None,
                ))

                # Add thinking as separate message if present
                thoughts = msg.get("thoughts", [])
                if isinstance(thoughts, list) and thoughts:
                    thinking_text = "\n".join(
                        f"[{t.get('subject', '')}] {t.get('description', '')}"
                        for t in thoughts if isinstance(t, dict)
                    )
                    if thinking_text.strip():
                        messages.append(NormalizedMessage(
                            role="assistant",
                            content_text=thinking_text,
                            model_name=msg_model,
                            ts=created_at,
                            interaction_kind="thinking",
                        ))

        if not messages:
            return None

        # Build layer_meta
        layer_meta: dict = {}
        if data.get("project_hash"):
            layer_meta["project_hash"] = data["project_hash"]
        if data.get("kind"):
            layer_meta["kind"] = data["kind"]

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=session_id,
            title_hint=title,
            messages=messages,
            confidence=0.95,
            layer_meta=layer_meta if layer_meta else None,
        )
