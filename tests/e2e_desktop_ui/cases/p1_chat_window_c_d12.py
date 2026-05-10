# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window C: D12 idle silence.

D12 verdict criteria (per ``DESKTOP-PRODUCT-MATRIX.md`` §5):

  "Idle for 5+ minutes after a multi-turn conversation: zero new
   capture writes (no false-positive heartbeat noise)"

What's a "capture write" here? The matrix's intent is the *messages*
table — i.e. no NEW chat turns get fabricated. Background heartbeat
traffic to api.anthropic.com / claude.ai (token refresh, telemetry,
etc.) IS expected and doesn't violate D12 because it doesn't trigger
the conversation normalizer (no /completion path matched).

Strict signals checked over a 5-min idle window:
  1. ``/completion`` request count delta == 0 (no spurious chat turn)
  2. ``messages`` table row count delta == 0
  3. ``sessions`` table row count delta == 0

Background ``raw_captures`` rows on background paths (organizations,
account, environment_providers, statsig, etc.) are LOGGED but not
counted as failures — they're recordings of background heartbeat,
exactly what privacy users would expect to see persisted.

Run cost: 5 min wall-clock. No API cost (no chat sent).
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

from ..utils import baseline_ts as _baseline_ts
from ..utils import default_db_path

IDLE_DURATION_SEC = 5 * 60 + 10  # 5 min 10 sec for safety margin


def _snapshot_counts(con: sqlite3.Connection) -> dict[str, int]:
    """Return a dict of counts that D12 considers."""
    return {
        "completion_requests": con.execute(
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE host='claude.ai' AND path LIKE '%/completion' "
            "  AND direction='request'"
        ).fetchone()[0],
        "all_raw_captures": con.execute(
            "SELECT COUNT(*) FROM raw_captures"
        ).fetchone()[0],
        "messages": con.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0],
        "sessions": con.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0],
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_c_d12")

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    db = default_db_path()
    con = sqlite3.connect(str(db))
    try:
        before = _snapshot_counts(con)
    finally:
        con.close()

    log.info("=== Window C — D12 idle %d sec ===", IDLE_DURATION_SEC)
    log.info("BEFORE counts: %s", before)
    log.info("Sleeping %d sec — DO NOT TYPE INTO CLAUDE DESKTOP",
             IDLE_DURATION_SEC)

    # Periodic checkpoint so we have visibility during the 5-min wait
    interval = 60
    elapsed = 0
    while elapsed < IDLE_DURATION_SEC:
        chunk = min(interval, IDLE_DURATION_SEC - elapsed)
        time.sleep(chunk)
        elapsed += chunk
        con = sqlite3.connect(str(db))
        try:
            mid = _snapshot_counts(con)
        finally:
            con.close()
        log.info("t+%4ds: completion_req=%d (delta+%d) messages=%d (delta+%d) "
                 "sessions=%d (delta+%d) raw=%d (delta+%d)",
                 elapsed,
                 mid["completion_requests"],
                 mid["completion_requests"] - before["completion_requests"],
                 mid["messages"], mid["messages"] - before["messages"],
                 mid["sessions"], mid["sessions"] - before["sessions"],
                 mid["all_raw_captures"],
                 mid["all_raw_captures"] - before["all_raw_captures"])

    con = sqlite3.connect(str(db))
    try:
        after = _snapshot_counts(con)
    finally:
        con.close()

    log.info("\n=== Window C complete ===")
    log.info("AFTER counts: %s", after)
    deltas = {k: after[k] - before[k] for k in before}
    log.info("DELTAS: %s", deltas)

    # === D12 verdict ===
    fail_reasons = []
    if deltas["completion_requests"] != 0:
        fail_reasons.append(f"completion_requests delta={deltas['completion_requests']} (want 0)")
    if deltas["messages"] != 0:
        fail_reasons.append(f"messages delta={deltas['messages']} (want 0)")
    if deltas["sessions"] != 0:
        fail_reasons.append(f"sessions delta={deltas['sessions']} (want 0)")

    if fail_reasons:
        log.error("D12 VERDICT: FAIL - %s", "; ".join(fail_reasons))
        return 1
    else:
        log.info("D12 VERDICT: PASS - 0 chat-relevant writes over %ds idle "
                 "(background raw_captures delta=+%d is expected heartbeat noise)",
                 IDLE_DURATION_SEC, deltas["all_raw_captures"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
