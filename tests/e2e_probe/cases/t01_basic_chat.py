# SPDX-License-Identifier: Apache-2.0
"""T01 — basic prompt-and-capture round trip.

The canonical end-to-end probe case. Validates the full pipeline:

    pytest -> probe.dom.type -> chrome.scripting -> page input
    -> AI streams response -> content_script captures
    -> chrome.runtime.sendMessage -> background.ts handleCapture
    -> POST /api/v1/captures -> PCE Core ingest -> sqlite
    -> probe.capture.wait_for_token resolves -> pytest assertions
    -> GET /api/v1/sessions, /api/v1/sessions/{id}/messages -> assert.

A unique-token prompt makes the assertion exact: every (site, T01)
case knows the token it just typed and asserts that exact token shows
up in the captured ``content_text``. No string-similarity, no
near-matches.

A T01 PASS means the **whole vertical works** for this site: the
content_script normalizes correctly, the background pipeline forwards
correctly, PCE Core stores correctly, and the session_key wiring is
right. A T01 FAIL is interesting evidence; the pytest output includes
the agent_hint from the probe, the recent capture events, and the
PCE Core sessions/messages JSON.
"""
from __future__ import annotations

import time
import uuid

import httpx

from pce_probe import (
    CaptureNotSeenError,
    ProbeClient,
    ProbeError,
    SelectorNotFoundError,
)

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the token {token}. "
    "Reply in one short line, no formatting, no quotes."
)


class T01BasicChatCase(BaseCase):
    id = "T01"
    name = "basic_chat_token_roundtrip"
    description = (
        "Send one prompt containing a unique token; observe one "
        "PCE_CAPTURE row that carries the token through the extension; "
        "verify the row landed in PCE Core's session/messages API."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        token = f"PROBE-{adapter.name.upper()}-T01-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {"token": token, "prompt": prompt}

        # 1. Open + login check (don't replicate T00 logic here; we
        #    rely on T00 having already greenlit the adapter, but we
        #    still need a tab and a login validation per case because
        #    pytest tears down between cases).
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"adapter.open failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open", "code": exc.code},
            )

        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                f"not logged in / input not reachable: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 2. Send the prompt.
        try:
            adapter.send_prompt(probe, tab_id, prompt)
        except SelectorNotFoundError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"send_prompt: selector_not_found ({exc.message}); "
                f"agent_hint={exc.agent_hint!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "send_prompt", "code": exc.code},
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "send_prompt", "code": exc.code},
            )

        # 3. Wait for the capture pipeline to observe the token.
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=adapter.response_timeout_ms,
                provider=adapter.provider,
            )
        except CaptureNotSeenError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            recent_summary = ", ".join(
                f"{e.get('kind')}@{e.get('host')}"
                for e in recent[-5:]
            )
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"capture.wait_for_token timeout for {token!r}; "
                f"hint={exc.agent_hint!r}; "
                f"recent({len(recent)}): {recent_summary}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "wait_for_token",
                    "recent_events": recent[-10:],
                },
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"capture.wait_for_token: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_token", "code": exc.code},
            )

        details["captured"] = captured
        session_hint = captured.get("session_hint")
        if not session_hint:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "captured event had no session_hint; check "
                "service_worker normalization for this provider",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "session_hint_missing"},
            )

        # 4. Verify via PCE Core HTTP API.
        try:
            sessions_resp = pce_core.get(
                "/api/v1/sessions",
                params={"provider": adapter.provider},
            )
        except httpx.HTTPError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"PCE Core /api/v1/sessions request failed: {exc!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "sessions_request"},
            )
        if sessions_resp.status_code != 200:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"PCE Core /api/v1/sessions returned {sessions_resp.status_code}: "
                f"{sessions_resp.text[:200]}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "sessions_status"},
            )
        sessions = sessions_resp.json() or []
        matching = [
            s for s in sessions
            if str(s.get("session_key", "")).find(str(session_hint)) != -1
        ]
        if not matching:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"no PCE Core session matched session_hint={session_hint!r}; "
                f"capture observer saw the token but row didn't land in "
                f"storage \u2014 check ingest path",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "session_match",
                    "session_hint": session_hint,
                    "n_sessions": len(sessions),
                },
            )

        session_id = matching[0].get("id")
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        if msg_resp.status_code != 200:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"PCE Core /api/v1/sessions/{session_id}/messages "
                f"returned {msg_resp.status_code}: {msg_resp.text[:200]}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "messages_status"},
            )
        msgs = msg_resp.json() or []
        if not any(token in (m.get("content_text") or "") for m in msgs):
            preview = [
                (m.get("role"), (m.get("content_text") or "")[:80])
                for m in msgs
            ]
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"session {session_id} has {len(msgs)} messages but none "
                f"contain token {token!r}; preview={preview!r}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "token_in_messages",
                    "session_id": session_id,
                    "n_messages": len(msgs),
                },
            )

        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=(
                f"token {token} round-tripped through capture + ingest; "
                f"session_id={session_id}, n_messages={len(msgs)}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={
                **details,
                "session_id": session_id,
                "n_messages": len(msgs),
            },
        )
