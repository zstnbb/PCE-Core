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

    # T20: Gemini's Activity page (``/app/activity``) is a guaranteed
    # non-chat surface listed under Surface 19 of the GEMINI doc.
    # Settings proper (``/app/settings*``) sometimes redirects to a
    # Google Account page on a different origin, which would defeat
    # the in-tab silence assertion; activity stays on
    # ``gemini.google.com`` and never streams a chat. Reference:
    # ``GEMINI-FULL-COVERAGE.md`` G18.
    settings_url = "https://gemini.google.com/app/activity"

    # T07: Gemini renders the edit pencil inside ``user-query``
    # custom-element turns via hover. Mat-tooltip variant exists for
    # accessibility-disabled users. Reference: legacy
    # ``tests/e2e/sites/gemini.py:550-572``.
    edit_button_selectors = (
        'button[aria-label*="Edit" i]',
        'button[mattooltip*="Edit" i]',
        'button[aria-label*="\u7f16\u8f91"]',
        'button[aria-label*="\u4fee\u6539"]',
    )

    # T08: Gemini's regenerate fans out across "Regenerate" /
    # "Retry" / "\u91cd\u65b0\u751f\u6210" / "\u91cd\u505a"
    # / "\u91cd\u8bd5" labels depending on UI variant. The
    # ``data-test-id`` shortcut is a reliable primary. Reference:
    # ``GEMINI-FULL-COVERAGE.md`` G08 + legacy
    # ``tests/e2e/sites/gemini.py:1115-1124``.
    regenerate_button_selectors = (
        'button[data-test-id="regenerate-button"]',
        'button[aria-label*="Regenerate" i]',
        'button[mattooltip*="Regenerate" i]',
        'button[aria-label*="Retry" i]',
        'button[aria-label*="\u91cd\u65b0\u751f\u6210"]',
        'button[aria-label*="\u91cd\u505a"]',
        'button[aria-label*="\u91cd\u8bd5"]',
    )

    # T10 / T11: Gemini's composer routes uploads through a hidden
    # ``<input type=file>``. The legacy adapter notes
    # ``supports_file_upload = True   # via clipboard paste fallback``
    # \u2014 the file-input path may not work on Quill / Angular's
    # custom upload pipeline; if the DataTransfer trick fails the
    # case will surface a "DataTransfer dispatch did not land"
    # FAIL with a clear hint that paste-based upload is needed.
    file_input_selectors = (
        'input[type="file"]',
    )
    image_input_selectors = (
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    )

    # T06: Gemini's "model" picker is labeled "Mode" in some locales.
    # Reasoning model = "2.5 Pro with Thinking" / "Pro" + Thinking
    # toggle. Reference: legacy
    # ``tests/e2e/sites/gemini.py:1349-1361``.
    model_switcher_selectors = (
        'button[data-test-id="bard-mode-menu-button"]',
        'button[aria-label*="\u6a21\u5f0f\u9009\u62e9\u5668"]',
        'button[aria-label*="\u6a21\u5f0f"]',
        'button[aria-label*="Mode" i]',
        'button[data-test-id*="model"]',
        'button[aria-label*="Model" i]',
    )
    reasoning_model_labels = (
        "2.5 Pro",
        "Pro",
        "Thinking",
        "Deep Think",
        "\u601d\u8003",
        "\u63a8\u7406",
    )

    # T14: Gemini's "Deep Research" / search-grounding mode.
    # Reference: legacy ``tests/e2e/sites/gemini.py:1373-1380``.
    web_search_button_selectors = (
        'button[aria-label*="Deep Research" i]',
        'button[aria-label*="\u6df1\u5ea6\u7814\u7a76"]',
        '[data-test-id*="deep-research"]',
    )

    # T15: Gemini Canvas. Per
    # ``GEMINI-FULL-COVERAGE.md`` G14 + legacy
    # ``tests/e2e/sites/gemini.py:1383-1391``.
    canvas_indicator_selectors = (
        '[data-test-id*="canvas"]',
        '[class*="canvas"]',
        'aside[aria-label*="Canvas" i]',
        'aside[aria-label*="\u753b\u5e03"]',
    )

    # T09 branch flip: Gemini calls these "drafts" (G09). The
    # ``Show drafts`` toggle reveals < 1/2/3 > arrows on the
    # assistant turn. Reference: legacy
    # ``tests/e2e/sites/gemini.py:1192-1242``.
    branch_prev_selectors = (
        'button[aria-label*="Previous draft" i]',
        'button[aria-label*="Previous" i]',
        'button[aria-label*="\u4e0a\u4e00\u8349\u7a3f"]',
    )
    branch_next_selectors = (
        'button[aria-label*="Next draft" i]',
        'button[aria-label*="Next" i]',
        'button[aria-label*="\u4e0b\u4e00\u8349\u7a3f"]',
    )

    # T12 image generation: Imagen via Advanced. Auto-routes from
    # natural-language prompts; no invocation prefix needed.
    image_gen_invocation = None

    # T12 / T13 / T15 advanced-tool toggle. Modern Gemini (2026-Q2)
    # has a "Tools" pulldown on the composer for Canvas / Deep
    # Research / etc. The picker reliably opens Canvas (T15 gain) but
    # the broad "Code" / "Image" labels collide with Gemini's
    # composer toolbar in ways that DISRUPT the prompt flow on T13
    # / T12 (matrix regression: T13 went from PASS via auto-router
    # to FAIL after picker wiring). Empty ``code_interpreter`` and
    # ``image_gen`` label tuples below intentionally fall through to
    # auto-routing (which works on Gemini for those two surfaces);
    # only Canvas uses the picker.
    tool_picker_selectors = (
        'button[aria-label*="Tools" i]',
        'button[mattooltip*="Tools" i]',
        'button[aria-label*="\u5de5\u5177" i]',
        'button[data-test-id*="tools"]',
    )

    # Empty by design \u2014 see comment above. Gemini auto-routes
    # code execution from the prompt text reliably enough that the
    # picker click is net-negative.
    code_interpreter_button_selectors = ()
    code_interpreter_menu_labels = ()

    canvas_button_selectors = ()
    canvas_menu_labels = (
        "Canvas",
        "\u753b\u5e03",
    )

    # Empty by design \u2014 same rationale as code_interpreter:
    # Gemini's Imagen auto-router fires from natural-language
    # prompts; the picker click matched the wrong button on T12
    # and broke the flow.
    image_gen_button_selectors = ()
    image_gen_menu_labels = ()

    # ===== T15 / T13 / T12 explicit trigger prompts =====
    #
    # Gemini auto-routes Canvas, code execution, and Imagen reasonably
    # well, but explicit phrasing is more deterministic on the matrix.
    canvas_trigger_prompt = (
        "Open Canvas and create a long-form document (target 1800 to "
        "2200 words, twelve paragraphs minimum) titled 'PCE T15 "
        "Gemini Canvas {token}'. Topic: a detailed history of the "
        "Linux kernel from 1991 to 2026, covering its origins, the "
        "GPL controversy, the 2.6 unification, the rise of containers, "
        "and the rolling-release era. You MUST put this content inside "
        "the Canvas pane (not as an inline chat reply). After opening "
        "the Canvas, also write a one-line chat-side acknowledgement "
        "that ends with the literal token {token}."
    )
    code_interp_trigger_prompt = (
        "Use the code execution tool to compute the SHA-256 hex digest "
        "of the literal string 'PCE-T13-{token}'. You MUST actually "
        "execute Python code with hashlib (do not estimate or pretend) "
        "— SHA-256 cannot be computed by hand. Show the Python "
        "source AND the runner's text output containing the "
        "64-character hex digest. After the code output, end your "
        "reply with the literal token {token}."
    )
    image_gen_trigger_prompt = (
        "Generate an image with Imagen: a small blue square centered "
        "on a white background, flat shading, no shadow. After the "
        "image, write a one-line caption ending with the literal "
        "token {token}."
    )
