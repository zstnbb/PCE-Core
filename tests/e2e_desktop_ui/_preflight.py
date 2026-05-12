# SPDX-License-Identifier: Apache-2.0
"""Pre-flight check for run_p1_cowork_sweep --mode live.

Verifies:
  1. mitmdump listening on localhost:9080
  2. PCE DB exists + has recent activity (proves capture pipeline alive)
  3. L3g pipeline has at least one prior cowork session (proves watcher
     has run successfully at least once)

Exits 0 if all green, 1 if any condition fails.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import sys
import time
from pathlib import Path


def check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    print("=== run_p1_cowork_sweep preflight ===")
    failures: list[str] = []

    # 1. mitmdump — default 8080 per pce_core/config.py, but check both
    #    since some setups override via PCE_PROXY_PORT env.
    proxy_port = int(os.environ.get("PCE_PROXY_PORT", "8080"))
    mitm_ok = check_port("127.0.0.1", proxy_port)
    if not mitm_ok and proxy_port != 8080:
        # fallback: try the canonical default
        if check_port("127.0.0.1", 8080):
            proxy_port = 8080
            mitm_ok = True
    if not mitm_ok and proxy_port != 9080:
        # fallback: try 9080 in case user runs the legacy port
        if check_port("127.0.0.1", 9080):
            proxy_port = 9080
            mitm_ok = True
    print(f"[{'OK' if mitm_ok else 'FAIL'}] mitmdump on 127.0.0.1:{proxy_port}")
    if not mitm_ok:
        failures.append(f"mitmdump not listening on {proxy_port} (or 8080/9080)")

    # 1.5 persistence_watcher running (look for python process running it)
    watcher_running = False
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { "
             "$_.CommandLine -match 'pce_persistence_watcher' "
             "} | Select-Object -First 1 -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=10,
        )
        pid = (result.stdout or "").strip()
        watcher_running = bool(pid) and pid.isdigit()
        if watcher_running:
            print(f"[OK]   pce_persistence_watcher running (PID {pid})")
        else:
            print("[FAIL] pce_persistence_watcher NOT running")
            failures.append("pce_persistence_watcher not running")
    except Exception as exc:
        print(f"[WARN] could not verify watcher process: {exc}")
        failures.append(f"watcher probe error: {exc}")

    # 1.6 Claude Desktop running
    claude_running = False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process Claude -ErrorAction SilentlyContinue | "
             "Select-Object -First 1 -ExpandProperty Id"],
            capture_output=True, text=True, timeout=5,
        )
        pid = (result.stdout or "").strip()
        claude_running = bool(pid) and pid.isdigit()
        if claude_running:
            print(f"[OK]   Claude Desktop running (PID {pid})")
        else:
            print("[FAIL] Claude Desktop NOT running")
            failures.append("Claude Desktop not running")
    except Exception as exc:
        print(f"[WARN] could not verify Claude Desktop: {exc}")

    # 2. PCE DB
    db_path = Path.home() / ".pce" / "data" / "pce.db"
    if not db_path.exists():
        print(f"[FAIL] DB not found at {db_path}")
        failures.append("DB missing")
        print(f"\nFAIL: {len(failures)} issue(s): {failures}")
        return 1
    print(f"[OK]   DB found at {db_path}")

    con = sqlite3.connect(str(db_path))
    try:
        now = time.time()
        # recent raw_captures
        row = con.execute(
            "SELECT COUNT(*), MAX(created_at) FROM raw_captures WHERE created_at > ?",
            (now - 300,),
        ).fetchone()
        n_recent = row[0] or 0
        latest = row[1] or 0
        if latest:
            age_s = round(now - latest, 1)
            print(f"[{'OK' if n_recent > 0 else 'WARN'}] raw_captures last 5min: "
                  f"{n_recent} rows; latest age = {age_s}s")
        else:
            print("[WARN] no recent raw_captures (proxy may be idle — OK if Claude not active)")

        # L3g pipeline health
        row = con.execute(
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE source_id = 'l3g-local-persistence-default'"
        ).fetchone()
        n_l3g = row[0] or 0
        print(f"[{'OK' if n_l3g > 0 else 'WARN'}] L3g rows ever: {n_l3g}")

        # cowork sessions
        row = con.execute(
            "SELECT COUNT(*), MAX(started_at) FROM sessions "
            "WHERE tool_family = 'cowork-local-agent'"
        ).fetchone()
        n_cw = row[0] or 0
        max_cw = row[1] or 0
        print(f"[{'OK' if n_cw > 0 else 'WARN'}] cowork sessions ever: {n_cw}")

        # heartbeat last 5 min
        row = con.execute(
            "SELECT COUNT(*) FROM raw_captures "
            "WHERE created_at > ? AND path LIKE '%included_worker_types=cowork%'",
            (now - 300,),
        ).fetchone()
        n_hb = row[0] or 0
        if n_hb > 0:
            print(f"[OK]   cowork heartbeats in last 5min: {n_hb} "
                  "(Cowork tab is open + proxy capturing)")
        else:
            print(f"[WARN] 0 cowork heartbeats in last 5min "
                  "(open Cowork tab in Claude Desktop, then re-run preflight)")
            failures.append("no cowork heartbeats — Cowork tab likely not open")
    finally:
        con.close()

    # 3. CLAUDE_PROJECT_NAME info
    cpn = os.environ.get("CLAUDE_PROJECT_NAME")
    if cpn:
        print(f"[INFO] CLAUDE_PROJECT_NAME='{cpn}' (C12 will be exercised)")
    else:
        print("[INFO] CLAUDE_PROJECT_NAME not set (C12 will SKIP)")

    if failures:
        print(f"\n=== FAIL: {len(failures)} issue(s) ===")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n=== ALL GREEN — safe to launch live sweep ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
