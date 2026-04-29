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

    # ---- conveniences ----------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} name={self.name!r} url={self.url!r}>"
