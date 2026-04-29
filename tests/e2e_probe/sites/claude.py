# SPDX-License-Identifier: Apache-2.0
"""Claude (claude.ai) — probe site adapter."""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/chat/([a-f0-9-]+)", re.IGNORECASE)


class ClaudeAdapter(BaseProbeSiteAdapter):
    name = "claude"
    provider = "anthropic"
    url = "https://claude.ai/new"

    input_selectors = (
        '[contenteditable="true"][data-placeholder*="Reply" i]',
        '[contenteditable="true"][data-placeholder*="message" i]',
        '[contenteditable="true"]',
    )
    send_button_selectors = (
        'button[aria-label="Send Message"]',
        'button[aria-label*="Send" i]',
    )
    stop_button_selectors = (
        'button[data-testid="stop-button"]',
        'button[aria-label*="Stop" i]',
    )
    response_container_selectors = (
        '[data-testid="assistant-turn"]',
        '[data-is-streaming]',
        ".font-claude-message",
    )
    login_wall_selectors = (
        'button[data-testid="login-button"]',
        'a[href*="/login"]',
    )

    page_load_timeout_ms = 25_000
    response_timeout_ms = 90_000

    session_url_pattern = _CONVERSATION_RE
