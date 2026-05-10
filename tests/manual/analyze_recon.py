# SPDX-License-Identifier: Apache-2.0
"""Phase 4.0 · Post-recon analyzer.

Consumes ``tests/manual/recon_<ts>/events.jsonl`` and produces a
scope-decision report:

* **endpoint inventory** — group all HTTP responses by
  (host, method, path-template) where path-templates strip UUIDs /
  numeric IDs / org slugs, count occurrences, sample one body per
  group.
* **websocket inventory** — group WS frames by URL.
* **per-marker breakdown** — what traffic happened between each
  ``mark <label>`` event.
* **content-type distribution** — quick view of how much binary /
  multipart / SSE we're dealing with.
* **gap report** — comparison vs current ``capture_bridge`` URL
  patterns to highlight surfaces that are silently dropped.

Usage::

    python -m tests.manual.analyze_recon tests/manual/recon_<ts>/

Output:

* prints a markdown-ish report to stdout
* writes ``report.md`` next to ``events.jsonl``

This is **not** an automated test; the report is for human reading
to drive the next round of normalizer / capture_bridge changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Path templating
# ---------------------------------------------------------------------------

# Strip UUIDs (with and without dashes)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_NUM_RE = re.compile(r"\b\d{4,}\b")


def template_path(path: str) -> str:
    """Collapse stable identifiers in a URL path so we can group by template.

    Examples::

        /api/organizations/abc123-uuid-uuid/projects/another-uuid/...
            → /api/organizations/{uuid}/projects/{uuid}/...

        /api/conversation/12345/messages
            → /api/conversation/{num}/messages
    """
    if not path:
        return path
    p = path
    # Trim query string for grouping; keep path-only template.
    if "?" in p:
        p = p.split("?", 1)[0]
    p = _UUID_RE.sub("{uuid}", p)
    p = _HEX_RE.sub("{hex}", p)
    p = _NUM_RE.sub("{num}", p)
    return p


# ---------------------------------------------------------------------------
# Capture-bridge baseline (current production allowlist)
# ---------------------------------------------------------------------------


_CURRENT_BRIDGE_PATTERNS = [
    re.compile(r"^https://api\.anthropic\.com/v1/.*"),
    re.compile(r"^https://claude\.ai/api/.*"),
]


def is_currently_captured(url: str) -> bool:
    if not url:
        return False
    return any(p.search(url) for p in _CURRENT_BRIDGE_PATTERNS)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def iter_events(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def split_by_markers(events: list[dict]) -> dict[str, list[dict]]:
    """Bucket events by current marker label.

    Events before any marker land in ``"<unmarked>"``.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    current = "<unmarked>"
    for ev in events:
        kind = ev.get("kind")
        if kind == "marker":
            current = ev.get("label", "<unknown-marker>")
            continue
        if kind == "note":
            continue
        buckets[current].append(ev)
    return dict(buckets)


# ---------------------------------------------------------------------------
# Analysers
# ---------------------------------------------------------------------------


def analyse_http(events: list[dict]) -> list[dict]:
    """Group HTTP responses by (host, method, path-template). Return sorted list."""
    groups: dict[tuple[str, str, str], dict] = {}

    for ev in events:
        if ev.get("kind") != "http_response":
            continue
        url = ev.get("url", "")
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc
        method = ev.get("method", "GET")
        template = template_path(parsed.path)
        key = (host, method, template)

        g = groups.setdefault(key, {
            "host": host,
            "method": method,
            "path_template": template,
            "count": 0,
            "status_codes": Counter(),
            "content_types": Counter(),
            "sample_url": url,
            "sample_req_body": "",
            "sample_res_body": "",
            "currently_captured": is_currently_captured(url),
            "total_req_bytes": 0,
            "total_res_bytes": 0,
        })
        g["count"] += 1
        g["status_codes"][int(ev.get("status", 0))] += 1
        ctype = ev.get("content_type", "") or ""
        # Normalize to type/subtype (drop charset etc.)
        ctype_short = ctype.split(";", 1)[0].strip().lower()
        g["content_types"][ctype_short] += 1
        g["total_req_bytes"] += int(ev.get("req_body_bytes", 0) or 0)
        g["total_res_bytes"] += int(ev.get("res_body_bytes", 0) or 0)
        if not g["sample_req_body"]:
            g["sample_req_body"] = (ev.get("req_body_preview") or "")[:1500]
        if not g["sample_res_body"]:
            g["sample_res_body"] = (ev.get("res_body_preview") or "")[:1500]

    rows = list(groups.values())
    rows.sort(key=lambda r: (-r["count"], r["host"], r["path_template"]))
    return rows


def analyse_ws(events: list[dict]) -> list[dict]:
    """Group WebSocket activity by URL."""
    groups: dict[str, dict] = {}
    for ev in events:
        kind = ev.get("kind", "")
        if not kind.startswith("ws_"):
            continue
        url = ev.get("url", "")
        g = groups.setdefault(url, {
            "url": url,
            "open": 0,
            "recv": 0,
            "sent": 0,
            "close": 0,
            "sample_recv": "",
            "sample_sent": "",
            "total_recv_bytes": 0,
            "total_sent_bytes": 0,
        })
        if kind == "ws_open":
            g["open"] += 1
        elif kind == "ws_recv":
            g["recv"] += 1
            g["total_recv_bytes"] += int(ev.get("payload_bytes", 0) or 0)
            if not g["sample_recv"]:
                g["sample_recv"] = (ev.get("payload_preview") or "")[:800]
        elif kind == "ws_sent":
            g["sent"] += 1
            g["total_sent_bytes"] += int(ev.get("payload_bytes", 0) or 0)
            if not g["sample_sent"]:
                g["sample_sent"] = (ev.get("payload_preview") or "")[:800]
        elif kind == "ws_close":
            g["close"] += 1
    rows = list(groups.values())
    rows.sort(key=lambda r: -(r["recv"] + r["sent"]))
    return rows


def gap_report(http_groups: list[dict], ws_groups: list[dict]) -> dict:
    """Identify surfaces NOT covered by current capture_bridge allowlist."""
    missed_http = [g for g in http_groups if not g["currently_captured"]]
    return {
        "missed_http_endpoints": len(missed_http),
        "missed_http_total_responses": sum(g["count"] for g in missed_http),
        "ws_endpoints_total": len(ws_groups),
        "ws_endpoints_unmonitored": len(ws_groups),  # capture_bridge has no WS at all
        "ws_total_frames": sum(g["recv"] + g["sent"] for g in ws_groups),
        "currently_captured_endpoints": sum(
            1 for g in http_groups if g["currently_captured"]
        ),
        "currently_captured_responses": sum(
            g["count"] for g in http_groups if g["currently_captured"]
        ),
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _truncate(s: str, n: int = 600) -> str:
    if not s:
        return ""
    if len(s) > n:
        return s[:n] + f"…<+{len(s)-n}>"
    return s


def render_report(
    *,
    recon_dir: Path,
    meta: dict,
    summary: dict,
    by_marker: dict[str, list[dict]],
    overall_http: list[dict],
    overall_ws: list[dict],
    gap: dict,
) -> str:
    out: list[str] = []

    out.append(f"# Claude Desktop Recon Report — {recon_dir.name}")
    out.append("")
    out.append("## Run summary")
    out.append("")
    out.append(f"- recon_dir: `{recon_dir}`")
    out.append(f"- elapsed_s: {summary.get('elapsed_s')}")
    out.append(f"- http_responses: {summary.get('http_response')}")
    out.append(f"- ws_open / recv / sent: "
               f"{summary.get('ws_open')} / {summary.get('ws_recv')} / {summary.get('ws_sent')}")
    out.append(f"- markers: {summary.get('markers')}")
    out.append("")

    out.append("## Gap vs current capture_bridge")
    out.append("")
    out.append(f"- HTTP endpoints currently captured: **{gap['currently_captured_endpoints']}** "
               f"({gap['currently_captured_responses']} responses)")
    out.append(f"- HTTP endpoints **missed**: **{gap['missed_http_endpoints']}** "
               f"({gap['missed_http_total_responses']} responses)")
    out.append(f"- WebSocket endpoints seen: **{gap['ws_endpoints_total']}** "
               f"(all {gap['ws_endpoints_unmonitored']} unmonitored — "
               f"capture_bridge has no WS hook)")
    out.append(f"- WebSocket frames lost: **{gap['ws_total_frames']}**")
    out.append("")

    out.append("## HTTP endpoint inventory (top 50)")
    out.append("")
    out.append("| ✅/❌ | host | method | path template | count | top status | top content-type | req B | res B |")
    out.append("|---|---|---|---|---:|---|---|---:|---:|")
    for g in overall_http[:50]:
        flag = "✅" if g["currently_captured"] else "❌"
        top_status = g["status_codes"].most_common(1)[0][0] if g["status_codes"] else "?"
        top_ct = g["content_types"].most_common(1)[0][0] if g["content_types"] else "?"
        out.append(
            f"| {flag} | `{g['host']}` | `{g['method']}` | `{g['path_template']}` "
            f"| {g['count']} | {top_status} | `{top_ct}` "
            f"| {g['total_req_bytes']} | {g['total_res_bytes']} |"
        )
    if len(overall_http) > 50:
        out.append(f"| | … | | (+{len(overall_http) - 50} more) | | | | | |")
    out.append("")

    if overall_ws:
        out.append("## WebSocket inventory")
        out.append("")
        out.append("| url | open | recv | sent | close | recv B | sent B |")
        out.append("|---|---:|---:|---:|---:|---:|---:|")
        for g in overall_ws:
            out.append(
                f"| `{_truncate(g['url'], 80)}` | {g['open']} | {g['recv']} | "
                f"{g['sent']} | {g['close']} | {g['total_recv_bytes']} | {g['total_sent_bytes']} |"
            )
        out.append("")

    out.append("## Per-surface breakdown (by marker)")
    out.append("")
    if not by_marker:
        out.append("*No markers recorded. Re-run and use `mark <label>` between surfaces.*")
        out.append("")
    else:
        for label in sorted(by_marker.keys(), key=lambda x: (x == "<unmarked>", x)):
            evs = by_marker[label]
            http_count = sum(1 for e in evs if e.get("kind") == "http_response")
            ws_recv = sum(1 for e in evs if e.get("kind") == "ws_recv")
            ws_sent = sum(1 for e in evs if e.get("kind") == "ws_sent")
            out.append(f"### `{label}` — http={http_count} ws_recv={ws_recv} ws_sent={ws_sent}")
            out.append("")
            sub_http = analyse_http(evs)
            if sub_http:
                out.append("Top endpoints:")
                out.append("")
                for g in sub_http[:8]:
                    flag = "✅" if g["currently_captured"] else "❌"
                    out.append(
                        f"- {flag} `{g['method']} {g['host']}{g['path_template']}` "
                        f"× {g['count']} ({g['content_types'].most_common(1)[0][0] if g['content_types'] else '?'})"
                    )
                out.append("")
            sub_ws = analyse_ws(evs)
            if sub_ws:
                out.append("WebSockets:")
                out.append("")
                for g in sub_ws[:4]:
                    out.append(
                        f"- `{_truncate(g['url'], 80)}` recv={g['recv']} sent={g['sent']}"
                    )
                out.append("")

    out.append("## Sample bodies for top-3 missed HTTP endpoints")
    out.append("")
    missed = [g for g in overall_http if not g["currently_captured"]][:3]
    for i, g in enumerate(missed, 1):
        out.append(f"### {i}. `{g['method']} {g['host']}{g['path_template']}` × {g['count']}")
        out.append("")
        if g["sample_req_body"]:
            out.append("Request body sample:")
            out.append("")
            out.append("```")
            out.append(_truncate(g["sample_req_body"], 1200))
            out.append("```")
            out.append("")
        if g["sample_res_body"]:
            out.append("Response body sample:")
            out.append("")
            out.append("```")
            out.append(_truncate(g["sample_res_body"], 1200))
            out.append("```")
            out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyse a recon_<ts>/ directory and produce report.md"
    )
    parser.add_argument("recon_dir", type=Path, help="Path to recon_<ts>/")
    args = parser.parse_args(argv)

    recon_dir: Path = args.recon_dir
    if not recon_dir.is_dir():
        print(f"error: {recon_dir} is not a directory", file=sys.stderr)
        return 2

    events_path = recon_dir / "events.jsonl"
    summary_path = recon_dir / "summary.json"
    meta_path = recon_dir / "meta.json"

    if not events_path.exists():
        print(f"error: {events_path} missing", file=sys.stderr)
        return 2

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.exists()
        else {}
    )

    events = list(iter_events(events_path))
    by_marker = split_by_markers(events)
    overall_http = analyse_http(events)
    overall_ws = analyse_ws(events)
    gap = gap_report(overall_http, overall_ws)

    report = render_report(
        recon_dir=recon_dir,
        meta=meta,
        summary=summary,
        by_marker=by_marker,
        overall_http=overall_http,
        overall_ws=overall_ws,
        gap=gap,
    )

    out_path = recon_dir / "report.md"
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print()
    print(f"[analyze_recon] report written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
