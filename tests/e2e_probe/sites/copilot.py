# SPDX-License-Identifier: Apache-2.0
"""Microsoft Copilot (copilot.microsoft.com) — probe site adapter.

The WXT extractor (``entrypoints/copilot.content.ts``) emits
``provider="microsoft"``. Keep the adapter in lockstep so the matrix's
provider filter on the PCE Core sessions API actually matches.
"""
from __future__ import annotations

from .base import BaseProbeSiteAdapter


class CopilotAdapter(BaseProbeSiteAdapter):
    name = "copilot"
    provider = "microsoft"
    url = "https://copilot.microsoft.com/"

    input_selectors = (
        'textarea[data-testid="composer-input"]',
        'textarea[placeholder*="message" i]',
        'textarea[placeholder*="ask" i]',
        '[contenteditable="true"]',
        "textarea",
    )
    send_button_selectors = (
        'button[data-testid="submit-button"]',
        'button[aria-label*="Send" i]',
        'button[aria-label*="Submit" i]',
        'button[type="submit"]',
    )
    stop_button_selectors = (
        'button[data-testid="stop-button"]',
        'button[aria-label*="Stop" i]',
    )
    response_container_selectors = (
        '[data-testid*="message"]',
        '[class*="response" i]',
        ".prose",
    )
    login_wall_selectors = (
        'a[href*="login.microsoftonline"]',
        'a[href*="login.live.com"]',
    )

    response_timeout_ms = 90_000
