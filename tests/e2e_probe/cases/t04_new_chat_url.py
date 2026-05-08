# SPDX-License-Identifier: Apache-2.0
"""T04 \u2014 new-chat URL upgrade: entry URL flips to chat-id URL on submit.

Most chat sites land the user on a generic entry URL (``/``, ``/new``,
``/app``) and only mint a stable conversation ID after the first user
message. T04 verifies this transition is observable and that the
extension's ``getSessionHint`` extracts the right ID from the upgraded
URL.

Failure modes T04 catches that T01 cannot:

  - **session_url_pattern wrong** \u2014 the regex doesn't match the
    upgraded URL shape, so PCE Core stores ``session_key`` as a
    ``_new_<...>`` placeholder forever and history dedup is impossible.
  - **No URL flip** \u2014 the page submits to the same URL (rare but
    happens on grok x.com embedded surface, AI Studio's
    ``/prompts/new_chat``); we want to KNOW this and let the per-site
    ``session_url_pattern`` design absorb it. T04 PASS confirms the
    pattern is wired; SKIP confirms the site uses a stable URL where
    ID lives elsewhere (header, body).
  - **Hash-only navigation** \u2014 some SPAs flip ``#`` after the
    path, which doesn't fire ``hookHistoryApi`` listeners. The probe
    sees the new URL but the SW does not, so captures stay tagged
    with the entry URL.

Idempotent: opens a fresh tab. Relies on T01 already being green for
this site (``send_prompt`` works); a T04 FAIL is independent of T01
and points specifically at the URL-pattern wiring.
"""
from __future__ import annotations

import time
import uuid

import httpx

from pce_probe import ProbeClient, ProbeError

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the token {token}. "
    "Reply in one short line, no formatting, no quotes."
)


class T04NewChatUrlCase(BaseCase):
    id = "T04"
    name = "new_chat_url_upgrade"
    description = (
        "Submit a prompt from the site's entry URL; verify the tab "
        "URL flips to a per-conversation URL and that the adapter's "
        "session_url_pattern extracts a non-empty session hint from "
        "the new URL."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if adapter.session_url_pattern is None:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                "adapter has no session_url_pattern; this site uses a "
                "stable URL or the ID lives outside the URL \u2014 not "
                "applicable to T04",
                duration_ms=self._elapsed_ms(start),
                details={"phase": "skip_no_pattern"},
            )

        token = f"PROBE-{adapter.name.upper()}-T04-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {"token": token}

        # 1. Open + login.
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"adapter.open failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open"},
            )
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                f"not logged in: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 2. Snapshot pre-submit URL. The base adapter's
        #    ``_get_tab_url`` returns ``None`` on probe errors; we want
        #    a real string here so we can compare equality below.
        pre_url = self._get_url(probe, tab_id)
        if not pre_url:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "could not read tab URL via tab.list",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "pre_url_unreadable"},
            )
        details["pre_url"] = pre_url

        # If the entry URL ALREADY matches session_url_pattern, the
        # site doesn't do a flip on first submit (e.g. AI Studio's
        # ``/prompts/new_chat`` already matches ``/prompts/<id>``).
        # That's not a T04 failure \u2014 it's a different design;
        # skip with evidence.
        if adapter.session_hint_from_url(pre_url):
            return CaseResult.skipped(
                self.id,
                adapter.name,
                f"entry URL {pre_url!r} already matches "
                f"session_url_pattern; this site doesn't perform a "
                f"new-chat URL flip \u2014 not applicable to T04",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "skip_entry_already_session"},
            )

        # 3. Send. We rely on send_prompt's verify-fired path to
        #    confirm submit landed; we don't wait_for_done because
        #    T04's only assertion is the URL change, not the response
        #    content.
        try:
            adapter.send_prompt(probe, tab_id, prompt)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "send_prompt",
                    "send_prompt_context": exc.context or {},
                },
            )

        # 4. Poll for URL change. send_prompt's verify-fired pass
        #    already gives the site a chance to flip the URL; we still
        #    poll briefly here because some sites (Claude) flip the
        #    URL slightly AFTER the stop button appears, and
        #    send_prompt returns as soon as ANY one of the three
        #    submit signals fires (which may have been "stop button
        #    appeared" rather than "URL changed").
        deadline = time.time() + 8.0
        post_url = pre_url
        while time.time() < deadline:
            cur = self._get_url(probe, tab_id)
            if cur and cur != pre_url:
                post_url = cur
                break
            time.sleep(0.4)
        details["post_url"] = post_url

        if post_url == pre_url:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"URL did not change after submit (still {pre_url!r}); "
                f"this site may submit in-place \u2014 reconsider "
                f"session_url_pattern design",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_url_flip"},
            )

        # 5. The new URL must yield a session hint via the adapter's
        #    pattern. This is the actual regression target: a passing
        #    T04 means PCE Core can correlate this conversation by
        #    its hint downstream.
        hint = adapter.session_hint_from_url(post_url)
        details["session_hint"] = hint
        if not hint:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"URL flipped to {post_url!r} but session_url_pattern "
                f"({adapter.session_url_pattern.pattern!r}) did not "
                f"extract a hint; pattern likely needs an update",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "hint_extract_failed"},
            )

        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=(
                f"URL flipped {pre_url!r} \u2192 {post_url!r}; "
                f"session_hint={hint!r}"
            ),
            duration_ms=self._elapsed_ms(start),
            details=details,
        )

    @staticmethod
    def _get_url(probe: ProbeClient, tab_id: int) -> str:
        """Return the current tab URL. Empty string on any failure.

        Mirrors ``BaseProbeSiteAdapter._get_tab_url`` but returns ""
        instead of ``None`` so equality comparisons stay sane.
        """
        try:
            tabs = probe.tab.list().get("tabs", []) or []
        except ProbeError:
            return ""
        for t in tabs:
            if t.get("id") == tab_id:
                return str(t.get("url") or "")
        return ""
