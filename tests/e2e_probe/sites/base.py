# SPDX-License-Identifier: Apache-2.0
"""Base site adapter for probe-driven E2E.

Each subclass binds a single AI chat site to the four operations the
matrix runner needs:

    open(probe)                    -> tab_id
    check_logged_in(probe, tab_id) -> LoginCheckResult
    send_prompt(probe, tab_id, p)  -> None
    wait_for_done(probe, tab_id)   -> None

All four are implemented here in terms of probe RPC verbs (``tab.*``,
``dom.*``); subclasses normally only need to override the class-level
selector tuples. Sites with idiosyncratic UX (Gemini's ``rich-textarea``
shadow root, ChatGPT's ProseMirror, etc.) override the relevant method
directly.

Selectors are expressed as **tuples of CSS strings tried in order**, not
comma-OR'd into a single string. The probe surfaces
``selector_not_found`` per-attempt so we can fail fast on the first
match instead of letting the page race the wait timeout.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, ClassVar, Optional, Sequence

from pce_probe import (
    ProbeClient,
    ProbeError,
    SelectorNotFoundError,
)


@dataclass(frozen=True)
class LoginCheckResult:
    """Outcome of ``BaseProbeSiteAdapter.check_logged_in``.

    ``logged_in`` is the bool the matrix runner cases against; ``detail``
    is a short human-readable string ("input found: #prompt-textarea",
    "login wall: a[href*=login]", "timeout: no input selector matched")
    surfaced into the test failure / skip message so the agent reading
    the report knows whether to (a) log in manually, (b) update the
    selector list, or (c) investigate the site itself.
    """

    logged_in: bool
    detail: str


class BaseProbeSiteAdapter:
    """Abstract per-site driver for probe-based E2E matrix.

    Subclasses MUST set:

      * ``name``     — short identifier (``"chatgpt"``); also the pytest
                       parametrize id.
      * ``provider`` — PCE provider key (``"openai"``); matched against
                       PCE Core's ``provider`` column.
      * ``url``      — entry URL the matrix opens fresh tabs to.
      * ``input_selectors`` — at least one CSS selector for the chat
                              input.

    Other tunables have sensible defaults; override only when needed.
    """

    # ---- override in subclass --------------------------------------------

    name: ClassVar[str] = "unknown"
    provider: ClassVar[str] = "unknown"
    url: ClassVar[str] = ""

    # CSS selectors. Tried in order; first one that resolves wins.
    input_selectors: ClassVar[Sequence[str]] = ("textarea",)
    send_button_selectors: ClassVar[Sequence[str]] = ()  # empty -> Enter
    stop_button_selectors: ClassVar[Sequence[str]] = ()  # empty -> stability fallback
    response_container_selectors: ClassVar[Sequence[str]] = (
        '[class*="message"]',
    )
    login_wall_selectors: ClassVar[Sequence[str]] = ()

    # T07: separate selectors for the inline-edit textarea/contenteditable
    # that appears INSIDE a previously-sent user turn after the edit
    # pencil is clicked. On Claude / ChatGPT modern UIs this element is
    # different from the standard composer at the bottom; using
    # ``input_selectors`` resolves to the wrong element and the typed
    # text never lands in the edit. Empty default \u2014 T07 will SKIP
    # on adapters that haven't mapped this surface, since we can't
    # distinguish "edit didn't fire" from "extractor lost the edit"
    # without a known-good targeting selector.
    edit_input_selectors: ClassVar[Sequence[str]] = ()

    # T15 canvas / artifact trigger. Each site has its own auto-routing
    # rules; a generic "open in canvas" prompt fails on most accounts
    # because the model decides inline is "good enough". Per-site
    # explicit triggers (with the literal token slot ``{token}``) let
    # T15 reliably exercise the surface. ``None`` keeps the case's
    # generic fallback prompt.
    canvas_trigger_prompt: ClassVar[Optional[str]] = None

    # T13 code-interpreter / analysis-tool trigger. Same rationale:
    # auto-routing is unreliable across model variants, so each site
    # gets to provide an explicit prompt that nudges the router into
    # actually executing the code (not just emitting it as text).
    # ``None`` keeps the case's generic fallback.
    code_interp_trigger_prompt: ClassVar[Optional[str]] = None

    # T12 image-gen trigger. Some sites (Claude) have NO native image
    # generation and ``None`` is a legitimate "this site does not
    # support the surface" signal \u2014 T12 will SKIP up-front rather
    # than waste a model call. Sites that DO support it provide an
    # explicit prompt template (with ``{token}``) that reliably
    # auto-routes into the image-gen tool.
    image_gen_trigger_prompt: ClassVar[Optional[str]] = None

    # Behavior tuning (milliseconds).
    page_load_timeout_ms: ClassVar[int] = 20_000
    input_appear_timeout_ms: ClassVar[int] = 15_000
    type_settle_ms: ClassVar[int] = 250
    # 8 s, not 4 s. Sites with a slow first-token latency (Google AI
    # Studio peaks at ~6 s before the streaming container appears,
    # ChatGPT can spend ~3 s thinking before any text renders) need
    # the larger window. The downside is we pay this much extra time
    # on TRUE submit failures \u2014 acceptable, since the resulting
    # error is still descriptive and ``response_timeout_ms`` (90 s)
    # was the previous fallback for these sites.
    submit_verify_timeout_ms: ClassVar[int] = 8_000
    response_timeout_ms: ClassVar[int] = 90_000
    response_stability_ms: ClassVar[int] = 3_000
    post_send_settle_ms: ClassVar[int] = 800

    # If set, ``session_hint_from_url`` extracts the conversation UUID
    # via ``re.search(pattern, current_url).group(1)``.
    session_url_pattern: ClassVar[Optional[re.Pattern]] = None

    # Per-site URL of a guaranteed non-chat surface (settings, account,
    # activity, library, plan, etc.) used by T20 to assert capture
    # silence on inert routes. ``None`` means the adapter has not yet
    # been wired with a settings URL \u2014 T20 will SKIP for that
    # site rather than guess. Pick a route documented as silent in the
    # site's coverage doc (CHATGPT/CLAUDE/GEMINI/GAS-FULL or per-site
    # DIFF/SMOKE specs); avoid pages that lazy-load chat history
    # (e.g. ChatGPT's ``/c/<uuid>`` resume) since those would invite
    # legitimate captures and create false positives.
    settings_url: ClassVar[Optional[str]] = None

    # ---- per-action selectors / URLs (empty tuple / None -> auto-SKIP) ---
    #
    # The matrix runner treats these as opt-in: a site that hasn't wired
    # a given case's selector is reported as SKIP, not FAIL. This lets
    # us land a case implementation site-agnostically and add per-site
    # support over time without breaking the matrix.

    # T07 edit: button(s) on a user message that opens the edit editor.
    # Lives inside the user turn DOM and is typically hover-revealed;
    # :meth:`click_last_user_edit` uses ``dom.execute_js`` to fire
    # mouseenter on the last user turn before clicking. Empty tuple
    # \u2014 T07 SKIPs for that site (e.g. Grok per
    # ``GROK-COVERAGE-DIFF.md`` shared T-cases note).
    edit_button_selectors: ClassVar[Sequence[str]] = ()

    # T08 regenerate: button(s) on the latest assistant message that
    # triggers a new variant. Modern UIs render this without requiring
    # hover; :meth:`click_regenerate` iterates ``dom.click`` over each
    # entry. Empty \u2014 T08 SKIPs.
    regenerate_button_selectors: ClassVar[Sequence[str]] = ()

    # T08 fallback: when ``regenerate_button_selectors`` doesn't match,
    # the affordance is buried in a "More actions" / "..." overflow
    # menu on the assistant turn. Per the legacy adapter
    # (``tests/e2e/sites/chatgpt.py:447-494``), a 2-step click is
    # needed: open the menu, then click the labeled item.
    #
    # ``more_actions_button_selectors``: button that opens the menu.
    # ``regenerate_menu_labels``: text content of the menu item to
    # click after the menu opens. Both empty \u2014 fallback skipped,
    # only direct selectors are tried.
    more_actions_button_selectors: ClassVar[Sequence[str]] = ()
    regenerate_menu_labels: ClassVar[Sequence[str]] = (
        "Regenerate",
        "Try again",
        "\u91cd\u65b0\u751f\u6210",
        "\u91cd\u65b0\u56de\u7b54",
    )

    # T09 branch flip: previous/next arrows that appear after T07's
    # edit creates a branch tree. Empty tuples \u2014 T09 SKIPs.
    branch_prev_selectors: ClassVar[Sequence[str]] = ()
    branch_next_selectors: ClassVar[Sequence[str]] = ()

    # T10 / T11: native ``<input type='file'>`` element (often hidden
    # under a paperclip button). T11 falls back to
    # ``file_input_selectors`` when ``image_input_selectors`` is empty
    # \u2014 same control on most sites. Empty file selector \u2014
    # both T10 and T11 SKIP.
    file_input_selectors: ClassVar[Sequence[str]] = ()
    image_input_selectors: ClassVar[Sequence[str]] = ()

    # T12 / T13 / T14 / T15: tools menu / direct toggle for the named
    # tool. Three patterns are supported via two ClassVars each:
    #   1. Direct toggle button \u2014 selectors point at the toggle
    #      itself; helper just clicks it.
    #   2. Tools-menu opener + labelled item \u2014 the ``_label`` list
    #      identifies the menu item to click after the menu opens.
    #   3. Slash-command \u2014 ``_slash`` prefix; case prepends to
    #      the prompt instead of clicking a UI control.
    #
    # Modern ChatGPT (2026-Q2) and Gemini moved their advanced-tool
    # affordances behind a single composer "+" button (the
    # ``tool_picker_selectors`` below). When that's set, the helper
    # ``enable_tool`` opens the picker first, then JS-clicks the first
    # visible menuitem whose innerText contains any of the per-tool
    # ``*_menu_labels`` entries. Falls back to direct toggle (pattern 1)
    # when the picker is unset or unhelpful.
    tool_picker_selectors: ClassVar[Sequence[str]] = ()
    code_interpreter_button_selectors: ClassVar[Sequence[str]] = ()
    code_interpreter_menu_labels: ClassVar[Sequence[str]] = ()
    web_search_button_selectors: ClassVar[Sequence[str]] = ()
    web_search_menu_labels: ClassVar[Sequence[str]] = ()
    canvas_button_selectors: ClassVar[Sequence[str]] = ()
    canvas_menu_labels: ClassVar[Sequence[str]] = ()
    image_gen_button_selectors: ClassVar[Sequence[str]] = ()
    image_gen_menu_labels: ClassVar[Sequence[str]] = ()

    # T15 canvas: any selector that becomes visible AFTER a canvas /
    # artifact opens. T15 PASS condition is BOTH a chat-side capture
    # lands AND this selector renders \u2014 a chat capture alone
    # doesn't prove canvas opened. Empty \u2014 T15 SKIPs.
    canvas_indicator_selectors: ClassVar[Sequence[str]] = ()

    # T19 error: site-specific banner the page renders on failure
    # (rate limit, content policy, server error). T19 baselines the
    # capture-event count, sends a known-failing prompt, waits for
    # the banner, and asserts NO new PCE_CAPTURE landed with the
    # banner text as assistant content. ``error_trigger_prompt``
    # defaults to a content-policy bait shared across providers; sites
    # that reliably reject something cheaper override it.
    error_banner_selectors: ClassVar[Sequence[str]] = ()
    error_trigger_prompt: ClassVar[Optional[str]] = None

    # T06 thinking: model-switcher button + label substrings that
    # match a reasoning / thinking model entry (o1/o3, Opus, "Pro
    # with Thinking", etc.). Both must be set; either being empty
    # \u2014 T06 SKIPs (free accounts commonly cannot reach reasoning
    # models, this is correct behavior, not a fail).
    model_switcher_selectors: ClassVar[Sequence[str]] = ()
    reasoning_model_labels: ClassVar[Sequence[str]] = ()

    # T18 temporary chat: site URL that opens a privacy-mode chat
    # (ChatGPT's ``?temporary-chat=true``). Other sites have no
    # equivalent surface today (Gemini's G24 is deferred to v1.1).
    # ``None`` \u2014 T18 SKIPs.
    temporary_chat_url: ClassVar[Optional[str]] = None

    # T16 / T17: pre-existing GPT / Project URL the user wants
    # exercised. Defaults to ``None`` (case SKIPs) but can be
    # overridden via env vars in the per-site adapter file
    # (``PCE_PROBE_<NAME>_GPT_URL``, ``PCE_PROBE_<NAME>_PROJECT_URL``)
    # so account-specific URLs don't get committed to source.
    custom_gpt_url: ClassVar[Optional[str]] = None
    project_url: ClassVar[Optional[str]] = None

    # T12 image generation: prompt-template hint. Sites that auto-detect
    # image-generation intent from natural language ("draw a cat") leave
    # this ``None``; sites that need an explicit invocation (Grok's
    # Aurora, slash-command-only surfaces) prepend this string to the
    # prompt. Distinct from a full prompt template; only the prefix.
    image_gen_invocation: ClassVar[Optional[str]] = None

    # ---- core probe operations -------------------------------------------

    def open(self, probe: ProbeClient) -> int:
        """Open a fresh tab on this site, wait for load, return ``tab_id``.

        Some SPAs never fire ``readyState=='complete'`` because of
        long-polling streams; we swallow ``timeout`` from
        ``tab.wait_for_load`` and let ``check_logged_in`` decide whether
        the page is actually usable.
        """
        result = probe.tab.open(self.url, active=True)
        tab_id = int(result["tab_id"])
        try:
            probe.tab.wait_for_load(
                tab_id,
                timeout_ms=self.page_load_timeout_ms,
            )
        except ProbeError:
            # Long-polling SPAs (Claude, Gemini, ChatGPT) commonly leave
            # navigation in 'loading' forever. Proceed; login_check
            # gates the rest.
            pass
        return tab_id

    def check_logged_in(
        self,
        probe: ProbeClient,
        tab_id: int,
    ) -> LoginCheckResult:
        """Detect whether the chat input is reachable.

        Strategy:
          1. Probe each ``login_wall_selectors`` entry. If any matches,
             the user is on a login screen — return ``logged_in=False``.
          2. Probe each ``input_selectors`` entry with a wait. The first
             one that resolves wins.
          3. If neither hits, return ``logged_in=False`` with a timeout
             detail so the matrix can skip with a useful reason.
        """
        # 1. Login wall? (Fast negative.)
        for sel in self.login_wall_selectors:
            try:
                res = probe.dom.query(tab_id, sel)
                if res.get("matches"):
                    return LoginCheckResult(False, f"login wall: {sel}")
            except ProbeError:
                # Selector errors here are benign — the wall just isn't
                # present, which is what we hope for.
                pass

        # 2. Input present? (Each input gets a slice of the budget.)
        #
        # IMPORTANT: ``visible=False`` here. ``dom.wait_for_selector``
        # defaults to requiring ``getBoundingClientRect()`` to report
        # nonzero dimensions \u2014 but Chrome zeroes out layout for
        # tabs whose containing window isn't focused. With the test
        # browser running while the IDE is foregrounded, EVERY element
        # (including ``body``) reports ``visible=false`` and the wait
        # times out. Login-wall detection above already runs via
        # ``dom.query`` (existence-based, ignores visibility), so we
        # don't lose the "unauthenticated" signal by dropping the
        # visibility requirement here.
        per_selector_ms = max(
            1_000,
            self.input_appear_timeout_ms // max(1, len(self.input_selectors)),
        )
        for sel in self.input_selectors:
            try:
                probe.dom.wait_for_selector(
                    tab_id,
                    sel,
                    timeout_ms=per_selector_ms,
                    visible=False,
                )
                return LoginCheckResult(True, f"input found: {sel}")
            except SelectorNotFoundError:
                continue
            except ProbeError:
                continue

        return LoginCheckResult(
            False,
            f"timeout: none of {len(self.input_selectors)} input "
            f"selectors matched within {self.input_appear_timeout_ms}ms",
        )

    def resolve_input_selector(
        self,
        probe: ProbeClient,
        tab_id: int,
        *,
        timeout_ms: int | None = None,
    ) -> str:
        """Return the first input selector that currently resolves.

        Per-selector budget defaults to ``input_appear_timeout_ms``
        divided across all configured selectors (matches the strategy
        used by ``check_logged_in``). Pass ``timeout_ms`` to override
        the per-selector budget directly.

        Raises ``SelectorNotFoundError`` if none do (rich error context
        comes back via probe).

        We catch ``ProbeError`` (not just ``SelectorNotFoundError``)
        because the ``dom.wait_for_selector`` verb surfaces a generic
        ``timeout`` error code when its budget is exhausted, NOT a
        ``selector_not_found``. Without this catch, a slow first
        selector would block the fallback from ever being tried.
        """
        if timeout_ms is None:
            timeout_ms = max(
                1_000,
                self.input_appear_timeout_ms // max(1, len(self.input_selectors)),
            )
        # ``visible=False``: see ``check_logged_in`` for the rationale.
        # Backgrounded windows zero out layout, so we only require the
        # element to exist in the DOM.
        for sel in self.input_selectors:
            try:
                probe.dom.wait_for_selector(
                    tab_id,
                    sel,
                    timeout_ms=timeout_ms,
                    visible=False,
                )
                return sel
            except SelectorNotFoundError:
                continue
            except ProbeError:
                continue
        # Re-raise on the primary so the agent sees a probe-level
        # ``selector_not_found`` with full DOM excerpt.
        probe.dom.wait_for_selector(
            tab_id,
            self.input_selectors[0],
            timeout_ms=timeout_ms,
            visible=False,
        )
        return self.input_selectors[0]

    def send_prompt(
        self,
        probe: ProbeClient,
        tab_id: int,
        prompt: str,
    ) -> None:
        """Type ``prompt`` and submit, with verified submission.

        The naive ``probe.dom.type(submit=True)`` is fragile because:

          A. Pages with slow hydration (Claude, Gemini, ChatGPT) accept
             typed text before React has wired up the submit handler,
             so the synthetic Enter is silently consumed.
          B. Controlled inputs need a tick after ``input``/``change``
             events for the framework to commit state. If we fire
             Enter on the same tick, the form sees an empty field and
             the submit handler bails.
          C. Send buttons may be ``aria-disabled`` while the model is
             still streaming; ``dom.click`` succeeds but no submit
             fires.

        The mitigations work entirely through ``dom.wait_for_selector``
        / ``dom.type`` / ``dom.query`` / ``dom.click`` / ``dom.press_key``
        \u2014 verbs whose results are known-good. ``dom.execute_js``
        is intentionally avoided here; its current SW handler runs the
        user code via ``new Function`` and on MV3 isolated worlds
        Chrome silently drops the return value, so any verification
        hung off it would be a no-op.

        Sequence:
          1. ``resolve_input_selector`` waits for visibility (built-in).
          2. Type with ``submit=False``, then sleep ``type_settle_ms``
             so the framework commits its controlled-input state
             before we attempt submit.
          3. :meth:`_submit_with_verify` performs the submit and polls
             two signals (stop button visible, response text grew).
             On miss it retries via the alternate strategy
             (button \u2194 Enter) before raising.
        """
        selector = self.resolve_input_selector(probe, tab_id)
        self._type_with_verify(probe, tab_id, selector, prompt)
        self._submit_with_verify(probe, tab_id, selector)

    def _type_with_verify(
        self,
        probe: ProbeClient,
        tab_id: int,
        selector: str,
        prompt: str,
    ) -> None:
        """Type ``prompt`` and confirm the input actually contains it.

        Some sites (Gemini's Quill ``.ql-editor``, Claude's
        ProseMirror) wrap their inputs in a custom editor that owns
        the value model. ``probe.dom.type`` synthesizes ``input`` /
        ``beforeinput`` events on the underlying contenteditable, but
        these editors sometimes drop the events when the page is
        still hydrating, leaving the input visually empty. The send
        button on those sites is bound to ``editor.isEmpty`` so it
        stays ``aria-disabled``; the subsequent click "succeeds" at
        the DOM level but the page's submit handler bails. Result:
        ``_submit_with_verify`` waits the full timeout for a signal
        that can never fire, and the user pays for it.

        We mitigate by reading the input back via ``dom.query``
        (whose ``text_excerpt`` is populated for contenteditable +
        textarea elements) and retrying the type once if the prompt
        head isn't there. Two attempts is enough to absorb the
        hydration race in practice; persistent failures raise with
        the actual content we observed so the agent can see WHY the
        submit step is going to fail before it tries.
        """
        # We compare the first ``needle_len`` chars of the prompt
        # against the input's text excerpt. The full prompt is too
        # long for ``text_excerpt`` (which is truncated by the SW)
        # and quoting / smart-quote substitution by some editors
        # would defeat an exact-equality check anyway. The prompt
        # always starts with "Please reply with the literal word "
        # which is unique enough.
        needle_len = min(40, len(prompt))
        needle = prompt[:needle_len]
        max_attempts = 2
        last_actual = "<not read>"
        for attempt in range(1, max_attempts + 1):
            probe.dom.type(
                tab_id,
                selector,
                prompt,
                clear=True,
                submit=False,
            )
            # Settle: let React / Vue / Angular / Quill commit their
            # controlled-input state before we read or submit.
            time.sleep(self.type_settle_ms / 1_000)
            actual = self._read_input_text(probe, tab_id, selector)
            if actual is None:
                # Couldn't read \u2014 trust the type call and let
                # ``_submit_with_verify`` either succeed or surface
                # a descriptive error.
                return
            last_actual = actual
            if needle in actual:
                return
            # Backoff a bit on retry: longer settle gives the editor
            # extra time to flush its event queue.
            if attempt < max_attempts:
                time.sleep(self.type_settle_ms / 1_000)
        raise ProbeError(
            f"input value did not commit after {max_attempts} type "
            f"attempts; expected first {needle_len} chars of prompt in "
            f"input, got {len(last_actual)} chars head="
            f"{last_actual[:120]!r}",
            agent_hint=(
                "the input element accepted our keystrokes at the DOM "
                "layer but the page's editor framework (Quill / "
                "ProseMirror / Lexical / Angular controlled input) "
                "did not commit them to its model \u2014 the send "
                "button is therefore aria-disabled and a click would "
                "be a no-op; the page is likely still hydrating or "
                "the input selector resolves to a wrong target"
            ),
            context={
                "phase": "_type_with_verify",
                "selector": selector,
                "prompt_head": prompt[:120],
                "actual_head": last_actual[:200],
                "actual_len": len(last_actual),
                "attempts": max_attempts,
            },
        )

    def _read_input_text(
        self,
        probe: ProbeClient,
        tab_id: int,
        selector: str,
    ) -> Optional[str]:
        """Return the input's current visible text via ``dom.query``.

        Returns the longest ``text_excerpt`` across all matches. Some
        editors render hidden mirrors (Quill's ``.ql-clipboard``,
        TipTap's diagnostic mirror) whose text is always empty; we
        want the visible primary editor's content, not them.

        Returns ``None`` to mean "I can't measure this reliably, treat
        the type as a success and let downstream verify-submit catch
        a real failure." This happens in two cases:

          1. The probe call errored (network blip, selector vanished
             between resolution and read).
          2. The matched element is a ``<textarea>`` or ``<input>``.
             For these, ``text_excerpt`` (= ``el.textContent``) shows
             the INITIAL DOM content between the tags, NOT the user-
             entered ``el.value``. Reading ``text_excerpt`` of an
             Angular ``cdktextareaautosize`` (Google AI Studio) after
             typing returns ``""`` even though ``el.value`` correctly
             contains our prompt. Without this skip we'd false-fail
             on every AI Studio T01 run after the React/Angular value
             setter path \u2014 which IS reliable \u2014 successfully
             populated the textarea.

        Contenteditable elements (Quill, ProseMirror, TipTap, Lexical)
        DO reflect their value via ``textContent``, so verification
        works as intended for those.
        """
        try:
            res = probe.dom.query(tab_id, selector, all=True)
        except ProbeError:
            return None
        matches = res.get("matches", []) or []
        if not matches:
            return ""
        # Sniff element type from the outer HTML excerpt; the SW
        # truncates this to ~200 chars but it always starts with the
        # opening tag so a leading-substring check is safe.
        for m in matches:
            outer = str(m.get("outer_html_excerpt") or "").lstrip().lower()
            if outer.startswith("<textarea") or outer.startswith("<input"):
                # Native form control: ``el.value`` is unreachable via
                # ``text_excerpt``. Decline to measure; caller treats
                # this as "trust the type call".
                return None
        # Contenteditable: pick the match with the most text. Hidden
        # mirrors return "" so the longest match is the real editor.
        best = ""
        for m in matches:
            text = str(m.get("text_excerpt") or "")
            if len(text) > len(best):
                best = text
        return best

    # ---- submit with verification ----------------------------------------

    def _submit_with_verify(
        self,
        probe: ProbeClient,
        tab_id: int,
        selector: str,
    ) -> None:
        """Submit, then watch for any of three success signals.

        Success signals (any one is sufficient):
          1. A stop button is visible (response started streaming).
          2. A response container's text grew past its pre-submit size.
          3. The tab's URL changed (most chat sites flip
             ``/`` or ``/new`` \u2192 ``/chat/<uuid>`` synchronously
             on submit, regardless of response speed).

        Signal 3 closes a flake mode that signals 1+2 cannot: on Gemini
        the assistant's first token is normally rendered within ~2 s,
        but under load can take >15 s while the URL still flips
        within ~200 ms. Watching the URL lets us confirm submit fired
        without paying for slow-response variance.

        We capture pre-submit URL and response-text size up front so
        signals 2/3 can detect deltas. If no signal fires within
        ``submit_verify_timeout_ms``, we retry via the alternate
        submit strategy (button \u2194 Enter); if THAT also misses, we
        raise with full context so the matrix report tells the agent
        which phase failed instead of the user seeing a 90 s capture
        timeout.
        """
        primary = "button" if self.send_button_selectors else "enter"
        pre_submit_text_len = self._sum_response_text_len(probe, tab_id)
        pre_submit_url = self._get_tab_url(probe, tab_id)

        self._submit_via(probe, tab_id, selector, primary)
        if self._verify_submit_fired(
            probe,
            tab_id,
            pre_submit_text_len=pre_submit_text_len,
            pre_submit_url=pre_submit_url,
        ):
            return

        alt = "enter" if primary == "button" else "button"
        self._submit_via(probe, tab_id, selector, alt)
        if self._verify_submit_fired(
            probe,
            tab_id,
            pre_submit_text_len=pre_submit_text_len,
            pre_submit_url=pre_submit_url,
        ):
            return

        raise ProbeError(
            f"submit did not fire via {primary} or {alt}: no stop button "
            f"became visible, no response container's text grew, and the "
            f"URL did not change within {self.submit_verify_timeout_ms}ms "
            f"after each attempt",
            agent_hint=(
                "the page accepted the typed text but neither Enter nor "
                "send-button click produced a visible streaming response "
                "OR a URL flip \u2014 the send button may be aria-disabled "
                "(rate limited / context full), the page expects a real "
                "keyboard event (isTrusted check), or our send-button "
                "selector is matching a wrong-target button on this site"
            ),
            context={
                "phase": "_submit_with_verify",
                "selector": selector,
                "primary": primary,
                "alternate": alt,
                "pre_submit_text_len": pre_submit_text_len,
                "pre_submit_url": pre_submit_url,
                "current_url": self._get_tab_url(probe, tab_id),
            },
        )

    def _submit_via(
        self,
        probe: ProbeClient,
        tab_id: int,
        selector: str,
        mode: str,
    ) -> None:
        """Issue the submit. ``mode`` is ``'enter'`` or ``'button'``.

        For ``button`` mode we iterate ``send_button_selectors`` in
        order; the first selector whose click succeeds wins. We don't
        verify the button is enabled before clicking \u2014 that would
        require ``dom.execute_js`` (broken; see :meth:`send_prompt`)
        and the verify pass downstream catches the case anyway by
        observing no streaming response.

        Falls back to Enter on the input if every button selector
        fails (no match / element error), so a partially-misconfigured
        adapter still gets one submit attempt.
        """
        if mode == "enter":
            probe.dom.press_key(tab_id, "Enter", selector=selector)
            return
        for sel in self.send_button_selectors or ():
            try:
                probe.dom.click(tab_id, sel)
                return
            except (SelectorNotFoundError, ProbeError):
                continue
        # All button selectors failed \u2014 last-ditch Enter so the
        # verify pass at least sees ONE attempt's effect.
        probe.dom.press_key(tab_id, "Enter", selector=selector)

    def _verify_submit_fired(
        self,
        probe: ProbeClient,
        tab_id: int,
        *,
        pre_submit_text_len: int,
        pre_submit_url: Optional[str] = None,
    ) -> bool:
        """Poll for any submit-success signal. Returns True on first hit.

        Three signals, all observable through working verbs (no
        ``dom.execute_js`` dependency):

          1. Any ``stop_button_selectors`` entry is visible.
          2. Concatenated text across ``response_container_selectors``
             grew past the pre-submit baseline.
          3. The tab's URL changed from ``pre_submit_url``. Most chat
             sites flip the URL on first submit (e.g. ``/app`` \u2192
             ``/app/<uuid>`` for Gemini, ``/`` \u2192 ``/chat/<uuid>``
             for Grok). This signal fires within a few hundred ms of
             submit and is independent of model latency, so it covers
             the slow-first-token flake window where signals 1/2 don't.

        Returns False on timeout; caller decides whether to retry or
        raise.
        """
        deadline = time.time() + self.submit_verify_timeout_ms / 1_000
        while time.time() < deadline:
            # Signal 3 first \u2014 cheapest call (single tab.list) and
            # the most reliable indicator that submit actually fired.
            if pre_submit_url is not None:
                cur = self._get_tab_url(probe, tab_id)
                if cur is not None and cur != pre_submit_url:
                    return True
            for sel in self.stop_button_selectors or ():
                if self._selector_visible(probe, tab_id, sel):
                    return True
            if self._sum_response_text_len(probe, tab_id) > pre_submit_text_len:
                return True
            time.sleep(0.25)
        return False

    def _get_tab_url(
        self,
        probe: ProbeClient,
        tab_id: int,
    ) -> Optional[str]:
        """Return the tab's current URL, or ``None`` on any error.

        Used by ``_verify_submit_fired`` to detect SPA navigation as
        a submit-fired signal. Errors are silenced because callers
        already gate on ``is None`` \u2014 a missing URL just means
        we skip signal 3 and fall back to signals 1/2.
        """
        try:
            tabs = probe.tab.list().get("tabs", [])
        except ProbeError:
            return None
        for t in tabs:
            if t.get("id") == tab_id:
                url = t.get("url")
                return str(url) if url else None
        return None

    def _sum_response_text_len(
        self,
        probe: ProbeClient,
        tab_id: int,
    ) -> int:
        """Sum the visible text length across all response containers.

        Used as a baseline for detecting that submit produced new
        rendered output. We sum across selectors so multi-message
        layouts (Claude renders user + assistant in one tree) don't
        confuse the comparison.
        """
        total = 0
        for sel in self.response_container_selectors:
            try:
                res = probe.dom.query(tab_id, sel, all=True)
            except ProbeError:
                continue
            for m in res.get("matches", []) or []:
                total += len(str(m.get("text_excerpt") or ""))
        return total

    def wait_for_done(
        self,
        probe: ProbeClient,
        tab_id: int,
    ) -> None:
        """Block until the AI response stops streaming.

        Two strategies, in priority order:

          1. **Stop button cycle** — if ``stop_button_selectors`` is
             configured, wait for one to appear (response started) then
             for all of them to disappear (response done). This is the
             most reliable signal because the page's own UI uses it.
          2. **Response text stability** — fall back to checking that
             the concatenated text of the response containers stops
             changing for ``response_stability_ms``.

        Returns silently in either case; raises only if
        ``response_timeout_ms`` elapses without progress (so the matrix
        case can fail with a useful message).
        """
        if self.stop_button_selectors:
            self._wait_for_stop_button_cycle(probe, tab_id)
            return
        self._wait_for_response_text_stable(probe, tab_id)

    def _wait_for_stop_button_cycle(
        self,
        probe: ProbeClient,
        tab_id: int,
    ) -> None:
        deadline = time.time() + self.response_timeout_ms / 1_000

        # Phase A: wait for any stop button to appear (response started).
        # Some sites streams arrive too fast to ever paint the button; we
        # tolerate that by allowing Phase B to start without Phase A.
        appeared = False
        appear_deadline = time.time() + 8.0
        while time.time() < appear_deadline and not appeared:
            for sel in self.stop_button_selectors:
                if self._selector_visible(probe, tab_id, sel):
                    appeared = True
                    break
            if not appeared:
                time.sleep(0.3)

        # Phase B: wait for ALL stop buttons to disappear.
        gone_since: Optional[float] = None
        while time.time() < deadline:
            any_visible = False
            for sel in self.stop_button_selectors:
                if self._selector_visible(probe, tab_id, sel):
                    any_visible = True
                    break
            if not any_visible:
                if gone_since is None:
                    gone_since = time.time()
                # Require the button to be gone for a brief moment to
                # avoid the moment between two streaming chunks where
                # the page momentarily clears the stop UI.
                if (time.time() - gone_since) >= 0.7:
                    return
            else:
                gone_since = None
            time.sleep(0.4)

    def _selector_visible(
        self,
        probe: ProbeClient,
        tab_id: int,
        selector: str,
    ) -> bool:
        """Return True if ``selector`` appears to be on-screen.

        ``visible`` in the probe's ``dom.query`` reply is derived from
        ``getBoundingClientRect()`` having nonzero dimensions. Chrome
        zeroes the layout for tabs whose containing window isn't
        focused, so when the IDE has focus and the test browser sits
        in the background, EVERY element \u2014 including ``body``
        \u2014 reports ``visible=false``. To stay useful in that
        common case we fall back to existence: if any element matches
        the selector, treat it as visible. False positives are
        possible for ``display: none`` elements, but stop-button
        selectors in our adapters target nodes that only render while
        streaming, so existence is a strong proxy. The alternative
        (always returning False whenever the test window is
        backgrounded) is strictly worse because it makes
        ``_verify_submit_fired`` wait the full ``submit_verify_timeout``
        before falling back to text-growth detection.
        """
        try:
            res = probe.dom.query(tab_id, selector)
        except ProbeError:
            return False
        matches = res.get("matches", []) or []
        if not matches:
            return False
        # Fast path: the page reports a visible match.
        for m in matches:
            if m.get("visible"):
                return True
        # Backgrounded-window fallback: existence implies "potentially
        # visible". See docstring for tradeoffs.
        return True

    def _wait_for_response_text_stable(
        self,
        probe: ProbeClient,
        tab_id: int,
    ) -> None:
        deadline = time.time() + self.response_timeout_ms / 1_000
        last_total = -1
        stable_since = time.time()
        while time.time() < deadline:
            total = 0
            for sel in self.response_container_selectors:
                try:
                    res = probe.dom.query(tab_id, sel, all=True)
                except ProbeError:
                    continue
                for m in res.get("matches", []) or []:
                    total += len(str(m.get("text_excerpt") or ""))
            if total != last_total:
                last_total = total
                stable_since = time.time()
            elif (time.time() - stable_since) * 1_000 >= self.response_stability_ms:
                return
            time.sleep(0.5)

    # ---- session correlation ---------------------------------------------

    def session_hint_from_url(self, current_url: str) -> Optional[str]:
        """Extract the conversation UUID from a URL using
        ``session_url_pattern`` if set. The pattern's first capture
        group is what gets returned. Returns ``None`` if no pattern is
        configured or the URL doesn't match.
        """
        if not self.session_url_pattern:
            return None
        m = self.session_url_pattern.search(current_url or "")
        return m.group(1) if m else None

    # ---- compound action helpers (used by Stage 2-5 cases) ---------------

    def click_first_visible(
        self,
        probe: "ProbeClient",
        tab_id: int,
        selectors: Sequence[str],
    ) -> bool:
        """Click the first selector that resolves successfully.

        Returns True on first successful click, False if every selector
        raised ``SelectorNotFoundError`` or another ``ProbeError``.
        Used by case helpers that need to try multiple legacy /
        locale-variant selectors in order.
        """
        for sel in selectors:
            try:
                probe.dom.click(tab_id, sel)
                return True
            except SelectorNotFoundError:
                continue
            except ProbeError:
                continue
        return False

    def wait_for_stop_button_visible(
        self,
        probe: "ProbeClient",
        tab_id: int,
        *,
        timeout_s: float = 12.0,
    ) -> bool:
        """Poll until any ``stop_button_selectors`` entry is visible.

        Used by T03 to confirm the response started before clicking
        Stop, and by T08 to confirm regen actually fired before
        waiting for completion. Returns False on timeout.
        """
        if not self.stop_button_selectors:
            return False
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for sel in self.stop_button_selectors:
                if self._selector_visible(probe, tab_id, sel):
                    return True
            time.sleep(0.3)
        return False

    def click_stop_button(
        self,
        probe: "ProbeClient",
        tab_id: int,
    ) -> bool:
        """Click the first matching stop button. Returns False if none."""
        return self.click_first_visible(probe, tab_id, self.stop_button_selectors)

    def click_last_user_edit(
        self,
        probe: "ProbeClient",
        tab_id: int,
    ) -> bool:
        """Open the edit-in-place editor for the latest user message.

        Two-phase strategy:
          1. ``dom.execute_js`` dispatches mouseenter / mouseover /
             pointerenter on the last user-turn-shaped ancestor so any
             hover-revealed buttons render.
          2. ``dom.execute_js`` again iterates ``edit_button_selectors``
             from last-defined to first and clicks the LAST visible
             match (which is the most recent user turn's edit button).

        Naive ``dom.click`` would hit the FIRST DOM match (an older
        user turn higher in the page). Going via ``execute_js``
        sidesteps the unscoped-click issue. Return values from
        ``execute_js`` are dropped under MV3 isolated worlds, so the
        caller verifies success by side effects (input editor
        re-mounts, send button enables, etc.). Returns False only
        when ``edit_button_selectors`` is empty (case SKIPs) or the
        execute_js call itself errored.
        """
        if not self.edit_button_selectors:
            return False
        # Step 1: hover-reveal the buttons. Best-effort; ignored on
        # error because the click in step 2 is the real signal.
        try:
            probe.dom.execute_js(
                tab_id,
                r"""
                (function () {
                  var sels = [
                    '[data-message-author-role="user"]',
                    '[data-testid="human-turn"]',
                    'user-query',
                    '.chat-turn-container.user',
                    'ms-chat-turn .chat-turn-container.user',
                    '[data-turn-role="user"]'
                  ];
                  for (var i = 0; i < sels.length; i++) {
                    var matches = document.querySelectorAll(sels[i]);
                    if (!matches || !matches.length) continue;
                    var last = matches[matches.length - 1];
                    ['mouseenter', 'mouseover', 'pointerenter']
                      .forEach(function (ev) {
                        try {
                          last.dispatchEvent(new MouseEvent(ev, {
                            bubbles: true, cancelable: true, view: window
                          }));
                        } catch (e) {}
                      });
                    return;
                  }
                })();
                """,
            )
        except ProbeError:
            pass
        # Step 2: click the LAST visible match across ``edit_button_selectors``.
        sels_json = "[" + ", ".join(_js_str(s) for s in self.edit_button_selectors) + "]"
        try:
            probe.dom.execute_js(
                tab_id,
                """
                (function () {
                  var sels = """ + sels_json + r""";
                  for (var i = sels.length - 1; i >= 0; i--) {
                    var matches = document.querySelectorAll(sels[i]);
                    if (!matches || !matches.length) continue;
                    for (var j = matches.length - 1; j >= 0; j--) {
                      var el = matches[j];
                      if (el && typeof el.click === 'function') {
                        try { el.click(); return; } catch (e) {}
                      }
                    }
                  }
                })();
                """,
            )
        except ProbeError:
            return False
        time.sleep(0.5)  # let the editor mount
        return True

    def click_regenerate(
        self,
        probe: "ProbeClient",
        tab_id: int,
    ) -> bool:
        """Click regenerate on the LATEST assistant turn.

        Single unified JS path that scopes both the direct-button
        affordance AND the more-actions menu to the latest assistant
        message. The earlier two-pass implementation used
        ``click_first_visible`` for the direct path which matches
        ANY visible button on the page \u2014 that hits global
        affordances like network-error "Retry" pills on ChatGPT and
        breaks T08 with a "regenerate_clicked=true but stop button
        never appeared" SKIP. Scoping to the assistant-turn DOM
        removes that whole class of false-positive.

        Strategy ladder (all within the latest assistant turn):

          1. Hover the turn to reveal the action strip (modern UIs
             keep regen hidden until ``:hover``).
          2. Find the first ``regenerate_button_selectors`` match
             that's a descendant of the turn or its sibling action
             rail. Click and return ``"direct"``.
          3. If no direct match, open the ``more_actions_button_selectors``
             button within the turn, wait for the menu, and click
             the first menuitem whose text contains one of the
             ``regenerate_menu_labels``. Return ``"menu"``.
          4. Otherwise write the diagnostic side-channel and return
             ``"none"``.

        We write the actual outcome (``direct`` / ``menu`` / a
        miss-reason string) to ``document.body.dataset.pceRegenResult``
        so the caller can surface it without depending on the
        execute_js return value (dropped under MV3 isolated worlds).
        """
        if not self.regenerate_button_selectors and not self.more_actions_button_selectors:
            return False
        regen_sels_json = (
            "[" + ", ".join(_js_str(s) for s in self.regenerate_button_selectors) + "]"
        )
        more_sels_json = (
            "[" + ", ".join(_js_str(s) for s in self.more_actions_button_selectors) + "]"
        )
        labels_json = (
            "[" + ", ".join(_js_str(l) for l in self.regenerate_menu_labels) + "]"
        )
        # The JS:
        #   1. Locates the LATEST assistant turn via several role/testid hints.
        #   2. Hovers the turn AND its parent (modern ChatGPT mounts
        #      the action strip on the parent, not the turn itself).
        #   3. Walks the regen selectors limited to (a) the turn, (b) its
        #      action-strip sibling, (c) document. Document scope is the
        #      last resort \u2014 only used if NO turn-scoped match works.
        #   4. Falls back to opening the more-actions menu (turn-scoped),
        #      then clicking the labelled item.
        # IMPORTANT: must be a SYNC IIFE, not ``async``. ``execute_js``
        # in the MV3 isolated world drops the suspension point at the
        # first ``await``, so the post-await code never runs and the
        # side-channel span is never written. ``enable_tool`` uses the
        # same constraint and works because it never awaits. We mimic
        # that here: kick off the click chain synchronously, and use
        # ``setInterval`` to poll for the menu item asynchronously
        # WITHOUT any function-level await keyword. The polling
        # callback runs on the page's event loop independently of our
        # IIFE's call stack.
        js = (
            "(function () {\n"
            "  var regenSels = " + regen_sels_json + ";\n"
            "  var moreSels = " + more_sels_json + ";\n"
            "  var labels = " + labels_json + ";\n"
            "  function findAssistantTurn() {\n"
            "    var sels = ['[data-message-author-role=\"assistant\"]', '[data-testid*=\"conversation-turn\"]', 'article', 'message-content', '[data-test-id*=\"response\"]'];\n"
            "    for (var i = 0; i < sels.length; i++) {\n"
            "      var els = document.querySelectorAll(sels[i]);\n"
            "      if (els.length) return els[els.length - 1];\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "  function fireHoverOn(node) {\n"
            "    if (!node) return;\n"
            "    try {\n"
            "      ['mouseover','mouseenter','pointerover','pointerenter'].forEach(function (t) {\n"
            "        node.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, view: window}));\n"
            "      });\n"
            "    } catch (e) {}\n"
            "  }\n"
            "  function isVisible(n) {\n"
            "    if (!n) return false;\n"
            "    var r = n.getBoundingClientRect();\n"
            "    return r.width > 0 && r.height > 0;\n"
            "  }\n"
            "  function findScopedFirst(roots, selectors) {\n"
            "    for (var s = 0; s < selectors.length; s++) {\n"
            "      for (var r = 0; r < roots.length; r++) {\n"
            "        if (!roots[r]) continue;\n"
            "        var matches = roots[r].querySelectorAll(selectors[s]);\n"
            "        for (var k = 0; k < matches.length; k++) {\n"
            "          if (isVisible(matches[k])) return matches[k];\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "  function clickMenuItemByLabel() {\n"
            "    var nodes = Array.from(document.querySelectorAll('[role=\"menuitem\"], [role=\"menu\"] button, button, a'));\n"
            "    for (var i = 0; i < nodes.length; i++) {\n"
            "      var n = nodes[i];\n"
            "      if (!isVisible(n)) continue;\n"
            "      var t = (n.innerText || n.textContent || '').trim();\n"
            "      for (var j = 0; j < labels.length; j++) {\n"
            "        if (t.indexOf(labels[j]) !== -1) {\n"
            "          try { n.click(); return labels[j]; } catch (e) {}\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "  function recordResult(s) {\n"
            "    try {\n"
            "      // Channel 1: hidden span at body. Most reliable\n"
            "      // when present \u2014 dom.query reads textContent.\n"
            "      var tag = document.getElementById('pce-regen-result-tag');\n"
            "      if (!tag) {\n"
            "        tag = document.createElement('span');\n"
            "        tag.id = 'pce-regen-result-tag';\n"
            "        tag.style.cssText = 'position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;';\n"
            "        document.body.appendChild(tag);\n"
            "      }\n"
            "      tag.textContent = String(s);\n"
            "    } catch (e) {}\n"
            "    // Channel 2: body data attribute. Cheapest write,\n"
            "    // visible in body's outer_html_excerpt up to ~200\n"
            "    // chars in.\n"
            "    try { document.body.dataset.pceRegenResult = String(s); } catch (e) {}\n"
            "    // Channel 3: title attribute on documentElement.\n"
            "    // Always present, never react-managed, fits in\n"
            "    // outer_html_excerpt of <html>.\n"
            "    try { document.documentElement.setAttribute('data-pce-regen-result', String(s)); } catch (e) {}\n"
            "  }\n"
            "  recordResult('start');\n"
            "  var turn = findAssistantTurn();\n"
            "  if (!turn) { recordResult('no_assistant_turn'); return; }\n"
            "  fireHoverOn(turn);\n"
            "  fireHoverOn(turn.parentElement);\n"
            "  // 1. direct regen button (scoped to turn or its parent).\n"
            "  var roots = [turn, turn.parentElement, turn.parentElement && turn.parentElement.parentElement];\n"
            "  var btn = findScopedFirst(roots, regenSels);\n"
            "  if (btn) {\n"
            "    fireHoverOn(btn);\n"
            "    try { btn.click(); recordResult('direct:' + btn.outerHTML.slice(0, 80)); return; }\n"
            "    catch (e) { recordResult('direct_click_threw'); return; }\n"
            "  }\n"
            "  recordResult('no_direct_button');\n"
            "  // 2. more-actions menu within scope.\n"
            "  var more = findScopedFirst(roots, moreSels);\n"
            "  if (!more) { recordResult('no_direct_no_more'); return; }\n"
            "  fireHoverOn(more);\n"
            "  try { more.click(); } catch (e) { recordResult('click_more_threw'); return; }\n"
            "  recordResult('more_clicked_polling');\n"
            "  // 3. setInterval poll for the labelled menu item; no\n"
            "  //    await keyword so the IIFE stack stays alive.\n"
            "  var attempts = 0;\n"
            "  var t = setInterval(function () {\n"
            "    attempts++;\n"
            "    var hit = clickMenuItemByLabel();\n"
            "    if (hit) { clearInterval(t); recordResult('menu:' + hit); return; }\n"
            "    if (attempts >= 12) { clearInterval(t); recordResult('menu_no_match'); }\n"
            "  }, 150);\n"
            "})();"
        )
        try:
            probe.dom.execute_js(tab_id, js)
        except ProbeError:
            return False
        # Side-channel result lives in document.body.dataset.pceRegenResult.
        # Caller can read via dom.query when surfacing diag context.
        time.sleep(1.5)  # extra time for the menu-path scenario
        return True

    def enable_tool(
        self,
        probe: "ProbeClient",
        tab_id: int,
        *,
        button_selectors: Sequence[str],
        menu_labels: Sequence[str],
    ) -> str:
        """Toggle an advanced tool (T12 image / T13 code / T15 canvas).

        Strategy ladder (returns the first one that fires):

          1. ``"direct"``  \u2014 ``button_selectors`` matches a
             top-level toggle that's already visible on the composer
             (e.g. older ChatGPT UI's web-search pill, or a
             Gemini ``Tools`` chip). One ``dom.click`` is enough.
          2. ``"picker"``  \u2014 we open ``tool_picker_selectors``
             (the modern composer "+" button), wait briefly for the
             menu to render, and JS-click the first visible
             ``[role="menuitem"]`` / button whose ``innerText``
             contains any of ``menu_labels``. Required on
             ChatGPT 2026-Q2 paid accounts where the auto-router no
             longer fires Code Interpreter / Canvas / DALL\u00b7E
             from prompt text alone.
          3. ``"none"``  \u2014 nothing was clickable. Caller falls
             back to relying on the model's auto-routing (which still
             works on some accounts / model variants) and lets the
             post-send capture decide PASS / SKIP / FAIL.

        We deliberately do NOT raise on miss: the case-level decision
        tree handles "tool wasn't enabled" via the captured
        attachment shape, which is the authoritative signal.
        """
        # 1. Direct toggle.
        if button_selectors:
            try:
                if self.click_first_visible(probe, tab_id, button_selectors):
                    time.sleep(0.6)
                    return "direct"
            except ProbeError:
                pass
        # 2. Picker + labelled item.
        if not self.tool_picker_selectors or not menu_labels:
            return "none"
        try:
            opened = self.click_first_visible(
                probe, tab_id, self.tool_picker_selectors,
            )
        except ProbeError:
            opened = False
        if not opened:
            return "none"
        time.sleep(0.6)  # let the picker render
        labels_json = (
            "[" + ", ".join(_js_str(l) for l in menu_labels) + "]"
        )
        # The JS walks visible menuitem-shaped nodes and clicks the
        # first whose text contains any of the requested labels. We
        # use ``role=menuitem`` first (semantically correct), then
        # fall back to plain buttons within the recently-opened
        # popup.
        #
        # MV3 isolated worlds drop ``execute_js`` return values, so we
        # write the outcome to ``document.body.dataset.pceEnableTool``
        # as a side channel. The caller reads it back via dom.query
        # to surface the actual match label / available item count
        # in case details. This matters for diagnostics when the
        # picker clicks succeed but the labels don't match anything
        # (wrong menu opened, or label list is stale).
        js = (
            "(function () {\n"
            "  var labels = " + labels_json + ";\n"
            "  var sels = ['[role=\"menuitem\"]','[role=\"option\"]','button','a'];\n"
            "  var visible_items = [];\n"
            "  var matched = null;\n"
            "  for (var s = 0; s < sels.length && !matched; s++) {\n"
            "    var nodes = Array.from(document.querySelectorAll(sels[s]));\n"
            "    for (var i = 0; i < nodes.length && !matched; i++) {\n"
            "      var n = nodes[i];\n"
            "      var rect = n.getBoundingClientRect();\n"
            "      if (rect.width <= 0 || rect.height <= 0) continue;\n"
            "      var t = (n.innerText || n.textContent || '').trim();\n"
            "      if (!t) continue;\n"
            "      if (visible_items.length < 24) visible_items.push(t.slice(0, 40));\n"
            "      for (var j = 0; j < labels.length; j++) {\n"
            "        if (t.indexOf(labels[j]) !== -1) {\n"
            "          try { n.click(); matched = labels[j]; break; }\n"
            "          catch (e) {}\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "  try {\n"
            "    document.body.dataset.pceEnableTool = JSON.stringify({\n"
            "      matched: matched, items: visible_items, n_items: visible_items.length\n"
            "    });\n"
            "  } catch (e) {}\n"
            "})();"
        )
        try:
            probe.dom.execute_js(tab_id, js)
        except ProbeError:
            return "none"
        time.sleep(0.8)  # let the toggle settle / pill render
        return "picker"

    def upload_file_via_input(
        self,
        probe: "ProbeClient",
        tab_id: int,
        *,
        file_name: str,
        file_b64: str,
        media_type: str,
        image: bool = False,
    ) -> bool:
        """Attach a file/image via the page's ``<input type=file>``.

        Mimics the legacy autopilot's ``send_keys(path)`` but works
        through the probe's CDP-via-content-script bridge:

          1. Resolve the input selector \u2014
             ``image_input_selectors`` if ``image`` is true and that
             tuple is non-empty, otherwise ``file_input_selectors``.
             Empty \u2014 return False (case SKIPs).
          2. ``dom.execute_js`` builds a ``Blob`` from the base64
             bytes, wraps it in a ``File`` with the supplied name and
             type, packs it into a ``DataTransfer``, assigns to
             ``input.files``, and dispatches ``input``+``change``.

        Some sites (Gemini Quill, Claude TipTap) bind their upload
        handler to a paste/drop event on the contenteditable, NOT to
        a file input ``change``. Those sites need a different path
        and will surface as case-level FAIL with an instructive
        message rather than a corrupting silent skip.
        """
        selectors = (
            self.image_input_selectors
            if image and self.image_input_selectors
            else self.file_input_selectors
        )
        if not selectors:
            return False
        sels_json = "[" + ", ".join(_js_str(s) for s in selectors) + "]"
        js = (
            "(function () {\n"
            "  var sels = " + sels_json + ";\n"
            "  var input = null;\n"
            "  for (var i = 0; i < sels.length; i++) {\n"
            "    input = document.querySelector(sels[i]);\n"
            "    if (input) break;\n"
            "  }\n"
            "  if (!input) return;\n"
            "  var b64 = " + _js_str(file_b64) + ";\n"
            "  var bin = atob(b64);\n"
            "  var bytes = new Uint8Array(bin.length);\n"
            "  for (var k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);\n"
            "  var blob = new Blob([bytes], { type: " + _js_str(media_type) + " });\n"
            "  var file = new File([blob], " + _js_str(file_name) + ", { type: " + _js_str(media_type) + " });\n"
            "  try {\n"
            "    var dt = new DataTransfer();\n"
            "    dt.items.add(file);\n"
            "    input.files = dt.files;\n"
            "  } catch (e) {}\n"
            "  input.dispatchEvent(new Event('input', { bubbles: true }));\n"
            "  input.dispatchEvent(new Event('change', { bubbles: true }));\n"
            "})();"
        )
        try:
            probe.dom.execute_js(tab_id, js)
        except ProbeError:
            return False
        time.sleep(1.5)  # let the upload chip mount
        return True

    def current_url(
        self,
        probe: "ProbeClient",
        tab_id: int,
    ) -> Optional[str]:
        """Public accessor for the tab's current URL."""
        return self._get_tab_url(probe, tab_id)

    # ---- conveniences ----------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} name={self.name!r} url={self.url!r}>"


def _js_str(value: str) -> str:
    """Encode a Python string as a JavaScript string literal.

    Naive ``json.dumps(value)`` would also work, but it imports a heavy
    module per call; this is enough for the simple identifier-and-text
    strings the helpers above pass through.
    """
    return (
        '"'
        + value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t")
        + '"'
    )
