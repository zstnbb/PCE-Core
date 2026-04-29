"""One-shot launcher: relaunch Chrome with Hurricane profile + debug port.

Bypasses PowerShell ``Start-Process -ArgumentList`` arg-parsing which
splits on internal spaces (turning ``--profile-directory=Profile 1``
into two argv entries and silently loading the wrong profile). Python
``subprocess.Popen(list)`` passes argv via WinAPI CreateProcess
quoting so spaces are preserved.

Run after killing any running Chrome::

    taskkill /F /IM chrome.exe
    python -m tests.e2e._relaunch_hurricane            # port 9333 default
    python -m tests.e2e._relaunch_hurricane --port 9444
    python -m tests.e2e._relaunch_hurricane --profile "Profile 4"

Default port is 9333, NOT 9222, because 9222 tends to accumulate stale
TIME_WAIT / CLOSE_WAIT TCP state on Windows after a ``taskkill /F``
on a previous Chrome -- if Chrome can't bind it just silently disables
DevTools and the relaunch is undebuggable. Use 9333+ for a clean port.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

USER_DATA = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
EXT_DIR = r"F:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\.output\chrome-mv3"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9333,
                   help="DevTools port (default 9333; avoid 9222 because it "
                        "often has stale TIME_WAIT after taskkill /F)")
    p.add_argument("--profile", default="Profile 1",
                   help="Chrome on-disk profile dir name (default 'Profile 1' "
                        "= Hurricane). Use 'Default', 'Profile 3', etc. for "
                        "other profiles.")
    p.add_argument("--user-data-dir", default=USER_DATA,
                   help="Chrome user-data-dir (default = daily Chrome's)")
    p.add_argument("--no-extension", action="store_true",
                   help="Skip --load-extension (debug only)")
    args = p.parse_args(argv)

    # ----- Clean up crash markers from previous taskkill /F ---------
    # When Chrome is killed with /F (or otherwise dies abruptly) it
    # writes ``profile.exit_type = "Crashed"`` to Preferences on the
    # next start. The first action of a "Crashed" boot is to enter
    # session-recovery mode, which (a) shows the "Restore pages?"
    # bubble and (b) silently DISABLES the DevTools port binding
    # until the user dismisses the bubble. Result: 14 chrome.exe
    # procs running, no port bound, no DevToolsActivePort file, no
    # way for any automation to attach.
    #
    # Chromedriver / Puppeteer / etc. all do the same fix: rewrite
    # ``exit_type=Normal`` + ``exited_cleanly=true`` BEFORE launching.
    prefs_path = os.path.join(args.user_data_dir, args.profile, "Preferences")
    if os.path.exists(prefs_path):
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            profile = prefs.setdefault("profile", {})
            old_exit = profile.get("exit_type")
            if old_exit and old_exit != "Normal":
                profile["exit_type"] = "Normal"
                profile["exited_cleanly"] = True
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False)
                print(f"Reset crash markers in Preferences "
                      f"(was exit_type={old_exit!r})")
            else:
                print(f"Preferences already clean (exit_type={old_exit!r})")
        except Exception as exc:
            print(f"WARN: could not rewrite Preferences: {exc}")

    cmd = [
        CHROME,
        f"--user-data-dir={args.user_data_dir}",
        f"--profile-directory={args.profile}",
        f"--remote-debugging-port={args.port}",
        "--remote-allow-origins=*",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if not args.no_extension:
        cmd += [
            f"--load-extension={EXT_DIR}",
            "--disable-features=DisableLoadExtensionCommandLineSwitch",
        ]
    print("Launching Chrome with argv:")
    for a in cmd:
        print(f"  {a!r}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        ),
    )
    print(f"\nLauncher PID: {proc.pid}")

    base = f"http://127.0.0.1:{args.port}"
    print(f"Waiting up to 30s for {base} ...")
    deadline = time.time() + 30
    info = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"{base}/json/version", timeout=2,
            ) as r:
                info = json.loads(r.read())
                break
        except Exception:
            time.sleep(0.5)
    if info is None:
        print(f"ERROR: {args.port} did not come up", file=sys.stderr)
        return 1
    print(f"Chrome ready: {info.get('Browser')}")

    try:
        with urllib.request.urlopen(
            f"{base}/json/list", timeout=3,
        ) as r:
            tabs = json.loads(r.read())
    except Exception as exc:
        print(f"WARN: could not list tabs: {exc}")
        tabs = []

    print(f"\nTabs visible to debugger: {len(tabs)}")
    for t in tabs[:10]:
        ttype = t.get("type")
        url = (t.get("url") or "")[:90]
        title = (t.get("title") or "")[:60]
        print(f"  - type={ttype!s:7s}  url={url}")
        if title:
            print(f"      title={title}")

    print("\nIf you see your Hurricane session restored above (Gmail / "
          "ChatGPT / etc.) the profile loaded correctly. If you only "
          "see chrome://newtab or about:blank, the profile is empty "
          "and something is still wrong.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
