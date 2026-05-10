"""Inspect Window A run: score D03 multi-turn / D07 code block / D04 cancel."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

BASELINE = float(Path("_baseline_ts.txt").read_text(encoding="ascii").strip())

PAIR_IDS = {
    "greet":      "721740a0ad",
    "follow_up":  "3584dc2156",
    "code_block": "3ebe95658b",
    "cancel_mid": "3e4c9b4110",
    "closure":    "46680c9886",
}

con = sqlite3.connect(str(Path.home() / ".pce" / "data" / "pce.db"))
con.row_factory = sqlite3.Row

print(f"baseline_ts = {BASELINE:.0f}  ({time.strftime('%H:%M:%S', time.localtime(BASELINE))})")
print(f"now         = {time.time():.0f}  ({time.strftime('%H:%M:%S')})")
print()

# Resolve full pair_ids from prefixes
print("=" * 70)
print("[A] Resolve pair_ids and inspect raw_captures rows")
print("=" * 70)
full_ids: dict[str, str] = {}
for label, prefix in PAIR_IDS.items():
    rows = con.execute(
        "SELECT DISTINCT pair_id FROM raw_captures "
        "WHERE created_at >= ? AND pair_id LIKE ?",
        (BASELINE, prefix + "%"),
    ).fetchall()
    if rows:
        full_ids[label] = rows[0]["pair_id"]
        print(f"  {label:12s} -> {rows[0]['pair_id']}")
    else:
        print(f"  {label:12s} -> (NOT FOUND)")

print()
print("Raw row presence per pair_id:")
for label, pid in full_ids.items():
    rows = con.execute(
        "SELECT direction, status_code, length(body_text_or_json) AS L, "
        "       body_format, model_name "
        "FROM raw_captures WHERE pair_id=?",
        (pid,),
    ).fetchall()
    dirs = {r["direction"]: r for r in rows}
    req = dirs.get("request")
    resp = dirs.get("response")
    print(f"  {label:12s}  req={'Y' if req else 'N':1s} "
          f"resp={'Y' if resp else 'N':1s}  ", end="")
    if resp:
        print(f"resp_status={resp['status_code']} body={resp['L']}B "
              f"fmt={resp['body_format']!r} model={resp['model_name']!r}")
    else:
        print(f"resp=MISSING  (req body_len={req['L'] if req else '-'})")

# === D03 — multi-turn session grouping ===
print()
print("=" * 70)
print("[B] D03 score — sessions & messages for these 5 pair_ids")
print("=" * 70)
pids = list(full_ids.values())
if pids:
    placeholders = ",".join("?" * len(pids))
    msg_rows = con.execute(
        f"SELECT capture_pair_id, session_id, role, turn_index, model_name, "
        f"       length(content_text) AS L "
        f"FROM messages WHERE capture_pair_id IN ({placeholders}) "
        f"ORDER BY ts",
        pids,
    ).fetchall()
    print(f"  messages rows: {len(msg_rows)}")
    sessions_seen = {}
    for m in msg_rows:
        sid = m["session_id"]
        sessions_seen.setdefault(sid, []).append(m)
        print(f"    pair={(m['capture_pair_id'] or '')[:10]}  "
              f"session={(sid or '')[:10]}  role={m['role']:10s} "
              f"turn={m['turn_index'] or 0:2d}  text_len={m['L'] or 0:4d}  "
              f"model={m['model_name']!r}")
    print()
    print(f"  distinct session_ids: {len(sessions_seen)}")
    for sid, mlist in sessions_seen.items():
        s = con.execute(
            "SELECT session_key, model_names, started_at, ended_at, "
            "       provider, tool_family "
            "FROM sessions WHERE id=?", (sid,),
        ).fetchone()
        if s:
            sk = s["session_key"]
            mc = con.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE session_id=?", (sid,),
            ).fetchone()["c"]
            print(f"    [{sid[:10]}] session_key={sk!r}  total_msgs={mc}  "
                  f"models={s['model_names']!r}  provider={s['provider']!r}  "
                  f"({len(mlist)} from this run)")

    # D03 verdict
    if len(sessions_seen) == 1:
        print(f"\n  D03 VERDICT: PASS - all {len(msg_rows)} messages in 1 session")
    else:
        print(f"\n  D03 VERDICT: FAIL - {len(sessions_seen)} sessions for 5 turns")

# === D07 — code block in turn 3 ===
print()
print("=" * 70)
print("[C] D07 score — turn 3 (code_block) assistant content")
print("=" * 70)
if "code_block" in full_ids:
    code_pid = full_ids["code_block"]
    msg = con.execute(
        "SELECT role, content_text, content_json FROM messages "
        "WHERE capture_pair_id=? AND role='assistant'",
        (code_pid,),
    ).fetchone()
    if msg:
        text = msg["content_text"] or ""
        cj_raw = msg["content_json"] or ""
        print(f"  assistant text length: {len(text)} chars")
        print(f"  text[:300]: {text[:300]!r}")
        # Heuristic: look for fenced code block markers
        has_fence = "```" in text
        has_python_tag = "```python" in text or "```py" in text
        print(f"  has fenced ``` block:    {has_fence}")
        print(f"  has python language tag: {has_python_tag}")
        # Check content_json for any structured code-block info
        if cj_raw:
            try:
                cj = json.loads(cj_raw)
                print(f"  content_json keys: {list(cj.keys()) if isinstance(cj, dict) else type(cj)}")
                if isinstance(cj, dict):
                    pt = cj.get("plain_text", "")
                    print(f"  cj.plain_text len: {len(pt)}")
                    print(f"  cj.plain_text[:200]: {pt[:200]!r}")
            except Exception as e:
                print(f"  content_json parse error: {e}")

        if has_python_tag:
            print(f"\n  D07 VERDICT: PASS - fenced ```python``` block preserved")
        elif has_fence:
            print(f"\n  D07 VERDICT: PARTIAL - fenced ``` block present but language tag missing")
        else:
            print(f"\n  D07 VERDICT: FAIL - no fenced code block in stored text")
    else:
        print(f"  assistant message NOT FOUND for code_block pair")
        print(f"  D07 VERDICT: FAIL - no assistant row")

# === D04 — cancel mid-stream ===
print()
print("=" * 70)
print("[D] D04 score — turn 4 (cancel_mid) outcome")
print("=" * 70)
if "cancel_mid" in full_ids:
    cancel_pid = full_ids["cancel_mid"]
    # Re-check raw_captures NOW (may have arrived after the case timed out at 15s)
    rows = con.execute(
        "SELECT direction, status_code, length(body_text_or_json) AS L, "
        "       body_text_or_json, body_format, created_at "
        "FROM raw_captures WHERE pair_id=?",
        (cancel_pid,),
    ).fetchall()
    print(f"  raw_captures rows for cancel_mid: {len(rows)}")
    for r in rows:
        ts = time.strftime("%H:%M:%S", time.localtime(r["created_at"]))
        print(f"    {ts}  dir={r['direction']:8s} status={str(r['status_code'] or '-'):3s} "
              f"body={r['L']:5d}B fmt={r['body_format']!r}")
    resp_row = next((r for r in rows if r["direction"] == "response"), None)
    if resp_row:
        body = resp_row["body_text_or_json"] or ""
        print(f"\n  response body[:400]: {body[:400]!r}")
        print(f"\n  response body[-300:]: {body[-300:]!r}" if len(body) > 400 else "")
        # Look for cancel indicators in the SSE stream
        markers = {
            "message_stop": "(natural finish)" if "message_stop" in body else "",
            "stop_reason": "stop_reason" in body,
            "max_tokens": "max_tokens" in body,
            "end_turn": "end_turn" in body,
            "interrupted": "interrupted" in body,
            "canceled": "canceled" in body or "cancelled" in body,
        }
        print(f"\n  SSE markers in response body:")
        for marker, val in markers.items():
            print(f"    {marker:15s}: {val}")
    # Also check messages table (note: schema has no `error` column —
    # cancel state would have to live in content_json or interaction_kind).
    msgs = con.execute(
        "SELECT role, length(content_text) AS L, interaction_kind, "
        "       content_json FROM messages "
        "WHERE capture_pair_id=? ORDER BY ts",
        (cancel_pid,),
    ).fetchall()
    print(f"\n  messages rows for cancel_mid: {len(msgs)}")
    for m in msgs:
        cj_summary = ""
        if m["content_json"]:
            try:
                cj = json.loads(m["content_json"])
                if isinstance(cj, dict):
                    cj_summary = f" cj_keys={list(cj.keys())}"
            except Exception:
                cj_summary = " cj=invalid"
        print(f"    role={m['role']:10s} text_len={m['L']:4d} "
              f"kind={m['interaction_kind']!r}{cj_summary}")

    # D04 verdict
    user_count = sum(1 for m in msgs if m["role"] == "user")
    asst_count = sum(1 for m in msgs if m["role"] == "assistant")
    has_resp_row = any(r["direction"] == "response" for r in rows)
    print()
    if not has_resp_row and user_count >= 1 and asst_count == 0:
        print(f"  D04 VERDICT: FAIL - cancel happened but pipeline lost evidence "
              f"(no response row in raw_captures, no assistant message in messages)")
        print(f"  Root cause hypothesis: pce_proxy/addon.py does not flush "
              f"buffered SSE body when client aborts the connection mid-stream")
    elif has_resp_row and asst_count >= 1:
        # Check if response body suggests truncation (no message_stop)
        body = (rows[0]["body_text_or_json"] or "") if rows else ""
        # rows variable was already fetched above; let's get response body
        resp_row = next((r for r in rows if r["direction"] == "response"), None)
        rbody = (resp_row["body_text_or_json"] or "") if resp_row else ""
        if "message_stop" in rbody:
            print(f"  D04 VERDICT: PARTIAL - response was persisted but contains "
                  f"message_stop event (cancel may have arrived after stream "
                  f"naturally finished; need a longer prompt)")
        else:
            print(f"  D04 VERDICT: PASS - response persisted with truncation "
                  f"signal (no message_stop), assistant message exists")
    else:
        print(f"  D04 VERDICT: UNCLEAR - manual review needed")

con.close()
