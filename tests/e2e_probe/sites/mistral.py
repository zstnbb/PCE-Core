# SPDX-License-Identifier: Apache-2.0
"""Mistral (chat.mistral.ai) — probe site adapter."""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class MistralAdapter(BaseProbeSiteAdapter):
    name = "mistral"
    provider = "mistral"
    url = "https://chat.mistral.ai/chat"

    input_selectors = (
        'textarea[placeholder*="message" i]',
        'textarea[placeholder*="ask" i]',
        'div[role="textbox"][contenteditable="true"]',
        "textarea",
    )
    send_button_selectors = (
        'button[type="submit"]',
        'button[aria-label*="send" i]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
    )
    response_container_selectors = (
        '[class*="assistant"] .prose',
        '[class*="message"] .prose',
        '[class*="markdown"]',
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
