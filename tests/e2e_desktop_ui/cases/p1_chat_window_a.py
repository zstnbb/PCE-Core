# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window A: D03 multi-turn + D07 code block + D04 cancel.

Scope: drive Claude Desktop through a single 5-turn conversation that
exercises three D-cases at once:

- **D03 multi-turn persistence**: 5 user turns must collapse into ONE
  ``sessions`` row (after Bug 1 fix in commit 75cdfc2 — the
  ``chat_conversations/<UUID>`` path-UUID fallback for ``session_key``).
- **D07 code block**: turn 3 asks for Python code; the assistant's reply
  must contain a fenced code block with the ``python`` language tag
  preserved into ``messages.content_text`` (or ``content_json``).
- **D04 cancel mid-stream**: turn 4 asks for a long answer; the script
  cancels via Esc partway through. The assistant message that lands in
  the DB should have partial text + an indicator the stream was
  truncated (or no assistant message at all if cancellation happened
  before the SSE produced any content).

Usage::

    python -m tests.e2e_desktop_ui.cases.p1_chat_window_a

The script writes its baseline_ts to ``_baseline_ts.txt`` (UTC seconds,
real, not PowerShell-local) so subsequent inspect scripts can window
their queries to this run.

Pre-conditions (caller's responsibility):

1. Claude Desktop is installed, signed in, and visible (not hidden in
   tray).
2. mitmdump is running on :8080 with ``pce_proxy/addon.py`` and
   upstream chain to Clash :7890 (or wherever your egress is).
3. System proxy is set to 127.0.0.1:8080.
4. mitm CA is in ``Cert:\\CurrentUser\\Root``.

The script does NOT manage proxy / mitm — that's a separate setup
concern. If those aren't in place, no ``/completion`` requests will
land and the case will report failure cleanly.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts


PROMPTS = [
    # turn 1 — D03 turn 1, simple greeting
    ("greet",
     "你好,请简短回复"),
    # turn 2 — D03 turn 2, follow-up
    ("follow_up",
     "再说一句"),
    # turn 3 — D07 code block
    ("code_block",
     "用 Python 写一个 fib(n) 递归函数,代码要简短,只回代码块"),
    # turn 4 — D04 cancel mid-stream
    ("cancel_mid",
     "请从 attention 一路详细讲到 LayerNorm,要长,至少 800 字"),
    # turn 5 — D03 turn 5 closure
    ("closure",
     "thanks"),
]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_a")

    # Save UTC baseline for inspect scripts
    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved to _baseline_ts.txt)", base)

    driver = ClaudeDesktopDriver()

    log.info("=== Window A — 5 turns: D03 + D07 + D04 ===")

    pair_ids: list[tuple[str, str]] = []  # (label, pair_id)

    for i, (label, prompt) in enumerate(PROMPTS, start=1):
        log.info("--- Turn %d/%d (%s) ---", i, len(PROMPTS), label)

        if label == "cancel_mid":
            # Submit, wait briefly for SSE to start, then send Esc.
            log.info("D04: submitting long prompt without waiting for done...")
            pair_id = driver.send_message(prompt, wait_done=False)
            if not pair_id:
                log.error("D04: no /completion request observed; turn skipped")
                continue
            pair_ids.append((label, pair_id))

            # Wait long enough for assistant SSE to start producing
            # content but short enough to cancel before completion.
            time.sleep(4.0)

            log.info("D04: sending cancel signal (Esc)...")
            driver.cancel_current()

            # Now wait for response row to land (whether it's the full
            # response or a truncated one — mitmproxy will persist
            # whatever the SSE stream produced when it terminated).
            log.info("D04: waiting up to 15s for response row to settle...")
            done = driver.wait_done(pair_id, timeout=15.0)
            if done:
                log.info("D04: response settled (status=%s, body=%dB)",
                         done["status_code"], done["body_len"])
            else:
                log.warning("D04: response row did NOT appear within 15s")
        else:
            pair_id = driver.send_message(
                prompt, wait_done=True, wait_timeout=45.0,
            )
            if not pair_id:
                log.error("Turn %d (%s): no /completion request — driver failed", i, label)
                continue
            pair_ids.append((label, pair_id))

        # Brief pause between turns to let UI settle.
        time.sleep(2.0)

    log.info("\n=== Window A complete ===")
    log.info("turns sent: %d / %d", len(pair_ids), len(PROMPTS))
    for label, pid in pair_ids:
        log.info("  %s -> pair_id=%s", label, pid[:10])

    log.info("\nNext: run an inspect script to score D03/D04/D07")
    log.info("  python tools/inspect_capture_window.py  (or run the inline diag)")

    return 0 if len(pair_ids) == len(PROMPTS) else 1


if __name__ == "__main__":
    sys.exit(main())
