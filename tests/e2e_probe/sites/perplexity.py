# SPDX-License-Identifier: Apache-2.0
"""Perplexity (www.perplexity.ai) — probe site adapter."""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_THREAD_RE = re.compile(r"/(?:search|thread)/([a-zA-Z0-9_\-]+)")


class PerplexityAdapter(BaseProbeSiteAdapter):
    name = "perplexity"
    provider = "perplexity"
    url = "https://www.perplexity.ai/"

    input_selectors = (
        'div[role="textbox"][contenteditable="true"]',
        '[contenteditable="true"][data-lexical-editor="true"]',
        "#ask-input",
        'textarea[placeholder*="ask" i]',
        "textarea",
    )
    send_button_selectors = (
        'button[aria-label*="Submit" i]',
        'button[aria-label*="Send" i]',
        'button[type="submit"]',
        'button[data-testid*="submit" i]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[aria-label*="Pause" i]',
        'button[aria-label*="Cancel" i]',
        'button[data-testid*="stop" i]',
    )
    response_container_selectors = (
        '[class*="answer" i]',
        '[class*="prose" i]',
        '[data-testid*="answer" i]',
        ".markdown-body",
    )
    login_wall_selectors = (
        'button[data-testid="login-button"]',
        'a[href*="/login"]',
    )

    response_timeout_ms = 120_000  # Pro mode pulls many sources

    session_url_pattern = _THREAD_RE
