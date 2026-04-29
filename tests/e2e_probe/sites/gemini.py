# SPDX-License-Identifier: Apache-2.0
"""Gemini (gemini.google.com) — probe site adapter.

Gemini uses Quill (``.ql-editor``) inside an Angular ``rich-textarea``.
The probe's ``dom.type`` handles contenteditable correctly. Send is
either via ``button[aria-label*="Send"]`` or the matTooltip fallback
when the page localizes to Chinese.
"""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/(?:app|chat)/([a-f0-9]+)", re.IGNORECASE)


class GeminiAdapter(BaseProbeSiteAdapter):
    name = "gemini"
    provider = "google"
    url = "https://gemini.google.com/app"

    input_selectors = (
        ".ql-editor",
        'rich-textarea [contenteditable="true"]',
        '[contenteditable="true"]',
    )
    # Ordering matters: ``_submit_via`` clicks the FIRST selector that
    # resolves and stops trying once a click succeeds. Gemini renders
    # several buttons whose ``aria-label`` contains "Send" \u2014 not
    # all of them are the chat send button (e.g. "Send feedback" in
    # the help menu). The ``send-button`` class is unique to the chat
    # composer's submit control across both English and Chinese
    # locales, so we lead with it. Substring fallbacks remain after
    # the specific matches in case the class name changes in a
    # future Gemini revision.
    send_button_selectors = (
        'button.send-button',
        'button[data-test-id*="send" i]',
        'button[aria-label="Send message"]',
        'button[mattooltip="Send message"]',
        'button[aria-label*="\u53d1\u9001"]',  # 鍙戦�?
        'button[mattooltip*="\u53d1\u9001"]',
        'button[aria-label*="Send" i]',
        'button[mattooltip*="Send" i]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[mattooltip*="Stop" i]',
        'mat-icon[fonticon="stop"]',
    )
    response_container_selectors = (
        ".response-container",
        ".model-response-text",
        "model-response",
        "message-content",
    )
    login_wall_selectors = (
        'a[href*="accounts.google.com/ServiceLogin"]',
        'a[data-action="sign in"]',
    )

    page_load_timeout_ms = 30_000
    response_timeout_ms = 120_000  # Gemini deep research can be slow
    # Gemini's first-token latency on a fresh ``/app`` tab is normally
    # ~2 s but flakes up to ~12 s under load (likely server-side warm-
    # up after history sync). The default 8 s submit-verify budget hits
    # exactly that flake window: the click did submit (URL flips to
    # ``/app/<id>``) but neither the stop button nor a response
    # container has rendered yet, so ``_verify_submit_fired`` retries
    # the alt strategy on a now-empty ``.ql-editor``, wastes another
    # 8 s, and raises. 15 s gives enough headroom for the slow case
    # without making real submit failures noticeably worse to debug.
    submit_verify_timeout_ms = 15_000

    session_url_pattern = _CONVERSATION_RE
