# SPDX-License-Identifier: Apache-2.0
"""Post-harvest verification — scan ``raw_captures`` and report coverage.

Reads from ``~/.pce/data/pce.db`` and produces a per-host coverage
matrix for captures created since ``--since``. Designed to be run
immediately after ``setup_proxy_chain.ps1`` + ``harvest_cdp.py``
sessions so the operator can see what landed without writing SQL.

Output format:

    === Harvest coverage report (since 2026-05-13T00:00:00) ===

    By source_id + app_name:
      cdp-embedded  cursor       12 captures (6 pairs)
      cdp-embedded  windsurf     10 captures (5 pairs)
      proxy         (vscode)      8 captures (4 pairs) - GitHub Copilot

    By host (top 10):
      api2.cursor.sh                       8  req=4 resp=4 body_avg=2.1KB
      server.codeium.com                   6  req=3 resp=3 body_avg=1.4KB
      api.githubcopilot.com                4  req=2 resp=2 body_avg=4.8KB

    By status_code:
      200    18
      204    3
      400    1

    Sample paths per host (first 3):
      api2.cursor.sh:
        POST /aiserver.v1.AiService/StreamChat
        POST /aiserver.v1.AiService/AvailableModels
        ...

Usage::

    python scripts/harvest/verify_harvest.py --since "2026-05-13T00:00:00"
    python scripts/harvest/verify_harvest.py --since-minutes 60
    python scripts/harvest/verify_harvest.py --since-minutes 60 --json

Exit codes:
  0 — captures present
  2 — no captures in window (probable harvest failure)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional


def _db_path() -> Path:
    """Default DB location.

    Resolves to ``~/.pce/data/pce.db`` unless ``PCE_DATA_DIR`` is set.
    """
    import os
    base = os.environ.get("PCE_DATA_DIR")
    if base:
        return Path(base) / "pce.db"
    return Path.home() / ".pce" / "data" / "pce.db"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--since",
        help="ISO timestamp lower bound (e.g. '2026-05-13T00:00:00').",
    )
    g.add_argument(
        "--since-minutes",
        type=int,
        help="Look at captures from the last N minutes.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=_db_path(),
        help=f"Database path (default {_db_path()}).",
    )
    p.add_argument(
        "--top-hosts",
        type=int,
        default=20,
        help="How many hosts to list (default 20).",
    )
    p.add_argument(
        "--sample-paths",
        type=int,
        default=5,
        help="How many sample paths per host (default 5).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    return p.parse_args()


def resolve_since(args: argparse.Namespace) -> float:
    if args.since:
        # tolerant ISO parser
        s = args.since.replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(s)
        except ValueError:
            print(f"ERROR: could not parse --since {args.since!r}", file=sys.stderr)
            sys.exit(2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc).astimezone()  # local tz
        return dt.timestamp()
    # since-minutes
    return _dt.datetime.now().timestamp() - args.since_minutes * 60


def gather(db: Path, since_ts: float, top_hosts: int, sample_paths: int) -> dict[str, Any]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, source_id, app_name, direction, pair_id,
                   host, path, method, provider, status_code,
                   COALESCE(LENGTH(body_text_or_json), 0) AS body_len
            FROM raw_captures
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (since_ts,),
        ).fetchall()
    finally:
        conn.close()

    rows = [dict(r) for r in rows]
    total = len(rows)

    # By source_id + app_name
    src_app: dict[tuple[str, str], dict] = defaultdict(lambda: {"captures": 0, "pairs": set()})
    for r in rows:
        key = (r["source_id"] or "?", r["app_name"] or "(none)")
        src_app[key]["captures"] += 1
        src_app[key]["pairs"].add(r["pair_id"])

    # By host
    host_stats: dict[str, dict] = defaultdict(lambda: {
        "captures": 0,
        "request": 0,
        "response": 0,
        "body_lens": [],
        "paths": set(),
        "status_codes": defaultdict(int),
    })
    for r in rows:
        h = r["host"] or "(unknown)"
        host_stats[h]["captures"] += 1
        host_stats[h][r["direction"]] = host_stats[h].get(r["direction"], 0) + 1
        host_stats[h]["body_lens"].append(r["body_len"])
        host_stats[h]["paths"].add(r["path"] or "/")
        if r["status_code"]:
            host_stats[h]["status_codes"][r["status_code"]] += 1

    # By status code (global)
    status_global: dict[int, int] = defaultdict(int)
    for r in rows:
        if r["status_code"]:
            status_global[r["status_code"]] += 1

    # Sort
    src_app_sorted = sorted(src_app.items(), key=lambda kv: -kv[1]["captures"])
    host_sorted = sorted(host_stats.items(), key=lambda kv: -kv[1]["captures"])

    return {
        "total": total,
        "src_app": [
            {
                "source_id": k[0],
                "app_name": k[1],
                "captures": v["captures"],
                "pairs": len(v["pairs"]),
            }
            for k, v in src_app_sorted
        ],
        "hosts": [
            {
                "host": h,
                "captures": s["captures"],
                "request": s.get("request", 0),
                "response": s.get("response", 0),
                "body_avg": (sum(s["body_lens"]) / max(1, len(s["body_lens"]))),
                "body_max": max(s["body_lens"]) if s["body_lens"] else 0,
                "n_distinct_paths": len(s["paths"]),
                "sample_paths": sorted(s["paths"])[:sample_paths],
                "status_codes": dict(s["status_codes"]),
            }
            for h, s in host_sorted[:top_hosts]
        ],
        "status_codes": dict(sorted(status_global.items())),
    }


def render_text(report: dict[str, Any], since_iso: str) -> None:
    print(f"\n=== Harvest coverage report (since {since_iso}) ===\n")

    if report["total"] == 0:
        print("⚠  NO CAPTURES in this window.\n")
        print("   Possible causes:")
        print("   1. Harvest scripts never ran or crashed early")
        print("   2. mitmdump not started / system proxy not switched")
        print("   3. CDP harvester couldn't attach (--remote-debugging-port missing)")
        print("   4. App produced no traffic during the window")
        return

    print(f"Total captures: {report['total']}\n")

    print("By source + app:")
    for s in report["src_app"]:
        print(f"  {s['source_id']:<15} {s['app_name']:<20} "
              f"{s['captures']:>4} captures ({s['pairs']} pairs)")
    print()

    print(f"By host (top {len(report['hosts'])}):")
    for h in report["hosts"]:
        avg_kb = h["body_avg"] / 1024
        max_kb = h["body_max"] / 1024
        print(f"  {h['host']:<40} {h['captures']:>4}  "
              f"req={h['request']} resp={h['response']} "
              f"body_avg={avg_kb:>6.1f}KB max={max_kb:.1f}KB  "
              f"paths={h['n_distinct_paths']}")
    print()

    print("By status_code:")
    for code, n in report["status_codes"].items():
        print(f"  {code:>5}  {n}")
    print()

    print("Sample paths per host:")
    for h in report["hosts"][:10]:
        print(f"  {h['host']}:")
        for path in h["sample_paths"]:
            print(f"    {path[:100]}")
    print()


def main() -> int:
    args = parse_args()
    since_ts = resolve_since(args)
    since_iso = _dt.datetime.fromtimestamp(since_ts).isoformat()

    if not args.db.exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 2

    report = gather(args.db, since_ts, args.top_hosts, args.sample_paths)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        render_text(report, since_iso)

    return 0 if report["total"] > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
