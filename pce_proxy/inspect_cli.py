# SPDX-License-Identifier: Apache-2.0
"""PCE Proxy – minimal CLI to inspect captured data.

Usage:
    python -m pce_proxy.inspect_cli                 # last 20 captures
    python -m pce_proxy.inspect_cli --last 5        # last 5
    python -m pce_proxy.inspect_cli --pair <id>     # show a request/response pair
    python -m pce_proxy.inspect_cli --stats         # summary statistics
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH
from .db import get_connection


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _trunc(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def print_captures(rows: list[dict]) -> None:
    if not rows:
        print("(no captures found)")
        return

    for r in rows:
        direction_icon = ">>" if r["direction"] == "request" else "<<"
        status = f" [{r['status_code']}]" if r["status_code"] else ""
        latency = f" {r['latency_ms']:.0f}ms" if r["latency_ms"] else ""
        model = f" model={r['model_name']}" if r["model_name"] else ""
        print(
            f"  {direction_icon} {_ts(r['created_at'])}  "
            f"{r['method']} {r['host']}{r['path']}{status}{latency}{model}  "
            f"pair={r['pair_id']}"
        )
        body = r.get("body_text_or_json", "")
        if body:
            print(f"     body: {_trunc(body)}")
        print()


def print_pair(pair_id: str, conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM raw_captures WHERE pair_id = ? ORDER BY created_at", (pair_id,)
    ).fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        print(f"No captures found for pair_id={pair_id}")
        return
    print(f"--- Pair {pair_id} ({len(rows)} records) ---\n")
    for r in rows:
        direction_icon = ">>" if r["direction"] == "request" else "<<"
        status = f" [{r['status_code']}]" if r["status_code"] else ""
        latency = f" {r['latency_ms']:.0f}ms" if r["latency_ms"] else ""
        print(f"{direction_icon} {_ts(r['created_at'])} {r['method']} {r['host']}{r['path']}{status}{latency}")
        print(f"   headers: {r['headers_redacted_json']}")
        body = r.get("body_text_or_json", "")
        if body:
            print(f"   body:\n{body[:2000]}")
        print()


def print_stats(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) as c FROM raw_captures").fetchone()["c"]
    print(f"Total captures: {total}")

    if total == 0:
        return

    providers = conn.execute(
        "SELECT provider, COUNT(*) as c FROM raw_captures GROUP BY provider ORDER BY c DESC"
    ).fetchall()
    print("\nBy provider:")
    for p in providers:
        print(f"  {p['provider']}: {p['c']}")

    directions = conn.execute(
        "SELECT direction, COUNT(*) as c FROM raw_captures GROUP BY direction"
    ).fetchall()
    print("\nBy direction:")
    for d in directions:
        print(f"  {d['direction']}: {d['c']}")

    earliest = conn.execute("SELECT MIN(created_at) as t FROM raw_captures").fetchone()["t"]
    latest = conn.execute("SELECT MAX(created_at) as t FROM raw_captures").fetchone()["t"]
    print(f"\nTime range: {_ts(earliest)} → {_ts(latest)}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="PCE Proxy – inspect captured AI interactions")
    parser.add_argument("--last", type=int, default=20, help="Number of recent captures to show")
    parser.add_argument("--pair", type=str, default=None, help="Show a specific request/response pair")
    parser.add_argument("--stats", action="store_true", help="Show summary statistics")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"Database not found at {db_path}")
        print("Start the proxy first to create the database, or specify --db <path>")
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        if args.stats:
            print_stats(conn)
        elif args.pair:
            print_pair(args.pair, conn)
        else:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM raw_captures ORDER BY created_at DESC LIMIT ?", (args.last,)
            ).fetchall()
            rows = [dict(r) for r in reversed(rows)]  # chronological order
            print(f"Last {len(rows)} captures (oldest first):\n")
            print_captures(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
