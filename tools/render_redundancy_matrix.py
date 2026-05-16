# SPDX-License-Identifier: Apache-2.0
"""tools/render_redundancy_matrix.py — render REDUNDANCY-MATRIX.svg.

W5-T1 deliverable per
``Docs/stability/redundancy-sprint/05-wave5-nightly-closure.md`` §2.

Layout (13 cards):

  ┌──────────────────────────────────────────────────────────────┐
  │  PCE Redundancy Matrix — 2026-05-19 14:30 UTC                │
  ├──────────────────────────────────────────────────────────────┤
  │  P0 Web (5)                                                  │
  │     f1_chatgpt_web    L1●  L3a●  L3d●     🟢 redundant       │
  │     ...                                                      │
  │  P0 Desktop (8)                                              │
  │     f4_p1_claude_desktop   L1●  L3g●  L3f●  🟢 redundant     │
  │     f4_p2_chatgpt_desktop  L1●  A2◐  L4b○   🟠 impaired      │
  │     ...                                                      │
  │  Phase A status: 10/13 redundant. Phase B in progress.       │
  └──────────────────────────────────────────────────────────────┘

Data flows:
  - ``--status-url`` → ``GET /api/v1/supervisor/status`` (live, runtime)
  - ``--scenarios-yaml`` → fallback when supervisor /status unreachable
    (renders all-grey "no data yet" cards so the SVG never goes missing)

Output: ``Docs/stability/REDUNDANCY-MATRIX.svg`` (committed back to repo
by nightly workflow per W5-T1 §2.3).

Exits 0 even on transport failure — the SVG is best-effort, the
nightly probe must keep going.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("pce.tools.render_redundancy_matrix")


# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

COLOR_HEX: dict[str, str] = {
    "green":  "#22c55e",
    "yellow": "#eab308",
    "orange": "#f97316",
    "red":    "#ef4444",
    "grey":   "#94a3b8",
}

COLOR_LABEL: dict[str, str] = {
    "green":  "redundant",
    "yellow": "minimal",
    "orange": "impaired",
    "red":    "down",
    "grey":   "no data",
}

LEG_HEALTH_GLYPH: dict[str, str] = {
    "green":   "●",
    "yellow":  "◐",
    "red":     "○",
    "unknown": "·",
}

LEG_HEALTH_COLOR: dict[str, str] = {
    "green":   "#22c55e",
    "yellow":  "#eab308",
    "red":     "#ef4444",
    "unknown": "#94a3b8",
}

# Layout (px)
WIDTH: int = 1100
HEADER_H: int = 90
SECTION_H: int = 36       # "P0 Web (5)" sub-header height
ROW_H: int = 34
FOOTER_H: int = 50
LEFT_MARGIN: int = 40
ID_X: int = LEFT_MARGIN + 24
LEG_X_START: int = 360
LEG_X_STEP: int = 70
STATUS_X: int = 880


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_status(status_url: str, *, timeout: float = 5.0) -> Optional[dict]:
    """Fetch /api/v1/supervisor/status; return None on transport failure."""
    try:
        req = urllib.request.Request(status_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        logger.warning("fetch_status failed: %r", exc)
        return None


def fallback_from_scenarios_yaml(scenarios_path: Path) -> dict:
    """Build a no-data snapshot from scenarios.yaml when /status is down."""
    from pce_core.capture_supervisor.policy import load_scenarios
    registry = load_scenarios(scenarios_path)
    scenarios = []
    for sc in registry.scenarios:
        scenarios.append({
            "id": sc.id,
            "label": sc.label,
            "tier": sc.tier,
            "redundancy_target": sc.redundancy_target,
            "phase_b": sc.phase_b,
            "legs_active": 0,
            "legs_degraded": 0,
            "status": "down",
            "color": "grey",
            "legs": [
                {
                    "source": leg.source,
                    "priority": leg.priority,
                    "independent_basis": leg.independent_basis,
                    "health": "unknown",
                    "last_pass_ts": None,
                    "last_fail_ts": None,
                }
                for leg in sc.legs
            ],
        })
    return {
        "schema_version": registry.schema_version,
        "computed_at": time.time(),
        "window_hours": 24.0,
        "scenarios": scenarios,
        "dedup_metrics": {},
        "_fallback": True,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_svg(snapshot: dict) -> str:
    """Return a self-contained SVG string for the given snapshot."""
    scenarios = snapshot.get("scenarios", []) or []
    web = [s for s in scenarios if s.get("id", "").startswith("f1_")]
    desktop = [s for s in scenarios
               if not s.get("id", "").startswith("f1_")]
    redundant_n = sum(1 for s in scenarios if s.get("status") == "redundant")
    total_n = len(scenarios)

    # Compute total height
    height = (
        HEADER_H
        + SECTION_H + ROW_H * len(web)
        + SECTION_H + ROW_H * len(desktop)
        + FOOTER_H
    )

    parts: list[str] = []
    parts.append(_svg_header(WIDTH, height))
    parts.append(_render_title(snapshot, redundant_n=redundant_n, total_n=total_n))

    y = HEADER_H
    parts.append(_render_section_header(y, f"P0 Web ({len(web)})"))
    y += SECTION_H
    for row in web:
        parts.append(_render_row(row, y))
        y += ROW_H
    parts.append(_render_section_header(y, f"P0 Desktop ({len(desktop)})"))
    y += SECTION_H
    for row in desktop:
        parts.append(_render_row(row, y))
        y += ROW_H

    parts.append(_render_footer(snapshot, height,
                                redundant_n=redundant_n, total_n=total_n))
    parts.append("</svg>\n")
    return "".join(parts)


def _svg_header(w: int, h: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="Inter, -apple-system, system-ui, sans-serif">\n'
        f'<style>'
        f'  .title{{font-size:18px;font-weight:700;fill:#1f2937}}'
        f'  .subtitle{{font-size:12px;fill:#6b7280}}'
        f'  .section{{font-size:13px;font-weight:600;fill:#374151}}'
        f'  .scenario-id{{font-size:13px;font-weight:500;fill:#1f2937;font-family:"JetBrains Mono",monospace}}'
        f'  .scenario-label{{font-size:11px;fill:#6b7280}}'
        f'  .status-text{{font-size:12px;font-weight:600}}'
        f'  .leg-glyph{{font-size:14px;font-weight:700;font-family:"Segoe UI Symbol","Apple Color Emoji",sans-serif}}'
        f'  .leg-source{{font-size:9px;fill:#9ca3af;font-family:"JetBrains Mono",monospace}}'
        f'  .footer{{font-size:11px;fill:#9ca3af}}'
        f'  .phase-b{{font-size:9px;font-weight:600;fill:#7c3aed}}'
        f'</style>\n'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>\n'
    )


def _render_title(snapshot: dict, *, redundant_n: int, total_n: int) -> str:
    ts = snapshot.get("computed_at", time.time())
    when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f'<text class="title" x="{LEFT_MARGIN}" y="36">'
        f'PCE Redundancy Matrix'
        f'</text>\n'
        f'<text class="subtitle" x="{LEFT_MARGIN}" y="56">'
        f'computed at {_xml_escape(when)} · '
        f'{redundant_n}/{total_n} redundant · '
        f'window {snapshot.get("window_hours", 24):.0f}h'
        f'</text>\n'
        f'<text class="subtitle" x="{LEFT_MARGIN}" y="74">'
        f'STRICT MODE — 13/13 P0 ≥ 3 V-GREEN required for v1.1.6'
        f'</text>\n'
    )


def _render_section_header(y: int, label: str) -> str:
    return (
        f'<line x1="{LEFT_MARGIN}" y1="{y + 4}" x2="{WIDTH - LEFT_MARGIN}" y2="{y + 4}" '
        f'stroke="#e5e7eb" stroke-width="1"/>\n'
        f'<text class="section" x="{LEFT_MARGIN}" y="{y + 24}">{_xml_escape(label)}</text>\n'
    )


def _render_row(row: dict, y: int) -> str:
    color = row.get("color", "grey")
    status = row.get("status", "down")
    legs_active = row.get("legs_active", 0)
    target = row.get("redundancy_target", 3)
    phase_b = row.get("phase_b", False)
    legs = row.get("legs", []) or []
    parts: list[str] = []
    # Scenario id
    parts.append(
        f'<text class="scenario-id" x="{ID_X}" y="{y + 16}">'
        f'{_xml_escape(row.get("id", "?"))}'
        f'</text>\n'
    )
    parts.append(
        f'<text class="scenario-label" x="{ID_X}" y="{y + 28}">'
        f'{_xml_escape(_truncate(row.get("label", ""), 50))}'
        f'</text>\n'
    )
    if phase_b:
        parts.append(
            f'<text class="phase-b" x="{ID_X + 220}" y="{y + 16}">'
            f'PHASE B'
            f'</text>\n'
        )
    # Legs (up to 4)
    for idx, leg in enumerate(legs[:4]):
        x = LEG_X_START + idx * LEG_X_STEP
        health = leg.get("health", "unknown")
        glyph = LEG_HEALTH_GLYPH.get(health, "·")
        glyph_color = LEG_HEALTH_COLOR.get(health, "#94a3b8")
        parts.append(
            f'<text class="leg-glyph" x="{x}" y="{y + 16}" '
            f'fill="{glyph_color}">'
            f'{_xml_escape(glyph)}'
            f'</text>\n'
        )
        parts.append(
            f'<text class="leg-source" x="{x - 12}" y="{y + 28}">'
            f'{_xml_escape(_short_source(leg.get("source", "?")))}'
            f'</text>\n'
        )
    # Status text
    fill = COLOR_HEX.get(color, "#94a3b8")
    label = COLOR_LABEL.get(color, status)
    parts.append(
        f'<circle cx="{STATUS_X}" cy="{y + 14}" r="7" fill="{fill}"/>\n'
        f'<text class="status-text" x="{STATUS_X + 16}" y="{y + 18}" '
        f'fill="{fill}">{_xml_escape(label)} '
        f'<tspan fill="#6b7280" font-weight="400">'
        f'{legs_active}/{target}'
        f'</tspan></text>\n'
    )
    return "".join(parts)


def _render_footer(snapshot: dict, height: int, *,
                   redundant_n: int, total_n: int) -> str:
    fallback = snapshot.get("_fallback", False)
    note = (
        "Fallback render (supervisor /status unreachable)"
        if fallback else
        "Live data via /api/v1/supervisor/status"
    )
    metrics = snapshot.get("dedup_metrics", {}) or {}
    dedup_str = ""
    if metrics:
        dedup_str = (
            f' · dedup: {metrics.get("claims_total", 0)} claims '
            f'({metrics.get("claims_duplicate", 0)} dupes, '
            f'hit_ratio={metrics.get("hit_ratio", 0):.2f})'
        )
    return (
        f'<line x1="{LEFT_MARGIN}" y1="{height - FOOTER_H + 8}" '
        f'x2="{WIDTH - LEFT_MARGIN}" y2="{height - FOOTER_H + 8}" '
        f'stroke="#e5e7eb" stroke-width="1"/>\n'
        f'<text class="footer" x="{LEFT_MARGIN}" y="{height - 22}">'
        f'{_xml_escape(note)}{_xml_escape(dedup_str)}'
        f'</text>\n'
        f'<text class="footer" x="{LEFT_MARGIN}" y="{height - 8}">'
        f'See Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md '
        f'· tools/render_redundancy_matrix.py'
        f'</text>\n'
    )


def _short_source(s: str) -> str:
    """L3a_browser_ext → L3a, L4a_clipboard → L4a, etc."""
    return (s or "?").split("_", 1)[0]


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _xml_escape(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.render_redundancy_matrix")
    parser.add_argument("--status-url",
                        default="http://127.0.0.1:9800/api/v1/supervisor/status")
    parser.add_argument("--scenarios-yaml",
                        default="pce_core/capture_supervisor/scenarios.yaml")
    parser.add_argument("--out",
                        default="Docs/stability/REDUNDANCY-MATRIX.svg")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    snapshot = fetch_status(args.status_url)
    if snapshot is None:
        scenarios_path = Path(args.scenarios_yaml)
        if not scenarios_path.is_absolute():
            scenarios_path = _REPO_ROOT / scenarios_path
        logger.info("falling back to scenarios.yaml render at %s", scenarios_path)
        snapshot = fallback_from_scenarios_yaml(scenarios_path)

    svg = render_svg(snapshot)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")
    logger.info("wrote %d byte(s) to %s", len(svg), out_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
