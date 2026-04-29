# SPDX-License-Identifier: Apache-2.0
"""Gemini (gemini.google.com) — probe site adapter.

Gemini uses Quill (``.ql-editor``) inside an Angular ``rich-textarea``.
The probe's ``dom.type`` handles contenteditable correctly. Send is
either via ``button[aria-label*="Send"]`` or the matTooltip fallback
when the page localizes to Chinese.
"""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/(?:app|chat)/([a-f0-9]+)", re.IGNORECASE)


class GeminiAdapter(BaseProbeSiteAdapter):
    name = "gemini"
    provider = "google"
    url = "https://gemini.google.com/app"

    input_selectors = (
        ".ql-editor",
        'rich-textarea [contenteditable="true"]',
        '[contenteditable="true"]',
    )
    send_button_selectors = (
        'button[aria-label*="Send" i]',
        'button[mattooltip*="Send" i]',
        'button[aria-label*="\u53d1\u9001"]',
        'button[mattooltip*="\u53d1\u9001"]',
        'button.send-button',
        'button[data-test-id*="send"]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[mattooltip*="Stop" i]',
        'mat-icon[fonticon="stop"]',
    )
    response_container_selectors = (
        ".response-container",
        ".model-response-text",
        "model-response",
        "message-content",
    )
    login_wall_selectors = (
        'a[href*="accounts.google.com/ServiceLogin"]',
        'a[data-action="sign in"]',
    )

    page_load_timeout_ms = 30_000
    response_timeout_ms = 120_000  # Gemini deep research can be slow

    session_url_pattern = _CONVERSATION_RE
