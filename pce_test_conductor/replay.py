# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.replay — fixture playback (P5.C.2 stub).

Per ADR-017 §3.2, ``run_case(target, case, mode="replay")`` should
exercise a saved fixture instead of driving the live UI / network. The
goal is "0.5 s vs live 30 s" so debugging normalizers doesn't require
re-driving the user's browser.

P5.C.2 lands the **fixture path convention** + a clean
``NotImplementedError`` so any caller invoking ``mode="replay"``
discovers the contract early. The actual replay loop arrives in
P5.C.4 alongside the YAML-config rollout (which makes adapter
behaviour deterministic enough to replay reliably).

Fixture directory convention:

    pce_test_conductor/fixtures/<target_id>/<case_id>/
        request.json    # last live raw_payload (request side)
        response.json   # last live raw_payload (response side)
        meta.json       # captured_at, pce_session_id, normalizer_version

The directory is git-tracked; payloads are scrubbed of PII / tokens
before commit (an automated PR review check that's part of the
P5.C.4 plan).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Default fixture root (alongside this module).
DEFAULT_FIXTURES_DIR: Path = Path(__file__).parent / "fixtures"


@dataclass
class FixturePath:
    """Resolved on-disk locations for one (target, case) fixture."""

    root: Path
    request: Path
    response: Path
    meta: Path

    def all_present(self) -> bool:
        return self.request.exists() and self.response.exists() and self.meta.exists()


def fixture_path(
    target_id: str,
    case_id: str,
    *,
    fixtures_dir: Optional[Path] = None,
) -> FixturePath:
    """Compute the fixture path triple for one (target_id, case_id)."""
    base = fixtures_dir or DEFAULT_FIXTURES_DIR
    case_dir = base / target_id / case_id
    return FixturePath(
        root=case_dir,
        request=case_dir / "request.json",
        response=case_dir / "response.json",
        meta=case_dir / "meta.json",
    )


def replay_case(
    target_id: str,
    case_id: str,
    *,
    fixtures_dir: Optional[Path] = None,
) -> None:
    """Run a recorded fixture against the local PCE Core normalizer pipeline.

    P5.C.2 SCOPE: this function deliberately raises ``NotImplementedError``
    so callers see the contract gap explicitly rather than a silent
    no-op. The full implementation arrives in P5.C.4.

    A complete implementation will:

    1. Load ``request.json`` + ``response.json`` from the fixture path
    2. POST them through ``/api/v1/captures/v2`` against an ephemeral
       (in-memory) PCE Core, exactly as live capture would
    3. Compare the resulting ``sessions`` + ``messages`` rows against
       the case's expected pass_gate (loaded via case_standard_module)
    4. Return a ``RunRecord`` shaped identically to ``run_case(mode="live")``

    Until then, callers should use ``mode="live"`` and accept the
    full live driver round-trip cost.
    """
    fp = fixture_path(target_id, case_id, fixtures_dir=fixtures_dir)
    raise NotImplementedError(
        f"replay mode is a P5.C.4 deliverable; fixture path would be {fp.root}. "
        f"Use mode='live' for now."
    )
