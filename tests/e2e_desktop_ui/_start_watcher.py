# SPDX-License-Identifier: Apache-2.0
"""Start pce_persistence_watcher in watch mode as a detached background
process. Idempotent — if a watcher is already running, prints its PID
and exits 0 without starting a second one.
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = Path(os.environ.get("TEMP", "C:/Temp"))


def _existing_watcher_pid() -> int | None:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR "
             "Name='pythonw.exe'\" | Where-Object { "
             "$_.CommandLine -like '*pce_persistence_watcher*' } | "
             "Select-Object -First 1 -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=10,
        )
        out = (result.stdout or "").strip()
        if out and out.isdigit():
            return int(out)
    except Exception:
        pass
    return None


def main() -> int:
    existing = _existing_watcher_pid()
    if existing is not None:
        print(f"Watcher already running, PID={existing} — nothing to do")
        return 0

    stdout_log = LOG_DIR / "pce_watcher.stdout.log"
    stderr_log = LOG_DIR / "pce_watcher.stderr.log"

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    cmd = [
        sys.executable, "-m", "pce_persistence_watcher", "watch",
        "--poll-interval", "5",
    ]
    print(f"Starting watcher: {' '.join(cmd)}")
    print(f"  cwd: {REPO_ROOT}")
    print(f"  stdout: {stdout_log}")
    print(f"  stderr: {stderr_log}")

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
    print(f"Launched watcher pid={proc.pid}")

    # Wait for evidence that it started successfully (poll the process)
    time.sleep(3)
    if proc.poll() is not None:
        print(f"Watcher died with exit code {proc.returncode}!")
        try:
            print("stderr tail:")
            print(stderr_log.read_text(encoding="utf-8", errors="replace")[-2000:])
        except Exception:
            pass
        return 1

    # Verify via process list
    confirmed_pid = _existing_watcher_pid()
    if confirmed_pid is None:
        print("Watcher launched but process probe can't find it")
        return 1
    print(f"OK — watcher confirmed running, PID={confirmed_pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
