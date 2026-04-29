# SPDX-License-Identifier: Apache-2.0
"""HuggingChat (huggingface.co/chat) — probe site adapter."""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class HuggingFaceAdapter(BaseProbeSiteAdapter):
    name = "huggingface"
    provider = "huggingface"
    url = "https://huggingface.co/chat/"

    input_selectors = (
        'textarea[placeholder*="message" i]',
        'textarea[placeholder*="ask" i]',
        "textarea",
    )
    send_button_selectors = (
        'button[type="submit"]',
        'button[aria-label*="send" i]',
        "form button:last-child",
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[type="button"][class*="stop" i]',
    )
    response_container_selectors = (
        '[data-message-role="assistant"]',
        '[class*="chat-message"]',
        '[class*="message"] .prose',
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
