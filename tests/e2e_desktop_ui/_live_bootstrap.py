# SPDX-License-Identifier: Apache-2.0
"""Bootstrap the live-mode sweep environment.

Steps:
  1. Check / start mitmdump in background (port 8080, run_proxy.py addon).
  2. Enable Windows system proxy -> 127.0.0.1:8080 via
     pce_core.proxy_toggle.enable_system_proxy().
  3. Drive UIA to open the Cowork tab in Claude Desktop.
  4. Wait up to ~30s for cowork heartbeat rows to appear in DB.
  5. Persist a state file at ``%TEMP%\\pce_live_sweep_state.json`` so the
     teardown script can restore the previous proxy state.

Exits 0 on success, 1 on any failure. Never sleeps more than ~45s total.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from pce_core.proxy_toggle import (  # noqa: E402
    enable_system_proxy,
    get_proxy_state,
)

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8080
STATE_FILE = Path(os.environ.get("TEMP", "C:/Temp")) / "pce_live_sweep_state.json"
DB_PATH = Path.home() / ".pce" / "data" / "pce.db"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _step1_mitmdump() -> dict:
    print("[1/4] Checking mitmdump on 127.0.0.1:8080 ...")
    if _port_open(PROXY_HOST, PROXY_PORT):
        print("       already listening — reusing existing process")
        return {"started_here": False, "pid": None}

    print(f"       not listening — starting mitmdump in background ...")
    addon_path = REPO_ROOT / "run_proxy.py"
    if not addon_path.exists():
        raise RuntimeError(f"missing addon at {addon_path}")

    log_dir = Path(os.environ.get("TEMP", "C:/Temp"))
    stdout_log = log_dir / "pce_mitmdump.stdout.log"
    stderr_log = log_dir / "pce_mitmdump.stderr.log"

    # Use DETACHED_PROCESS so the child survives our exit.
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    cmd = [
        "mitmdump",
        "-s", str(addon_path),
        "-p", str(PROXY_PORT),
        "--set", "stream_large_bodies=1m",
    ]
    print(f"       cmd: {' '.join(cmd)}")
    print(f"       stdout: {stdout_log}")
    print(f"       stderr: {stderr_log}")

    stdout_f = open(stdout_log, "ab", buffering=0)
    stderr_f = open(stderr_log, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=stdout_f,
        stderr=stderr_f,
        stdin=subprocess.DEVNULL,
        creationflags=creation_flags,
        close_fds=True,
    )
    pid = proc.pid
    print(f"       launched mitmdump pid={pid}")

    # Wait for port to open
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if _port_open(PROXY_HOST, PROXY_PORT):
            print(f"       OK — port 8080 listening after "
                  f"{round(20.0 - (deadline - time.time()), 1)}s")
            return {"started_here": True, "pid": pid}
        time.sleep(0.5)
    raise RuntimeError("mitmdump failed to start listening within 20s")


def _step2_system_proxy() -> dict:
    print("[2/4] Enabling Windows system proxy ...")
    prev_state = get_proxy_state()
    prev_dict = prev_state.as_dict()
    print(f"       previous state: enabled={prev_state.enabled}, "
          f"host={prev_state.host}, port={prev_state.port}")

    result = enable_system_proxy(host=PROXY_HOST, port=PROXY_PORT)
    if not result.ok:
        print(f"       FAIL: {result.message}")
        raise RuntimeError(f"enable_system_proxy failed: {result.message}")
    print(f"       OK — {result.message}")
    return {"prev_state": prev_dict, "set_to": f"{PROXY_HOST}:{PROXY_PORT}"}


def _step3_open_cowork_tab() -> dict:
    print("[3/4] Opening Cowork tab in Claude Desktop via UIA ...")
    from tests.e2e_desktop_ui.drivers.claude_desktop import ClaudeDesktopDriver

    driver = ClaudeDesktopDriver()
    try:
        driver.focus()
        time.sleep(0.5)
    except Exception as exc:
        print(f"       WARN: focus() raised: {exc}")

    try:
        ok = driver.open_cowork_tab()
    except Exception as exc:
        print(f"       FAIL: open_cowork_tab() raised: {exc}")
        raise

    if not ok:
        print("       FAIL: open_cowork_tab() returned False")
        raise RuntimeError("could not click Cowork tab")
    print("       OK — Cowork tab clicked")
    return {"clicked": True}


def _step4_verify_heartbeat() -> dict:
    print("[4/4] Waiting for cowork heartbeat in DB ...")
    if not DB_PATH.exists():
        raise RuntimeError(f"DB missing at {DB_PATH}")

    deadline = time.time() + 30.0
    last_count = 0
    while time.time() < deadline:
        con = sqlite3.connect(str(DB_PATH))
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM raw_captures "
                "WHERE created_at > ? "
                "  AND path LIKE '%included_worker_types=cowork%'",
                (time.time() - 60,),
            ).fetchone()
            n = row[0] if row else 0
        finally:
            con.close()
        if n > last_count:
            last_count = n
            print(f"       {round(30.0 - (deadline - time.time()), 1)}s : "
                  f"{n} heartbeat row(s) in last 60s")
        if n >= 1:
            print(f"       OK — heartbeats flowing ({n} in last 60s)")
            return {"heartbeats_last_60s": n}
        time.sleep(2)
    raise RuntimeError(
        "no cowork heartbeat after 30s — proxy may not be intercepting "
        "Claude traffic (CA cert? Cowork tab actually visible?)"
    )


def main() -> int:
    state: dict = {"started_at": time.time()}
    try:
        state["mitmdump"] = _step1_mitmdump()
        state["system_proxy"] = _step2_system_proxy()
        state["cowork_tab"] = _step3_open_cowork_tab()
        state["heartbeat"] = _step4_verify_heartbeat()
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
        STATE_FILE.write_text(
            json.dumps(state, indent=2, default=str), encoding="utf-8")
        print(f"\nBOOTSTRAP FAILED — state saved to {STATE_FILE}")
        print(f"Error: {exc}")
        return 1

    state["finished_at"] = time.time()
    state["elapsed_s"] = round(state["finished_at"] - state["started_at"], 1)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8")
    print(f"\nBOOTSTRAP DONE in {state['elapsed_s']}s — state saved to {STATE_FILE}")
    print("Ready to run: python -m tests.e2e_desktop_ui.run_p1_cowork_sweep --mode live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
