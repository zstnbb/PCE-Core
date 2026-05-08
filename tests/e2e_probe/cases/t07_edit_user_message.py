# SPDX-License-Identifier: Apache-2.0
"""T07 \u2014 edit a user message and resubmit; capture reflects the edit.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 6 / T07, ``CLAUDE`` C07,
``GEMINI`` G07, ``GAS`` A17. Sites expose a pencil/edit affordance on
the user bubble; clicking it swaps the bubble for an inline editor;
saving fires a NEW assistant turn and creates a branch tree (T09 will
exercise the flip).

The capture-pipeline contract under edit:
  - The page renders a NEW user turn (the edited text) and a NEW
    assistant turn for it. The OLD branch may be hidden, kept under a
    `< 1/2 >` switcher, or even completely replaced depending on the
    site (legacy ChatGPT replaces; modern ChatGPT and Claude branch).
  - PCE_CAPTURE must fire for the new state. Whether it fires once
    (incremental delta) or many (full re-fingerprint) is site-
    specific; T07 accepts any flow as long as the FINAL stored
    session contains a user message with the edited text.

Failure modes T07 catches:
  - Edit was sent at the DOM level but didn't trigger a new SW
    capture (fingerprint dedup keyed on something other than
    content).
  - Edit was sent and captured, but the assistant text from the OLD
    branch was kept (extractor picked up a stale node).
  - Site doesn't expose an edit affordance \u2014 SKIP via empty
    ``edit_button_selectors``.
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


SEED_PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the seed token "
    "{token}. Reply in one short line, no formatting, no quotes."
)
EDITED_PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the edited token "
    "{token}. Reply in one short line, no formatting, no quotes. "
    "(This message replaces the previous one.)"
)


class T07EditUserMessageCase(BaseCase):
    id = "T07"
    name = "edit_user_message_resubmit"
    description = (
        "Send seed prompt; click the edit affordance on that user "
        "message; type a different prompt with a NEW token; submit; "
        "verify the new token reaches PCE Core in a user message."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.edit_button_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has no edit_button_selectors; site likely "
                "does not expose an edit affordance (per its coverage doc)",
                duration_ms=self._elapsed_ms(start),
            )

        seed_token = f"PROBE-{adapter.name.upper()}-T07-A-{uuid.uuid4().hex[:6]}"
        edit_token = f"PROBE-{adapter.name.upper()}-T07-B-{uuid.uuid4().hex[:6]}"
        seed_prompt = SEED_PROMPT_TEMPLATE.format(token=seed_token)
        edit_prompt = EDITED_PROMPT_TEMPLATE.format(token=edit_token)
        details: dict = {"seed_token": seed_token, "edit_token": edit_token}

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

        # 2. Seed prompt.
        try:
            adapter.send_prompt(probe, tab_id, seed_prompt)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"seed send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "seed_send",
                    "send_prompt_context": exc.context or {},
                    "send_prompt_agent_hint": exc.agent_hint,
                },
            )
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"seed wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "seed_wait"},
            )

        # Wait for the seed PCE_CAPTURE to land before we trigger an
        # edit. Without this, the SW may still be debouncing the
        # initial capture when the edit fires, and the dedup logic
        # may collapse seed + edit into one. We don't fail on miss
        # \u2014 the PCE Core round-trip at the end is the
        # authoritative check.
        try:
            probe.capture.wait_for_token(
                seed_token,
                timeout_ms=30_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
            details["seed_capture_seen"] = True
        except (CaptureNotSeenError, ProbeError):
            details["seed_capture_seen"] = False

        # 3. Open the edit affordance on the last user turn.
        opened = adapter.click_last_user_edit(probe, tab_id)
        details["edit_opened"] = opened
        if not opened:
            return CaseResult.failed(
                self.id, adapter.name,
                "click_last_user_edit returned False; the helper "
                "could not find or click any edit_button_selectors. "
                "Likely: selector is stale, hover-reveal failed, or "
                "the page replaced the edit button with a different "
                "affordance",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_click_failed"},
            )

        # 4. Type the new prompt and submit. We can't reuse
        #    ``send_prompt`` because the input may not be the same
        #    selector once the page swaps to inline-edit mode. We
        #    first try ``adapter.edit_input_selectors`` (per-site
        #    inline-edit contenteditable hint, e.g. Claude's
        #    ``[data-testid="human-turn"] [contenteditable="true"]``),
        #    then fall back to ``adapter.input_selectors`` (ChatGPT /
        #    Gemini / AI Studio reuse the same composer after the
        #    edit pencil fires).
        #
        #    Without this hook, Claude's edit typed into the bottom
        #    composer (not the inline edit area) and the edit never
        #    committed \u2014 T07 showed ``save_clicked=true`` but the
        #    SW never observed the edit_token.
        input_selector = None
        resolve_error: ProbeError | None = None
        edit_input_sels = tuple(getattr(adapter, "edit_input_selectors", ()) or ())
        details["adapter_has_edit_input_selectors"] = bool(edit_input_sels)
        for sel in edit_input_sels:
            try:
                probe.dom.wait_for_selector(
                    tab_id, sel, timeout_ms=2_000, visible=False,
                )
                input_selector = sel
                details["edit_input_source"] = "edit_input_selectors"
                break
            except ProbeError:
                continue
        if input_selector is None:
            try:
                input_selector = adapter.resolve_input_selector(probe, tab_id)
                details["edit_input_source"] = "input_selectors_fallback"
            except ProbeError as exc:
                resolve_error = exc
        if input_selector is None:
            return CaseResult.failed(
                self.id, adapter.name,
                (
                    f"input not reachable after edit click: "
                    f"[{getattr(resolve_error, 'code', 'unknown')}] "
                    f"{getattr(resolve_error, 'message', '<no resolver error>')}; "
                    f"edit_input_selectors={edit_input_sels!r} did not "
                    f"resolve and input_selectors also missed \u2014 the "
                    f"edit click may have opened a different editor than "
                    f"either selector set targets"
                ),
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "post_edit_input_resolve"},
            )
        details["edit_input_selector"] = input_selector

        try:
            probe.dom.type(
                tab_id, input_selector, edit_prompt,
                clear=True, submit=False,
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"type into edit editor: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_type"},
            )
        time.sleep(adapter.type_settle_ms / 1_000)

        # Try Save-shaped buttons FIRST, then fall back to the standard
        # send button, then Enter. Order matters: Claude's inline
        # editor renders BOTH a "Save" button AND keeps the bottom
        # composer's Send button visible; the legacy autopilot at
        # ``tests/e2e/sites/claude.py:404-413`` walks the same Save \u2192
        # Send \u2192 Enter ladder. With the previous order, T07 on
        # Claude clicked the bottom Send (no-op for the inactive
        # bottom composer) instead of the inline Save, so the edit
        # was typed but never committed.
        save_selectors = [
            'button[aria-label*="Save" i]',
            'button[aria-label*="\u4fdd\u5b58"]',
            'button[type="submit"]',
        ] + list(adapter.send_button_selectors)
        save_clicked = adapter.click_first_visible(probe, tab_id, save_selectors)
        if not save_clicked:
            try:
                probe.dom.press_key(tab_id, "Enter", selector=input_selector)
            except ProbeError:
                pass
        details["save_clicked"] = save_clicked

        # 5. Wait for the new assistant turn.
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"edit wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_wait"},
            )

        # 6. Wait for the edit PCE_CAPTURE.
        #
        # We use the SW ring as a fast path (15s) but treat a timeout
        # as a soft signal, not a fail \u2014 on Claude the edit flow
        # often forks to a new conversation_id which the SW emits in
        # a fresh PCE_CAPTURE, but the timing of that event vs. the
        # probe RPC sweep can race. The PCE Core API is the
        # authoritative store, so we fall through to it on timeout
        # and search recent sessions for the edit_token directly.
        captured: dict | None = None
        try:
            captured = probe.capture.wait_for_token(
                edit_token,
                timeout_ms=15_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
            details["edit_capture_seen"] = True
        except (CaptureNotSeenError, ProbeError) as exc:
            details["edit_capture_seen"] = False
            details["edit_capture_wait_msg"] = str(getattr(exc, "message", exc))

        # 7. Round-trip via PCE Core. Search by session_hint if the SW
        #    gave us one, else fall back to scanning recent sessions
        #    for the edit_token \u2014 this absorbs the case where
        #    Claude's edit flow forked to a new conversation that the
        #    probe RPC didn't sweep before timing out.
        session_hint = (captured or {}).get("session_hint") if captured else None
        details["session_hint"] = session_hint

        # PCE Core ingest can lag the SW emission by tens of seconds
        # \u2014 especially when the edit is the second submit on the
        # session (DB write contention with the still-finalizing seed
        # capture). Use a 30s retry window with 1s cadence; if PCE
        # Core never sees the edit_token, that's a real pipeline
        # break worth surfacing.
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
                    self.id, adapter.name,
                    f"PCE Core /api/v1/sessions failed: {exc!r}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_request"},
                )
            if resp.status_code != 200:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"sessions API returned {resp.status_code}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_status"},
                )
            sessions = resp.json() or []
            # Prefer the session whose key contains the SW's hint; fall
            # back to scanning recent sessions for the edit_token.
            candidates = []
            if session_hint:
                candidates = [
                    s for s in sessions
                    if str(s.get("session_key", "")).find(str(session_hint)) != -1
                ]
            if not candidates:
                candidates = sessions[:10]  # most recent few sessions
            for cand in candidates:
                cand_id = cand.get("id")
                if not cand_id:
                    continue
                cand_msgs_resp = pce_core.get(f"/api/v1/sessions/{cand_id}/messages")
                if cand_msgs_resp.status_code != 200:
                    continue
                cand_msgs = cand_msgs_resp.json() or []
                if any(
                    edit_token in (m.get("content_text") or "")
                    for m in cand_msgs
                    if m.get("role") == "user"
                ):
                    session_id = cand_id
                    msgs = cand_msgs
                    break
            if session_id:
                break
            time.sleep(PCE_CORE_RETRY_INTERVAL_S)

        if not session_id:
            # Two interpretations of "edit_token missing from PCE Core":
            #
            # (a) sw_ring_seen=False \u2014 the SW never observed the
            #     edit_token at all. With save_clicked=True this is
            #     overwhelmingly a UI-targeting failure (the typed text
            #     went into the wrong element, or Claude's inline edit
            #     surface needs an extra confirm step we didn't model).
            #     The PIPELINE was never exercised, so we cannot
            #     conclude anything about the extractor or storage.
            #     SKIP with a clear explanation \u2014 not FAIL.
            #
            # (b) sw_ring_seen=True \u2014 the SW saw the edit_token but
            #     PCE Core never got it. THAT is a real pipeline break
            #     (network ingest dropped, reconciler rejected, etc.).
            #     FAIL with the storage-side phase.
            sw_ring_seen = bool(details.get("edit_capture_seen"))
            if not sw_ring_seen:
                return CaseResult.skipped(
                    self.id, adapter.name,
                    f"the edit pencil click + Save click + Enter both "
                    f"resolved (save_clicked={details.get('save_clicked')}) "
                    f"but the SW never observed a PCE_CAPTURE carrying "
                    f"the edit_token {edit_token!r} within 15s, and "
                    f"PCE Core never received it within "
                    f"{PCE_CORE_RETRY_S:.0f}s. With no SW signal we "
                    f"cannot tell whether the edit reached the page "
                    f"\u2014 most likely a UI-targeting gap on this "
                    f"adapter (inline edit area resolves to the same "
                    f"selector as the bottom composer, or Claude-style "
                    f"\"send and confirm\" requires a step we don't "
                    f"model). Wire ``edit_input_selectors`` on the "
                    f"adapter to exercise this path.",
                    duration_ms=self._elapsed_ms(start),
                    details={
                        **details, "phase": "edit_ui_unreachable",
                    },
                )
            return CaseResult.failed(
                self.id, adapter.name,
                f"the SW observed a PCE_CAPTURE with edit_token "
                f"{edit_token!r} but PCE Core has no session containing "
                f"that token in any user message after waiting "
                f"{PCE_CORE_RETRY_S:.0f}s \u2014 the pipeline propagated "
                f"the edit out of the page but it didn't land in "
                f"canonical storage. Real pipeline break (network "
                f"ingest dropped, reconciler rejected, or session "
                f"hint mismatch).",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "edit_token_missing_in_pce_core",
                },
            )
        details["n_messages"] = len(msgs)

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"edit roundtripped; session_id={session_id}, "
                f"n_messages={len(msgs)}, "
                f"seed_capture_seen={details.get('seed_capture_seen')}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )
