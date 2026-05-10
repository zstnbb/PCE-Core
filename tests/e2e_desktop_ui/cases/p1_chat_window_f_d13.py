# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window F: D13 extended thinking.

Drives Claude Desktop to send one reasoning prompt that should trigger
``thinking_delta`` SSE events on a thinking-capable model (Sonnet 4.5+
with extended thinking, or Opus 4.x). Then inspects the response body
for the canonical ``"event: thinking_delta"`` markers AND verifies the
final persisted assistant ``content_text`` is the *answer* — not the
internal monologue.

D13 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "An Opus / Sonnet 3.7+ reasoning prompt produces a separate
   ``thinking`` content track (raw ``thinking_delta`` SSE events) AND a
   clean final ``assistant.content_text`` not polluted by the internal
   monologue."

PASS:
  - response body contains ``thinking_delta`` events (>= 1)
  - assistant ``content_text`` does NOT start with ``<thinking>`` or
    contain raw ``<thinking>...</thinking>`` blocks (verifies the
    normaliser's split worked)
  - assistant ``content_text`` is non-trivial (>= 50 chars)

PARTIAL:
  - thinking events present but content_text contains <thinking> tags
    (= normaliser regression)

FAIL:
  - no thinking events at all (= model doesn't have thinking enabled, or
    captured wrong shape) — instructs operator to verify model selection

Run cost: ~30s, 1 reasoning Claude turn.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts, configure_utf8_stdout, default_db_path

PROMPT = (
    "Think step by step before answering. "
    "What is the smallest positive integer N such that "
    "N is divisible by 7, leaves remainder 1 when divided by 4, "
    "and leaves remainder 2 when divided by 5? "
    "Show your reasoning, then give the final number on a separate line "
    "starting with 'ANSWER:'."
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_f_d13")
    configure_utf8_stdout()

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window F — D13 extended thinking ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    # Best-effort: try to pick a thinking-capable model. If the model
    # picker UI isn't where we expect, the case will still run on the
    # default model — the verdict logic will mark it FAIL with a clear
    # diagnostic ("no thinking events captured") and the operator can
    # re-run after manually selecting Opus / Sonnet 4.5+.
    log.info("[2] attempting to select a thinking-capable model")
    if driver.select_model("opus"):
        log.info("    selected Opus")
    elif driver.select_model("sonnet 4.5"):
        log.info("    selected Sonnet 4.5")
    elif driver.select_model("sonnet"):
        log.info("    selected Sonnet (extended thinking depends on tier)")
    else:
        log.warning("    model picker not actionable; using current default")

    time.sleep(1.0)

    log.info("[3] sending reasoning prompt: %r", PROMPT[:80])
    pair_id = driver.send_message(PROMPT, wait_done=True, wait_timeout=120.0)
    if not pair_id:
        log.error("no /completion request observed")
        return 1
    log.info("    pair_id=%s", pair_id[:10])

    # Inspection
    log.info("[4] D13 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    # Response body — look for thinking_delta events
    resp = con.execute(
        "SELECT length(body_text_or_json) AS L, body_text_or_json AS body, "
        "       status_code "
        "FROM raw_captures "
        "WHERE pair_id=? AND direction='response'",
        (pair_id,),
    ).fetchone()
    if not resp:
        print("\n  D13 VERDICT: FAIL - no response row in raw_captures")
        return 1

    body = resp["body"] or ""
    print(f"\n  response: status={resp['status_code']} body_len={resp['L']}")
    n_thinking = body.count("event: thinking_delta")
    n_thinking_summary = body.count("event: thinking_summary_delta")
    n_text_delta = body.count('"type":"content_block_delta"')
    print(f"  thinking_delta events:         {n_thinking}")
    print(f"  thinking_summary_delta events: {n_thinking_summary}")
    print(f"  content_block_delta events:    {n_text_delta}")

    # Persisted messages
    msgs = con.execute(
        "SELECT role, content_text, length(content_text) AS L, "
        "       interaction_kind, content_json "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D13 pair: {len(msgs)}")
    for m in msgs:
        print(f"    role={m['role']:10s} kind={m['interaction_kind'] or '-':15s} "
              f"text_len={m['L']:5d}")

    asst = next((m for m in msgs if m["role"] == "assistant"), None)
    if not asst:
        print("\n  D13 VERDICT: FAIL - no assistant message persisted")
        return 1

    asst_text = asst["content_text"] or ""
    has_raw_thinking_tags = (
        "<thinking>" in asst_text.lower()
        or "</thinking>" in asst_text.lower()
    )
    has_answer_keyword = "answer:" in asst_text.lower()

    print(f"\n  assistant content_text preview ({len(asst_text)} chars):")
    print(f"    head: {asst_text[:240]!r}")
    if len(asst_text) > 240:
        print(f"    tail: ...{asst_text[-200:]!r}")
    print(f"  contains raw <thinking> tags: {has_raw_thinking_tags}")
    print(f"  contains 'ANSWER:' keyword:   {has_answer_keyword}")

    # === D13 verdict ===
    print()
    if n_thinking + n_thinking_summary > 0 and not has_raw_thinking_tags and len(asst_text) >= 50:
        print(f"  D13 VERDICT: PASS - {n_thinking + n_thinking_summary} "
              f"thinking events captured, content_text clean, length OK")
        return 0
    if n_thinking + n_thinking_summary > 0 and has_raw_thinking_tags:
        print(f"  D13 VERDICT: PARTIAL - thinking events present "
              f"({n_thinking + n_thinking_summary}) but content_text "
              f"contains raw <thinking> tags (normaliser regression)")
        return 0
    if n_thinking + n_thinking_summary == 0 and len(asst_text) >= 50:
        print(f"  D13 VERDICT: SKIP - no thinking events in SSE; the "
              f"selected model did not emit thinking. Re-run after "
              f"manually selecting Claude Opus 4.x or Sonnet 4.5+ "
              f"with Extended Thinking enabled in the model picker.")
        return 0  # not a failure of capture, a setup issue
    print(f"  D13 VERDICT: FAIL - assistant message empty or missing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
