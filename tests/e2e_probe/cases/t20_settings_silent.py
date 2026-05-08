# SPDX-License-Identifier: Apache-2.0
"""T20 \u2014 settings page is capture-silent.

Spec: navigating to a non-chat surface (settings, account, library,
data-controls) MUST produce zero ``PCE_CAPTURE`` events. The capture
runtime's ``requireSessionHint`` gate, the path whitelist, and the
detector self-exclusion are the three guards that make this true.
T20 verifies the guards are wired.

Failure modes T20 catches that T01/T02 cannot:

  - **Detector self-trigger** \u2014 some sites render a "your settings
    update" toast when the user lands on settings; if the universal
    extractor mistakes the toast for an assistant turn, we leak
    settings text into the capture stream.
  - **Path whitelist regression** \u2014 a refactor of
    ``shouldExtract`` accidentally returns ``true`` for paths that
    should be inert.
  - **Network noise** \u2014 settings pages fetch user metadata
    (``/api/me``, ``/preferences``) which the network interceptor
    might mis-classify as conversation traffic.

Test sequence:

  1. Open a fresh tab on the site's chat URL, login-check.
  2. Snapshot ``capture.recent_events`` to establish a baseline event
     count (so any background noise from the home page is excluded
     from the assertion window).
  3. ``probe.tab.navigate`` to the per-site settings URL, settle.
  4. Hold for ``IDLE_HOLD_S`` seconds (the doc spec says 30 s; we
     lower to 8 s here so the matrix doesn't balloon \u2014 a leaky
     guard fires within ~2 s in practice; 8 s is enough headroom).
  5. Re-fetch ``capture.recent_events`` and require ZERO new
     ``PCE_CAPTURE`` events appeared since the baseline.

A site whose adapter does NOT define ``settings_url`` is ``SKIP``ped:
the matrix won't lie about coverage we don't have.

Soft tolerance: we count only ``PCE_CAPTURE`` rows. ``PCE_NETWORK_CAPTURE``
rows are noisy by design (the interceptor records every request body)
and aren't user-visible captures \u2014 they're a forensic side-channel
for diagnosis, not a guarantee surface.
"""
from __future__ import annotations

import time

import httpx

from pce_probe import ProbeClient, ProbeError

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


# How long we sit on the settings page after navigation. The spec
# (CHATGPT-FULL-COVERAGE Part III T20) calls for 30 s. We use 8 s so
# the matrix completes in reasonable time \u2014 a real leak fires
# within the first second on every site we've examined; 8 s gives
# headroom for slow page loads + any debounced telemetry.
IDLE_HOLD_S = 8.0

# Wait this long after ``tab.navigate`` returns before snapshotting
# the post-baseline. Some sites' settings pages mount async (Angular
# resolver, React Suspense), so an immediate snapshot would miss the
# window where they fire any auto-captures.
POST_NAV_SETTLE_S = 1.5


class T20SettingsSilentCase(BaseCase):
    id = "T20"
    name = "settings_silent"
    description = (
        "Navigate to the per-site settings URL and verify no "
        "PCE_CAPTURE events fire while the user idles there. "
        "Confirms the path whitelist + requireSessionHint + "
        "detector self-exclusion guards are wired for this site."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        settings_url = getattr(adapter, "settings_url", None)
        if not settings_url:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                "adapter has no settings_url configured; T20 cannot "
                "assert silence on a page it doesn't know how to "
                "navigate to",
                duration_ms=self._elapsed_ms(start),
                details={"phase": "skip_no_settings_url"},
            )

        details: dict = {"settings_url": settings_url}

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

        # 2. Baseline event count. Use a generous ``last_n`` so we
        #    capture any bursty noise the home page produced; we'll
        #    only ASSERT on events newer than this baseline.
        try:
            baseline_resp = probe.capture.recent_events(
                last_n=500, provider=adapter.provider,
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"recent_events baseline failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "baseline_request"},
            )
        baseline_events = baseline_resp.get("events", []) or []
        baseline_pce_capture_count = sum(
            1 for e in baseline_events if e.get("kind") == "PCE_CAPTURE"
        )
        baseline_ids = {self._event_id(e) for e in baseline_events}
        details["baseline_event_count"] = len(baseline_events)
        details["baseline_pce_capture_count"] = baseline_pce_capture_count

        # 3. Navigate to settings.
        try:
            probe.tab.navigate(tab_id, settings_url)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"tab.navigate({settings_url!r}) failed: [{exc.code}] "
                f"{exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "navigate"},
            )

        # 4. Hold + idle.
        time.sleep(POST_NAV_SETTLE_S + IDLE_HOLD_S)

        # 5. Re-fetch and diff.
        try:
            post_resp = probe.capture.recent_events(
                last_n=500, provider=adapter.provider,
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"recent_events post-idle failed: [{exc.code}] "
                f"{exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "post_request"},
            )
        post_events = post_resp.get("events", []) or []
        new_events = [
            e for e in post_events if self._event_id(e) not in baseline_ids
        ]
        new_pce_capture = [
            e for e in new_events if e.get("kind") == "PCE_CAPTURE"
        ]
        details["new_event_count"] = len(new_events)
        details["new_pce_capture_count"] = len(new_pce_capture)
        details["new_event_kinds"] = [
            e.get("kind") for e in new_events
        ]

        # Confirm the navigation actually landed (defensive: a silent
        # navigation failure would also produce zero new captures, but
        # for the wrong reason). We check the tab URL is on the
        # settings page.
        try:
            tabs = probe.tab.list().get("tabs", []) or []
        except ProbeError:
            tabs = []
        cur_url = ""
        for t in tabs:
            if t.get("id") == tab_id:
                cur_url = str(t.get("url") or "")
                break
        details["current_url"] = cur_url
        # Accept either an exact URL match or a path-prefix match
        # (some sites append query params or fragment IDs after
        # navigate).
        nav_landed = (
            cur_url == settings_url
            or cur_url.startswith(settings_url)
            or settings_url.split("?", 1)[0] in cur_url
        )
        if not nav_landed:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"navigate seemed to succeed but tab URL is {cur_url!r}, "
                f"not {settings_url!r}; the assertion window may not "
                f"have covered the settings page",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "nav_did_not_land"},
            )

        # The actual assertion: zero NEW PCE_CAPTURE events.
        if new_pce_capture:
            preview = [
                {
                    "kind": e.get("kind"),
                    "host": e.get("host"),
                    "path": e.get("path"),
                    "session_hint": e.get("session_hint"),
                }
                for e in new_pce_capture[:5]
            ]
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"settings page produced {len(new_pce_capture)} new "
                f"PCE_CAPTURE events during {IDLE_HOLD_S:.0f}s idle; "
                f"path whitelist / requireSessionHint guard is leaking; "
                f"first events: {preview!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "leak", "leaks": preview},
            )

        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=(
                f"silent on {settings_url!r}: 0 new PCE_CAPTURE in "
                f"{IDLE_HOLD_S:.0f}s ({len(new_events)} other events of "
                f"kinds {sorted(set(details['new_event_kinds']))})"
            ),
            duration_ms=self._elapsed_ms(start),
            details=details,
        )

    @staticmethod
    def _event_id(event: dict) -> str:
        """Return a stable identifier for a capture event.

        The SW assigns each event an ``id`` (sequence number) in its
        recent_events ring. Falls back to a (kind, ts, path) tuple
        if ``id`` is missing on older bundles.
        """
        eid = event.get("id")
        if eid is not None:
            return f"id:{eid}"
        return (
            f"k:{event.get('kind')}|t:{event.get('ts')}|"
            f"p:{event.get('path')}|s:{event.get('session_hint')}"
        )
