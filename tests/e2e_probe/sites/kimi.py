# SPDX-License-Identifier: Apache-2.0
"""Kimi (kimi.com) — probe site adapter.

Kimi uses a div.chat-input-editor contenteditable with no standard
send button (the send icon is a non-standard SVG that doesn't expose a
testid). Use Enter key for submit.
"""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class KimiAdapter(BaseProbeSiteAdapter):
    name = "kimi"
    provider = "moonshot"
    url = "https://kimi.com/?chat_enter_method=new_chat"

    input_selectors = (
        'div.chat-input-editor[contenteditable="true"]',
        'div[role="textbox"][contenteditable="true"]',
        '[contenteditable="true"]',
    )
    # No reliable send button selector — fallback to Enter via the
    # base class's default (empty tuple -> dom.type submit=True).
    send_button_selectors = ()
    stop_button_selectors = (
        'div[class*="stop" i]',
        'button[class*="stop" i]',
    )
    response_container_selectors = (
        '[class*="assistant"]',
        '[class*="markdown"]',
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
