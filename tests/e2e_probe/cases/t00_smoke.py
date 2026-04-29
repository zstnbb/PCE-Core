# SPDX-License-Identifier: Apache-2.0
"""T00 — site smoke test.

The cheapest case in the matrix. It validates the **adapter itself** is
not broken, independently of whether the probe / capture pipeline / PCE
Core round-trip works. Specifically:

    1. ``adapter.open(probe)`` succeeds and returns a tab id.
    2. ``adapter.check_logged_in(probe, tab_id)`` resolves within
       its budget. If the user isn't logged in we SKIP (not FAIL) — a
       missing login is an environment problem, not an adapter bug.
    3. The probe's ``capture.pipeline_state`` reports
       ``server_online == True`` so the rest of the matrix has any
       hope of doing round-trip verification.

T00 must always run first per site so the matrix surfaces "adapter is
broken" with a clear T00 FAIL instead of a confusing T01 selector
error. Keep it fast (single-digit seconds in the happy path) and free
of any LLM interaction — no prompt is sent.
"""
from __future__ import annotations

import time

import httpx

from pce_probe import ProbeClient, ProbeError

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


class T00SmokeCase(BaseCase):
    id = "T00"
    name = "site_smoke"
    description = (
        "Adapter health: open the entry URL, confirm the chat input is "
        "reachable, confirm PCE pipeline state is healthy. No prompt "
        "is sent and nothing is captured."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        # 1. Open the site.
        try:
            tab_id = adapter.open(probe)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"adapter.open failed: [{exc.code}] {exc.message}; "
                f"hint={exc.agent_hint!r}",
                duration_ms=self._elapsed_ms(start),
                details={"phase": "open", "code": exc.code},
            )

        # 2. Login + input check.
        login = adapter.check_logged_in(probe, tab_id)
        if not login.logged_in:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                f"not logged in / input not reachable ({login.detail}); "
                f"sign in manually in the attached browser and re-run",
                duration_ms=self._elapsed_ms(start),
                details={"phase": "login", "detail": login.detail, "tab_id": tab_id},
            )

        # 3. Pipeline health (single shared check, but cheap; running
        #    it per site catches "PCE server died mid-suite").
        try:
            pstate = probe.capture.pipeline_state()
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"capture.pipeline_state failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={"phase": "pipeline_state", "code": exc.code},
            )
        if not pstate.get("server_online"):
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"PCE Core unreachable from extension: server_online=False; "
                f"last_error={pstate.get('last_error')!r}",
                duration_ms=self._elapsed_ms(start),
                details={"phase": "pipeline_state", "pipeline": pstate},
            )

        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=f"{login.detail}; pipeline ok",
            duration_ms=self._elapsed_ms(start),
            details={"tab_id": tab_id, "pipeline": pstate},
        )
