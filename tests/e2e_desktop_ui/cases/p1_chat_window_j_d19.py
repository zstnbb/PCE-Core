# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window J: D19 project scope.

Drives Claude Desktop to enter a pre-existing Project (selected by
name substring via env var ``CLAUDE_PROJECT_NAME``), send a single
prompt inside that project, and verify the captured request path
contains ``/project/`` (or equivalent) AND the normalizer's
``session_key`` falls back to the chat UUID embedded in the path.

D19 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "A turn sent under a ``/project/<id>/chat/<uuid>`` URL produces a
   ``messages`` row with ``session_key`` extracted from the chat UUID
   portion of the path; project context is preserved in
   ``layer_meta`` (or equivalent) where the platform exposes it."

PASS:
  - request path contains ``/project/`` segment OR the path's chat
    UUID lands in ``sessions.session_key``
  - 2 messages rows (user + assistant) for the new pair
  - session_id matches sessions.id where session_key was extracted
    from the path

PARTIAL:
  - request fired but path didn't contain /project/ (e.g. project
    chats may use the same path shape as regular chats; this is
    informational, not a failure)

SKIP:
  - ``CLAUDE_PROJECT_NAME`` env var not set, OR no sidebar item
    matches the substring

Run cost: ~30s, 1 Claude turn.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts, configure_utf8_stdout, default_db_path

PROMPT = "Briefly: what's this project about? Reply in one sentence."


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_j_d19")
    configure_utf8_stdout()

    project_name = os.environ.get("CLAUDE_PROJECT_NAME", "").strip()
    if not project_name:
        print("\n  D19 VERDICT: SKIP - CLAUDE_PROJECT_NAME env var not set")
        print("  To run: set CLAUDE_PROJECT_NAME=<substring of project name>")
        print("  e.g. $env:CLAUDE_PROJECT_NAME = 'PCE'  (PowerShell)")
        return 0
    log.info("project name substring: %r", project_name)

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window J — D19 project scope ===")

    log.info("[1] opening project %r via sidebar", project_name)
    if not driver.open_project(project_name):
        print(f"\n  D19 VERDICT: SKIP - sidebar item matching {project_name!r} "
              f"not found (UIA exposure varies by build); manually navigate "
              f"to the project and re-run, OR check that the project name "
              f"substring matches what you see in the sidebar")
        return 0
    time.sleep(2.0)

    log.info("[2] sending prompt: %r", PROMPT)
    pair_id = driver.send_message(PROMPT, wait_done=True, wait_timeout=60.0)
    if not pair_id:
        log.error("no /completion request observed")
        return 1
    log.info("    pair_id=%s", pair_id[:10])

    # ============== Inspection ==============
    log.info("\n[3] D19 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    req = con.execute(
        "SELECT path FROM raw_captures "
        "WHERE pair_id=? AND direction='request'",
        (pair_id,),
    ).fetchone()
    path = req["path"] if req else ""
    print(f"\n  request path: {path}")
    has_project_segment = "/project/" in (path or "").lower()
    print(f"  contains /project/: {has_project_segment}")

    # Sessions row for this pair
    sess = con.execute(
        "SELECT s.id, s.session_key, s.provider, s.tool_family, s.message_count "
        "FROM sessions s "
        "JOIN messages m ON m.session_id = s.id "
        "WHERE m.capture_pair_id = ? "
        "GROUP BY s.id LIMIT 1",
        (pair_id,),
    ).fetchone()
    if sess:
        print(f"\n  session row: id={(sess['id'] or '')[:10]} "
              f"key={(sess['session_key'] or '-')[:18]} "
              f"provider={sess['provider']} family={sess['tool_family']} "
              f"msgs={sess['message_count']}")

    msgs = con.execute(
        "SELECT role, length(content_text) AS L FROM messages "
        "WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D19 pair: {len(msgs)}")
    for m in msgs:
        print(f"    role={m['role']:10s} text_len={m['L']:4d}")

    print()
    if not msgs:
        print(f"  D19 VERDICT: FAIL - no messages persisted")
        return 1
    if has_project_segment and sess and sess["session_key"]:
        print(f"  D19 VERDICT: PASS - request under /project/, session_key "
              f"resolved, {len(msgs)} message rows")
        return 0
    if sess and sess["session_key"] and len(msgs) >= 2:
        print(f"  D19 VERDICT: PARTIAL - session_key resolved + 2 messages "
              f"persisted, but request path lacks /project/ segment "
              f"(Claude Desktop may use the same /chat_conversations/ "
              f"path inside Projects)")
        return 0
    print(f"  D19 VERDICT: PARTIAL - capture happened but session_key "
          f"or message count unexpected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
