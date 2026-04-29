# SPDX-License-Identifier: Apache-2.0
"""Poe (poe.com) — probe site adapter."""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class PoeAdapter(BaseProbeSiteAdapter):
    name = "poe"
    provider = "poe"
    url = "https://poe.com/"

    input_selectors = (
        "textarea.GrowingTextArea_textArea__ZWQbP",
        'textarea[placeholder*="chat" i]',
        'textarea[placeholder*="\u804a\u5929" i]',
        "textarea",
    )
    send_button_selectors = (
        'button[aria-label="Send message"]',
        'button[aria-label="\u53d1\u9001\u4fe1\u606f"]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
    )
    response_container_selectors = (
        '[class*="Message_botMessage"]',
        '[class*="ChatMessage"]',
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
