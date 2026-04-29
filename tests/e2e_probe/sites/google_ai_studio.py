# SPDX-License-Identifier: Apache-2.0
"""Google AI Studio (aistudio.google.com) — probe site adapter."""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/prompts/([a-zA-Z0-9_\-]+)", re.IGNORECASE)


class GoogleAIStudioAdapter(BaseProbeSiteAdapter):
    name = "googleaistudio"
    provider = "google"
    url = "https://aistudio.google.com/prompts/new_chat"

    input_selectors = (
        'textarea[aria-label="Enter a prompt"]',
        'textarea[placeholder*="prompt" i]',
        "textarea",
    )
    # Ordering matters: ``_submit_via`` clicks the FIRST selector that
    # resolves and returns. AI Studio renders BOTH a Run-settings
    # toggle (icon-only ``aria-label="Toggle run settings panel"``)
    # AND the real Run button (``type="submit"`` with visible
    # text "Run Ctrl <return>"). The substring filter
    # ``button[aria-label*="Run" i]`` matches the SETTINGS toggle
    # first, so the click opens a side panel instead of submitting.
    # ``type="submit"`` is the unambiguous selector for the actual
    # action button on this page; the others are fallbacks for the
    # rare A/B variants that drop ``type``.
    send_button_selectors = (
        'button[type="submit"]',
        'button[mattooltipclass="run-button-tooltip"]',
        'button[aria-label="Run"]',  # exact match, not substring
        'button[aria-label*="Send" i]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[aria-label*="Cancel" i]',
    )
    # AI Studio's chat surface uses Angular custom elements
    # (``ms-chat-turn``); the legacy ``.chat-turn-container.*``
    # classes are stale on the latest UI revision (2026-Q2). We
    # include both so the adapter is forward- and back-compatible.
    response_container_selectors = (
        "ms-chat-turn",
        ".chat-turn-container.model",
        ".chat-turn-container.render.model",
        ".chat-session-content",
    )
    login_wall_selectors = (
        'a[href*="accounts.google.com/ServiceLogin"]',
    )

    page_load_timeout_ms = 30_000
    response_timeout_ms = 120_000

    session_url_pattern = _CONVERSATION_RE
