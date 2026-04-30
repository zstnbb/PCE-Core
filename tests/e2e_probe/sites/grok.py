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

    # T20: ``GROK-COVERAGE-DIFF.md`` shared T-cases section says
    # Grok's settings live under ``/settings`` but the 2026-Q2 UI
    # rev redirects ``/settings`` -> ``/`` (the home / chat-launch
    # surface), so T20's nav-landed assertion fails. ``/imagine``
    # is the Aurora image-gen page and IS a stable non-chat
    # surface (different DOM shell, no chat turns). We use it as
    # the negative-capture target. Reference: GROK-COVERAGE-DIFF
    # GK5 (Aurora / Imagine surface).
    settings_url = "https://grok.com/imagine"

    # T07: Grok's edit affordance is unreliable per
    # ``GROK-COVERAGE-DIFF.md`` Section 2: "Edit user message (if UI
    # exposes it \u2014 Grok frequently doesn't)". Empty selectors
    # \u2014 T07 auto-SKIPs for Grok. If a future Grok UI rev exposes
    # a stable edit button, populate this tuple.
    edit_button_selectors = ()

    # T08: Grok exposes a generic regenerate via the assistant turn's
    # action strip. Selectors are speculative and may need tuning
    # against the live UI; the matrix triage report will surface
    # selector-not-found if so.
    regenerate_button_selectors = (
        'button[aria-label*="Regenerate" i]',
        'button[aria-label*="Retry" i]',
        'button[aria-label*="Try again" i]',
    )

    # T10 / T11: Grok's composer has a paperclip; the underlying
    # ``<input type=file>`` is hidden but reachable. Per
    # ``GROK-COVERAGE-DIFF.md`` shared T-cases section, file/image
    # upload is supported on Pro accounts. Grok's TipTap editor may
    # route the upload through a paste handler instead of the file
    # input change event \u2014 if the DataTransfer trick fails the
    # case fails fast with a clear hint.
    file_input_selectors = (
        'input[type="file"]',
    )

    # T06 / T13 / T14 / T15: Grok is free-tier on the test account so
    # advanced features (Big Brain reasoning, code interpreter, canvas)
    # SKIP via empty selectors. T14 DeepSearch is exposed even in free
    # tier so we wire its toggle for an opportunistic try.
    model_switcher_selectors = ()
    reasoning_model_labels = ()
    code_interpreter_button_selectors = ()
    canvas_indicator_selectors = ()
    web_search_button_selectors = (
        'button[aria-label*="DeepSearch" i]',
        'button[aria-label*="Deep Search" i]',
        'button[aria-label*="\u6df1\u5ea6\u641c\u7d22"]',
    )

    # T09 branch flip: Grok edit is unreliable per the diff doc, so
    # branches don't reliably exist either. T12 image gen requires
    # Pro Aurora access (account-gated). Both SKIP via empty config.
    branch_prev_selectors = ()
    branch_next_selectors = ()
    image_gen_invocation = None
