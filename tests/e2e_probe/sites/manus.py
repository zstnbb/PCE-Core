# SPDX-License-Identifier: Apache-2.0
"""Manus (manus.im) — probe site adapter."""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class ManusAdapter(BaseProbeSiteAdapter):
    name = "manus"
    provider = "manus"
    url = "https://manus.im/app"

    input_selectors = (
        'div.tiptap.ProseMirror[contenteditable="true"]',
        '[contenteditable="true"]',
    )
    # Manus's send affordance is non-standard; rely on Enter (no
    # send_button_selectors -> base class uses dom.type submit=True).
    send_button_selectors = ()
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
    )
    response_container_selectors = (
        '[class*="message"]',
        '[class*="task"]',
        '[class*="answer"]',
        ".prose",
    )
    login_wall_selectors = (
        'a[href*="/login"]',
        'button[class*="login" i]',
    )

    response_timeout_ms = 120_000  # Manus runs long-form tasks
