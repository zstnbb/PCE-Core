"""All-in-one diagnostic for the failed Chrome+remote-debugging launch.

Prints (1) Chrome policies in the registry that could block remote
debugging, (2) any non-chrome.exe processes that may hold locks on
the daily user-data-dir, (3) results of 3 launch strategies to
isolate the root cause.

Run AFTER killing all chrome.exe::

    taskkill /F /IM chrome.exe
    python -m tests.e2e._diag_chrome
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DAILY_UD = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")


def section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def kill_chrome():
    subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)


def check_policies():
    section("1. Chrome registry policies that could disable remote debugging")
    try:
        import winreg
    except ImportError:
        print("  winreg not available")
        return
    candidates = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Google\Chrome"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Policies\Google\Chrome"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Policies\Google\Chrome"),
    ]
    found_any = False
    for hive, path in candidates:
        try:
            k = winreg.OpenKey(hive, path)
        except FileNotFoundError:
            continue
        found_any = True
        hive_name = "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        print(f"  {hive_name}\\{path}")
        try:
            i = 0
            while True:
                try:
                    name, val, typ = winreg.EnumValue(k, i)
                    print(f"    {name} = {val!r}  (type {typ})")
                    i += 1
                except OSError:
                    break
        finally:
            winreg.CloseKey(k)
    if not found_any:
        print("  (no Chrome policy keys found anywhere)")
    else:
        print("  -> If you see RemoteDebuggingAllowed=0 or "
              "RemoteDebuggingAddress restrictions, that's the cause.")


def check_lock_holders():
    section("2. Non-chrome processes that might hold daily user-data-dir locks")
    try:
        import psutil
    except ImportError:
        print("  psutil not available")
        return
    suspicious_names = ("googlecrash", "googleupdat", "googleelevate",
                        "edgeupdate", "msedge", "browser_broker")
    found = []
    for p in psutil.process_iter(attrs=["pid", "name", "exe"]):
        name = (p.info.get("name") or "").lower()
        if name == "chrome.exe":
            continue
        if any(s in name for s in suspicious_names):
            found.append((p.info["pid"], p.info["name"], p.info.get("exe") or ""))
    if not found:
        print("  No suspicious processes")
    else:
        for pid, name, exe in found:
            print(f"  PID {pid}: {name}  ({exe})")


def check_singleton():
    section("3. Singleton + DevTools markers in daily user-data-dir")
    files = [
        "SingletonLock", "SingletonCookie", "SingletonSocket",
        "DevToolsActivePort", "Local State",
    ]
    for f in files:
        p = os.path.join(DAILY_UD, f)
        if os.path.exists(p):
            sz = os.path.getsize(p) if os.path.isfile(p) else "(dir)"
            print(f"  {f}: EXISTS  size={sz}")
        else:
            print(f"  {f}: absent")


def poll_port(port, timeout=15):
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


def check_devtools_file():
    fp = os.path.join(DAILY_UD, "DevToolsActivePort")
    if os.path.exists(fp):
        try:
            return open(fp, encoding="utf-8").read().strip()
        except Exception:
            return "(unreadable)"
    return None


def strategy_a():
    section("4A. TEMP user-data-dir + Popen + port 9444  (isolates daily UD)")
    tmp = tempfile.mkdtemp(prefix="pce_chrome_test_")
    print(f"  TEMP UD: {tmp}")
    cmd = [
        CHROME, f"--user-data-dir={tmp}",
        "--remote-debugging-port=9444",
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"  PID: {proc.pid}")
    info, attempts = poll_port(9444, timeout=15)
    if info:
        print(f"  PASS in {attempts} probes: {info.get('Browser')}")
    else:
        print(f"  FAIL after {attempts} probes")
    return info is not None


def strategy_b():
    section("4B. Daily UD + Profile 1 + Popen + STDERR captured + port 9445")
    log_path = os.path.join(tempfile.gettempdir(), "pce_chrome_diag_b.log")
    print(f"  stderr -> {log_path}")
    cmd = [
        CHROME, f"--user-data-dir={DAILY_UD}",
        "--profile-directory=Profile 1",
        "--remote-debugging-port=9445",
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check",
        "--enable-logging=stderr", "--v=1",
    ]
    f_err = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=f_err,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"  PID: {proc.pid}")
    info, attempts = poll_port(9445, timeout=15)
    f_err.flush()
    if info:
        print(f"  PASS in {attempts} probes: {info.get('Browser')}")
    else:
        print(f"  FAIL after {attempts} probes")
        try:
            f_err.close()
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            relevant_lines = [
                ln for ln in content.splitlines()
                if any(k in ln.lower() for k in (
                    "devtool", "remote", "policy", "profile", "lock",
                    "fail", "error", "fatal", "denied", "address",
                ))
            ]
            print(f"  stderr total {len(content)} bytes, {len(relevant_lines)} relevant lines:")
            for line in relevant_lines[-30:]:
                print(f"    {line[:280]}")
        except Exception as e:
            print(f"  could not read stderr: {e}")
    return info is not None


def strategy_c():
    section("4C. Daily UD + Profile 1 + cmd.exe start + port 9446")
    cmd = [
        "cmd", "/c", "start", "",
        CHROME,
        f"--user-data-dir={DAILY_UD}",
        "--profile-directory=Profile 1",
        "--remote-debugging-port=9446",
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(f"  cmd start rc={proc.returncode}")
    if proc.stdout: print(f"  stdout: {proc.stdout[:300]}")
    if proc.stderr: print(f"  stderr: {proc.stderr[:300]}")
    info, attempts = poll_port(9446, timeout=15)
    if info:
        print(f"  PASS in {attempts} probes: {info.get('Browser')}")
    else:
        print(f"  FAIL after {attempts} probes")
    return info is not None


def main():
    print(f"Daily UD: {DAILY_UD}")
    print(f"Daily UD exists: {os.path.isdir(DAILY_UD)}")
    print(f"Chrome.exe path: {CHROME} ({'present' if os.path.exists(CHROME) else 'MISSING'})")

    kill_chrome()
    check_policies()
    check_lock_holders()
    check_singleton()

    a_ok = strategy_a()
    kill_chrome()
    b_ok = strategy_b()
    kill_chrome()
    c_ok = strategy_c()
    kill_chrome()

    section("SUMMARY")
    print(f"  A (TEMP UD)       : {'PASS' if a_ok else 'FAIL'}")
    print(f"  B (daily UD popen): {'PASS' if b_ok else 'FAIL'}")
    print(f"  C (daily UD cmd)  : {'PASS' if c_ok else 'FAIL'}")
    print()
    if a_ok and not b_ok and not c_ok:
        print("DIAGNOSIS: subprocess.Popen + cmd.exe both work fine on a")
        print("  TEMP UD. Both fail on the daily UD. Root cause is")
        print("  something specific to the daily user-data-dir or Profile 1.")
        print("  Likely: Chrome enterprise policy on Profile 1, or a")
        print("  Local State entry that disables debugging for this UD.")
    elif a_ok and (b_ok or c_ok):
        print("DIAGNOSIS: at least one daily-UD strategy worked.")
        print("  Use the working strategy in the relaunch helper.")
    elif not a_ok:
        print("DIAGNOSIS: even TEMP UD fails. The launch mechanism")
        print("  itself is broken. Possibly chrome.exe is wrong path")
        print("  or system-wide policy blocks remote debugging.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
