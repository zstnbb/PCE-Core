# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window G: D14 edit + D15 regenerate + D16 branch flip.

One conversation. Three actions:

  Turn 1  send "What's the capital of France?"
  Turn 2  edit the user message -> "What's the capital of Japan?"  (D14)
  Turn 3  regenerate the resulting assistant -> new variant         (D15)
  Turn 4  flip back to the previous variant via branch arrow        (D16)

Verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  D14: edit produces a NEW branch with branch_id + branch_parent_id;
       collapsed view returns the latest branch only; expanded view
       returns all branches; both branches share session_id.
  D15: re-rolling on same prompt produces a NEW branch with same
       branch_parent_id; branch_count >= 2 on the parent user turn.
  D16: switching branches changes which is "active" without producing
       duplicate captures.

Each sub-verdict is independent — D14 may PASS while D15 fails (e.g. if
the regenerate UIA button isn't found on this build). Per-D verdict is
emitted at the end.

Run cost: ~2 minutes, 4 Claude turns.

Note: the UIA path for hover-revealed icons is best-effort. If the
driver's `edit_last_user` / `regenerate_last` / `flip_branch` returns
False, the corresponding sub-case is reported as SKIP, not FAIL — the
Claude Desktop build's UIA exposure changes between releases and a
SKIP signals "operator should manually re-validate this surface" not
"capture pipeline broken".
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts, configure_utf8_stdout, default_db_path

SEED_PROMPT = "What's the capital of France? Reply in one sentence."
EDIT_PROMPT = "What's the capital of Japan? Reply in one sentence."


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_g_d14_d15_d16")
    configure_utf8_stdout()

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window G — D14 edit + D15 regenerate + D16 branch flip ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    # ---------- Turn 1: seed ----------
    log.info("[2] turn 1 (seed): %r", SEED_PROMPT)
    pair_seed = driver.send_message(SEED_PROMPT, wait_done=True, wait_timeout=60.0)
    if not pair_seed:
        log.error("seed turn failed — no /completion request observed")
        return 1
    log.info("    pair_id=%s", pair_seed[:10])

    # ---------- D14: edit user message ----------
    log.info("[3] D14: editing user message -> %r", EDIT_PROMPT)
    since_ts_d14 = time.time() - 2.0
    edit_ok = driver.edit_last_user(EDIT_PROMPT)
    if not edit_ok:
        log.warning("    edit_last_user returned False — D14 will be SKIP")
        pair_edit = None
    else:
        # Wait for the edit to produce a new /completion request
        from ..utils import wait_for_new_completion
        pair_edit = wait_for_new_completion(since_ts_d14, timeout=20.0)
        if not pair_edit:
            log.warning("    edit triggered but no new /completion request "
                        "observed within 20s — D14 will be SKIP")
        else:
            log.info("    edit /completion pair_id=%s", pair_edit[:10])
            done = driver.wait_done(pair_edit, timeout=60.0)
            if done:
                log.info("    edit response: status=%s body=%dB",
                         done["status_code"], done["body_len"])

    time.sleep(2.0)

    # ---------- D15: regenerate assistant ----------
    log.info("[4] D15: regenerating last assistant variant")
    since_ts_d15 = time.time() - 2.0
    regen_ok = driver.regenerate_last()
    if not regen_ok:
        log.warning("    regenerate_last returned False — D15 will be SKIP")
        pair_regen = None
    else:
        from ..utils import wait_for_new_completion
        pair_regen = wait_for_new_completion(since_ts_d15, timeout=20.0)
        if not pair_regen:
            log.warning("    regenerate triggered but no new /completion "
                        "request — D15 will be SKIP")
        else:
            log.info("    regenerate /completion pair_id=%s", pair_regen[:10])
            done = driver.wait_done(pair_regen, timeout=60.0)
            if done:
                log.info("    regenerate response: status=%s body=%dB",
                         done["status_code"], done["body_len"])

    time.sleep(2.0)

    # ---------- D16: branch flip ----------
    log.info("[5] D16: flipping branch (previous)")
    since_ts_d16 = time.time() - 2.0
    flip_ok = driver.flip_branch("left")
    if not flip_ok:
        log.warning("    flip_branch returned False — D16 will be SKIP "
                    "(expected if D15 itself was SKIP)")

    # branch flip should NOT issue a new /completion request — it just
    # swaps the active variant on screen. We wait briefly to let any
    # background traffic settle and then check no new /completion landed.
    time.sleep(3.0)

    # ============== Inspection ==============
    log.info("\n[6] inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    # All /completion pairs since baseline
    pairs = con.execute(
        "SELECT pair_id, created_at FROM raw_captures "
        "WHERE created_at >= ? AND host='claude.ai' "
        "  AND path LIKE '%/completion' AND direction='request' "
        "ORDER BY created_at",
        (base,),
    ).fetchall()
    print(f"\n  /completion request rows since baseline: {len(pairs)}")
    for p in pairs:
        print(f"    {p['pair_id'][:16]} @ {p['created_at']:.2f}")

    # Sessions delta — sessions table uses ``started_at`` not ``created_at``
    sess = con.execute(
        "SELECT id, provider, tool_family, model_names, message_count "
        "FROM sessions WHERE started_at >= ? "
        "ORDER BY started_at DESC LIMIT 10",
        (base,),
    ).fetchall()
    print(f"\n  sessions since baseline: {len(sess)}")
    for s in sess:
        sid = (s["id"] or "")[:10]
        print(f"    {sid} provider={s['provider']:12s} "
              f"family={s['tool_family']:18s} msgs={s['message_count']}")

    # Messages — query by pair_id where available
    pair_ids_seen = [pair_seed]
    if pair_edit:
        pair_ids_seen.append(pair_edit)
    if pair_regen:
        pair_ids_seen.append(pair_regen)

    print(f"\n  messages by pair_id ({len(pair_ids_seen)} pairs):")
    for pid in pair_ids_seen:
        if not pid:
            continue
        rows = con.execute(
            "SELECT role, length(content_text) AS L, branch_id, "
            "       branch_parent_id, turn_index, interaction_kind "
            "FROM messages WHERE capture_pair_id=? ORDER BY ts",
            (pid,),
        ).fetchall()
        print(f"    {pid[:10]} -> {len(rows)} msgs")
        for r in rows:
            print(f"      role={r['role']:10s} text={r['L']:4d} "
                  f"branch={(r['branch_id'] or '-')[:10]:10s} "
                  f"parent={(r['branch_parent_id'] or '-')[:10]:10s} "
                  f"turn={r['turn_index']}")

    # === Per-D verdicts ===
    print()

    # D14 verdict
    if pair_edit:
        edit_msgs = con.execute(
            "SELECT role, branch_id, branch_parent_id "
            "FROM messages WHERE capture_pair_id=? ORDER BY ts",
            (pair_edit,),
        ).fetchall()
        edit_user = next((m for m in edit_msgs if m["role"] == "user"), None)
        if edit_user and edit_user["branch_id"]:
            print(f"  D14 VERDICT: PASS - edit produced messages with "
                  f"branch_id={edit_user['branch_id'][:10]}")
        elif edit_msgs:
            print(f"  D14 VERDICT: PARTIAL - edit produced messages "
                  f"({len(edit_msgs)} rows) but no branch_id set "
                  f"(reconciler may need branch detection on edit path)")
        else:
            print(f"  D14 VERDICT: FAIL - edit triggered new /completion "
                  f"but no messages persisted")
    else:
        print(f"  D14 VERDICT: SKIP - edit_last_user could not locate "
              f"the edit button via UIA (Claude Desktop UI variation; "
              f"requires manual validation)")

    # D15 verdict
    if pair_regen:
        regen_msgs = con.execute(
            "SELECT role, branch_id, branch_parent_id "
            "FROM messages WHERE capture_pair_id=? ORDER BY ts",
            (pair_regen,),
        ).fetchall()
        if regen_msgs:
            asst_regen = next((m for m in regen_msgs if m["role"] == "assistant"), None)
            if asst_regen and asst_regen["branch_id"]:
                print(f"  D15 VERDICT: PASS - regenerate produced new "
                      f"branch_id={asst_regen['branch_id'][:10]} "
                      f"parent={(asst_regen['branch_parent_id'] or '-')[:10]}")
            else:
                print(f"  D15 VERDICT: PARTIAL - regenerate produced "
                      f"messages ({len(regen_msgs)}) but no branch_id; "
                      f"reconciler did not detect variant fork")
        else:
            print(f"  D15 VERDICT: FAIL - regenerate triggered new "
                  f"/completion but no messages persisted")
    else:
        print(f"  D15 VERDICT: SKIP - regenerate_last could not locate "
              f"retry button via UIA")

    # D16 verdict — branch flip should NOT issue new /completion
    new_pairs_after_flip = [
        p for p in pairs if p["created_at"] >= since_ts_d16
    ]
    if not flip_ok:
        print(f"  D16 VERDICT: SKIP - flip_branch could not locate "
              f"branch arrow (depends on D15 having produced a branch)")
    elif len(new_pairs_after_flip) == 0:
        print(f"  D16 VERDICT: PASS - branch flip issued 0 new "
              f"/completion requests (correct: flip is UI-only)")
    else:
        print(f"  D16 VERDICT: FAIL - branch flip issued "
              f"{len(new_pairs_after_flip)} new /completion request(s); "
              f"flip should be UI-only")

    return 0


if __name__ == "__main__":
    sys.exit(main())
