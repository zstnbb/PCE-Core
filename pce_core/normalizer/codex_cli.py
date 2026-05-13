# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Codex CLI L3g normalizer.

Handles captures from OpenAI Codex CLI's local session JSONL storage.
The IDE scanner reads sessions from ``~/.codex/sessions/`` and feeds
each session as a JSON body with::

    host = "local-codex-cli"
    path = "/<session_id>"
    direction = "conversation"
    body_text_or_json = <session JSON with meta + turns>

Expected body shape::

    {
      "session_id": "<uuid>",
      "model": "gpt-5-codex",
      "cwd": "/path/to/project",
      "cli_version": "0.51.0",
      "originator": "codex_cli_rs" | "codex_vscode",
      "git_repo": "https://...",
      "turns": [
        {"role": "user"|"assistant", "content_text": "...", "timestamp": "..."},
        ...
      ]
    }
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.codex_cli")

_HOST = "local-codex-cli"
_PROVIDER = "openai"
_TOOL_FAMILY = "codex-cli-l3g"


class CodexCliNormalizer(BaseNormalizer):
    """Normalise a Codex CLI session dump into messages."""

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

        turns = data.get("turns")
        if not isinstance(turns, list) or not turns:
            return None

        resolved_model = model_name or data.get("model")

        messages: list[NormalizedMessage] = []
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            text = turn.get("content_text", "")
            if not text or role not in ("user", "assistant"):
                continue
            messages.append(NormalizedMessage(
                role=role,
                content_text=text,
                model_name=resolved_model,
                ts=created_at,
            ))

        if not messages:
            return None

        layer_meta = {}
        if data.get("cwd"):
            layer_meta["cwd"] = data["cwd"]
        if data.get("git_repo"):
            layer_meta["git_repo"] = data["git_repo"]
        if data.get("originator"):
            layer_meta["originator"] = data["originator"]

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=session_id,
            title_hint=None,
            messages=messages,
            confidence=0.95,
            layer_meta=layer_meta or None,
        )
