# SPDX-License-Identifier: Apache-2.0
"""ChatGPT (chatgpt.com) — probe site adapter.

Selectors verified against the legacy Selenium adapter at
``tests/e2e/sites/chatgpt.py``. The probe's ``dom.type`` already
handles ChatGPT's ProseMirror contenteditable input transparently, so
the adapter is just selector configuration.
"""
from __future__ import annotations

import re
from typing import ClassVar

from .base import BaseProbeSiteAdapter

_CONVERSATION_RE = re.compile(r"/c/([a-z0-9-]+)", re.IGNORECASE)


class ChatGPTAdapter(BaseProbeSiteAdapter):
    name = "chatgpt"
    provider = "openai"
    url = "https://chatgpt.com/"

    # Per redesign Pathology 2: ChatGPT's ProseMirror needs 5-8 s on
    # cold cache to mount; the inherited 15 s budget split across
    # 4 input selectors gives only 3.75 s per selector \u2014 too
    # tight. Bump page_load + input_appear so a fresh tab on a cold
    # session hydrates fully before we declare login state.
    page_load_timeout_ms: ClassVar[int] = 30_000
    input_appear_timeout_ms: ClassVar[int] = 30_000

    input_selectors = (
        "#prompt-textarea",
        '[contenteditable="true"][id="prompt-textarea"]',
        '[contenteditable="true"][data-virtualkeyboard="true"]',
        '[contenteditable="true"][data-testid*="prompt"]',
    )
    send_button_selectors = (
        'button[data-testid="send-button"]',
        'button[aria-label*="Send" i]',
    )
    stop_button_selectors = (
        'button[data-testid="stop-button"]',
        'button[aria-label*="Stop" i]',
        'button[aria-label*="\u505c\u6b62"]',
    )
    response_container_selectors = (
        '[data-message-author-role="assistant"]',
        '[data-testid^="conversation-turn"][data-turn="assistant"]',
    )
    login_wall_selectors = (
        'button[data-testid="login-button"]',
        '[data-testid="auth-login-button"]',
        'a[href*="/auth/login"]',
    )

    page_load_timeout_ms = 25_000
    # 120s (was 90s): the first cell after a fresh Chrome attach hits a
    # cold ChatGPT page; the SSE / WS handshake on the first prompt is
    # measurably slower than warm subsequent cells, occasionally pushing
    # past 90s and producing a misleading T01 timeout. Warm cells finish
    # in 5-15s so the extra headroom only matters during cold-start and
    # never costs real wall-time on a healthy run.
    response_timeout_ms = 120_000

    session_url_pattern = _CONVERSATION_RE

    # T20: ChatGPT settings live on a hash route (``/#settings/...``)
    # which renders a modal over the chat shell. The modal is a clean
    # non-chat surface \u2014 the underlying tab still has the entry
    # ``/`` URL but the path whitelist + ``requireSessionHint``
    # combination should keep it silent. Reference:
    # ``CHATGPT-FULL-COVERAGE.md`` Part III.1 T20.
    settings_url = "https://chatgpt.com/#settings/data-controls"

    # T07: ChatGPT renders the edit pencil inside the user turn. The
    # button is hover-revealed; ``click_last_user_edit`` dispatches
    # mouseenter on ``[data-message-author-role="user"]`` (covered in
    # base.py) before clicking. Aria-label localizes per UI language.
    # Reference: legacy ``tests/e2e/sites/chatgpt.py:400-415``.
    edit_button_selectors = (
        'button[aria-label*="Edit message" i]',
        'button[aria-label*="Edit" i]',
        'button[aria-label*="\u7f16\u8f91"]',
        'button[aria-label*="\u7de8\u8f2f"]',
    )

    # T08: ChatGPT exposes regenerate via a direct button on the last
    # assistant turn. Per ``CHATGPT-FULL-COVERAGE.md`` row T08
    # (``20260423-013603``), the button is consistently a child of the
    # assistant turn with ``data-testid*="regenerate"``. Some legacy
    # variants use aria-label only.
    # T08 fallback: when the direct regenerate button isn't visible,
    # ChatGPT buries regen behind a "..." menu on the assistant turn
    # (hover-revealed). Per the legacy adapter at
    # ``tests/e2e/sites/chatgpt.py:447-466``, the menu opener has
    # aria-label "More" / "\u66f4\u591a" / "\u66f4\u591a\u64cd\u4f5c".
    more_actions_button_selectors = (
        'button[aria-label*="More actions" i]',
        'button[aria-label*="More" i]',
        'button[aria-label*="\u66f4\u591a"]',
        'button[aria-label*="\u66f4\u591a\u64cd\u4f5c"]',
    )

    regenerate_button_selectors = (
        'button[data-testid*="regenerate"]',
        'button[data-testid*="retry"]',
        'button[aria-label="Try again"]',
        'button[aria-label*="Try again" i]',
        'button[aria-label*="Retry" i]',
        'button[aria-label*="Regenerate" i]',
        'button[aria-label*="\u91cd\u65b0\u751f\u6210"]',
        'button[aria-label*="\u91cd\u8bd5"]',
        'button[aria-label*="\u91cd\u65b0\u56de\u7b54"]',
    )

    # T18: ChatGPT's privacy mode is a query-param switch. Per
    # ``CHATGPT-FULL-COVERAGE.md`` Surface 17 / T18.
    temporary_chat_url = "https://chatgpt.com/?temporary-chat=true"

    # T10: ChatGPT renders TWO ``<input type=file>`` elements in the
    # composer area: one general-purpose and one image-specific
    # (``accept*="image"``). The general one accepts PDF / CSV /
    # docx etc.; the image one is for vision uploads. Per legacy
    # ``tests/e2e/sites/chatgpt.py:51-54``.
    file_input_selectors = (
        'input[type="file"]:not([accept*="image"])',
        'input[type="file"]',
    )

    # T11: ChatGPT routes vision uploads through the dedicated image
    # input. Falls back to the general file input.
    image_input_selectors = (
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    )

    # T06: ChatGPT model switcher lives in the top toolbar; clicking
    # opens a menu of model variants. Reasoning models (o1/o3/o4)
    # are gated on Plus / Team / Enterprise tiers but should be
    # accessible on the user's paid account. Modern UI (2026-Q2)
    # uses ``data-testid="model-switcher-dropdown-button"`` plus a
    # generic top-bar button shape. The legacy
    # ``button[data-testid*="model"]`` substring match still covers
    # most variants but the toolbar opener has been observed under
    # ``role="combobox"`` and ``data-radix-collection-item`` shapes
    # on rolling releases \u2014 we add those explicitly.
    model_switcher_selectors = (
        'button[data-testid="model-switcher-dropdown-button"]',
        'button[data-testid*="model-switcher"]',
        'button[data-testid*="model"]',
        '[data-testid="model-switcher"]',
        'button[aria-haspopup="menu"][aria-label*="Model" i]',
        'button[aria-label*="Model" i]',
        'button[aria-label*="\u6a21\u578b"]',
        'button[aria-label*="\u5207\u6362\u6a21\u578b"]',
        'button[role="combobox"][aria-label*="Model" i]',
    )
    reasoning_model_labels = (
        "o1",
        "o3",
        "o4",
        "GPT-5 Thinking",
        "Thinking",
        "Reasoning",
        "\u63a8\u7406",
    )

    # T12 / T13 / T15 advanced-tool toggle. Modern ChatGPT (2026-Q2)
    # paid accounts no longer auto-route Code Interpreter / Canvas /
    # DALL\u00b7E from prompt text alone \u2014 the user has to click
    # the composer "+" button (the tools picker) and pick the tool
    # explicitly. The legacy direct-button selectors below are kept
    # for adapters that DO expose the toggle as a top-level pill (some
    # rolling releases) but the picker path is the reliable one.
    tool_picker_selectors = (
        'button[aria-label="Add photos & files"]',
        'button[aria-label*="Add photos" i]',
        'button[data-testid="composer-plus-btn"]',
        'button[data-testid*="composer-plus"]',
        'button[data-testid*="composer-button-plus"]',
        'button[aria-label*="Attach" i][aria-label*="upload" i]',
        'button[aria-label="Tools"]',
        'button[aria-label*="Tools" i]',
    )

    # T13: Code Interpreter \u2014 listed as "Run code" / "Code
    # interpreter" / "Python" under the picker depending on tier.
    code_interpreter_button_selectors = ()
    code_interpreter_menu_labels = (
        "Run code",
        "Code interpreter",
        "Code Interpreter",
        "Run Python",
        "Python",
        "Analyze",
        "Code",
        "\u8fd0\u884c\u4ee3\u7801",
        "\u4ee3\u7801\u89e3\u91ca\u5668",
    )

    # T15: Canvas \u2014 the menu item is literally "Canvas" on
    # English locales; "\u753b\u5e03" on zh-CN.
    canvas_button_selectors = ()
    canvas_menu_labels = (
        "Canvas",
        "\u753b\u5e03",
    )

    # T12: DALL\u00b7E / image gen \u2014 picker entry is "Create
    # image" or "Image" depending on tier. We avoid "DALL" as the
    # only label because some tiers strip the brand name.
    image_gen_button_selectors = ()
    image_gen_menu_labels = (
        "Create an image",
        "Create image",
        "Generate image",
        "Image",
        "DALL",
        "\u521b\u5efa\u56fe\u50cf",
        "\u751f\u6210\u56fe\u7247",
    )

    # T14: ChatGPT exposes "Search the web" as a tool option. Per
    # ``CHATGPT-FULL-COVERAGE.md`` Surface 11 + legacy T14 PASS,
    # auto-routing usually handles current-events questions, so the
    # toggle is optional.
    web_search_button_selectors = (
        'button[aria-label*="Search" i][aria-label*="web" i]',
        'button[aria-label*="Web search" i]',
        'button[aria-label*="\u641c\u7d22"]',
    )

    # T15: ChatGPT Canvas mounts a side panel when the user asks for
    # extended editing. The panel has a distinct testid; if absent
    # we fall back to a class-substring match (legacy DOM probes).
    canvas_indicator_selectors = (
        '[data-testid*="canvas"]',
        '[class*="canvas"]',
        'div[class*="textarea"][class*="canvas"]',
    )

    # T09 branch flip: current ChatGPT UI labels the branch arrows
    # ``\u4e0a\u4e00\u56de\u590d`` / ``\u4e0b\u4e00\u56de\u590d``
    # on Chinese locale; English aria-labels include "Previous" /
    # "Next response". Per ``CHATGPT-FULL-COVERAGE.md`` T09
    # ``20260423-113523`` PASS evidence.
    branch_prev_selectors = (
        'button[aria-label*="Previous response" i]',
        'button[aria-label*="Previous" i]',
        'button[aria-label*="\u4e0a\u4e00\u56de\u590d"]',
        'button[aria-label*="\u4e0a\u4e00\u4e2a"]',
    )
    branch_next_selectors = (
        'button[aria-label*="Next response" i]',
        'button[aria-label*="Next" i]',
        'button[aria-label*="\u4e0b\u4e00\u56de\u590d"]',
        'button[aria-label*="\u4e0b\u4e00\u4e2a"]',
    )

    # T12 image generation: ChatGPT auto-routes "draw / generate an
    # image" prompts to DALL-E without an explicit invocation prefix.
    image_gen_invocation = None

    # ===== T15 / T13 / T12 explicit trigger prompts =====
    #
    # Generic "open in canvas" / "use python" prompts get auto-routed
    # inline by GPT-4o on roughly half of paid accounts. These site-
    # tuned prompts match the legacy autopilot's known-PASS set and
    # the 2026-Q2 ChatGPT-FULL-COVERAGE.md surface trigger guidance.
    canvas_trigger_prompt = (
        "Open Canvas and write a long-form essay (target 1800 to 2200 "
        "words, twelve paragraphs minimum) titled 'PCE T15 ChatGPT "
        "Canvas {token}'. Topic: a detailed history of GPU shortages "
        "and their effect on LLM training from 2020 through 2026, "
        "covering hyperscaler procurement, the Hopper-to-Blackwell "
        "transition, export controls, and second-source vendors. "
        "You MUST put this content inside the Canvas pane (not as an "
        "inline chat reply). After opening the Canvas, also write a "
        "one-line chat-side acknowledgement that ends with the literal "
        "token {token}."
    )
    code_interp_trigger_prompt = (
        "Use Python via the Code Interpreter tool to compute the "
        "SHA-256 hex digest of the literal string 'PCE-T13-{token}'. "
        "You MUST actually run the code with hashlib (do not estimate "
        "or pretend) — SHA-256 cannot be computed by hand. Show "
        "the Python source AND the runner's text output containing the "
        "64-character hex digest. After the code output, end your "
        "reply with the literal token {token}."
    )
    image_gen_trigger_prompt = (
        "Please draw a picture for me: a small solid-blue square "
        "centered on a plain white background, flat shading, no "
        "shadow, simple geometric style. Generate the image. "
        "Then in the chat caption below the picture, write one short "
        "line ending with the literal token {token}."
    )
