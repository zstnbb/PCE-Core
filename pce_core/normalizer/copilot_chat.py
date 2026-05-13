# SPDX-License-Identifier: Apache-2.0
"""PCE Core – GitHub Copilot Chat L3g normalizer.

Handles captures from VS Code's Copilot Chat extension local storage.
The persistence watcher reads JSONL session files from
``%APPDATA%/Code/User/globalStorage/emptyWindowChatSessions/`` and
feeds each replayed session state as a single JSON body with::

    host = "local-copilot-chat"
    path = "/<session_id>"
    direction = "conversation"
    body_text_or_json = <replayed session state as JSON string>

This normalizer parses that state into session + message records.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.copilot_chat")

_HOST = "local-copilot-chat"
_PROVIDER = "github"
_TOOL_FAMILY = "copilot-chat-l3g"

_THINKING_KINDS = frozenset({"thinking", "mcpServersStarting",
                             "progressMessage", "toolInvocationSerialized"})


def _extract_assistant_text(response_blocks: list) -> str:
    parts = []
    for b in response_blocks:
        if not isinstance(b, dict):
            continue
        kind = b.get("kind")
        if kind == "markdown":
            parts.append(b.get("value", ""))
        elif kind == "codeblock":
            lang = b.get("language", "")
            code = b.get("code", "")
            parts.append(f"```{lang}\n{code}\n```")
        elif kind in _THINKING_KINDS:
            continue
        elif kind is None and "value" in b:
            parts.append(b.get("value", ""))
    return "\n".join(p for p in parts if p)


class CopilotChatNormalizer(BaseNormalizer):
    """Normalise a replayed Copilot Chat session state into messages."""

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
            state = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(state, dict):
            return None

        session_id = state.get("sessionId", "")
        if not session_id:
            return None

        requests = state.get("requests")
        if not isinstance(requests, list):
            return None

        input_state = state.get("inputState") or {}
        sm = input_state.get("selectedModel")
        session_model = sm.get("identifier") if isinstance(sm, dict) else None
        resolved_model = model_name or session_model

        title = state.get("customTitle")

        messages: list[NormalizedMessage] = []

        for req in requests:
            if not isinstance(req, dict):
                continue

            msg = req.get("message") or {}
            user_text = msg.get("text", "") if isinstance(msg, dict) else ""
            if not user_text:
                continue

            timestamp_ms = req.get("timestamp")
            ts = timestamp_ms / 1000.0 if timestamp_ms else None

            messages.append(NormalizedMessage(
                role="user",
                content_text=user_text,
                model_name=resolved_model,
                ts=ts,
            ))

            response_blocks = req.get("response") or []
            assistant_text = _extract_assistant_text(response_blocks)
            if assistant_text:
                tokens = req.get("completionTokens")
                messages.append(NormalizedMessage(
                    role="assistant",
                    content_text=assistant_text,
                    model_name=resolved_model,
                    token_estimate=tokens,
                    ts=ts,
                ))

        if not messages:
            return None

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=session_id,
            title_hint=title,
            messages=messages,
            confidence=0.95,
        )
