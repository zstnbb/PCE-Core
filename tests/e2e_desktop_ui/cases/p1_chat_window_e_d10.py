"""Window E — D10 error mid-stream (Claude Desktop chat).

Scenario
--------
Send a long-form prompt. Once Claude Desktop's `/completion` SSE stream is
mid-flight, abruptly kill the proxy (mitmdump) so the upstream channel dies
mid-response. Then immediately restart mitmdump and verify:

1. The request side of the pair WAS captured (proxy's request hook fired
   before the kill).
2. The response side may or may not be captured — what matters is that the
   system did **not** silently corrupt state. Acceptable outcomes:
     a) request only, no response, no message  → "fail closed", PASS
     b) request + truncated response with status_code=None and partial body,
        normalizer either persists nothing or persists a marked message → PASS
     c) full response captured (proxy buffered everything before the kill) →
        PASS (the kill happened too late to truncate)
3. After restart, fresh requests resume capture normally.

What constitutes FAIL
---------------------
- DB exceptions / pipeline_errors with stack traces tied to this pair's
  half-state.
- A "phantom" message row whose content_text is gibberish or contains raw
  unparsed SSE frames.
- Subsequent captures broken after restart (registers a regression in proxy
  bring-up).

Run:
    python -m tests.e2e_desktop_ui.cases.p1_chat_window_e_d10
"""
from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
import time

from tests.e2e_desktop_ui.drivers.claude_desktop import ClaudeDesktopDriver
from tests.e2e_desktop_ui.utils import (
    baseline_ts,
    count_completions,
    latest_completion_pair_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("p1_chat_window_e_d10")

ROOT = pathlib.Path(__file__).resolve().parents[3]
PID_FILE = ROOT / "_mitm.pid"
MITM_OUT = ROOT / "_mitm.out"
MITM_ERR = ROOT / "_mitm.err"

# Match the existing mitmdump invocation seen in the running process.
MITMDUMP_EXE = pathlib.Path(
    r"C:\Users\ZST\AppData\Local\Programs\Python\Python312\Scripts\mitmdump.exe"
)
MITMDUMP_ARGS = [
    "-s", "run_proxy.py",
    "-p", "8080",
    "--mode", "upstream:http://127.0.0.1:7890",
    "--set", "stream_large_bodies=1m",
    "--set", "upstream_cert=false",
]

# Long-form prompt designed to keep Claude streaming for 10+ seconds.
LONG_PROMPT = (
    "Please write a detailed 800-word essay about the history of computing, "
    "covering Babbage, Turing, Von Neumann, the transistor era, microprocessor "
    "era, the internet age, mobile computing, and the AI era. Include dates "
    "and technical milestones. Be thorough."
)


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _stop_mitmdump(pid: int) -> bool:
    log.info("[kill] Stop-Process -Id %s -Force", pid)
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue; "
         f"Start-Sleep -Milliseconds 200; "
         f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{exit 1}} else {{exit 0}}"],
        capture_output=True, text=True, timeout=10,
    )
    return res.returncode == 0


def _start_mitmdump() -> int | None:
    log.info("[restart] launching mitmdump again with same flags")
    out_f = open(MITM_OUT, "ab")
    err_f = open(MITM_ERR, "ab")
    proc = subprocess.Popen(
        [str(MITMDUMP_EXE), *MITMDUMP_ARGS],
        cwd=str(ROOT),
        stdout=out_f,
        stderr=err_f,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
    )
    PID_FILE.write_text(str(proc.pid))
    log.info("[restart] new mitmdump pid=%s", proc.pid)
    # Give it 3s to bind :8080
    time.sleep(3.0)
    if proc.poll() is None:
        return proc.pid
    log.error("[restart] mitmdump exited rc=%s — see %s / %s", proc.returncode, MITM_OUT, MITM_ERR)
    return None


def main() -> int:
    if not MITMDUMP_EXE.exists():
        log.error("mitmdump.exe not found at %s — adjust path", MITMDUMP_EXE)
        return 2

    bts = baseline_ts()
    bts_path = ROOT / "_baseline_ts.txt"
    bts_path.write_text(f"{bts:.3f}")
    log.info("baseline_ts = %.3f (saved)", bts)
    log.info("=== Window E — D10 error mid-stream ===")

    pid = _read_pid()
    if not pid:
        log.error("could not read %s — refusing to run", PID_FILE)
        return 2
    log.info("[snapshot] mitmdump pid=%s", pid)

    pre_count = count_completions(since_ts=bts)
    log.info("[1] focusing Claude Desktop and sending long-form prompt")
    drv = ClaudeDesktopDriver()
    drv.focus()
    drv.click_composer()
    drv.send_message(LONG_PROMPT, wait_done=False)
    log.info("[2] sleeping 3s to let streaming get rolling...")
    time.sleep(3.0)

    pair_id = latest_completion_pair_id(since_ts=bts) or "(unknown)"
    log.info("    in-flight /completion pair_id=%s", pair_id)

    log.info("[3] killing mitmdump (Stop-Process -Force)")
    killed = _stop_mitmdump(pid)
    if not killed:
        log.warning("kill did not confirm dead; proceeding anyway")

    log.info("[4] sleeping 12s for the desktop client to surface the error")
    time.sleep(12.0)

    log.info("[5] restarting mitmdump for cleanup")
    new_pid = _start_mitmdump()
    if not new_pid:
        log.error("FAILED to restart mitmdump — manual intervention required")
        return 3

    log.info("[6] sending a tiny smoke prompt to confirm proxy is healthy again")
    smoke_count_before = count_completions(since_ts=bts)
    try:
        drv.click_composer()
        drv.send_message("Reply with the word OK only.", wait_timeout=30)
    except Exception as exc:
        log.warning("post-restart smoke prompt threw: %s — non-fatal", exc)

    smoke_count_after = count_completions(since_ts=bts)
    log.info("    completion delta after smoke prompt: %d -> %d",
             smoke_count_before, smoke_count_after)

    log.info("\n=== Window E run complete ===")
    log.info("D10 in-flight pair_id = %s", pair_id)
    log.info("inspect with: python _inspect_window_e.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
