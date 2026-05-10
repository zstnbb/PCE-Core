"""Focused D06 root-cause: did the normalizer get called, and what did it see?"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3

DB = pathlib.Path(os.path.expanduser("~/.pce/data/pce.db"))
PAIR = "4e88d79d75fb4cd2"
BASELINE_TS = 1778407148.167


def main() -> int:
    con = sqlite3.connect(str(DB))
    cur = con.cursor()

    # 1. Confirm both directions exist
    cur.execute(
        "SELECT id, direction, status_code, length(body_text_or_json) FROM raw_captures WHERE pair_id=? ORDER BY created_at",
        (PAIR,),
    )
    print("== /completion captures for D06 pair ==")
    cap_rows = cur.fetchall()
    for r in cap_rows:
        print(f"  id={r[0]} dir={r[1]} status={r[2]} body_len={r[3]}")

    # 2. Request body — only the keys + relevant subset
    cur.execute(
        "SELECT body_text_or_json FROM raw_captures WHERE pair_id=? AND direction='request'",
        (PAIR,),
    )
    row = cur.fetchone()
    if row:
        try:
            data = json.loads(row[0])
            print("\n== Request keys ==")
            print(f"  {sorted(data.keys())}")
            # important fields only
            for k in ("prompt", "files", "attachments", "parent_message_uuid",
                      "timezone", "rendering_mode", "model", "personalization_options",
                      "sync_sources"):
                if k in data:
                    val = data[k]
                    s = str(val)
                    if len(s) > 200:
                        s = s[:200] + f" ...({len(str(val))} chars total)"
                    print(f"  {k}: {s}")
        except Exception as e:
            print(f"  parse err: {e}")

    # 3. Response body — first 500 + length, plus check for SSE markers
    cur.execute(
        "SELECT body_text_or_json FROM raw_captures WHERE pair_id=? AND direction='response'",
        (PAIR,),
    )
    row = cur.fetchone()
    if row:
        body = row[0] or ""
        print(f"\n== Response body ({len(body)} chars) ==")
        # SSE format check
        is_sse = body.lstrip().startswith("event:") or "data:" in body[:200]
        print(f"  looks like SSE: {is_sse}")
        # Show the first event and a 'completion' line if present
        lines = body.split("\n")
        first_events = [ln for ln in lines if ln.startswith("event:")][:5]
        first_data = [ln for ln in lines if ln.startswith("data:")][:3]
        last_data = [ln for ln in lines if ln.startswith("data:")][-3:]
        print(f"  total lines: {len(lines)}")
        print(f"  first events: {first_events}")
        print(f"  first data: {[ln[:160] for ln in first_data]}")
        print(f"  last data:  {[ln[:160] for ln in last_data]}")
        # check completion / message_stop / message_delta presence
        for marker in ("event: message_start", "event: message_stop",
                        "event: content_block_start",
                        "event: content_block_delta",
                        "completion", '"type":"text"'):
            print(f"  has '{marker}': {marker in body}")

    # 4. Messages for the conversation UUID (in case they bound to a different pair)
    print("\n== messages tied to this conversation UUID ==")
    cur.execute(
        """
        SELECT m.id, m.role, m.session_id, length(m.content_text), m.capture_pair_id, s.session_key
        FROM messages m
        LEFT JOIN sessions s ON s.id = m.session_id
        WHERE s.session_key = ?
        ORDER BY m.ts DESC
        LIMIT 10
        """,
        ("68e3bd8d-ce7a-4e07-abb3-bc28e07ed400",),
    )
    for r in cur.fetchall():
        print(f"  {r}")

    # 5. pipeline_errors for this pair
    print("\n== pipeline_errors for this pair_id ==")
    cur.execute(
        "SELECT id, ts, stage, level, source_id, substr(message,1,200) FROM pipeline_errors WHERE pair_id=? ORDER BY ts DESC LIMIT 10",
        (PAIR,),
    )
    for r in cur.fetchall():
        print(f"  {r}")
    print("\n== pipeline_errors since baseline ==")
    cur.execute(
        "SELECT id, ts, stage, level, source_id, pair_id, substr(message,1,200) FROM pipeline_errors WHERE ts >= ? ORDER BY ts DESC LIMIT 20",
        (BASELINE_TS,),
    )
    for r in cur.fetchall():
        print(f"  {r}")

    # 6. Sessions started since baseline
    print("\n== Sessions started since baseline ==")
    cur.execute(
        "SELECT id, session_key, provider, message_count, started_at FROM sessions WHERE started_at >= ? ORDER BY started_at",
        (BASELINE_TS,),
    )
    for r in cur.fetchall():
        print(f"  {r}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
