# SPDX-License-Identifier: Apache-2.0
"""Zhipu / Z.AI (chat.z.ai) — probe site adapter."""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class ZhiPuAdapter(BaseProbeSiteAdapter):
    name = "zhipu"
    provider = "zhipu"
    url = "https://chat.z.ai/"

    input_selectors = (
        '[class*="chat-input"] textarea',
        '[class*="input-box"] textarea',
        '[contenteditable="true"]',
        "textarea",
    )
    send_button_selectors = (
        'button[class*="send"]',
        '[class*="send-btn"]',
        'button[type="submit"]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[class*="stop" i]',
    )
    response_container_selectors = (
        '[class*="message"][class*="assistant"]',
        '[class*="bot-message"]',
        '[class*="answer"]',
        '[class*="markdown"]',
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
