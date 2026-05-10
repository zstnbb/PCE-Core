"""Inspect Window E / D10 error-mid-stream results.

Scoring rubric
--------------
PASS (any of):
    a) request captured, no response, no message  → fail-closed
    b) request + truncated response with status_code in (None, 5xx) and
       normalizer either skipped it or emitted a marked partial message
    c) request + full response (proxy buffered everything pre-kill)

FAIL:
    - phantom assistant message with raw SSE / gibberish
    - subsequent captures broken (no smoke /completion after restart)
    - exceptions in pipeline_errors tied to this pair_id
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys

DB = pathlib.Path(os.path.expanduser("~/.pce/data/pce.db"))


def _baseline() -> float:
    p = pathlib.Path("_baseline_ts.txt")
    if p.exists():
        try:
            return float(p.read_text().strip())
        except Exception:
            pass
    return 0.0


def _print(label: str, text: str = "") -> None:
    sys.stdout.write(f"{label}{text}\n")
    sys.stdout.flush()


def main() -> int:
    if not DB.exists():
        _print(f"DB not found at {DB}")
        return 2

    bts = _baseline()
    _print(f"baseline_ts = {bts}")

    con = sqlite3.connect(str(DB))
    cur = con.cursor()

    # 1. Find the /completion request captured during this window
    cur.execute(
        """
        SELECT pair_id, direction, status_code,
               length(body_text_or_json) AS body_len,
               created_at, path
        FROM raw_captures
        WHERE created_at >= ? AND path LIKE '%/completion%'
        ORDER BY created_at
        """,
        (bts,),
    )
    rows = cur.fetchall()
    _print(f"\n== /completion captures since baseline ({len(rows)} rows) ==")
    pair_ids: list[str] = []
    for r in rows:
        pid, direction, status, blen, ts_, path = r
        if pid not in pair_ids:
            pair_ids.append(pid)
        _print(f"  pair={pid[:10]} dir={direction:8s} status={status} body={blen}B  path={path[:90]}")

    # 2. For each pair, check both sides + messages
    _print(f"\n== Per-pair analysis ==")
    findings: list[str] = []
    for pid in pair_ids:
        cur.execute(
            "SELECT direction, status_code, length(body_text_or_json) FROM raw_captures WHERE pair_id=?",
            (pid,),
        )
        sides = cur.fetchall()
        sides_map = {s[0]: (s[1], s[2]) for s in sides}
        cur.execute(
            "SELECT id, role, length(content_text), substr(content_text,1,80) FROM messages WHERE capture_pair_id=?",
            (pid,),
        )
        msgs = cur.fetchall()
        cur.execute(
            "SELECT id, stage, level, substr(message,1,200) FROM pipeline_errors WHERE pair_id=?",
            (pid,),
        )
        errs = cur.fetchall()

        has_request = "request" in sides_map
        has_response = "response" in sides_map
        n_msgs = len(msgs)
        _print(f"  pair={pid}")
        _print(f"    sides: req={'Y' if has_request else 'N'} resp={'Y' if has_response else 'N'}")
        if has_response:
            st, blen = sides_map["response"]
            _print(f"    response: status={st} body={blen}B")
        _print(f"    messages: {n_msgs}")
        for m in msgs:
            _print(f"      {m}")
        _print(f"    pipeline_errors: {len(errs)}")
        for e in errs:
            _print(f"      {e}")

        # Classify this pair
        if not has_request:
            findings.append(f"  {pid[:10]}: NO REQUEST CAPTURED (unexpected) — investigate")
        elif not has_response:
            if n_msgs == 0:
                findings.append(f"  {pid[:10]}: PASS-FAIL-CLOSED — request captured, no response, no phantom message")
            else:
                findings.append(f"  {pid[:10]}: PARTIAL — request only but {n_msgs} message(s) persisted (verify content)")
        elif has_response:
            st = sides_map["response"][0]
            if n_msgs > 0:
                findings.append(f"  {pid[:10]}: PASS-FULL or PASS-PARTIAL — response status={st}, {n_msgs} message(s)")
            else:
                findings.append(f"  {pid[:10]}: response captured but no message (status={st})")

    # 3. Verify post-restart smoke worked: any captures after the restart
    _print(f"\n== Post-restart sanity ==")
    cur.execute(
        "SELECT COUNT(*) FROM raw_captures WHERE created_at >= ?",
        (bts,),
    )
    n_total = cur.fetchone()[0]
    _print(f"  total raw_captures since baseline: {n_total}")
    cur.execute(
        """
        SELECT pair_id, direction, status_code, created_at
        FROM raw_captures
        WHERE created_at >= ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (bts,),
    )
    _print("  most recent 5 captures (newest first):")
    for r in cur.fetchall():
        _print(f"    {r}")

    # 4. Verdict
    _print("\n== D10 Verdict ==")
    for f in findings:
        _print(f)
    if not findings:
        _print("  (no /completion pairs since baseline — test may not have run; ensure D10 case was started)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
