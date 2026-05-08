# SPDX-License-Identifier: Apache-2.0
"""T19 \u2014 error banner does NOT leak into a capture as assistant content.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 21 / T19, ``CLAUDE`` C20,
``GEMINI`` G19, ``GAS`` A20. When the page renders a generic
"Something went wrong" / safety / quota banner, PCE must NOT capture
that banner as an assistant message. The negative-capture invariant
mirrors T20's: a known-bad surface produces zero NEW PCE_CAPTUREs.

Approach (without ``Network.emulateNetworkConditions``):
  - Use a per-site ``error_trigger_prompt`` to provoke a banner.
  - Wait for any ``error_banner_selectors`` to render.
  - Diff PCE Core's session count + the SW's recent PCE_CAPTURE
    ring against the pre-trigger baseline. Any new capture whose
    assistant content contains banner text fails the case; zero
    new captures (or new captures whose assistant content is the
    user's original prompt only) is the PASS shape.

Sites with empty ``error_banner_selectors`` or empty
``error_trigger_prompt`` SKIP with a note. We don't paper over the
gap by guessing a trigger \u2014 a wrong trigger that succeeds is
indistinguishable from a real "captures clean" PASS.

Failure modes T19 catches:
  - Banner text leaks into ``content_text`` of a NEW assistant
    message (the surface 21 regression).
  - Capture pipeline goes silent on errored requests (acceptable
    in the spec) but still injects the banner via fingerprint
    re-render of the existing assistant turn (the more subtle
    regression).
"""
from __future__ import annotations

import time
import uuid

import httpx

from pce_probe import (
    ProbeClient,
    ProbeError,
)

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


class T19ErrorStateCase(BaseCase):
    id = "T19"
    name = "error_banner_does_not_leak_to_assistant"
    description = (
        "Trigger a known error surface; assert no NEW PCE_CAPTURE "
        "contains the error-banner text as assistant content."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.error_banner_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has no error_banner_selectors; T19 cannot "
                "verify silence without knowing what banner shape to "
                "look for. Without offline emulation we don't have a "
                "reliable cross-site trigger \u2014 sites that need "
                "T19 must wire selectors + a per-site trigger prompt.",
                duration_ms=self._elapsed_ms(start),
            )
        if not adapter.error_trigger_prompt:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has error_banner_selectors but no "
                "error_trigger_prompt; cannot reliably provoke an "
                "error in a one-shot pass without offline emulation",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T19-{uuid.uuid4().hex[:8]}"
        prompt = adapter.error_trigger_prompt + f" Reference: {token}."
        details: dict = {"token": token, "prompt": prompt}

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

        # 2. Snapshot baselines so we can detect "new capture" /
        #    "new session" deltas after the trigger.
        try:
            baseline_ring = (
                probe.capture.recent_events(
                    last_n=200, provider=adapter.provider,
                ).get("events", [])
                or []
            )
        except ProbeError:
            baseline_ring = []
        baseline_event_count = len(baseline_ring)
        try:
            sess_resp = pce_core.get(
                "/api/v1/sessions",
                params={"provider": adapter.provider},
            )
            baseline_session_count = (
                len(sess_resp.json() or []) if sess_resp.status_code == 200 else 0
            )
        except httpx.HTTPError:
            baseline_session_count = 0
        details["baseline_event_count"] = baseline_event_count
        details["baseline_session_count"] = baseline_session_count

        # 3. Send the trigger prompt.
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

        # 4. Wait for the banner. We give it a generous window because
        #    rate-limit / safety errors arrive ~5-15 s after submit.
        banner_visible = False
        banner_text = ""
        deadline = time.time() + 25.0
        while time.time() < deadline:
            for sel in adapter.error_banner_selectors:
                if adapter._selector_visible(probe, tab_id, sel):
                    banner_visible = True
                    # Try to capture banner text for the leak assertion.
                    try:
                        res = probe.dom.query(tab_id, sel, all=True)
                        for m in res.get("matches", []) or []:
                            text = str(m.get("text_excerpt") or "")
                            if text and len(text) > len(banner_text):
                                banner_text = text
                    except ProbeError:
                        pass
                    break
            if banner_visible:
                break
            time.sleep(0.5)
        details["banner_visible"] = banner_visible
        details["banner_text"] = banner_text[:200]

        if not banner_visible:
            return CaseResult.skipped(
                self.id, adapter.name,
                "error_trigger_prompt did not produce any of the "
                "configured banner selectors within 25s. The trigger "
                "may have been answered normally on this account / "
                "model variant; the test invariant wasn't exercised.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "banner_not_triggered"},
            )

        # 5. Hold for 8 s so any debounced re-fingerprint has a chance
        #    to fire the spurious capture we want to NOT see.
        time.sleep(8.0)

        # 6. Check the SW ring for new PCE_CAPTUREs. We allow new
        #    captures (the user's prompt itself may legitimately have
        #    been captured before the error fired) but those captures
        #    must NOT have the banner text in their assistant body.
        try:
            ring = (
                probe.capture.recent_events(
                    last_n=400, provider=adapter.provider,
                ).get("events", [])
                or []
            )
        except ProbeError:
            ring = []
        new_events = ring[baseline_event_count:] if len(ring) >= baseline_event_count else ring
        new_pce_captures = [
            e for e in new_events if e.get("kind") == "PCE_CAPTURE"
        ]
        details["new_pce_capture_count"] = len(new_pce_captures)

        # 7. The banner text should not appear in any new capture's
        #    assistant body. Use a substring of the banner (first 40
        #    chars) since some sites paginate / truncate banners.
        banner_needle = banner_text.strip()[:40]
        details["banner_needle"] = banner_needle
        leaked = []
        if banner_needle:
            for ev in new_pce_captures:
                # Look at multiple possible body containers.
                bodies: list = []
                for k in ("body", "body_excerpt", "summary"):
                    v = ev.get(k)
                    if isinstance(v, str):
                        bodies.append(v)
                payload = ev.get("payload") or {}
                if isinstance(payload, dict):
                    for k in ("body", "body_excerpt", "content_text"):
                        v = payload.get(k)
                        if isinstance(v, str):
                            bodies.append(v)
                joined = "\n".join(bodies)
                if banner_needle in joined:
                    leaked.append(ev)

        # 8. PCE Core check: any NEW assistant message with banner
        #    text in ``content_text`` is a hard fail. Sessions API
        #    is the authoritative store.
        try:
            sess_resp = pce_core.get(
                "/api/v1/sessions",
                params={"provider": adapter.provider},
            )
            current_sessions = (
                sess_resp.json() if sess_resp.status_code == 200 else []
            )
        except httpx.HTTPError:
            current_sessions = []
        # Inspect the latest few sessions (in case a brand-new one
        # was created for the trigger turn).
        recent_sessions = current_sessions[-3:] if current_sessions else []
        leaked_in_pce: list = []
        if banner_needle:
            for s in recent_sessions:
                sid = s.get("id")
                if not sid:
                    continue
                try:
                    mr = pce_core.get(f"/api/v1/sessions/{sid}/messages")
                    if mr.status_code != 200:
                        continue
                    for m in mr.json() or []:
                        if m.get("role") != "assistant":
                            continue
                        content = str(m.get("content_text") or "")
                        if banner_needle in content:
                            leaked_in_pce.append({
                                "session_id": sid,
                                "head": content[:160],
                            })
                except httpx.HTTPError:
                    continue
        details["leaked_in_pce_count"] = len(leaked_in_pce)
        details["leaked_in_ring_count"] = len(leaked)

        if leaked or leaked_in_pce:
            return CaseResult.failed(
                self.id, adapter.name,
                f"banner text leaked into a captured assistant message. "
                f"banner_needle={banner_needle!r}, "
                f"ring_leaks={len(leaked)}, pce_leaks={len(leaked_in_pce)}. "
                f"This is the G8 / surface-21 regression: error toast "
                f"is being captured as if it were the model's reply.",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "banner_leaked",
                    "leaked_in_pce": leaked_in_pce,
                },
            )

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"banner triggered + silenced; new_captures="
                f"{len(new_pce_captures)}, leaks=0"
            ),
            duration_ms=self._elapsed_ms(start),
            details=details,
        )
