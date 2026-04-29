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
        timeout_ms: int = 5_000,
    ) -> str:
        """Return the first input selector that currently resolves.

        Raises ``SelectorNotFoundError`` if none do (rich error context
        comes back via probe).
        """
        for sel in self.input_selectors:
            try:
                probe.dom.wait_for_selector(tab_id, sel, timeout_ms=timeout_ms)
                return sel
            except SelectorNotFoundError:
                continue
        # Re-raise on the primary so the agent sees a probe-level
        # ``selector_not_found`` with full DOM excerpt.
        probe.dom.wait_for_selector(
            tab_id,
            self.input_selectors[0],
            timeout_ms=timeout_ms,
        )
        return self.input_selectors[0]

    def send_prompt(
        self,
        probe: ProbeClient,
        tab_id: int,
        prompt: str,
    ) -> None:
        """Type ``prompt`` into the input and submit.

        If the site declares a send button via
        ``send_button_selectors``, we type without submit then click
        the first matching button. Otherwise we submit via Enter
        (probe's ``dom.type(submit=True)`` dispatches a synthetic
        Enter keydown which works for all React/ProseMirror inputs we've
        observed).
        """
        selector = self.resolve_input_selector(probe, tab_id)
        if self.send_button_selectors:
            probe.dom.type(tab_id, selector, prompt, clear=True, submit=False)
            time.sleep(self.post_send_settle_ms / 1_000 / 2)
            self._click_send_button(probe, tab_id, fallback_input=selector)
        else:
            probe.dom.type(tab_id, selector, prompt, clear=True, submit=True)

    def _click_send_button(
        self,
        probe: ProbeClient,
        tab_id: int,
        *,
        fallback_input: str,
    ) -> None:
        for sel in self.send_button_selectors:
            try:
                probe.dom.click(tab_id, sel)
                return
            except SelectorNotFoundError:
                continue
            except ProbeError:
                continue
        # No send button found — fall back to Enter on the input.
        probe.dom.press_key(tab_id, "Enter", selector=fallback_input)

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
        try:
            res = probe.dom.query(tab_id, selector)
        except ProbeError:
            return False
        for m in res.get("matches", []) or []:
            if m.get("visible"):
                return True
        return False

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
