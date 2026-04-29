# SPDX-License-Identifier: Apache-2.0
"""ChatGPT (chatgpt.com) — probe site adapter.

Selectors verified against the legacy Selenium adapter at
``tests/e2e/sites/chatgpt.py``. The probe's ``dom.type`` already
handles ChatGPT's ProseMirror contenteditable input transparently, so
the adapter is just selector configuration.
"""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/c/([a-z0-9-]+)", re.IGNORECASE)


class ChatGPTAdapter(BaseProbeSiteAdapter):
    name = "chatgpt"
    provider = "openai"
    url = "https://chatgpt.com/"

    input_selectors = (
        "#prompt-textarea",
        '[contenteditable="true"][id="prompt-textarea"]',
        '[contenteditable="true"][data-virtualkeyboard="true"]',
        '[contenteditable="true"][data-testid*="prompt"]',
    )
    send_button_selectors = (
        'button[data-testid="send-button"]',
        'button[aria-label*="Send" i]',
    )
    stop_button_selectors = (
        'button[data-testid="stop-button"]',
        'button[aria-label*="Stop" i]',
        'button[aria-label*="\u505c\u6b62"]',
    )
    response_container_selectors = (
        '[data-message-author-role="assistant"]',
        '[data-testid^="conversation-turn"][data-turn="assistant"]',
    )
    login_wall_selectors = (
        'button[data-testid="login-button"]',
        '[data-testid="auth-login-button"]',
        'a[href*="/auth/login"]',
    )

    page_load_timeout_ms = 25_000
    response_timeout_ms = 90_000

    session_url_pattern = _CONVERSATION_RE
