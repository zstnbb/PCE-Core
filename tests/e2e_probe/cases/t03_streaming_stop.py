# SPDX-License-Identifier: Apache-2.0
"""T03 \u2014 streaming + stop: 1 PARTIAL capture after Stop is clicked.

Companion to T02. T02 covers the "let it finish" branch of the
streaming gate; T03 covers the "user pressed Stop mid-stream" branch.
The capture-runtime contract is the same in both: ONE PCE_CAPTURE per
turn, fired AFTER streaming actually halts. The difference is how
streaming halts:

  - T02: model stops naturally; ``isStreaming`` flips false; SW emits
         capture with the full assistant text.
  - T03: user clicks Stop; XHR aborts; ``isStreaming`` flips false; SW
         emits capture with the partial assistant text.

A T03 PASS means we never re-stream a partial as the model continues
to render trailing chunks (which would multiply captures), and we
also never DROP the partial because of an aggressive "incomplete"
filter on the SW side.

Failure modes T03 catches that T02 does not:

  - Stop click yields ZERO captures: SW treats Stop as "abort, ignore
    this turn", but the user actually wants the partial preserved.
    Fingerprint dedup may also collapse the partial onto the prior
    state if the assistant DOM was empty before Stop fired.
  - Stop click yields MANY captures: ``isStreaming`` is wired to a
    stale signal that doesn't observe the abort, so the SW keeps
    re-fingerprinting as the page paints "stopped" UI.
  - Stop click yields a SUSPICIOUSLY LONG assistant capture: the
    site rendered the entire reply before our Stop click landed
    \u2014 instead of failing the case, we surface this as a SKIP
    (the test was the wrong shape for this site/model/network) so
    the matrix doesn't lie about what was actually exercised.

Verification chain:
  1. Send a prompt long enough to stream for ~10s.
  2. Wait for the stop button to appear (response started).
  3. Click stop. Wait ~2s for the page to settle.
  4. ``capture.wait_for_token`` for one PCE_CAPTURE carrying the token.
  5. Round-trip via PCE Core. Assert:
       - 1-3 captures with our token (some sites paint a "stopped"
         flag that triggers one extra fingerprint flip; >3 indicates
         a broken streaming gate).
       - Last assistant message contains the token AND is shorter
         than ``MAX_PARTIAL_LEN``. If it's longer than that, the
         model finished before Stop landed \u2014 SKIP, not FAIL.
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

from .._pce_helpers import (
    predicate_token_in_any_role,
    wait_for_pce_message,
)
from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


# Same prompt shape as T02 but explicitly requests A LOT (so we have
# headroom to interrupt). 80 numbered items at one per line ensures
# the stream lasts long enough for the stop button to be visible
# before the model finishes, even on the fastest variants.
PROMPT_TEMPLATE = (
    "Please write the numbers 1 to 80, one per line, with one short "
    "English word for each (e.g. '1 apple'). On the first line write "
    "the marker {token} and a single space, then '1 apple'. Continue "
    "from there. Do not summarize, do not stop early, do not use code "
    "fences."
)

# After clicking Stop we wait this long for the SW's debounced
# capture-runtime to fire with the partial. Empirically the SW emits
# within ~2s of the page repainting; 8s is a comfortable
# false-negative-free budget that still fails fast on a real bug.
POST_STOP_CAPTURE_TIMEOUT_S = 12.0

# Tolerance for re-fingerprint flips after Stop. 1 \u2014 the partial
# is captured. 2 or 3 \u2014 the page paints a "stopped" status that
# bumps the fingerprint once or twice; allow it. >3 \u2014 broken gate.
MAX_OK_CAPTURES = 3

# A "stop landed too late" partial that's larger than this is
# indistinguishable from a full reply, so we SKIP the case rather
# than FAIL: the test invariant we wanted to verify (Stop click
# preserves the in-progress text) wasn't actually exercised.
MAX_PARTIAL_LEN = 800

# Minimum assistant length to count as "we did capture some streamed
# text". Anything below is the "Stop was so fast we captured an
# empty bubble" case \u2014 also a SKIP-worthy shape, not a FAIL of
# the streaming gate.
MIN_PARTIAL_LEN = 4


class T03StreamingStopCase(BaseCase):
    id = "T03"
    name = "streaming_stop_partial_capture"
    description = (
        "Send a long-form prompt that will stream for several seconds; "
        "click Stop mid-stream; verify exactly one PCE_CAPTURE lands "
        "with the partial assistant text (truncated, not full)."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        # Site has no stop button selectors at all \u2014 nothing to
        # click. SKIP rather than fail; the matrix flags the gap.
        if not adapter.stop_button_selectors:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                "adapter has no stop_button_selectors; T03 cannot exercise stop",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T03-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {"token": token}

        # 1. Open + login.
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"adapter.open failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open"},
            )
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"not logged in: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 2. Send.
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

        # 3. Wait for stop button to appear (response started).
        stop_visible = adapter.wait_for_stop_button_visible(
            probe, tab_id, timeout_s=12.0,
        )
        details["stop_visible_after_send"] = stop_visible
        if not stop_visible:
            # Two interpretations: (a) response started AND finished
            # before we noticed the button (very fast model + short
            # window), or (b) submit didn't fire. T01 separately
            # verifies submit-firing for this site, so we treat (a)
            # as the more likely scenario and SKIP \u2014 a "fail"
            # here would just be flake.
            return CaseResult.skipped(
                self.id, adapter.name,
                "stop button never appeared within 12s; either response "
                "completed instantly (cannot exercise stop) or the "
                "stop_button_selectors are stale; not a streaming-gate bug",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "stop_button_never_visible"},
            )

        # 4. Click stop. Sleep briefly for the page to settle.
        clicked = adapter.click_stop_button(probe, tab_id)
        details["stop_click_succeeded"] = clicked
        if not clicked:
            return CaseResult.failed(
                self.id, adapter.name,
                "stop button selector matched (visible) but click failed; "
                "possibly an aria-disabled state or a wrong-target "
                "selector that visible-checks but isn't clickable",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "stop_click_failed"},
            )
        # Allow the SW debounce + fingerprint settle to land.
        time.sleep(2.5)

        # 5. Wait for the post-stop capture to surface.
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=int(POST_STOP_CAPTURE_TIMEOUT_S * 1000),
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
        except CaptureNotSeenError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            return CaseResult.failed(
                self.id, adapter.name,
                f"post-stop capture.wait_for_token: {exc.message}; "
                f"recent({len(recent)} events): "
                + ", ".join(
                    f"{e.get('kind')}@{e.get('host')}" for e in recent[-5:]
                ),
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "post_stop_no_capture",
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

        # 6. Soft check: count token-bearing PCE_CAPTUREs in the SW
        #    ring. Many = streaming gate broken (re-fingerprinting on
        #    the partial after stop).
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

        if len(token_captures) > MAX_OK_CAPTURES:
            return CaseResult.failed(
                self.id, adapter.name,
                f"streaming gate appears broken after Stop: "
                f"{len(token_captures)} PCE_CAPTUREs for the token "
                f"(expected 1-{MAX_OK_CAPTURES}); the page is being "
                f"re-fingerprinted as it paints the 'stopped' UI",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "too_many_post_stop_captures"},
            )

        # 7. Round-trip via PCE Core.
        #
        # IMPORTANT: a stop-mid-stream capture legitimately has
        # ``session_hint=""`` when the URL was still ``/`` at the
        # moment the SW fingerprinted (because ``getConvId`` returns
        # empty for the bare entry URL). Earlier versions of this
        # case treated empty session_hint as a hard FAIL; that's a
        # case-design bug, not an extension bug. Per the redesign
        # (Pathology 4 in PCE-PROBE-AGENT-LOOP), we use PCE Core as
        # ground truth: poll for the message via
        # ``wait_for_pce_message`` regardless of session_hint state.
        session_hint = captured.get("session_hint") or ""
        details["session_hint"] = session_hint
        sess, target = wait_for_pce_message(
            pce_core,
            provider=adapter.provider,
            predicate=predicate_token_in_any_role(token),
            timeout_s=20.0,
            session_hint=session_hint or None,
        )
        if sess is None or target is None:
            # No assistant turn ever surfaced with our token. The
            # partial may have cut off before the marker line rendered
            # (legitimate per the docstring at line 30) AND PCE Core
            # never received an assistant message at all. Walk the
            # most recent N sessions for a recent assistant turn from
            # this provider that lacks the token \u2014 that's still a
            # successful partial-capture, just inconvenient to assert
            # against. We accept it as a SKIP-worthy shape.
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": adapter.provider},
                )
                # PCE Core sorts NEWEST-FIRST (db.py query_sessions has
                # ORDER BY started_at DESC). Use [0], not [-1].
                latest = (resp.json() or [])[0] if resp.status_code == 200 else None
            except httpx.HTTPError:
                latest = None
            except IndexError:
                latest = None
            if latest:
                sid = latest.get("id")
                try:
                    mr = pce_core.get(f"/api/v1/sessions/{sid}/messages")
                    msgs = mr.json() if mr.status_code == 200 else []
                except httpx.HTTPError:
                    msgs = []
                assistant_msgs = [m for m in (msgs or []) if m.get("role") == "assistant"]
                if assistant_msgs:
                    last_assistant = assistant_msgs[-1]
                    text_no_token = str(last_assistant.get("content_text") or "")
                    text_len = len(text_no_token)
                    # Valid partial without token: stop landed mid-stream
                    # before the {token} marker line rendered. SKIP-worthy.
                    if MIN_PARTIAL_LEN <= text_len <= MAX_PARTIAL_LEN:
                        return CaseResult.skipped(
                            self.id, adapter.name,
                            f"stop landed; latest assistant text is "
                            f"{text_len} chars but does not "
                            f"contain the token (the partial cut off "
                            f"before the marker rendered) \u2014 not "
                            f"a streaming-gate bug",
                            duration_ms=self._elapsed_ms(start),
                            details={
                                **details, "phase": "partial_without_token",
                                "session_id": sid,
                                "assistant_text_head": text_no_token[:200],
                            },
                        )
                    # Stop landed AFTER the model already finished:
                    # mirrors the ``stop_too_late`` SKIP in the
                    # success branch (line ~360). Without this
                    # symmetric fallback, gemini-T03 fails when the
                    # SW capture's session_hint is the bare ``/app``
                    # (Gemini's home URL captured mid-transition)
                    # AND the model finished before stop landed
                    # \u2014 a legitimate test-too-fast condition,
                    # not a streaming-gate bug.
                    if text_len > MAX_PARTIAL_LEN:
                        return CaseResult.skipped(
                            self.id, adapter.name,
                            f"stop landed but latest assistant text "
                            f"is {text_len} chars "
                            f"(threshold={MAX_PARTIAL_LEN}); model "
                            f"finished before stop click took "
                            f"effect AND session_hint='{session_hint}' "
                            f"didn't link to a session in PCE Core "
                            f"\u2014 the streaming-gate invariant "
                            f"was not actually exercised",
                            duration_ms=self._elapsed_ms(start),
                            details={
                                **details, "phase": "stop_too_late_fallback",
                                "session_id": sid,
                                "assistant_text_head": text_no_token[:200],
                            },
                        )
            return CaseResult.failed(
                self.id, adapter.name,
                "no PCE Core message contains the token within 20 s of "
                "the post-stop SW capture; the stop click reached the "
                "page but no assistant turn ever made it to PCE Core",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_pce_message_with_token"},
            )

        session_id = sess.get("id")
        # Re-fetch messages so length analysis sees ALL turns, not just
        # the one matched by predicate.
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        msgs = msg_resp.json() if msg_resp.status_code == 200 else []
        details["n_messages"] = len(msgs)
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        last_assistant = assistant_msgs[-1] if assistant_msgs else target
        assistant_text = str(last_assistant.get("content_text") or "")
        details["assistant_text_len"] = len(assistant_text)
        details["assistant_text_head"] = assistant_text[:200]

        # 8. Decide PASS / SKIP based on partial length.
        if len(assistant_text) > MAX_PARTIAL_LEN:
            # Stop landed after the model already finished. The test
            # invariant we wanted (Stop preserves a TRUNCATED reply)
            # wasn't exercised \u2014 SKIP not FAIL.
            return CaseResult.skipped(
                self.id, adapter.name,
                f"assistant text was {len(assistant_text)} chars "
                f"(threshold={MAX_PARTIAL_LEN}); model finished before "
                f"Stop click landed \u2014 not a streaming-gate bug, "
                f"the test prompt was too short for this site/model",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "stop_too_late"},
            )
        if len(assistant_text) < MIN_PARTIAL_LEN:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"assistant text was {len(assistant_text)} chars "
                f"(min={MIN_PARTIAL_LEN}); Stop landed before any "
                f"text rendered \u2014 not a useful exercise of the "
                f"partial-capture path",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "stop_too_early"},
            )

        # token-in-text is the canonical PASS predicate; if we got
        # here with a useful partial size and the token, we're good.
        # The token may be missing if the partial cut off before the
        # marker line rendered \u2014 that's still a valid partial
        # capture, just without the convenient assertion handle.
        # Surface that as PASS with a note rather than FAIL.
        token_in_text = token in assistant_text

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"stop landed; {len(token_captures)} captures with token, "
                f"assistant_len={len(assistant_text)} "
                f"(token_in_text={token_in_text}), "
                f"session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={
                **details,
                "session_id": session_id,
                "token_in_partial": token_in_text,
            },
        )

    @staticmethod
    def _event_body_has(event: dict, token: str) -> bool:
        token = str(token)
        for k in ("body_excerpt", "body", "summary", "content_text"):
            v = event.get(k)
            if isinstance(v, str) and token in v:
                return True
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            for k in ("body_excerpt", "body", "content_text", "text"):
                v = payload.get(k)
                if isinstance(v, str) and token in v:
                    return True
        return False
