# SPDX-License-Identifier: Apache-2.0
"""SSLKEYLOGFILE — env var management (W2-T2 / ADR-018 Phase 5).

Sets / clears ``SSLKEYLOGFILE`` so Chromium-based apps (Chrome, Edge,
ChatGPT Desktop, Claude Desktop) write their TLS pre-master secrets
to a path PCE controls. The actual NSS Key Log Format parsing lives in
``pce_proxy/keylog_mode.py`` so the proxy addon can ingest it.

Privacy contract (Wave 2 §4.4):

  - Default location is ``%LOCALAPPDATA%\\pce\\keylog.txt`` —
    user-private directory, other Windows accounts can't read it.
  - keylog content is **never** written to ``raw_captures.body``.
    Only metadata (``completeness``, ``session_count``) goes into
    ``meta_json.keylog`` for forensic-grade corroboration.
  - User can wipe the file at any time: ``pce keylog clear`` →
    :func:`clear_keylog_file`.

Cross-platform: only Windows is in v1 scope per SCOPE-LOCK §2.2,
but the code is portable (``%LOCALAPPDATA%`` resolves via
``os.path.expandvars`` on Windows, falls back to ``~/.pce`` elsewhere).

Per ADR-018 Phase 5 §5.2 the env var must be **user-scope on Windows**,
NOT system-scope — system-scope leaks secrets to other accounts.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.cert_wizard.keylog")

KEYLOG_ENV_VAR = "SSLKEYLOGFILE"


# ---------------------------------------------------------------------------
# Default path
# ---------------------------------------------------------------------------

def default_keylog_path() -> Path:
    """``%LOCALAPPDATA%\\pce\\keylog.txt`` on Windows, ``~/.pce/keylog.txt`` else."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "pce" / "keylog.txt"
        # Fallback: %APPDATA% / Local
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / ".." / "Local" / "pce" / "keylog.txt"
    return Path.home() / ".pce" / "keylog.txt"


def ensure_keylog_dir(path: Path) -> None:
    """Create parent directory with restrictive perms where possible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Windows":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Env var management — user scope (NOT system) per ADR-018 §5.2
# ---------------------------------------------------------------------------

def get_keylog_env() -> Optional[str]:
    """Return the current value of ``SSLKEYLOGFILE`` in the **process** env."""
    return os.environ.get(KEYLOG_ENV_VAR)


def set_keylog_env_user(path: Optional[Path] = None) -> Path:
    """Persist ``SSLKEYLOGFILE`` at user scope (Windows registry HKCU).

    On non-Windows platforms this is a no-op for persistence (we still
    set the value in the current process's env so the live test
    works); the user is expected to add it to their shell rc themselves.

    Returns the resolved path.
    """
    target = path or default_keylog_path()
    target = Path(target)
    ensure_keylog_dir(target)
    target_str = str(target)
    os.environ[KEYLOG_ENV_VAR] = target_str

    if platform.system() == "Windows":
        _persist_user_env_windows(KEYLOG_ENV_VAR, target_str)
    else:
        logger.info("on %s, persistence requires editing your shell rc; "
                    "set %s=%s manually for cross-session use",
                    platform.system(), KEYLOG_ENV_VAR, target_str)
    return target


def clear_keylog_env() -> None:
    """Remove ``SSLKEYLOGFILE`` from the user environment."""
    os.environ.pop(KEYLOG_ENV_VAR, None)
    if platform.system() == "Windows":
        _remove_user_env_windows(KEYLOG_ENV_VAR)


def clear_keylog_file(path: Optional[Path] = None) -> bool:
    """Delete the keylog file (privacy: ``pce keylog clear``)."""
    target = Path(path or default_keylog_path())
    if target.exists():
        try:
            target.unlink()
            return True
        except OSError as exc:
            logger.warning("could not delete %s: %s", target, exc)
            return False
    return False


# ---------------------------------------------------------------------------
# Windows registry helpers (winreg only on Windows)
# ---------------------------------------------------------------------------

def _persist_user_env_windows(name: str, value: str) -> None:
    """Write to HKCU\\Environment + broadcast WM_SETTINGCHANGE."""
    if platform.system() != "Windows":
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        logger.warning("winreg unavailable; skipping persistence")
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError as exc:
        logger.warning("failed to persist user env var %s: %s", name, exc)
        return
    _broadcast_settings_change()


def _remove_user_env_windows(name: str) -> None:
    if platform.system() != "Windows":
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    except OSError as exc:
        logger.warning("failed to delete user env var %s: %s", name, exc)
        return
    _broadcast_settings_change()


def _broadcast_settings_change() -> None:
    """Notify Windows that env vars changed (so newly-spawned procs see it)."""
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        from ctypes import wintypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002

        SendMessageTimeoutW = ctypes.windll.user32.SendMessageTimeoutW
        SendMessageTimeoutW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
            ctypes.c_wchar_p, wintypes.UINT, wintypes.UINT,
            ctypes.POINTER(wintypes.DWORD),
        )
        SendMessageTimeoutW.restype = wintypes.LPARAM
        result = wintypes.DWORD()
        SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 5000, ctypes.byref(result),
        )
    except Exception:  # pragma: no cover
        logger.debug("WM_SETTINGCHANGE broadcast failed (non-fatal)")


# ---------------------------------------------------------------------------
# CLI shim — surfaced as ``pce keylog ...`` via the click wrapper layer
# ---------------------------------------------------------------------------

def cli_set(path: Optional[str] = None) -> int:
    target = Path(path) if path else default_keylog_path()
    resolved = set_keylog_env_user(target)
    print(f"set {KEYLOG_ENV_VAR}={resolved}")
    return 0


def cli_clear(*, also_delete_file: bool = False) -> int:
    clear_keylog_env()
    print(f"unset {KEYLOG_ENV_VAR}")
    if also_delete_file:
        ok = clear_keylog_file()
        print(f"keylog file: {'deleted' if ok else 'not present'}")
    return 0


def cli_status() -> int:
    val = get_keylog_env()
    if val:
        path = Path(val)
        size = path.stat().st_size if path.exists() else 0
        print(f"{KEYLOG_ENV_VAR}={val}")
        print(f"  exists={path.exists()} size_bytes={size}")
    else:
        print(f"{KEYLOG_ENV_VAR} not set in current process")
    return 0
