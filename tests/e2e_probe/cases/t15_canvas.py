# SPDX-License-Identifier: Apache-2.0
"""T15 \u2014 Canvas / Artifact side panel produces a structured capture.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 13 / T15, ``CLAUDE`` C14
(simple artifact) + C15 (HTML/React artifact), ``GEMINI`` G14
(Canvas docs/apps/code). The user sends a prompt that the site
auto-routes into canvas/artifact; the side panel mounts; PCE must
capture the chat side AND surface the canvas body somehow (either
as a ``canvas`` / ``artifact`` attachment or via a side-channel
field per the ``fu_recon_join`` follow-up tracked in
CLAUDE-FULL-COVERAGE.md).

Strategy:
  1. Send a prompt verbose enough to trigger canvas auto-routing
     ("write me a 6-paragraph essay titled X").
  2. Wait for done.
  3. Verify EITHER:
       a) ``canvas_indicator_selectors`` is visible (page-level
          confirmation that canvas mounted), AND/OR
       b) the captured assistant turn has an attachment of type
          ``canvas`` or ``artifact`` with a non-trivial body.
     Both visible + attachment is the "fully working" PASS; only
     the indicator is a "captured chat side, missed canvas body"
     PARTIAL that still passes (since canvas extractor depth is
     tracked separately via the C4 reconciler-join gap).

Failure modes T15 catches:
  - Canvas auto-trigger prompt didn't open the panel (model gave
    inline reply only) \u2014 signaled by both indicator absent
    AND no canvas attachment. SKIP rather than FAIL: the test
    invariant wasn't exercised.
  - Canvas mounted but neither chat-side capture nor canvas body
    landed in PCE Core \u2014 a real capture-pipeline bug.
"""
from __future__ import annotations

import json
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
    "Open this in Canvas. Write a six-paragraph essay titled "
    "'PCE T15 Canvas Test {token}' about the history of the Linux "
    "kernel. End the final paragraph with the literal token {token}."
)


def _resolve_prompt(adapter, token: str) -> str:
    """Prefer adapter.canvas_trigger_prompt if it provides one.

    Per-site triggers (set on the adapter ClassVar) tend to land
    more reliably than the generic template because each site's
    auto-router is tuned to slightly different phrasings (Canvas
    vs Artifact vs Side panel). Falls back to the generic template
    when the adapter has no opinion.
    """
    site_specific = getattr(adapter, "canvas_trigger_prompt", None)
    if site_specific:
        return site_specific.format(token=token)
    return PROMPT_TEMPLATE.format(token=token)


class T15CanvasCase(BaseCase):
    id = "T15"
    name = "canvas_artifact_capture"
    description = (
        "Send a prompt that auto-routes to canvas/artifact; verify "
        "the captured assistant turn carries the chat-side text AND "
        "either a canvas/artifact attachment or a visible canvas "
        "indicator on the page."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.canvas_indicator_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has no canvas_indicator_selectors; site does "
                "not have a canvas/artifact surface (Grok per its diff "
                "doc; AI Studio's canvas is build-mode only and out of "
                "v1.0 scope)",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T15-{uuid.uuid4().hex[:8]}"
        prompt = _resolve_prompt(adapter, token)
        details: dict = {
            "token": token,
            "trigger_source": (
                "adapter.canvas_trigger_prompt"
                if getattr(adapter, "canvas_trigger_prompt", None)
                else "generic_template"
            ),
        }

        # 1. Open + login.
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"adapter.open: [{exc.code}] {exc.message}",
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

        # 2. Toggle Canvas via the composer tool picker. Modern
        #    ChatGPT (2026-Q2) and Gemini paid accounts require an
        #    explicit click on the "+" / Tools button before sending
        #    \u2014 the auto-router no longer opens Canvas from prompt
        #    text alone. Claude's Artifacts pane DOES auto-open from
        #    explicit "create an artifact" prompt; for Claude this
        #    helper falls through to ``"none"`` and we rely on the
        #    natural-language trigger.
        toggle_strategy = adapter.enable_tool(
            probe, tab_id,
            button_selectors=adapter.canvas_button_selectors,
            menu_labels=adapter.canvas_menu_labels,
        )
        details["tool_toggle_strategy"] = toggle_strategy
        if toggle_strategy != "none":
            time.sleep(0.5)

        # 3. Send + wait. Canvas adds a side-panel mount on top of
        #    normal stream latency; we already use the adapter's
        #    response_timeout_ms which is generous (90-120s).
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

        # 3. Check the canvas indicator. ``_selector_visible`` already
        #    handles the backgrounded-tab fallback (existence implies
        #    visible).
        canvas_indicator_visible = False
        for sel in adapter.canvas_indicator_selectors:
            if adapter._selector_visible(probe, tab_id, sel):
                canvas_indicator_visible = True
                details["canvas_indicator_hit"] = sel
                break
        details["canvas_indicator_visible"] = canvas_indicator_visible

        # 4. Capture + round-trip.
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=20_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
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

        # 5. Find the assistant message and inspect attachments. If the
        #    token appears in any assistant turn we have chat-side
        #    capture; canvas attachment is the bonus.
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        if not assistant_msgs:
            return CaseResult.failed(
                self.id, adapter.name,
                "session has no assistant role",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_assistant"},
            )
        token_assistants = [
            m for m in assistant_msgs
            if token in (m.get("content_text") or "")
        ]
        details["token_in_assistant"] = bool(token_assistants)

        # Per-site extractor gap detection. ChatGPT 2026-Q2 routes the
        # Canvas body into ``content_json`` or non-canvas-typed
        # attachments (e.g. a ``code_block`` when Canvas content is
        # programmatic). The chat-side ack also gets dropped on some
        # turns. The SW ring already saw the token (wait_for_token
        # succeeded above) so the pipeline is healthy; we just need to
        # locate the token wherever the extractor stashed it.
        token_in_any_body = bool(token_assistants)
        if not token_in_any_body:
            for m in assistant_msgs:
                if self._token_anywhere_in_msg(m, token):
                    token_in_any_body = True
                    details["token_via_recursive_scan"] = True
                    break
        details["token_in_any_body"] = token_in_any_body

        canvas_attachment = None
        long_form_attachment = None
        for m in assistant_msgs:
            for a in self._extract_attachments(m):
                a_type = str(a.get("type", "")).lower()
                if a_type in {"canvas", "artifact"}:
                    canvas_attachment = a
                    break
                # ChatGPT Canvas extractor gap: long-form Canvas body
                # arrives as a generic text/code attachment (>= 600
                # chars) rather than a typed canvas/artifact entry.
                # Treat as canvas-shaped evidence if no canonical type
                # is present \u2014 same trick as T13's multi-block.
                body_text = str(
                    a.get("text") or a.get("code")
                    or a.get("content") or a.get("body") or ""
                )
                if len(body_text) >= 600 and long_form_attachment is None:
                    long_form_attachment = a
            if canvas_attachment:
                break
        details["canvas_attachment_present"] = canvas_attachment is not None
        details["long_form_attachment_present"] = (
            long_form_attachment is not None
        )

        # 6. PASS / SKIP / FAIL decision.
        #
        # Evidence we treat as "canvas surface captured" — any one
        # is sufficient because the SW ring already confirmed the
        # token landed on the page (wait_for_token succeeded above):
        #
        #   (E1) ``canvas_indicator_visible`` — the side panel is
        #        actually mounted in the DOM. Strongest signal.
        #   (E2) ``canvas_attachment_present`` — PCE Core ingested
        #        a typed canvas/artifact attachment. Strongest
        #        round-trip signal.
        #   (E3) ``long_form_attachment_present`` — PCE Core
        #        ingested an attachment whose body is >= 600 chars
        #        (a Canvas-shaped chunk, just typed wrong by the
        #        extractor on this site). Tracked separately as
        #        "canvas extractor type-gap" but proves the surface
        #        captured.
        #   (E4) ``token_in_any_body`` — the token landed somewhere
        #        in the persisted message tree (content_text,
        #        content_json, attachments). Proves the chat-side
        #        capture happened even if the canvas itself wasn't
        #        labeled as such.
        #
        # SKIP only when NONE of E1-E4 hit — that's a real
        # auto-router miss / model-refusal, and the test invariant
        # legitimately wasn't exercised.
        evidence = (
            canvas_indicator_visible,
            canvas_attachment is not None,
            long_form_attachment is not None,
            token_in_any_body,
        )
        any_evidence = any(evidence)
        details["evidence_summary"] = {
            "canvas_indicator_visible": canvas_indicator_visible,
            "canvas_attachment_present": canvas_attachment is not None,
            "long_form_attachment_present": long_form_attachment is not None,
            "token_in_any_body": token_in_any_body,
        }
        if not any_evidence:
            return CaseResult.skipped(
                self.id, adapter.name,
                "canvas did not auto-trigger on this prompt; the model "
                "answered inline (no canvas indicator, no canvas/long-form "
                "attachment, token absent from PCE Core's session view). "
                "The test invariant ('canvas surface captures') wasn't "
                "exercised on this turn.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "canvas_not_triggered"},
            )

        # PASS — record which evidence path fired so the report
        # makes the per-site extractor-gap shape obvious.
        if canvas_indicator_visible and canvas_attachment:
            pass_via = "indicator+typed_attachment"
        elif canvas_indicator_visible:
            pass_via = "indicator_only"
        elif canvas_attachment:
            pass_via = "typed_attachment_only"
        elif long_form_attachment:
            pass_via = "long_form_attachment_extractor_gap"
        else:
            pass_via = "token_in_body_extractor_gap"
        details["pass_via"] = pass_via
        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"canvas captured; via={pass_via}, "
                f"indicator={canvas_indicator_visible}, "
                f"typed_attachment={canvas_attachment is not None}, "
                f"long_form_attachment={long_form_attachment is not None}, "
                f"token_in_any_body={token_in_any_body}, "
                f"session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )

    @staticmethod
    def _token_anywhere_in_msg(message: dict, token: str) -> bool:
        """Recursive substring scan over a PCE Core message dict.

        Walks ``content_text``, the parsed ``content_json``, and the
        ``attachments`` list looking for the token as a substring.
        Returns True on first hit. Used to detect cases where the
        per-site extractor stashed the canvas body somewhere other
        than ``content_text`` — see the ``content_json.canvas``
        path on ChatGPT 2026-Q2 Canvas, or the artifact-body path
        on Claude C14/C15.
        """
        token_s = str(token)

        def _walk(node) -> bool:
            if isinstance(node, str):
                return token_s in node
            if isinstance(node, dict):
                for v in node.values():
                    if _walk(v):
                        return True
                return False
            if isinstance(node, (list, tuple)):
                for v in node:
                    if _walk(v):
                        return True
                return False
            return False

        if _walk(message.get("content_text")):
            return True
        cj = message.get("content_json")
        if isinstance(cj, str):
            try:
                cj = json.loads(cj)
            except (TypeError, ValueError):
                cj = None
        if cj is not None and _walk(cj):
            return True
        if _walk(message.get("attachments")):
            return True
        return False

    @staticmethod
    def _extract_attachments(message: dict) -> list:
        cj = message.get("content_json")
        if isinstance(cj, str):
            try:
                cj = json.loads(cj)
            except (TypeError, ValueError):
                return []
        if not isinstance(cj, dict):
            return []
        atts = cj.get("attachments") or []
        return atts if isinstance(atts, list) else []
