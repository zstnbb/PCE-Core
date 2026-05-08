# SPDX-License-Identifier: Apache-2.0
"""T17 \u2014 Project chat captures end-to-end with the right URL shape.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 15 / T17 (ChatGPT
``/project/<id>`` and ``/project/<id>/c/<uuid>``), ``CLAUDE`` C13
(Claude ``/project/<id>`` + nested ``/chat/<uuid>``). Gemini's
equivalent is "Workspace" / NotebookLM-folded, deferred to v1.2;
AI Studio and Grok have no Project surface.

URL source priority:
  1. Env var ``PCE_PROBE_<NAME>_PROJECT_URL`` \u2014 lets each test
     account use its own Project.
  2. ``adapter.project_url`` ClassVar \u2014 only set if the site
     has a stable public Project URL we can rely on (currently none).
  3. Otherwise \u2014 SKIP.

Failure modes T17 catches:
  - Project chat path not matched by ``getConvId`` regex (the C3
    Projects-URL gap on Claude, the G1 family gap on ChatGPT \u2014
    both empirically resolved via 2026-04-23 / 2026-04-26 P5.B
    rounds; this case locks them).
  - Project URL recognized but ``session_hint`` not extracted to
    the right shape (e.g. captures under a generic ``/`` hint
    instead of the project-scoped UUID).
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
    "Reply in one short line."
)


class T17ProjectChatCase(BaseCase):
    id = "T17"
    name = "project_chat_route_captures"
    description = (
        "Open a Project URL provided via env var or adapter "
        "ClassVar; send a prompt; verify PCE Core captures with a "
        "session_hint scoped to the Project URL pattern."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        env_var = f"PCE_PROBE_{adapter.name.upper()}_PROJECT_URL"
        project_url = os.environ.get(env_var, "").strip() or adapter.project_url
        details: dict = {"env_var": env_var, "project_url": project_url}
        if not project_url:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"no Project URL configured. Set ${env_var} to a "
                f"reachable Project URL on your account, or add "
                f"``project_url`` to the adapter for a stable public "
                f"Project. Site has no Project surface? That's "
                f"expected for AI Studio / Grok adapters.",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T17-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details["token"] = token

        # 1. Open the project URL.
        try:
            res = probe.tab.open(project_url, active=True)
            tab_id = int(res["tab_id"])
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"tab.open project failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open_project"},
            )
        try:
            probe.tab.wait_for_load(
                tab_id, timeout_ms=adapter.page_load_timeout_ms,
            )
        except ProbeError:
            pass

        # Some Project pages list multiple chats and don't auto-focus
        # the input; we try to click any "New chat" button. Best
        # effort \u2014 check_logged_in + send_prompt are the
        # authoritative gates.
        try:
            probe.dom.execute_js(
                tab_id,
                r"""
                (function () {
                  var labels = ["new chat", "new conversation", "start", "\u65b0\u5efa\u804a\u5929", "\u5f00\u59cb"];
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

        # 2. Login + send + wait + capture.
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"not logged in on project tab: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

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

        # 3. URL must still contain ``/project`` after submit \u2014 a
        #    session_hint that derives from the bare entry URL would
        #    indicate the conv-id regex didn't match the project
        #    pattern.
        cur_url = adapter.current_url(probe, tab_id) or ""
        details["current_url"] = cur_url
        details["session_hint"] = captured.get("session_hint")
        if "/project" not in cur_url:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"current URL {cur_url!r} does not contain "
                f"``/project`` \u2014 the project URL redirected.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "url_lost_project_segment"},
            )

        # 4. Round-trip.
        session_hint = captured.get("session_hint")
        if not session_hint:
            return CaseResult.failed(
                self.id, adapter.name,
                "captured event had no session_hint; "
                "Project URL pattern likely not in the regex",
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
                f"project chat roundtripped; current_url={cur_url}, "
                f"session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )
