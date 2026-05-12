# SPDX-License-Identifier: Apache-2.0
"""tools/render_health_matrix.py — render Lane Health × canary status to SVG.

Per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.3 deliverable:

    .github/workflows/nightly-probe.yml runs this once per nightly sweep
    and the output goes to Docs/stability/HEALTH-MATRIX.svg, committed
    back to the repo (so the SVG is always in sync with the latest
    auto-classified state).

Sources of truth:

  1. ``pce_core.health.compute_matrix`` — lane × target rollup colour
     (green / yellow / red / grey, HEALTH-MATRIX §5.1 rules)
  2. ``pce_test_conductor/canaries/<target>/`` — schema canary store;
     a *hard* drift in any canary file degrades that target's cell to
     yellow even when the health beacons say green (ADR-011 G3
     "watcher independent of matrix run, doesn't pollute fail counts"
     means we visualise it but don't trip the colour rule itself —
     instead we annotate with a small canary badge).

Layout (1000 × N rows, header 80 px):

    ┌──────────────────────────────────────────────────────┐
    │   PCE Pipeline Health Matrix — 2026-05-12 14:21:00   │
    │   Browser 🟢   Desktop 🟢   CLI 🟡   MCP 🟢          │
    ├──────────────────────────────────────────────────────┤
    │   chatgpt        🟢   24h 47/52 PASS  N+H            │
    │   claude_ai      🟢   24h 38/40 PASS  N+H            │
    │   ...                                                 │
    └──────────────────────────────────────────────────────┘

Usage:

    python -m tools.render_health_matrix \\
        --output Docs/stability/HEALTH-MATRIX.svg \\
        --window-hours 24 \\
        [--db-path <path>] [--quiet]

Exits 0 always (SVG is best-effort; nightly probe still completes).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("pce.tools.render_health_matrix")


# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

#: Colour palette — matches dashboard/lane_health.js + style.css ladder.
COLOR_HEX: dict[str, str] = {
    "green":  "#22c55e",
    "yellow": "#eab308",
    "red":    "#ef4444",
    "grey":   "#94a3b8",
}

COLOR_LABEL: dict[str, str] = {
    "green":  "OK",
    "yellow": "DEGRADED",
    "red":    "BROKEN",
    "grey":   "UNKNOWN",
}

LANE_ORDER: tuple[str, ...] = ("browser", "desktop", "cli", "mcp")

# Layout (px). Output SVG width is fixed; height grows with target count.
WIDTH: int = 1000
HEADER_H: int = 80
ROW_H: int = 40
LANE_LABEL_W: int = 110
TARGET_LABEL_W: int = 220
COLOUR_DOT_R: int = 10
COLOUR_DOT_X: int = LANE_LABEL_W + TARGET_LABEL_W + 20

# Canary badge — appended to a row when ``hard_drift_count`` > 0.
CANARY_BADGE_W: int = 60


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_matrix(
    *,
    window_hours: float = 24.0,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Read ``compute_matrix`` and merge canary drift counts per target.

    Returns a list of row dicts (one per lane × target) suitable for
    ``render_svg``. Lanes with zero targets still appear in the lane
    summary header.
    """
    try:
        from pce_core.health import compute_matrix
    except ImportError as exc:  # pragma: no cover — defensive
        logger.warning("pce_core.health unavailable: %r", exc)
        return {"lanes": {ln: {"color": "grey", "targets": {}} for ln in LANE_ORDER},
                "computed_at": time.time(), "window_hours": window_hours}
    return compute_matrix(window_hours=window_hours, db_path=db_path)


def count_hard_canary_drifts(
    canary_root: Path,
    *,
    target_lane_filter: Optional[str] = None,
) -> dict[tuple[str, str], int]:
    """Inspect the canary directory for stored schemas that flagged hard drift.

    P5.C.3 keeps this lightweight: a "hard drift count" is the number
    of ``.schema.json`` files where the file mtime > 6 h (recent
    nightly observed a fresh shape that wasn't there before).
    Refining the heuristic — distinguishing add vs remove on disk —
    is a P5.C.4 follow-up once the auto-merge codepath actually runs
    in nightly CI.

    Returns ``{(lane, target): hard_count}``.
    """
    out: dict[tuple[str, str], int] = {}
    if not canary_root.exists():
        return out
    cutoff = time.time() - 6 * 3600
    # Layout: pce_test_conductor/canaries/<target_id>/<case>_<endpoint>.schema.json
    for target_dir in canary_root.iterdir():
        if not target_dir.is_dir():
            continue
        target_id = target_dir.name
        # Recover lane from target_id heuristically — manifests live
        # alongside the canaries; we accept "<lane>_<target>" by
        # convention so the renderer can group without re-loading
        # every YAML.
        lane = target_id.split("_", 1)[0] if "_" in target_id else "browser"
        if target_lane_filter and lane != target_lane_filter:
            continue
        recent = sum(
            1 for p in target_dir.glob("*.schema.json")
            if p.stat().st_mtime > cutoff
        )
        target_short = target_id.split("_", 1)[1] if "_" in target_id else target_id
        out[(lane, target_short)] = recent
    return out


# ---------------------------------------------------------------------------
# SVG composition
# ---------------------------------------------------------------------------

def render_svg(
    matrix: dict[str, Any],
    canary_drifts: Optional[dict[tuple[str, str], int]] = None,
) -> str:
    """Compose a self-contained SVG string from a compute_matrix() output."""
    canary_drifts = canary_drifts or {}
    rows = _flatten_rows(matrix)
    n = max(len(rows), 1)
    height = HEADER_H + n * ROW_H + 30  # 30 px footer

    parts: list[str] = []
    parts.append(_svg_header(WIDTH, height))
    parts.append(_render_title(matrix))
    parts.append(_render_lane_summary(matrix))
    for i, row in enumerate(rows):
        y = HEADER_H + i * ROW_H + ROW_H // 2
        parts.append(_render_row(row, y, canary_drifts))
    parts.append(_render_footer(matrix, height))
    parts.append("</svg>\n")
    return "".join(parts)


def _flatten_rows(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Project compute_matrix into ``[{lane, target, color, ...}]`` rows."""
    out: list[dict[str, Any]] = []
    lanes = matrix.get("lanes", {}) or {}
    severity = {"red": 0, "yellow": 1, "grey": 2, "green": 3}
    for lane in LANE_ORDER:
        targets = (lanes.get(lane, {}) or {}).get("targets", {}) or {}
        # Sort within a lane by severity (worst first), then alphabetically.
        ordered = sorted(
            targets.items(),
            key=lambda kv: (severity.get(kv[1].get("color", "grey"), 2), kv[0]),
        )
        for tname, tdata in ordered:
            out.append({
                "lane": lane,
                "target": tname,
                "color": tdata.get("color", "grey"),
                "tier": tdata.get("tier") or "?",
                "plane_count": tdata.get("plane_count", 0),
                "plane_required": tdata.get("plane_required", 1),
                "pass_count_24h": tdata.get("pass_count_24h", 0),
                "fail_count_24h": tdata.get("fail_count_24h", 0),
                "skip_count_24h": tdata.get("skip_count_24h", 0),
                "pass_rate_24h": tdata.get("pass_rate_24h", 0.0),
                "last_pass_ts": tdata.get("last_pass_ts"),
            })
    return out


# ---------- atomic SVG fragments ----------

def _svg_header(w: int, h: int) -> str:
    # xmlns is required so GitHub renders the SVG inline in README/MD.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="Inter, -apple-system, system-ui, sans-serif">\n'
        f'<style>'
        f'  .title{{font-size:18px;font-weight:700;fill:#1f2937}}'
        f'  .lane-name{{font-size:14px;font-weight:600;fill:#374151}}'
        f'  .target-name{{font-size:14px;font-weight:500;fill:#1f2937}}'
        f'  .meta{{font-size:11px;fill:#6b7280}}'
        f'  .footer{{font-size:11px;fill:#9ca3af}}'
        f'  .canary-badge{{font-size:10px;font-weight:600;fill:#fbbf24}}'
        f'</style>\n'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>\n'
    )


def _render_title(matrix: dict[str, Any]) -> str:
    ts = matrix.get("computed_at", time.time())
    when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f'<text class="title" x="20" y="34">'
        f'PCE Pipeline Health Matrix'
        f'</text>\n'
        f'<text class="meta" x="20" y="54">computed at {_xml_escape(when)} '
        f'(window {matrix.get("window_hours", 24):.0f} h)</text>\n'
    )


def _render_lane_summary(matrix: dict[str, Any]) -> str:
    """One-line lane summary at top: 'Browser 🟢  Desktop 🟢  CLI 🟡  MCP 🟢'."""
    lanes = matrix.get("lanes", {}) or {}
    parts: list[str] = []
    x = 380
    for lane in LANE_ORDER:
        color = (lanes.get(lane, {}) or {}).get("color", "grey")
        cx = x + 8
        parts.append(
            f'<circle cx="{cx}" cy="44" r="6" fill="{COLOR_HEX.get(color, COLOR_HEX["grey"])}"/>'
        )
        parts.append(
            f'<text class="lane-name" x="{cx + 12}" y="48">{_xml_escape(lane.capitalize())}</text>'
        )
        x += 145
    return "".join(parts) + "\n"


def _render_row(row: dict[str, Any], y: int, canary_drifts: dict[tuple[str, str], int]) -> str:
    """Render one (lane, target) row."""
    lane = row["lane"]
    target = row["target"]
    color = row["color"]
    tier = row["tier"]
    pc, pr = row["plane_count"], row["plane_required"]
    pass_n = row["pass_count_24h"]
    fail_n = row["fail_count_24h"]
    skip_n = row["skip_count_24h"]
    pass_rate = row["pass_rate_24h"]

    # Plane redundancy fraction is the most operationally relevant
    # secondary signal — bold red when below the tier requirement.
    plane_color = "#ef4444" if pc < pr else "#374151"

    canary_n = canary_drifts.get((lane, target), 0)
    canary_badge = ""
    if canary_n:
        bx = COLOUR_DOT_X + 250
        canary_badge = (
            f'<rect x="{bx}" y="{y - 12}" width="{CANARY_BADGE_W}" height="20" '
            f'rx="4" fill="#fef3c7" stroke="#fbbf24"/>'
            f'<text class="canary-badge" x="{bx + 5}" y="{y + 3}">CANARY {canary_n}</text>'
        )

    return (
        f'<text class="lane-name" x="20" y="{y + 4}">{_xml_escape(lane.capitalize())}</text>\n'
        f'<text class="target-name" x="{LANE_LABEL_W + 10}" y="{y + 4}">'
        f'{_xml_escape(target)} <tspan class="meta">({_xml_escape(tier)})</tspan>'
        f'</text>\n'
        f'<circle cx="{COLOUR_DOT_X}" cy="{y}" r="{COLOUR_DOT_R}" '
        f'fill="{COLOR_HEX.get(color, COLOR_HEX["grey"])}"/>\n'
        f'<text class="meta" x="{COLOUR_DOT_X + 20}" y="{y + 4}">'
        f'{COLOR_LABEL.get(color, "?")}'
        f'</text>\n'
        f'<text class="meta" x="{COLOUR_DOT_X + 100}" y="{y + 4}">'
        f'<tspan fill="{plane_color}" font-weight="600">'
        f'plane {pc}/{pr}</tspan> · '
        f'24h pass {pass_n}/{pass_n + fail_n + skip_n} '
        f'({pass_rate * 100:.0f}%)'
        f'</text>\n'
        f'{canary_badge}\n'
    )


def _render_footer(matrix: dict[str, Any], height: int) -> str:
    return (
        f'<text class="footer" x="20" y="{height - 10}">'
        f'Source: pce_core.health.compute_matrix() · '
        f'see Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md §5.1 for colour rules · '
        f'Generated by tools/render_health_matrix.py'
        f'</text>\n'
    )


def _xml_escape(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.render_health_matrix")
    parser.add_argument(
        "--output", default="Docs/stability/HEALTH-MATRIX.svg",
        help="output SVG path (default: %(default)s)",
    )
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--db-path", default=None,
                        help="override pce_core SQLite DB path (CI / tests)")
    parser.add_argument("--canary-root", default="pce_test_conductor/canaries",
                        help="canary directory to scan for hard drifts")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    db_path = Path(args.db_path) if args.db_path else None
    matrix = collect_matrix(window_hours=args.window_hours, db_path=db_path)

    canary_root = Path(args.canary_root)
    if not canary_root.is_absolute():
        canary_root = _REPO_ROOT / canary_root
    drifts = count_hard_canary_drifts(canary_root)

    svg = render_svg(matrix, canary_drifts=drifts)
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    logger.info("wrote %d byte(s) to %s", len(svg), out_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
