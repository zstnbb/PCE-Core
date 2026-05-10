"""Inspect Window B (D11 long-context) — score:
- Session count == 1
- 50 user + 50 assistant messages (all 100 with our pair_ids)
- Cumulative token_estimate >= 8000
- Monotonic turn_index
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

BASELINE = float(Path("_baseline_ts.txt").read_text(encoding="ascii").strip())
DB = Path.home() / ".pce" / "data" / "pce.db"

print(f"baseline_ts = {BASELINE:.0f}  ({time.strftime('%H:%M:%S', time.localtime(BASELINE))})")
print(f"now         = {time.time():.0f}  ({time.strftime('%H:%M:%S')})")
print()

con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row

# Query all /completion request pair_ids since baseline (no log parsing)
print("=" * 70)
print("[A] /completion request pair_ids since baseline (delta-window query)")
print("=" * 70)
rows = con.execute(
    "SELECT DISTINCT pair_id, created_at FROM raw_captures "
    "WHERE created_at >= ? AND host='claude.ai' "
    "  AND path LIKE '%/completion' AND direction='request' "
    "ORDER BY created_at",
    (BASELINE,),
).fetchall()
full_ids = [r["pair_id"] for r in rows]
print(f"  found: {len(full_ids)} /completion requests in delta")

# Response presence
missing_resp = []
for pid in full_ids:
    resp = con.execute(
        "SELECT length(body_text_or_json) AS L FROM raw_captures "
        "WHERE pair_id=? AND direction='response'",
        (pid,),
    ).fetchone()
    if not resp or not resp["L"]:
        missing_resp.append(pid)
print(f"  missing response: {len(missing_resp)}")
if missing_resp:
    for pid in missing_resp[:5]:
        print(f"    {pid}")

# === D11 — session collapse + zero drop + cumulative tokens ===
print()
print("=" * 70)
print("[B] D11 score — sessions / messages / tokens for 50-turn run")
print("=" * 70)
if full_ids:
    placeholders = ",".join("?" * len(full_ids))
    msgs = con.execute(
        f"SELECT capture_pair_id, session_id, role, turn_index, "
        f"       token_estimate, length(content_text) AS L "
        f"FROM messages WHERE capture_pair_id IN ({placeholders}) "
        f"ORDER BY ts",
        full_ids,
    ).fetchall()
    print(f"  messages rows for these 50 pair_ids: {len(msgs)}")

    user_count = sum(1 for m in msgs if m["role"] == "user")
    asst_count = sum(1 for m in msgs if m["role"] == "assistant")
    print(f"    user:      {user_count}")
    print(f"    assistant: {asst_count}")

    sessions_seen = sorted({m["session_id"] for m in msgs})
    print(f"    distinct session_ids: {len(sessions_seen)}")
    for sid in sessions_seen:
        s = con.execute(
            "SELECT session_key, model_names, started_at, ended_at, provider "
            "FROM sessions WHERE id=?", (sid,),
        ).fetchone()
        if s:
            mc = con.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,),
            ).fetchone()["c"]
            print(f"      [{sid[:10]}] session_key={s['session_key']!r}  "
                  f"total_msgs={mc}  models={s['model_names']!r}")

    # Token estimate sum
    tokens_user = sum(m["token_estimate"] or 0 for m in msgs if m["role"] == "user")
    tokens_asst = sum(m["token_estimate"] or 0 for m in msgs if m["role"] == "assistant")
    tokens_total = tokens_user + tokens_asst
    print(f"\n  token_estimate sums:")
    print(f"    user:      {tokens_user}")
    print(f"    assistant: {tokens_asst}")
    print(f"    TOTAL:     {tokens_total}")
    print(f"    >= 8000:   {tokens_total >= 8000}")

    # Turn index monotonicity (per session)
    print(f"\n  turn_index continuity check:")
    for sid in sessions_seen:
        sess_msgs = [m for m in msgs if m["session_id"] == sid]
        turns = [m["turn_index"] for m in sess_msgs]
        is_mono = all(turns[i] <= turns[i + 1] for i in range(len(turns) - 1))
        print(f"    [{sid[:10]}] turn_indices min={min(turns)} max={max(turns)}  "
              f"monotonic={is_mono}  count={len(turns)}")

    # === D11 verdict ===
    print()
    fail_reasons = []
    if len(sessions_seen) != 1:
        fail_reasons.append(f"sessions={len(sessions_seen)} (want 1)")
    if user_count != 50:
        fail_reasons.append(f"user_msgs={user_count} (want 50)")
    if asst_count != 50:
        fail_reasons.append(f"asst_msgs={asst_count} (want 50)")
    if tokens_total < 8000:
        fail_reasons.append(f"total_tokens={tokens_total} (<8000)")

    if fail_reasons:
        print(f"  D11 VERDICT: FAIL - {'; '.join(fail_reasons)}")
    else:
        print(f"  D11 VERDICT: PASS - 50/50 user + 50/50 asst, "
              f"1 session, {tokens_total} cumulative tokens >= 8K")
else:
    print(f"  D11 VERDICT: FAIL - 0 pair_ids resolved")

con.close()
