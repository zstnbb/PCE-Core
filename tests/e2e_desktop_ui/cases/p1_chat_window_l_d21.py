# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window L: D21 artifact (interactive HTML / React).

Asks Claude Desktop to create a React component as an artifact (a
counter with +/- buttons). Same SSE delta machinery as D20 but the
artifact body is JSX/TSX source code rather than markdown — verifies
that the reconciler handles non-markdown artifact content_types too.

D21 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "A 'create an HTML / React component' prompt produces an artifact
   whose source code is fully reconstructed in ``content_json``
   (same delta path as D20); ``content_type`` distinguishes the
   artifact kind."

PASS / PARTIAL / FAIL semantics mirror D20.

Run cost: ~30s, 1 Claude turn.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts, configure_utf8_stdout, default_db_path

PROMPT = (
    "Create an artifact with a simple React component. "
    "It should be a counter with +1 and -1 buttons and a display showing "
    "the current count. Use plain inline styles, no Tailwind. "
    "Title the artifact 'PCE D21 Counter'."
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_l_d21")
    configure_utf8_stdout()

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window L — D21 artifact (React) ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    log.info("[2] sending React artifact prompt")
    pair_id = driver.send_message(PROMPT, wait_done=True, wait_timeout=180.0)
    if not pair_id:
        log.error("no /completion request observed")
        return 1
    log.info("    pair_id=%s", pair_id[:10])

    # ============== Inspection ==============
    log.info("\n[3] D21 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    resp = con.execute(
        "SELECT length(body_text_or_json) AS L, body_text_or_json AS body, "
        "       status_code "
        "FROM raw_captures WHERE pair_id=? AND direction='response'",
        (pair_id,),
    ).fetchone()
    if not resp:
        print("\n  D21 VERDICT: FAIL - no response row")
        return 1

    body = resp["body"] or ""
    print(f"\n  response: status={resp['status_code']} body_len={resp['L']}")
    n_input_json_delta = body.count('"input_json_delta"')
    n_partial_json = body.count('"partial_json"')
    n_tool_use = body.count('"type":"tool_use"')
    has_react_keywords = any(
        k in body.lower()
        for k in ("usestate", "react", "function", "onclick", "setcount", "<button")
    )
    has_artifact_kind_react = any(
        k in body.lower() for k in (
            '"type":"application/vnd.ant.react"',
            '"language":"jsx"', '"language":"tsx"',
            "react", "vnd.ant",
        )
    )
    print(f"  input_json_delta:           {n_input_json_delta}")
    print(f"  partial_json:               {n_partial_json}")
    print(f"  tool_use markers:           {n_tool_use}")
    print(f"  body has React keywords:    {has_react_keywords}")
    print(f"  body has React artifact kind tag: {has_artifact_kind_react}")

    msgs = con.execute(
        "SELECT role, content_text, content_json, length(content_text) AS L "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D21 pair: {len(msgs)}")
    asst = next((m for m in msgs if m["role"] == "assistant"), None)
    artifact_in_msg = False
    if asst:
        atext = asst["content_text"] or ""
        print(f"    assistant text len: {len(atext)}")
        print(f"    head: {atext[:240]!r}")
        if asst["content_json"]:
            try:
                cj = json.loads(asst["content_json"])
                if isinstance(cj, dict):
                    arts = cj.get("attachments") or cj.get("artifacts") or []
                    print(f"    content_json.attachments|artifacts: {len(arts)} entries")
                    for a in arts[:5]:
                        astr = str(a)
                        print(f"      {astr[:200]!r}")
                        if any(k in astr.lower() for k in (
                            "usestate", "react", "onclick", "setcount", "counter",
                        )):
                            artifact_in_msg = True
            except Exception as exc:
                print(f"    content_json parse error: {exc}")

    print()
    if n_input_json_delta >= 5 and artifact_in_msg:
        print(f"  D21 VERDICT: PASS - {n_input_json_delta} delta events "
              f"AND React artifact reconstructed in content_json")
        return 0
    if n_input_json_delta >= 5 and has_react_keywords:
        print(f"  D21 VERDICT: PARTIAL - {n_input_json_delta} delta "
              f"events with React keywords in SSE BUT not yet reconciled "
              f"into messages.content_json (same gap as D20)")
        return 0
    if n_input_json_delta > 0:
        print(f"  D21 VERDICT: PARTIAL - {n_input_json_delta} delta events "
              f"present but React signal weak (model may have routed to "
              f"chat text instead of artifact)")
        return 0
    print(f"  D21 VERDICT: FAIL - no input_json_delta events; React "
          f"artifact was not actually created")
    return 1


if __name__ == "__main__":
    sys.exit(main())
