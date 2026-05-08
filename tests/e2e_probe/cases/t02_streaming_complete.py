# SPDX-License-Identifier: Apache-2.0
"""T02 \u2014 streaming completion: 1 capture AFTER stream ends, not partials.

T01 already round-trips one prompt. T02's job is to verify the
**streaming gate** in ``utils/capture-runtime.ts`` actually suppresses
mid-stream captures: the SW must hold its capture until the model has
finished streaming, then emit ONE PCE_CAPTURE with the complete reply.

Failure modes T02 catches that T01 cannot:

  - ``isStreaming`` gate broken \u2014 PCE_CAPTURE fires every few hundred
    ms during stream, each with a partial body. T01 still passes
    because at least one partial happens to contain the token.
  - ``mutationStableFor`` window too short \u2014 capture fires before
    the assistant has finished, so the persisted assistant text is
    truncated to the first sentence.
  - Per-site selector mismatch \u2014 ``response_container_selectors``
    matches a non-streaming chrome element, so ``wait_for_done`` falls
    through immediately and we capture an empty assistant.

Verification strategy:

  1. Issue a deliberately verbose prompt (4 bullet points + literal
     marker on its own line). Long-form replies are >= 200 chars even
     for terse models, so a partial capture truncates measurably.
  2. ``wait_for_done`` blocks until the site's stop button cycle
     completes (or the response-text size stays stable for
     ``response_stability_ms``).
  3. ``capture.wait_for_token`` with a short post-stream budget
     verifies the SW saw the token. Without ``kind="PCE_CAPTURE"`` we
     would race the network interceptor (see T01 for the full
     reasoning).
  4. Round-trip through PCE Core's session/messages API and assert:
       - the last assistant message contains the token, and
       - it is at least ``MIN_ASSISTANT_LEN`` chars long (a partial
         capture truncated to "Sure, here are 4 bullets:" is ~30
         chars; we set the threshold above that).
  5. Soft check: ``capture.recent_events`` should report the
     PCE_CAPTURE count for our token in a sane range. We accept up to
     ``MAX_OK_CAPTURES`` because some sites legitimately re-fingerprint
     once after stream end (e.g. when citation chips render a tick
     after the main text). > MAX_OK_CAPTURES is the broken-gate
     signature and FAILS hard.

Idempotent: opens a fresh tab. Inherits T01's resilience around
``send_prompt`` (descriptive errors with full ``ProbeError.context``).
"""
from __future__ import annotations

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
    "Please reply with exactly four short bullet points about the "
    "history of the year 2025 in technology. Each bullet must be one "
    "sentence ending with a period. After the final bullet, on a new "
    "line, write only the marker {token} and stop. Do not add any "
    "extra commentary. Do not use code fences."
)

# Minimum assistant content length to accept as "complete stream".
# Empirically, even the tersest 4-bullet reply lands around 250-400
# chars; a partial-capture truncation is normally < 80 chars, so 80 is
# a comfortable false-negative-free threshold.
MIN_ASSISTANT_LEN = 80

# How many PCE_CAPTURE events with the token are tolerable. Streaming
# gate working well: 1. Some sites re-fingerprint legitimately once or
# twice after stream end (citation chip render, model-name pill
# settles): up to 5. Anything above is the broken-gate signature.
MAX_OK_CAPTURES = 5


class T02StreamingCompleteCase(BaseCase):
    id = "T02"
    name = "streaming_completion"
    description = (
        "Send a long-form prompt that streams for several seconds; "
        "verify the SW emits ONE PCE_CAPTURE after the stream ends "
        "(not many partial captures during stream), and that the "
        "final session has 1 user + 1 assistant turn with the token "
        "in a substantial assistant reply."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        token = f"PROBE-{adapter.name.upper()}-T02-{uuid.uuid4().hex[:8]}"
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

        # 2. Send.
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
                    "send_prompt_agent_hint": exc.agent_hint,
                },
            )

        # 3. Wait for stream completion (the new step beyond T01).
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_done"},
            )
        wait_done_ms = self._elapsed_ms(start)
        details["wait_done_ms"] = wait_done_ms

        # 4. Best-effort wait for the post-stream PCE_CAPTURE. The SW
        #    ring is a fast path that gives us the session_hint; we do
        #    NOT gate the case on it because Claude in particular can
        #    take 30+s to finalize its DOM mutation observer after the
        #    stream visibly ends, and that variance shouldn't fail the
        #    streaming-gate test. PCE Core round-trip below is the
        #    authoritative pass/fail signal.
        captured: dict | None = None
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=15_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
            details["captured_kind"] = captured.get("kind")
            details["sw_ring_seen"] = True
        except (CaptureNotSeenError, ProbeError) as exc:
            details["sw_ring_seen"] = False
            details["sw_ring_wait_msg"] = str(getattr(exc, "message", exc))

        # 5. Soft-check: count PCE_CAPTURE events for our token in the
        #    SW ring. Many = streaming gate broken. We tolerate zero
        #    here \u2014 if the SW ring lost the event but PCE Core
        #    has the row, the gate worked but observability lagged.
        try:
            ring = (
                probe.capture.recent_events(
                    last_n=200, provider=adapter.provider,
                ).get("events", [])
                or []
            )
        except ProbeError:
            ring = []
        token_captures = [
            e for e in ring
            if e.get("kind") == "PCE_CAPTURE"
            and self._event_body_has(e, token)
        ]
        details["token_capture_count"] = len(token_captures)
        details["total_post_events"] = len(ring)

        if len(token_captures) > MAX_OK_CAPTURES:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"streaming gate appears broken: {len(token_captures)} "
                f"PCE_CAPTURE events for the same token (expected "
                f"1-{MAX_OK_CAPTURES}); isStreaming gate or fingerprint "
                f"dedup is not suppressing mid-stream re-captures",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "too_many_captures"},
            )

        # 6. PCE Core round-trip \u2014 authoritative gate. Prefer the
        #    SW's session_hint; fall back to scanning recent sessions
        #    for the token. 30s retry window absorbs Claude's
        #    end-of-stream lag without masking real pipeline breaks.
        session_hint = (captured or {}).get("session_hint") if captured else None
        details["session_hint"] = session_hint

        PCE_CORE_RETRY_S = 30.0
        PCE_CORE_RETRY_INTERVAL_S = 1.0
        attempts = int(PCE_CORE_RETRY_S / PCE_CORE_RETRY_INTERVAL_S)
        session_id: str | None = None
        msgs: list = []
        for _attempt in range(attempts):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": adapter.provider},
                )
            except httpx.HTTPError as exc:
                return CaseResult.failed(
                    self.id,
                    adapter.name,
                    f"PCE Core /api/v1/sessions failed: {exc!r}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_request"},
                )
            if resp.status_code != 200:
                return CaseResult.failed(
                    self.id,
                    adapter.name,
                    f"sessions API returned {resp.status_code}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_status"},
                )
            sessions = resp.json() or []
            candidates = []
            if session_hint:
                candidates = [
                    s for s in sessions
                    if str(s.get("session_key", "")).find(str(session_hint)) != -1
                ]
            if not candidates:
                candidates = sessions[:10]
            for cand in candidates:
                cand_id = cand.get("id")
                if not cand_id:
                    continue
                cand_msgs_resp = pce_core.get(f"/api/v1/sessions/{cand_id}/messages")
                if cand_msgs_resp.status_code != 200:
                    continue
                cand_msgs = cand_msgs_resp.json() or []
                if any(
                    token in (m.get("content_text") or "")
                    for m in cand_msgs
                ):
                    session_id = cand_id
                    msgs = cand_msgs
                    break
            if session_id:
                break
            time.sleep(PCE_CORE_RETRY_INTERVAL_S)

        if not session_id:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"no PCE Core session contains the token {token!r} "
                f"after waiting {PCE_CORE_RETRY_S:.0f}s "
                f"(sw_ring_seen={details.get('sw_ring_seen')}); the "
                f"prompt was sent and stream completed but no row "
                f"reached canonical storage \u2014 pipeline break",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "token_missing_in_pce_core"},
            )
        details["n_messages"] = len(msgs)

        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        if not assistant_msgs:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"session {session_id} has {len(msgs)} messages but no "
                f"assistant role: roles={[m.get('role') for m in msgs]!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_assistant"},
            )
        last_assistant = assistant_msgs[-1]
        assistant_text = str(last_assistant.get("content_text") or "")
        details["assistant_text_len"] = len(assistant_text)
        details["assistant_text_head"] = assistant_text[:200]

        if token not in assistant_text:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"token {token!r} not in last assistant message; "
                f"head={assistant_text[:160]!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "token_missing"},
            )
        if len(assistant_text) < MIN_ASSISTANT_LEN:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"assistant text suspiciously short ({len(assistant_text)} "
                f"chars, threshold={MIN_ASSISTANT_LEN}); likely a partial "
                f"mid-stream capture instead of post-stream final; "
                f"text={assistant_text!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "assistant_too_short"},
            )

        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=(
                f"streamed in {wait_done_ms}ms, "
                f"{len(token_captures)} captures with token, "
                f"assistant_len={len(assistant_text)}, "
                f"session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )

    @staticmethod
    def _event_body_has(event: dict, token: str) -> bool:
        """Best-effort substring search for ``token`` in an event.

        The SW ring summary (see ``CaptureEventSummary`` in
        ``probe-rpc-capture.ts``) carries the body in the
        ``fingerprint`` field \u2014 first 200 chars of the stringified
        message body \u2014 NOT in ``body_excerpt`` / ``content_text``.
        Earlier versions checked the wrong keys; the soft "too many
        captures" gate then silently always saw zero captures, masking
        real streaming-gate breaks. We now check ``fingerprint`` first.
        """
        token = str(token)
        for k in ("fingerprint", "body_excerpt", "body", "summary", "content_text"):
            v = event.get(k)
            if isinstance(v, str) and token in v:
                return True
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            for k in ("fingerprint", "body_excerpt", "body", "content_text", "text"):
                v = payload.get(k)
                if isinstance(v, str) and token in v:
                    return True
        return False
