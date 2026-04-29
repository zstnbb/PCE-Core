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
    send_button_selectors = (
        'button[aria-label*="Run" i]',
        'button[aria-label*="Send" i]',
        'button[type="submit"]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[aria-label*="Cancel" i]',
    )
    response_container_selectors = (
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
