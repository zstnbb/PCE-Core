# SPDX-License-Identifier: Apache-2.0
"""DeepSeek (chat.deepseek.com) — probe site adapter."""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class DeepSeekAdapter(BaseProbeSiteAdapter):
    name = "deepseek"
    provider = "deepseek"
    url = "https://chat.deepseek.com/"

    input_selectors = (
        "textarea#chat-input",
        'textarea[placeholder*="message" i]',
        "textarea",
    )
    send_button_selectors = (
        'div[role="button"][class*="send" i]',
        'button[class*="send" i]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'div[role="button"][class*="stop" i]',
    )
    response_container_selectors = (
        ".ds-markdown",
        '[class*="assistant-message"]',
        '[class*="markdown-body"]',
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
