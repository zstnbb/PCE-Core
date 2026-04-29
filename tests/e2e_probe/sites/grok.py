# SPDX-License-Identifier: Apache-2.0
"""Grok (grok.com) — probe site adapter.

Grok uses TipTap/ProseMirror; the probe's ``dom.type`` handles it
transparently.
"""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class GrokAdapter(BaseProbeSiteAdapter):
    name = "grok"
    provider = "xai"
    url = "https://grok.com/"

    input_selectors = (
        'div.tiptap.ProseMirror[contenteditable="true"]',
        '[contenteditable="true"]',
    )
    send_button_selectors = (
        'button[type="submit"]',
        'button[aria-label="Send"]',
        'button[aria-label="\u63d0\u4ea4"]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
    )
    response_container_selectors = (
        '[class*="message"]',
        '[class*="response"]',
        ".prose",
    )
    login_wall_selectors = (
        'a[href*="/sign-in"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 90_000
