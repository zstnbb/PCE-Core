# SPDX-License-Identifier: Apache-2.0
"""tools/check_redundancy_targets.py — verify P0 redundancy targets.

W5-T2 deliverable per
``Docs/stability/redundancy-sprint/05-wave5-nightly-closure.md`` §3.

Reads ``GET /api/v1/supervisor/status`` and prints / writes a JSON list
of every scenario whose ``legs_active < redundancy_target``. Designed
to feed ``tools/auto_issue_on_redundancy_degraded.py`` and the GitHub
Actions step output ``alert_count``.

Phase B suppression: scenarios marked ``phase_b: true`` (Wave 6 / 7 / 8
build-out) are excluded by default. The ``--no-suppress-phase-b`` flag
opts out, useful for the Phase C closing audit.

Threshold semantics (W5-T2 §3.1):

  - ``--threshold yellow``  → alert when status ∈ {minimal, impaired, down}
  - ``--threshold red``     → alert when status ∈ {impaired, down} (default)
  - ``--threshold none``    → alert only when ``status == down``

Exit codes:
  0 — alerts written; ``alert_count`` could be 0 or N
  1 — supervisor unreachable / malformed response
  2 — usage error
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pce.tools.check_redundancy_targets")


_THRESHOLD_TO_STATUS_SET: dict[str, set[str]] = {
    "yellow": {"minimal", "impaired", "down"},
    "red":    {"impaired", "down"},
    "none":   {"down"},
}


def fetch_status(url: str, *, timeout: float = 10.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        logger.error("fetch_status failed: %r", exc)
        return None


def filter_alerts(
    snapshot: dict,
    *,
    threshold: str,
    suppress_phase_b: bool,
    suppress_ids: Optional[set[str]] = None,
) -> list[dict]:
    """Return the list of scenarios that breach the threshold."""
    suppress_ids = suppress_ids or set()
    alert_status_set = _THRESHOLD_TO_STATUS_SET.get(threshold)
    if alert_status_set is None:
        raise ValueError(f"unknown threshold: {threshold!r}")
    out: list[dict] = []
    for scenario in snapshot.get("scenarios", []) or []:
        if scenario.get("status") not in alert_status_set:
            continue
        sid = scenario.get("id", "")
        if suppress_phase_b and scenario.get("phase_b"):
            continue
        if sid in suppress_ids:
            continue
        out.append({
            "scenario_id": sid,
            "label": scenario.get("label", ""),
            "tier": scenario.get("tier", ""),
            "status": scenario.get("status", ""),
            "color": scenario.get("color", ""),
            "phase_b": bool(scenario.get("phase_b", False)),
            "redundancy_target": int(scenario.get("redundancy_target", 3)),
            "legs_active": int(scenario.get("legs_active", 0)),
            "legs_degraded": int(scenario.get("legs_degraded", 0)),
            "failed_legs": [
                {
                    "source": leg.get("source"),
                    "independent_basis": leg.get("independent_basis"),
                    "health": leg.get("health"),
                    "last_pass_ts": leg.get("last_pass_ts"),
                    "last_fail_ts": leg.get("last_fail_ts"),
                }
                for leg in (scenario.get("legs", []) or [])
                if leg.get("health") in ("red", "yellow", "unknown")
            ],
            "computed_at": snapshot.get("computed_at"),
        })
    return out


# ---------------------------------------------------------------------------
# GitHub Actions step output
# ---------------------------------------------------------------------------

def write_gha_outputs(alerts: list[dict]) -> None:
    """Append step outputs to ``$GITHUB_OUTPUT`` if set."""
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(f"alert_count={len(alerts)}\n")
            ids = ",".join(a["scenario_id"] for a in alerts)
            fh.write(f"alert_ids={ids}\n")
    except OSError:
        logger.exception("failed to write GITHUB_OUTPUT")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.check_redundancy_targets")
    parser.add_argument("--status-url",
                        default="http://127.0.0.1:9800/api/v1/supervisor/status")
    parser.add_argument("--threshold", choices=("yellow", "red", "none"),
                        default="red",
                        help="alert level (default: red — alert at impaired/down)")
    parser.add_argument(
        "--suppress-phase-b-scenarios",
        default="",
        help="comma-separated scenario IDs to suppress in addition to "
             "phase_b: true rows in scenarios.yaml",
    )
    parser.add_argument(
        "--no-suppress-phase-b", dest="suppress_phase_b", action="store_false",
        help="disable phase_b suppression (Phase C closing audit)",
    )
    parser.set_defaults(suppress_phase_b=True)
    parser.add_argument("--output", default=None,
                        help="write alerts JSON to this path (default: stdout only)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    snapshot = fetch_status(args.status_url)
    if snapshot is None:
        # Surface as 0 alerts to GHA so the workflow can decide how to react.
        write_gha_outputs([])
        if args.output:
            Path(args.output).write_text("[]", encoding="utf-8")
        return 1

    suppress_ids: set[str] = set()
    if args.suppress_phase_b_scenarios:
        suppress_ids.update(s.strip() for s in
                            args.suppress_phase_b_scenarios.split(",")
                            if s.strip())

    alerts = filter_alerts(
        snapshot,
        threshold=args.threshold,
        suppress_phase_b=args.suppress_phase_b,
        suppress_ids=suppress_ids,
    )

    payload = json.dumps({
        "computed_at": snapshot.get("computed_at"),
        "threshold": args.threshold,
        "alert_count": len(alerts),
        "alerts": alerts,
    }, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)

    write_gha_outputs(alerts)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
