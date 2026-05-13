# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Cursor Chat L3g normalizer.

Handles captures from Cursor's local cursorDiskKV SQLite storage.
The persistence watcher reads session data from
``%APPDATA%/Cursor/User/globalStorage/state.vscdb`` and feeds each
session as a JSON body with::

    host = "local-cursor-chat"
    path = "/<composer_id>"
    direction = "conversation"
    body_text_or_json = <session JSON with composer metadata + bubbles>

Expected body shape::

    {
      "composer_id": "<uuid>",
      "name": "<session title>",
      "model_config": {"modelName": "...", "selectedModels": [...]},
      "conversation_headers": [...],
      "bubbles": [
        {
          "bubble_id": "<id>",
          "composer_id": "<uuid>",
          "bubble_type": 1|2,   // 1=user, 2=assistant
          "text": "...",
          "context": {...}
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

logger = logging.getLogger("pce.normalizer.cursor_chat")

_HOST = "local-cursor-chat"
_PROVIDER = "cursor"
_TOOL_FAMILY = "cursor-chat-l3g"

_BUBBLE_TYPE_USER = 1
_BUBBLE_TYPE_ASSISTANT = 2


def _resolve_model(model_config: dict) -> Optional[str]:
    if not isinstance(model_config, dict):
        return None
    name = model_config.get("modelName")
    if name and name != "default":
        return name
    selected = model_config.get("selectedModels")
    if isinstance(selected, list) and selected:
        first = selected[0]
        if isinstance(first, dict):
            mid = first.get("modelId")
            if mid and mid != "default":
                return mid
    return name


class CursorChatNormalizer(BaseNormalizer):
    """Normalise a Cursor cursorDiskKV session dump into messages."""

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

        composer_id = data.get("composer_id", "")
        if not composer_id:
            return None

        bubbles = data.get("bubbles")
        if not isinstance(bubbles, list) or not bubbles:
            return None

        resolved_model = model_name or _resolve_model(
            data.get("model_config") or {}
        )
        title = data.get("name")

        messages: list[NormalizedMessage] = []

        for bubble in bubbles:
            if not isinstance(bubble, dict):
                continue
            text = bubble.get("text", "")
            if not text:
                continue

            btype = bubble.get("bubble_type")
            if btype == _BUBBLE_TYPE_USER:
                role = "user"
            elif btype == _BUBBLE_TYPE_ASSISTANT:
                role = "assistant"
            else:
                role = "user" if not messages or messages[-1].role == "assistant" else "assistant"

            messages.append(NormalizedMessage(
                role=role,
                content_text=text,
                model_name=resolved_model,
                ts=created_at,
            ))

        if not messages:
            return None

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=composer_id,
            title_hint=title,
            messages=messages,
            confidence=0.95,
        )
