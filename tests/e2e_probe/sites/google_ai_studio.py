# SPDX-License-Identifier: Apache-2.0
"""Google AI Studio (aistudio.google.com) — probe site adapter."""
from __future__ import annotations

import re

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/prompts/([a-zA-Z0-9_\-]+)", re.IGNORECASE)


class GoogleAIStudioAdapter(BaseProbeSiteAdapter):
    name = "googleaistudio"
    provider = "google"
    url = "https://aistudio.google.com/prompts/new_chat"

    input_selectors = (
        'textarea[aria-label="Enter a prompt"]',
        'textarea[placeholder*="prompt" i]',
        "textarea",
    )
    # Ordering matters: ``_submit_via`` clicks the FIRST selector that
    # resolves and returns. AI Studio renders BOTH a Run-settings
    # toggle (icon-only ``aria-label="Toggle run settings panel"``)
    # AND the real Run button (``type="submit"`` with visible
    # text "Run Ctrl <return>"). The substring filter
    # ``button[aria-label*="Run" i]`` matches the SETTINGS toggle
    # first, so the click opens a side panel instead of submitting.
    # ``type="submit"`` is the unambiguous selector for the actual
    # action button on this page; the others are fallbacks for the
    # rare A/B variants that drop ``type``.
    send_button_selectors = (
        'button[type="submit"]',
        'button[mattooltipclass="run-button-tooltip"]',
        'button[aria-label="Run"]',  # exact match, not substring
        'button[aria-label*="Send" i]',
    )
    stop_button_selectors = (
        'button[aria-label*="Stop" i]',
        'button[aria-label*="Cancel" i]',
    )
    # AI Studio's chat surface uses Angular custom elements
    # (``ms-chat-turn``); the legacy ``.chat-turn-container.*``
    # classes are stale on the latest UI revision (2026-Q2). We
    # include both so the adapter is forward- and back-compatible.
    response_container_selectors = (
        "ms-chat-turn",
        ".chat-turn-container.model",
        ".chat-turn-container.render.model",
        ".chat-session-content",
    )
    login_wall_selectors = (
        'a[href*="accounts.google.com/ServiceLogin"]',
    )

    page_load_timeout_ms = 30_000
    response_timeout_ms = 120_000

    session_url_pattern = _CONVERSATION_RE

    # T20: AI Studio's API-key / billing surfaces are unambiguously
    # non-chat (``A45 Usage / quota / billing diagnostics`` in the
    # GAS coverage doc explicitly forbids captures here). The
    # ``/api-keys`` page renders a key list / billing summary with no
    # chat shell, so even a leaky path-whitelist would have nothing
    # ``ms-chat-turn``-shaped to extract. The 2026-Q2 UI revision
    # renamed ``/apikey`` -> ``/api-keys`` (hyphen + plural); the
    # legacy URL 308-redirects but T20's strict-equal URL assertion
    # treated the redirect target as a navigation FAIL. Reference:
    # ``GOOGLE-AI-STUDIO-FULL-COVERAGE.md`` Part I.1 row 45.
    settings_url = "https://aistudio.google.com/api-keys"

    # T07: AI Studio renders the edit affordance inside the user
    # ``ms-chat-turn``. Reference: legacy
    # ``tests/e2e/sites/google_ai_studio.py:802-862`` covers the
    # full set of aria-label / title / mattooltip variants.
    edit_button_selectors = (
        'button[aria-label*="Edit" i]',
        'button[title*="Edit" i]',
        'button[mattooltip*="Edit" i]',
    )

    # T08: AI Studio calls regenerate "Rerun this turn" /
    # "Rerun" / "Retry" / "Try again" / "Regenerate". The
    # ``name="rerun-button"`` form-attribute selector is the most
    # specific. Reference: legacy
    # ``tests/e2e/sites/google_ai_studio.py:776-799``.
    regenerate_button_selectors = (
        'button[name="rerun-button"]',
        'button[aria-label*="Rerun this turn" i]',
        'button[aria-label*="Rerun" i]',
        'button[aria-label*="Retry" i]',
        'button[aria-label*="Try again" i]',
        'button[aria-label*="Regenerate" i]',
    )

    # T10 / T11: AI Studio's prompt composer surfaces a single
    # ``<input type=file>`` behind the upload button (validated
    # 2026-Q2 against ``ms-prompt-input``). The same input accepts
    # PDF, image, video, audio per the GAS coverage doc Surfaces 7-10.
    file_input_selectors = (
        'input[type="file"]',
    )

    # T06: AI Studio model selector lives in the right-side run
    # settings; clicking it surfaces all Gemini variants. Reasoning
    # candidates: 2.5 Pro / Thinking models (free-tier accessible).
    # Reference: ``GOOGLE-AI-STUDIO-FULL-COVERAGE.md`` Surface 23.
    model_switcher_selectors = (
        'ms-model-selector button',
        'ms-model-selector',
        'button[aria-label*="model" i]',
        'button[aria-label*="\u6a21\u578b"]',
    )
    reasoning_model_labels = (
        "gemini-2.5-pro",
        "2.5 Pro",
        "Thinking",
        "thinking",
        "\u601d\u8003",
    )

    # T13: AI Studio's "Code execution" tool toggle in the run-settings
    # panel. Reference: ``GOOGLE-AI-STUDIO-FULL-COVERAGE.md`` A14.
    code_interpreter_button_selectors = (
        'button[aria-label*="Code execution" i]',
        'mat-slide-toggle[aria-label*="Code" i]',
        'button[aria-label*="\u4ee3\u7801\u6267\u884c"]',
    )

    # T14: AI Studio's "Grounding with Google Search" toggle.
    # Reference: ``GOOGLE-AI-STUDIO-FULL-COVERAGE.md`` A12.
    web_search_button_selectors = (
        'button[aria-label*="Grounding" i]',
        'mat-slide-toggle[aria-label*="Search" i]',
        'button[aria-label*="\u63a5\u5730" i]',
    )

    # T15: AI Studio's chat surface does NOT have a canvas-style
    # side panel by default in free tier; build mode (``/apps/*``)
    # is out of v1.0 scope per ``GOOGLE-AI-STUDIO-FULL-COVERAGE.md``
    # Surface 29 (deferred to v1.1). Empty \u2014 T15 SKIPs.
    canvas_indicator_selectors = ()

    # T09 branch flip: AI Studio's prompt UI doesn't expose branch
    # arrows on regular chat \u2014 the page's edit/rerun cycle
    # replaces the assistant turn rather than branching. Empty
    # \u2014 T09 SKIPs.
    branch_prev_selectors = ()
    branch_next_selectors = ()

    # T12 image generation: Imagen via API quota. Free tier may
    # produce a "billing required" banner instead of an image.
    # T12 fails with "no image_generation attachment" if so, which
    # is the right signal.
    image_gen_invocation = None
