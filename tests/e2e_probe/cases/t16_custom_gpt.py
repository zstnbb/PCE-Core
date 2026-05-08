# SPDX-License-Identifier: Apache-2.0
"""T16 \u2014 Custom GPT route captures end-to-end with the right URL shape.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 14 / T16. Only ChatGPT
exposes a Custom GPT product surface today (Claude has no
equivalent; Gemini's Gems are a different surface tracked under
G15). The user opens a Custom GPT URL (``/g/<slug>`` or
``/g/<slug>/c/<uuid>``) and sends a vanilla prompt; PCE must capture
under that URL pattern.

URL source priority:
  1. Env var ``PCE_PROBE_<NAME>_GPT_URL`` \u2014 lets each test
     account use its own GPT without code changes.
  2. ``adapter.custom_gpt_url`` ClassVar \u2014 only set if the
     site has a stable public GPT we can rely on (currently none
     codified \u2014 ChatGPT GPTs are scoped to creators).
  3. Otherwise \u2014 SKIP with instructions on which env var to
     set.

Failure modes T16 catches:
  - Custom GPT chat path (``/g/<slug>/c/<uuid>``) not handled by
    ``getConvId`` regex (the G1 gap, supposedly closed; this case
    locks it).
  - Custom GPT URL not in extension matches (the G2 gap, also
    closed).
"""
from __future__ import annotations

import os
import time
import uuid

import httpx

from pce_probe import (
    CaptureNotSeenError,
    ProbeClient,
    ProbeError,
)

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the token {token}. "
    "Reply in one short line, no formatting."
)


class T16CustomGPTCase(BaseCase):
    id = "T16"
    name = "custom_gpt_route_captures"
    description = (
        "Open a Custom GPT URL provided via env var or adapter "
        "ClassVar; send a prompt; verify PCE Core captures under "
        "the expected URL shape."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        env_var = f"PCE_PROBE_{adapter.name.upper()}_GPT_URL"
        gpt_url = os.environ.get(env_var, "").strip() or adapter.custom_gpt_url
        details: dict = {"env_var": env_var, "gpt_url": gpt_url}
        if not gpt_url:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"no Custom GPT URL configured. Set ${env_var} to a "
                f"reachable Custom GPT URL on your account, or add "
                f"``custom_gpt_url`` to the adapter for a stable "
                f"public GPT. Site has no Custom GPT surface? "
                f"That's expected for non-ChatGPT adapters.",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T16-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details["token"] = token

        # 1. Open a fresh tab DIRECTLY at the GPT URL.
        try:
            res = probe.tab.open(gpt_url, active=True)
            tab_id = int(res["tab_id"])
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"tab.open custom GPT failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open_gpt"},
            )
        try:
            probe.tab.wait_for_load(
                tab_id, timeout_ms=adapter.page_load_timeout_ms,
            )
        except ProbeError:
            pass  # SPA never fires complete; check_logged_in arbitrates

        # Some Custom GPTs show a "Start chat" intro screen. Click any
        # generic "Start" / "New" / "继续" button if present (best
        # effort \u2014 the chat input may also be directly available).
        try:
            probe.dom.execute_js(
                tab_id,
                r"""
                (function () {
                  var labels = ["start chat", "new chat", "begin", "continue", "\u5f00\u59cb", "\u7ee7\u7eed"];
                  var btns = Array.from(document.querySelectorAll('button, a'));
                  for (var i = 0; i < btns.length; i++) {
                    var text = (btns[i].innerText || btns[i].textContent || "").trim().toLowerCase();
                    for (var j = 0; j < labels.length; j++) {
                      if (text.indexOf(labels[j]) !== -1) {
                        try { btns[i].click(); return; } catch (e) {}
                      }
                    }
                  }
                })();
                """,
            )
        except ProbeError:
            pass
        time.sleep(1.0)

        # 2. Login check.
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"not logged in on GPT tab: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 3. Send + wait + capture.
        try:
            adapter.send_prompt(probe, tab_id, prompt)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "send_prompt",
                    "send_prompt_context": exc.context or {},
                    "send_prompt_agent_hint": exc.agent_hint,
                },
            )
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_done"},
            )

        try:
            captured = probe.capture.wait_for_token(
                token, timeout_ms=20_000,
                provider=adapter.provider, kind="PCE_CAPTURE",
            )
        except CaptureNotSeenError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            return CaseResult.failed(
                self.id, adapter.name,
                f"capture.wait_for_token: {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "no_capture",
                    "agent_hint": exc.agent_hint,
                    "recent_events": recent[-10:],
                },
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"capture.wait_for_token: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_token_error"},
            )

        # 4. Verify URL shape: the captured event's session_hint must
        #    derive from a /g/<slug> path (not the bare entry URL).
        cur_url = adapter.current_url(probe, tab_id) or ""
        details["current_url"] = cur_url
        details["session_hint"] = captured.get("session_hint")
        if "/g/" not in cur_url:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"current URL {cur_url!r} does not contain ``/g/`` "
                f"\u2014 the GPT URL likely redirected away from the "
                f"Custom GPT surface (free account hitting paywall, "
                f"or the GPT URL was stale). Test invariant not "
                f"exercised.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "url_lost_g_segment"},
            )

        # 5. Round-trip via PCE Core.
        session_hint = captured.get("session_hint")
        if not session_hint:
            return CaseResult.failed(
                self.id, adapter.name,
                "captured event had no session_hint",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "session_hint_missing"},
            )
        sessions: list = []
        matching: list = []
        for _ in range(6):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": adapter.provider},
                )
            except httpx.HTTPError as exc:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"sessions request: {exc!r}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_request"},
                )
            if resp.status_code != 200:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"sessions API {resp.status_code}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_status"},
                )
            sessions = resp.json() or []
            matching = [
                s for s in sessions
                if str(s.get("session_key", "")).find(str(session_hint)) != -1
            ]
            if matching:
                break
            time.sleep(0.5)
        if not matching:
            return CaseResult.failed(
                self.id, adapter.name,
                f"no PCE session matched session_hint={session_hint!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "session_match_missed"},
            )

        session_id = matching[0].get("id")
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        if msg_resp.status_code != 200:
            return CaseResult.failed(
                self.id, adapter.name,
                f"messages API {msg_resp.status_code}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "messages_status"},
            )
        msgs = msg_resp.json() or []
        if not any(token in (m.get("content_text") or "") for m in msgs):
            return CaseResult.failed(
                self.id, adapter.name,
                f"session has no message with token {token!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "token_missing"},
            )

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"custom GPT roundtripped; current_url={cur_url}, "
                f"session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )
