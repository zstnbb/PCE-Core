# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window K: D20 artifact (text / markdown).

Asks Claude Desktop to create a markdown artifact (a 5-item todo list).
Claude renders artifacts in a side panel; the chat-side text typically
just says "I've created an artifact named X". The actual artifact body
streams over the SSE response as a sequence of
``content_block_delta`` events with ``input_json_delta`` partial_json
chunks (Claude's ``create_file`` MCP-like tool — same mechanism the
web Round 2 / 2026-04-27 verification documented for C14 / C15).

D20 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "A 'create a markdown / SVG / Mermaid artifact' prompt produces an
   assistant message whose chat-side text references the artifact AND
   whose ``content_json`` contains the full artifact body, reconstructed
   from ``tool_use.input_json_delta`` SSE events."

PASS:
  - SSE response body contains >= 5 ``input_json_delta`` events
  - chat-side assistant text references "artifact" or "todo" or
    similar (informational; not strictly required)
  - either reassembled artifact body is in ``content_json`` of the
    assistant message (full PASS) OR the deltas are at least present
    in raw_captures (PARTIAL — reconciler join not yet wired)

PARTIAL:
  - input_json_delta present in SSE but content_json doesn't have
    reconstructed artifact body (= same gap as web's `fu_recon_join`
    item 1)

FAIL:
  - no input_json_delta events at all (artifact wasn't actually
    created — model declined or routed to chat-only)

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
    "Create a markdown artifact titled 'PCE D20 Test Todo' "
    "containing a 5-item todo list about preparing breakfast. "
    "Use checkbox markdown ([ ]) syntax."
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_k_d20")
    configure_utf8_stdout()

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window K — D20 artifact (text/markdown) ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    log.info("[2] sending artifact prompt")
    pair_id = driver.send_message(PROMPT, wait_done=True, wait_timeout=120.0)
    if not pair_id:
        log.error("no /completion request observed")
        return 1
    log.info("    pair_id=%s", pair_id[:10])

    # ============== Inspection ==============
    log.info("\n[3] D20 inspection")
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
        print("\n  D20 VERDICT: FAIL - no response row")
        return 1

    body = resp["body"] or ""
    print(f"\n  response: status={resp['status_code']} body_len={resp['L']}")
    n_input_json_delta = body.count('"input_json_delta"')
    n_partial_json = body.count('"partial_json"')
    n_tool_use = body.count('"type":"tool_use"')
    n_content_block = body.count("event: content_block_delta")
    print(f"  input_json_delta strings:    {n_input_json_delta}")
    print(f"  partial_json strings:        {n_partial_json}")
    print(f"  tool_use type markers:       {n_tool_use}")
    print(f"  content_block_delta events:  {n_content_block}")

    msgs = con.execute(
        "SELECT role, content_text, content_json, length(content_text) AS L "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D20 pair: {len(msgs)}")
    asst = next((m for m in msgs if m["role"] == "assistant"), None)
    artifact_body_in_msg = False
    if asst:
        atext = asst["content_text"] or ""
        print(f"    assistant text len: {len(atext)}")
        print(f"    assistant head: {atext[:240]!r}")
        if asst["content_json"]:
            try:
                cj = json.loads(asst["content_json"])
                if isinstance(cj, dict):
                    arts = cj.get("attachments") or cj.get("artifacts") or []
                    print(f"    content_json.attachments|artifacts: {len(arts)} entries")
                    for a in arts[:5]:
                        astr = str(a)
                        print(f"      {astr[:200]!r}")
                        if "[ ]" in astr or "todo" in astr.lower() or "breakfast" in astr.lower():
                            artifact_body_in_msg = True
            except Exception as exc:
                print(f"    content_json parse error: {exc}")

    print()
    if n_input_json_delta >= 5 and artifact_body_in_msg:
        print(f"  D20 VERDICT: PASS - {n_input_json_delta} input_json_delta "
              f"events captured AND artifact body present in "
              f"assistant content_json")
        return 0
    if n_input_json_delta >= 5:
        print(f"  D20 VERDICT: PARTIAL - {n_input_json_delta} "
              f"input_json_delta events in raw SSE BUT artifact body "
              f"not yet reconciled into messages.content_json "
              f"(= web `fu_recon_join` item 1 — same gap)")
        return 0
    if n_input_json_delta > 0:
        print(f"  D20 VERDICT: PARTIAL - only {n_input_json_delta} "
              f"input_json_delta events; artifact may have been small "
              f"or model didn't emit standard tool_use stream")
        return 0
    print(f"  D20 VERDICT: FAIL - no input_json_delta events; model "
          f"didn't create an artifact (likely answered as chat text only)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
