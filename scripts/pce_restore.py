# SPDX-License-Identifier: Apache-2.0
"""PCE Emergency Restore — standalone proxy-state recovery tool.

Run this if your computer has stopped reaching the network after PCE
crashed, was force-killed, or your machine was hard-rebooted while PCE
was running. The typical symptom is "everything stops working" — most
apps fail with connection-refused or HTTP 502 errors — because the OS-
level system proxy is still pointing at PCE's mitmproxy port (8080)
but mitmproxy is no longer listening.

The script is deliberately self-contained:
    * Standard-library only (no PCE imports, no pip deps)
    * Single file, drop-in runnable from any working directory
    * Idempotent: safe to run repeatedly

Usage::

    python pce_restore.py             # default: read snapshot, restore
    python pce_restore.py --disable   # forcibly turn system proxy OFF
    python pce_restore.py --show      # report current proxy state, do nothing
    python pce_restore.py --help

Recovery file location::

    ~/.pce/state/system_state.json    # snapshot from the last PCE run

Exit codes::

    0  — proxy state restored (or disabled cleanly)
    1  — could not read snapshot AND disable also failed
    2  — invalid CLI arguments
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATE_FILE = Path.home() / ".pce" / "state" / "system_state.json"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="PCE Emergency Restore — undo system-proxy changes.",
        epilog="If 'everything stopped working' after a PCE crash, "
               "just run this with no arguments.",
    )
    parser.add_argument(
        "--disable", action="store_true",
        help="Forcibly disable the OS system proxy WITHOUT reading the "
             "snapshot. Use this as a last resort.",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Print the current OS proxy state and exit; change nothing.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("PCE Emergency Restore")
    print("=" * 60)
    print()

    if args.show:
        print_current_state()
        return 0

    if args.disable:
        print("Forcibly disabling system proxy (--disable)…")
        try:
            disable_proxy()
            print("\n✓ System proxy disabled.")
            return 0
        except Exception as exc:
            print(f"\n✗ Could not disable: {exc!r}")
            return 1

    # Default path: try snapshot-based restore, fall back to disable.
    if STATE_FILE.is_file():
        print(f"Found PCE snapshot at: {STATE_FILE}")
        try:
            snap = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"WARNING: snapshot is unreadable ({exc!r})")
            print("Falling back to clean disable…")
            try:
                disable_proxy()
                STATE_FILE.unlink(missing_ok=True)
                print("\n✓ System proxy disabled. Snapshot deleted.")
                return 0
            except Exception as exc2:
                print(f"\n✗ Disable also failed: {exc2!r}")
                return 1

        proxy = snap.get("proxy") or {}
        if proxy.get("enabled") and proxy.get("host") and proxy.get("port"):
            print(f"Snapshot says: proxy = {proxy['host']}:{proxy['port']}")
            bypass = list(_dedup(proxy.get("bypass") or []))
            try:
                enable_proxy(proxy["host"], int(proxy["port"]), bypass)
                STATE_FILE.unlink(missing_ok=True)
                print(f"\n✓ Restored system proxy to "
                      f"{proxy['host']}:{proxy['port']}")
                print(f"  ({len(bypass)} bypass entries)")
                print("  Snapshot deleted.")
                return 0
            except Exception as exc:
                print(f"\n✗ Restore failed: {exc!r}")
                print("Falling back to disable…")
        else:
            print("Snapshot says proxy was OFF — disabling now.")

        try:
            disable_proxy()
            STATE_FILE.unlink(missing_ok=True)
            print("\n✓ System proxy disabled. Snapshot deleted.")
            return 0
        except Exception as exc:
            print(f"\n✗ Disable failed: {exc!r}")
            return 1

    # No snapshot.
    print(f"No PCE snapshot at {STATE_FILE}")
    print("Either PCE was never run, or it cleaned up correctly.")
    print()
    print("Checking current state in case something else broke it…")
    print()
    state = read_current_state()
    if state and state.get("enabled") and state.get("server"):
        srv = state["server"]
        if "127.0.0.1:8080" in srv or "localhost:8080" in srv:
            print(f"  ⚠ system proxy = {srv}")
            print(f"  This looks like an orphaned PCE setting.")
            print(f"  Disabling…")
            try:
                disable_proxy()
                print("\n✓ System proxy disabled.")
                return 0
            except Exception as exc:
                print(f"\n✗ Disable failed: {exc!r}")
                return 1
        else:
            print(f"  system proxy = {srv}  (probably not PCE — leaving alone)")
            return 0

    print("Nothing to do.")
    return 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _dedup(items):
    """Preserve-order dedup. Same logic as system_state_guard._dedup_bypass."""
    if not items:
        return []
    seen = set()
    out = []
    for v in items:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def print_current_state() -> None:
    state = read_current_state()
    print(f"Platform : {sys.platform}")
    print(f"Snapshot : {STATE_FILE} "
          f"({'EXISTS' if STATE_FILE.is_file() else 'absent'})")
    print()
    if not state:
        print("Could not read OS proxy state on this platform.")
        return
    for k, v in state.items():
        print(f"  {k:18}: {v}")


def read_current_state() -> dict | None:
    if sys.platform.startswith("win"):
        return _win_read_current()
    if sys.platform == "darwin":
        return _macos_read_current()
    if sys.platform.startswith("linux"):
        return _linux_read_current()
    return None


def disable_proxy() -> None:
    if sys.platform.startswith("win"):
        _win_disable()
    elif sys.platform == "darwin":
        _macos_disable()
    elif sys.platform.startswith("linux"):
        _linux_disable()
    else:
        raise NotImplementedError(f"unsupported platform: {sys.platform}")


def enable_proxy(host: str, port: int, bypass: list) -> None:
    if sys.platform.startswith("win"):
        _win_enable(host, port, bypass)
    elif sys.platform == "darwin":
        _macos_enable(host, port, bypass)
    elif sys.platform.startswith("linux"):
        _linux_enable(host, port, bypass)
    else:
        raise NotImplementedError(f"unsupported platform: {sys.platform}")


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------

_WIN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _win_read_current() -> dict:
    import winreg
    out = {"platform": "windows"}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY, 0,
                        winreg.KEY_READ) as key:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
            try:
                val, _ = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                val = None
            out[name] = val
        out["enabled"] = bool(out.get("ProxyEnable"))
        out["server"] = out.get("ProxyServer") or ""
    return out


def _win_write(enable: bool, server: str | None = None,
               override: str | None = None) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD,
                          1 if enable else 0)
        if server is not None:
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        if override is not None:
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, override)
    _win_refresh()


def _win_refresh() -> None:
    """Broadcast that proxy settings changed so WinINET (browsers, Edge,
    most CLI tools) picks up the new value immediately."""
    try:
        import ctypes
        wininet = ctypes.WinDLL("wininet")
        wininet.InternetSetOptionW(None, 39, None, 0)  # SETTINGS_CHANGED
        wininet.InternetSetOptionW(None, 37, None, 0)  # REFRESH
    except Exception:
        pass  # best-effort


def _win_enable(host: str, port: int, bypass: list) -> None:
    server = f"{host}:{port}"
    override = ";".join(_dedup(bypass)) if bypass else ""
    _win_write(True, server=server, override=override)


def _win_disable() -> None:
    _win_write(False)


# ---------------------------------------------------------------------------
# macOS backend — drive networksetup(8)
# ---------------------------------------------------------------------------

def _macos_run(*args) -> tuple[int, str, str]:
    import subprocess
    p = subprocess.run(list(args), capture_output=True, text=True,
                       check=False, timeout=10)
    return p.returncode, p.stdout, p.stderr


def _macos_active_services() -> list:
    rc, out, _ = _macos_run("networksetup", "-listallnetworkservices")
    if rc != 0:
        return []
    return [
        line.strip() for line in out.splitlines()[1:]
        if line.strip() and not line.startswith("*")
    ]


def _macos_read_current() -> dict:
    out = {"platform": "macos"}
    services = _macos_active_services()
    if not services:
        return out
    svc = services[0]
    out["service"] = svc
    rc, info, _ = _macos_run("networksetup", "-getwebproxy", svc)
    if rc == 0:
        out["info"] = info.strip()
        out["enabled"] = "Yes" in info.split("Enabled:")[1].split("\n")[0] \
            if "Enabled:" in info else False
    return out


def _macos_disable() -> None:
    for svc in _macos_active_services():
        _macos_run("networksetup", "-setwebproxystate", svc, "off")
        _macos_run("networksetup", "-setsecurewebproxystate", svc, "off")


def _macos_enable(host: str, port: int, bypass: list) -> None:
    for svc in _macos_active_services():
        _macos_run("networksetup", "-setwebproxy", svc, host, str(port))
        _macos_run("networksetup", "-setsecurewebproxy", svc, host, str(port))
        if bypass:
            _macos_run("networksetup", "-setproxybypassdomains", svc, *bypass)


# ---------------------------------------------------------------------------
# Linux backend (GNOME via gsettings)
# ---------------------------------------------------------------------------

def _linux_run(*args) -> tuple[int, str, str]:
    import subprocess
    p = subprocess.run(list(args), capture_output=True, text=True,
                       check=False, timeout=10)
    return p.returncode, p.stdout, p.stderr


def _linux_read_current() -> dict:
    out = {"platform": "linux"}
    rc, mode, _ = _linux_run("gsettings", "get",
                              "org.gnome.system.proxy", "mode")
    if rc == 0:
        out["mode"] = mode.strip().strip("'")
        out["enabled"] = out["mode"] == "manual"
    return out


def _linux_disable() -> None:
    _linux_run("gsettings", "set", "org.gnome.system.proxy",
               "mode", "none")


def _linux_enable(host: str, port: int, bypass: list) -> None:
    _linux_run("gsettings", "set", "org.gnome.system.proxy",
               "mode", "manual")
    for scheme in ("http", "https"):
        _linux_run("gsettings", "set", f"org.gnome.system.proxy.{scheme}",
                   "host", host)
        _linux_run("gsettings", "set", f"org.gnome.system.proxy.{scheme}",
                   "port", str(port))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(2)
