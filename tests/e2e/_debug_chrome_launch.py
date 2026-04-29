"""Diagnostic: test 4 different launch strategies to isolate why
Chrome+remote-debugging-port fails on the daily user-data-dir.

Each strategy is tested in turn. After each strategy, ALL chrome.exe
processes are killed so the next test starts from a clean slate.

Strategies:
  A) Popen + TEMP user-data-dir + port 9444  -> isolates daily UD-dir
  B) Popen + daily UD-dir + Profile 1 + port 9445 + stderr visible
  C) os.startfile-style launch (replicates Start-Process) on daily UD
  D) Popen + daily UD-dir + Profile 1 + port 9446 + --enable-logging
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DAILY_UD = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
EXT_DIR = r"F:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\.output\chrome-mv3"


def kill_all_chrome():
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)


def poll_port(port, timeout=20):
    deadline = time.time() + timeout
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2,
            ) as r:
                return json.loads(r.read()), attempts
        except Exception:
            time.sleep(0.5)
    return None, attempts


def strategy_a():
    print("\n=== Strategy A: TEMP user-data-dir + Popen + port 9444 ===")
    tmp = tempfile.mkdtemp(prefix="pce_chrome_test_")
    print(f"  TEMP user-data-dir: {tmp}")
    cmd = [
        CHROME,
        f"--user-data-dir={tmp}",
        "--remote-debugging-port=9444",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print("  cmd:", " ".join(repr(a) for a in cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"  PID: {proc.pid}")
    info, attempts = poll_port(9444, timeout=20)
    if info:
        print(f"  SUCCESS in {attempts} attempts: {info.get('Browser')}")
    else:
        print(f"  FAILED after {attempts} attempts")
    return info is not None


def strategy_b():
    print("\n=== Strategy B: daily UD + Profile 1 + Popen visible stderr ===")
    cmd = [
        CHROME,
        f"--user-data-dir={DAILY_UD}",
        "--profile-directory=Profile 1",
        "--remote-debugging-port=9445",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-logging=stderr",
        "--v=1",
    ]
    print("  cmd:", " ".join(repr(a) for a in cmd))
    # Capture stderr to file so we can read it back
    log_path = os.path.join(tempfile.gettempdir(), "pce_chrome_b_stderr.log")
    f_err = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=f_err,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"  PID: {proc.pid}, stderr -> {log_path}")
    info, attempts = poll_port(9445, timeout=20)
    if info:
        print(f"  SUCCESS in {attempts} attempts: {info.get('Browser')}")
    else:
        print(f"  FAILED after {attempts} attempts")
        # Dump first 2000 chars of stderr log
        time.sleep(1)
        try:
            f_err.flush(); f_err.close()
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read()[-3000:]
            print("  stderr tail:")
            for line in tail.splitlines()[-40:]:
                print("    ", line)
        except Exception as e:
            print(f"  could not read stderr log: {e}")
    return info is not None


def strategy_c():
    print("\n=== Strategy C: shell-style start (Start-Process equiv) ===")
    # Use cmd.exe start to spawn chrome in a fully detached way without
    # PowerShell's argv-splitting bug. Each token is a separate string,
    # spaces preserved by enclosing in CMD-style double-quotes that
    # cmd.exe forwards verbatim into chrome's WinMain ArgvW.
    args = [
        "cmd", "/c", "start", "",
        CHROME,
        f"--user-data-dir={DAILY_UD}",
        "--profile-directory=Profile 1",
        "--remote-debugging-port=9446",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    print("  cmd:", " ".join(repr(a) for a in args))
    proc = subprocess.run(args, capture_output=True, text=True)
    print(f"  cmd start returncode: {proc.returncode}")
    if proc.stdout: print(f"  stdout: {proc.stdout[:300]}")
    if proc.stderr: print(f"  stderr: {proc.stderr[:300]}")
    info, attempts = poll_port(9446, timeout=20)
    if info:
        print(f"  SUCCESS in {attempts} attempts: {info.get('Browser')}")
    else:
        print(f"  FAILED after {attempts} attempts")
    return info is not None


def main():
    print(f"Daily UD: {DAILY_UD}")
    print(f"Daily UD exists: {os.path.isdir(DAILY_UD)}")
    print(f"Profile 1 exists: {os.path.isdir(os.path.join(DAILY_UD, 'Profile 1'))}")

    kill_all_chrome()
    print("\n--- Initial chrome state cleared ---")

    a_ok = strategy_a()
    kill_all_chrome()

    b_ok = strategy_b()
    kill_all_chrome()

    c_ok = strategy_c()
    kill_all_chrome()

    print("\n=== SUMMARY ===")
    print(f"  A (TEMP UD)       : {'OK' if a_ok else 'FAIL'}")
    print(f"  B (daily UD popen): {'OK' if b_ok else 'FAIL'}")
    print(f"  C (cmd start)     : {'OK' if c_ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
