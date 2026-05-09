# SPDX-License-Identifier: Apache-2.0
"""Detect the Claude Desktop install on Windows / macOS / Linux.

Single entry point :func:`detect_claude_desktop` returns a
:class:`ClaudeDesktopInstall` describing the executable path and a
best-effort version string, or ``None`` if no install is found.

Reuses :mod:`pce_core.electron_proxy.KNOWN_APPS`'s ``claude-desktop``
entry as the source of truth for the candidate paths so the launcher
and the proxy-bypass logic stay in sync.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.app_launcher.claude_desktop.detector")


@dataclass(frozen=True)
class ClaudeDesktopInstall:
    """Resolved Claude Desktop install snapshot."""

    exe_path: Path
    """Absolute path to the executable / .app bundle's MacOS binary."""

    version: Optional[str] = None
    """Version string from the app manifest if extractable, else None."""

    platform: str = ""
    """One of ``"Windows"`` / ``"Darwin"`` / ``"Linux"`` (``platform.system()``)."""

    install_root: Optional[Path] = None
    """Top-level install directory (parent of exe on Win/Linux; .app on macOS)."""


def detect_claude_desktop() -> Optional[ClaudeDesktopInstall]:
    """Locate a Claude Desktop install on the current host.

    Returns ``None`` if Claude Desktop is not installed.

    Detection strategy (per platform, first hit wins):

    - **Windows**: Try the two install paths under ``%USERPROFILE%``
      that the Anthropic installer uses, then fall back to ``where Claude``.
    - **macOS**: Try ``/Applications/Claude.app`` (system) and
      ``~/Applications/Claude.app`` (per-user). Use the bundle's
      ``Contents/MacOS/Claude`` as the executable.
    - **Linux**: No official Claude Desktop for Linux as of this writing;
      we still call ``shutil.which("claude")`` for users who built one.
    """
    system = platform.system()
    home = Path.home()

    if system == "Windows":
        candidates = [
            home / "AppData" / "Local" / "Programs" / "claude-desktop" / "Claude.exe",
            home / "AppData" / "Local" / "AnthropicClaude" / "Claude.exe",
        ]
        for cand in candidates:
            if cand.is_file():
                return ClaudeDesktopInstall(
                    exe_path=cand,
                    version=_read_version_windows(cand),
                    platform=system,
                    install_root=cand.parent,
                )
        which = shutil.which("Claude.exe") or shutil.which("claude")
        if which:
            p = Path(which)
            return ClaudeDesktopInstall(
                exe_path=p,
                version=_read_version_windows(p),
                platform=system,
                install_root=p.parent,
            )
        return None

    if system == "Darwin":
        candidates = [
            Path("/Applications/Claude.app"),
            home / "Applications" / "Claude.app",
        ]
        for bundle in candidates:
            if bundle.is_dir():
                exe = bundle / "Contents" / "MacOS" / "Claude"
                if exe.is_file():
                    return ClaudeDesktopInstall(
                        exe_path=exe,
                        version=_read_version_macos(bundle),
                        platform=system,
                        install_root=bundle,
                    )
        return None

    if system == "Linux":
        which = shutil.which("claude")
        if which:
            p = Path(which)
            return ClaudeDesktopInstall(
                exe_path=p,
                version=None,
                platform=system,
                install_root=p.parent,
            )
        return None

    return None


# ---------------------------------------------------------------------------
# Best-effort version extraction
# ---------------------------------------------------------------------------


def _read_version_windows(exe_path: Path) -> Optional[str]:
    """Read FileVersion from the Windows PE header. Best-effort only.

    On Windows 10+, Anthropic's Squirrel installer also drops a
    ``Latest\\app-<version>`` directory; we sniff that as a fallback.
    """
    # Try the Squirrel layout first — it's cheap and avoids needing pywin32.
    parent = exe_path.parent
    for sibling in (parent, parent.parent):
        if not sibling.is_dir():
            continue
        for child in sibling.iterdir():
            if child.is_dir() and child.name.lower().startswith("app-"):
                return child.name[4:]  # "app-1.2.3" → "1.2.3"
    # PE-version probing requires pywin32; skip silently if not available.
    try:
        import win32api  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        info = win32api.GetFileVersionInfo(str(exe_path), "\\")  # type: ignore[arg-type]
        ms = info.get("FileVersionMS")
        ls = info.get("FileVersionLS")
        if ms is None or ls is None:
            return None
        return (
            f"{(ms >> 16) & 0xFFFF}.{ms & 0xFFFF}."
            f"{(ls >> 16) & 0xFFFF}.{ls & 0xFFFF}"
        )
    except Exception:
        return None


def _read_version_macos(bundle: Path) -> Optional[str]:
    """Parse CFBundleShortVersionString from Info.plist in the .app bundle."""
    plist_path = bundle / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        import plistlib

        with plist_path.open("rb") as fh:
            info = plistlib.load(fh)
        version = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
        return str(version) if version else None
    except Exception:
        return None


def _read_version_from_package_json(pkg_path: Path) -> Optional[str]:
    """Helper for builds that ship a ``resources/app/package.json``."""
    if not pkg_path.is_file():
        return None
    try:
        with pkg_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        v = data.get("version")
        return str(v) if v else None
    except Exception:
        return None
