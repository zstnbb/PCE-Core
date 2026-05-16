# SPDX-License-Identifier: Apache-2.0
"""PCE Capture Supervisor — runtime arbitration over capture legs.

OSS module per ADR-021 (Adopted 2026-05-15). Three minimal contracts:

- ``dedup`` — ``(pair_id, fingerprint)`` 30s sliding-window LRU. When two
  legs report the SAME pair (e.g. L1 MITM + L3a browser-ext both saw a
  ChatGPT request), keep ONE primary ``raw_captures`` row and write the
  other leg's source into the primary's ``deduped_by`` JSON array.
- ``policy`` — ``scenarios.yaml`` driven path priority + degrade decision
  for each of the 13 P0 scenarios (5 Web + 8 Desktop, per
  ``Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md``).
- ``status`` — derived health by ``(scenario × leg)`` from
  ``health_beacons``; surfaces ``redundant / minimal / impaired / down``.

A 4th surface — ``api`` — exposes these over HTTP at
``/api/v1/supervisor/*`` so the dashboard, the Pro registration call and
the nightly redundancy check can all read consistent state.

The Pro Edition (PCE-pro) registers extra capture legs (L0 kernel, L2
Frida, L4b accessibility) through ``POST /api/v1/supervisor/legs/register``.
It does NOT fork the dedup algorithm or the status state machine — see
ADR-021 §3.4.

Usage:

.. code-block:: python

    from pce_core.capture_supervisor import (
        CaptureDedup,
        compute_fingerprint,
        load_scenarios,
        compute_status,
    )

    dedup = CaptureDedup(window_s=30.0, max_entries=10000)
    fp = compute_fingerprint("POST", "chatgpt.com", "/backend-api/conversation",
                              body=b"...", ts_bucket_5min=1234)
    result = dedup.claim(pair_id="p-1", fingerprint=fp, source="L1_mitm")
    if result.is_primary:
        ...  # insert raw_captures
    else:
        ...  # append source to existing row's deduped_by JSON array
"""

from __future__ import annotations

from .dedup import (
    CaptureDedup,
    ClaimResult,
    compute_fingerprint,
)
from .policy import (
    Leg,
    Scenario,
    ScenarioRegistry,
    load_scenarios,
)
from .status import (
    LegHealth,
    LegStatus,
    ScenarioStatus,
    SupervisorSnapshot,
    compute_status,
)

__all__ = [
    # dedup
    "CaptureDedup",
    "ClaimResult",
    "compute_fingerprint",
    # policy
    "Leg",
    "Scenario",
    "ScenarioRegistry",
    "load_scenarios",
    # status
    "LegHealth",
    "LegStatus",
    "ScenarioStatus",
    "SupervisorSnapshot",
    "compute_status",
]
