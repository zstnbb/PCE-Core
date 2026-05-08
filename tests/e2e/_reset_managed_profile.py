# SPDX-License-Identifier: Apache-2.0
"""Reset the PCE managed Chrome profile so a fresh stealth-enabled launch
can bind the remote-debugging port.

Why this exists
---------------
``_open_login_tabs.py`` and ``conftest.py`` both spawn Chrome against
``~/.pce/chrome_profile``. If the previous test run's Chrome process is
still alive (or crashed without cleanup), Chrome's profile lockfile and
Singleton lock prevent a new instance from binding ``--remote-debugging-port``.
Stealth injection then silently fails because Selenium can't attach.

This helper:
1. Finds all chrome.exe whose command line references ``chrome_profile``
2. Terminates them gracefully (then force-kills survivors)
3. Removes Singleton{Lock,Cookie,Socket} files

Run via:
    python -m tests.e2e._reset_managed_profile
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROFILE_ROOT = Path.home() / ".pce" / "chrome_profile"


def _find_pce_chrome_pids() -> list[int]:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        print("ERROR: psutil not installed. Run: pip install psutil", file=sys.stderr)
        return []

    pids: list[int] = []
    for proc in psutil.process_iter(attrs=["name", "cmdline"]):
        info = proc.info
        if (info.get("name") or "").lower() != "chrome.exe":
            continue
        cmdline = info.get("cmdline") or []
        if any("chrome_profile" in (c or "") for c in cmdline):
            pids.append(proc.pid)
    return pids


def _find_open_login_tabs_pids() -> list[int]:
    """Find python.exe processes running ``_open_login_tabs.py`` -- they
    babysit a detached Chrome and prevent a clean re-launch.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []

    pids: list[int] = []
    for proc in psutil.process_iter(attrs=["name", "cmdline"]):
        info = proc.info
        name = (info.get("name") or "").lower()
        if not (name.startswith("python") or name == "py.exe"):
            continue
        cmdline = info.get("cmdline") or []
        if any("_open_login_tabs" in (c or "") for c in cmdline):
            pids.append(proc.pid)
    return pids


def _kill_pids(pids: list[int]) -> int:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return 0
    killed = 0
    for pid in pids:
        try:
            p = psutil.Process(pid)
            p.terminate()
            killed += 1
        except Exception as exc:
            print(f"  pid {pid}: terminate failed: {exc}", file=sys.stderr)
    # Give them a moment, then force-kill survivors.
    time.sleep(1.5)
    for pid in pids:
        try:
            p = psutil.Process(pid)
            if p.is_running():
                p.kill()
                print(f"  pid {pid}: force-killed")
        except Exception:
            pass
    return killed


def _remove_lockfiles() -> int:
    if not PROFILE_ROOT.is_dir():
        print(f"  profile dir does not exist: {PROFILE_ROOT}")
        return 0
    candidates = [
        PROFILE_ROOT / "SingletonLock",
        PROFILE_ROOT / "SingletonCookie",
        PROFILE_ROOT / "SingletonSocket",
        PROFILE_ROOT / "lockfile",
    ]
    removed = 0
    for path in candidates:
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                print(f"  removed: {path.name}")
                removed += 1
        except Exception as exc:
            print(f"  WARN: could not remove {path}: {exc}", file=sys.stderr)
    return removed


def main() -> int:
    print(f"PCE managed profile: {PROFILE_ROOT}")

    babysitters = _find_open_login_tabs_pids()
    if babysitters:
        print(f"Found {len(babysitters)} _open_login_tabs.py babysitters: {babysitters}")
        _kill_pids(babysitters)
    else:
        print("No _open_login_tabs.py babysitter processes.")

    pids = _find_pce_chrome_pids()
    if pids:
        print(f"Found {len(pids)} chrome.exe processes using this profile: {pids}")
        killed = _kill_pids(pids)
        print(f"Terminated {killed} processes.")
    else:
        print("No PCE-profile chrome processes running.")

    print("Cleaning Singleton lockfiles:")
    removed = _remove_lockfiles()
    if removed == 0:
        print("  no lockfiles to remove.")

    print("Done. The managed profile is ready for a fresh stealth-enabled launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
