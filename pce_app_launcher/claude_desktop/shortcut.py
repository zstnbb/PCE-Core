# SPDX-License-Identifier: Apache-2.0
"""Install / uninstall a desktop shortcut that launches Claude Desktop
through ``pce_app_launcher`` so users double-click the same icon they
always did and get transparent capture (ADR-016 §3.2).

Per-platform implementation strategy:

- **Windows**: Write a ``.lnk`` next to the existing Start Menu /
  Desktop shortcut whose target is::

      pythonw.exe -m pce_app_launcher run claude-desktop

  with ``--icon`` pointing at the original Claude.exe so the icon
  picture is preserved. Backup the original shortcut to
  ``<name>.pce-backup-<ts>.lnk`` next to it (or note its location in a
  manifest) so uninstall can restore. Pure-stdlib ``.lnk`` writing
  isn't available; we degrade gracefully via PowerShell (always
  installed on Win10+) when ``pywin32`` isn't on PATH.

- **macOS**: Drop an ``Automator``-style ``.app`` wrapper that runs
  ``python3 -m pce_app_launcher run claude-desktop`` and shows
  Claude.app's icon. Stub for v1.1.

- **Linux**: Write a ``.desktop`` file under
  ``~/.local/share/applications/`` referencing ``python3 -m
  pce_app_launcher run claude-desktop``. Backup any pre-existing file
  with the same name.

This module's design intent is **best-effort**. If any platform path
is unavailable (no PowerShell on Win, no /usr/bin/osascript on macOS,
no XDG dir on Linux) the function returns a structured "fallback"
record with copy-paste manual instructions so the user is never blocked.

Returns from :func:`install_shortcut` and :func:`uninstall_shortcut` are
:class:`ShortcutResult` dataclasses describing what actually happened.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .detector import detect_claude_desktop

logger = logging.getLogger("pce.app_launcher.claude_desktop.shortcut")


@dataclass
class ShortcutResult:
    """Outcome of an install / uninstall_shortcut call."""

    ok: bool
    action: str  # "installed" | "uninstalled" | "no-op" | "fallback"
    shortcut_path: Optional[Path] = None
    backup_path: Optional[Path] = None
    fallback_instructions: list[str] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_shortcut(
    *,
    python_exe: Optional[str] = None,
    pythonw_exe: Optional[str] = None,
    target: str = "desktop",
) -> ShortcutResult:
    """Install a PCE-wrapped Claude Desktop shortcut.

    Args:
        python_exe: Python interpreter for the wrapper command. Defaults
            to ``sys.executable``.
        pythonw_exe: On Windows, prefer ``pythonw.exe`` (no console
            window). Defaults to ``sys.executable`` with the ``python``
            → ``pythonw`` substitution.
        target: ``"desktop"`` (default) or ``"start_menu"`` (Windows
            only). Other platforms ignore this.
    """
    install = detect_claude_desktop()
    if install is None:
        return ShortcutResult(
            ok=False,
            action="no-op",
            error="Claude Desktop is not installed; nothing to wrap.",
        )

    system = platform.system()
    py = python_exe or sys.executable
    if system == "Windows":
        return _install_windows(install, py, pythonw_exe, target)
    if system == "Darwin":
        return _install_macos(install, py)
    if system == "Linux":
        return _install_linux(install, py)
    return ShortcutResult(
        ok=False,
        action="no-op",
        error=f"unsupported platform: {system}",
    )


def uninstall_shortcut(*, target: str = "desktop") -> ShortcutResult:
    """Remove a previously installed PCE-wrapped shortcut, restoring backup."""
    system = platform.system()
    if system == "Windows":
        return _uninstall_windows(target)
    if system == "Darwin":
        return _uninstall_macos()
    if system == "Linux":
        return _uninstall_linux()
    return ShortcutResult(
        ok=False,
        action="no-op",
        error=f"unsupported platform: {system}",
    )


# ---------------------------------------------------------------------------
# Internals — Windows
# ---------------------------------------------------------------------------


def _windows_shortcut_dir(target: str) -> Optional[Path]:
    home = Path.home()
    if target == "start_menu":
        sm = (
            home / "AppData" / "Roaming" / "Microsoft"
            / "Windows" / "Start Menu" / "Programs" / "Anthropic"
        )
        return sm if sm.is_dir() else None
    # default: desktop
    desktop = home / "Desktop"
    return desktop if desktop.is_dir() else None


def _install_windows(install, python_exe: str, pythonw_exe: Optional[str], target: str) -> ShortcutResult:
    shortcut_dir = _windows_shortcut_dir(target)
    if shortcut_dir is None:
        return _windows_fallback(install, python_exe, "no_shortcut_dir")

    pythonw = pythonw_exe or python_exe.replace("python.exe", "pythonw.exe")
    if not Path(pythonw).is_file():
        pythonw = python_exe  # fallback to python.exe

    shortcut_path = shortcut_dir / "Claude (via PCE).lnk"

    # Use PowerShell COM scripting to avoid a pywin32 dep.
    ps_script = (
        "$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path}'); "
        f"$Shortcut.TargetPath = '{pythonw}'; "
        f"$Shortcut.Arguments = '-m pce_app_launcher run claude-desktop'; "
        f"$Shortcut.WorkingDirectory = '{install.install_root}'; "
        f"$Shortcut.IconLocation = '{install.exe_path},0'; "
        "$Shortcut.Description = 'Claude Desktop launched via PCE for capture'; "
        "$Shortcut.Save()"
    )

    if shutil.which("powershell") is None and shutil.which("pwsh") is None:
        return _windows_fallback(install, python_exe, "no_powershell")

    backup_path = None
    if shortcut_path.exists():
        backup_path = shortcut_path.with_name(
            f"{shortcut_path.stem}.pce-backup-{int(time.time())}.lnk"
        )
        try:
            shortcut_path.replace(backup_path)
        except Exception as exc:
            return ShortcutResult(
                ok=False,
                action="no-op",
                error=f"could not back up existing shortcut: {exc}",
            )

    powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as exc:
        return ShortcutResult(
            ok=False,
            action="no-op",
            error=f"powershell invocation failed: {exc}",
        )
    if result.returncode != 0:
        return ShortcutResult(
            ok=False,
            action="no-op",
            error=(
                f"PowerShell shortcut creation failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            ),
            backup_path=backup_path,
        )
    return ShortcutResult(
        ok=True,
        action="installed",
        shortcut_path=shortcut_path,
        backup_path=backup_path,
    )


def _uninstall_windows(target: str) -> ShortcutResult:
    shortcut_dir = _windows_shortcut_dir(target)
    if shortcut_dir is None:
        return ShortcutResult(ok=False, action="no-op", error="no shortcut dir found")
    shortcut_path = shortcut_dir / "Claude (via PCE).lnk"

    backups = sorted(shortcut_dir.glob("Claude (via PCE).pce-backup-*.lnk"))
    if shortcut_path.is_file():
        try:
            shortcut_path.unlink()
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=str(exc))

    if backups:
        latest = backups[-1]
        restored = shortcut_dir / "Claude (via PCE).lnk"
        try:
            shutil.copy2(latest, restored)
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=f"restore failed: {exc}")
        return ShortcutResult(
            ok=True,
            action="uninstalled",
            shortcut_path=restored,
            backup_path=latest,
        )
    return ShortcutResult(ok=True, action="uninstalled", shortcut_path=shortcut_path)


def _windows_fallback(install, python_exe: str, reason: str) -> ShortcutResult:
    return ShortcutResult(
        ok=False,
        action="fallback",
        error=f"automatic shortcut install unavailable ({reason})",
        fallback_instructions=[
            "Right-click your Claude Desktop shortcut → Properties.",
            "In the Target field, replace the existing path with:",
            f'  "{python_exe}" -m pce_app_launcher run claude-desktop',
            f"  Start in: {install.install_root}",
            "Click Apply, then OK. Double-clicking the shortcut now routes",
            "through PCE for capture before launching Claude Desktop.",
        ],
    )


# ---------------------------------------------------------------------------
# Internals — macOS
# ---------------------------------------------------------------------------


def _macos_shortcut_path() -> Path:
    return Path.home() / "Applications" / "Claude (via PCE).app"


def _install_macos(install, python_exe: str) -> ShortcutResult:
    target = _macos_shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if target.exists():
        backup_path = target.with_name(
            f"{target.stem}.pce-backup-{int(time.time())}.app"
        )
        try:
            shutil.move(str(target), str(backup_path))
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=f"backup failed: {exc}")

    # Build a minimal .app bundle that runs our launcher.
    macos_dir = target / "Contents" / "MacOS"
    resources_dir = target / "Contents" / "Resources"
    try:
        macos_dir.mkdir(parents=True, exist_ok=True)
        resources_dir.mkdir(parents=True, exist_ok=True)

        runner_path = macos_dir / "Claude_via_PCE"
        runner_path.write_text(
            "#!/bin/sh\n"
            f'exec "{python_exe}" -m pce_app_launcher run claude-desktop "$@"\n',
            encoding="utf-8",
        )
        try:
            os.chmod(runner_path, 0o755)
        except Exception:
            pass

        info_plist = target / "Contents" / "Info.plist"
        info_plist.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
            "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
            "<plist version=\"1.0\"><dict>\n"
            "  <key>CFBundleExecutable</key><string>Claude_via_PCE</string>\n"
            "  <key>CFBundleIdentifier</key><string>com.pce.claude-via-pce</string>\n"
            "  <key>CFBundleName</key><string>Claude (via PCE)</string>\n"
            "  <key>CFBundlePackageType</key><string>APPL</string>\n"
            "  <key>CFBundleVersion</key><string>0.1.0</string>\n"
            "</dict></plist>\n",
            encoding="utf-8",
        )

        # Best-effort icon copy from the original .app bundle
        original_icns = install.install_root / "Contents" / "Resources" / "icon.icns"
        if original_icns.is_file():
            try:
                shutil.copy2(original_icns, resources_dir / "icon.icns")
            except Exception:
                pass

        return ShortcutResult(
            ok=True,
            action="installed",
            shortcut_path=target,
            backup_path=backup_path,
        )
    except Exception as exc:
        return ShortcutResult(ok=False, action="no-op", error=str(exc))


def _uninstall_macos() -> ShortcutResult:
    target = _macos_shortcut_path()
    if target.exists():
        try:
            shutil.rmtree(target)
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=str(exc))
    backups = sorted(target.parent.glob("Claude (via PCE).pce-backup-*.app"))
    if backups:
        latest = backups[-1]
        try:
            shutil.copytree(latest, target)
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=f"restore failed: {exc}")
        return ShortcutResult(
            ok=True,
            action="uninstalled",
            shortcut_path=target,
            backup_path=latest,
        )
    return ShortcutResult(ok=True, action="uninstalled", shortcut_path=target)


# ---------------------------------------------------------------------------
# Internals — Linux
# ---------------------------------------------------------------------------


def _linux_shortcut_path() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "applications" / "claude-via-pce.desktop"


def _install_linux(install, python_exe: str) -> ShortcutResult:
    target = _linux_shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if target.exists():
        backup_path = target.with_name(
            f"{target.stem}.pce-backup-{int(time.time())}.desktop"
        )
        try:
            shutil.move(str(target), str(backup_path))
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=f"backup failed: {exc}")

    contents = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Claude (via PCE)\n"
        "Comment=Claude Desktop launched via PCE for capture\n"
        f"Exec={python_exe} -m pce_app_launcher run claude-desktop %U\n"
        "Terminal=false\n"
        "Categories=Network;Chat;\n"
        "Icon=claude\n"
    )
    try:
        target.write_text(contents, encoding="utf-8")
        try:
            os.chmod(target, 0o755)
        except Exception:
            pass
        return ShortcutResult(
            ok=True,
            action="installed",
            shortcut_path=target,
            backup_path=backup_path,
        )
    except Exception as exc:
        return ShortcutResult(ok=False, action="no-op", error=str(exc))


def _uninstall_linux() -> ShortcutResult:
    target = _linux_shortcut_path()
    if target.exists():
        try:
            target.unlink()
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=str(exc))
    backups = sorted(target.parent.glob("claude-via-pce.pce-backup-*.desktop"))
    if backups:
        latest = backups[-1]
        try:
            shutil.copy2(latest, target)
        except Exception as exc:
            return ShortcutResult(ok=False, action="no-op", error=f"restore failed: {exc}")
        return ShortcutResult(
            ok=True,
            action="uninstalled",
            shortcut_path=target,
            backup_path=latest,
        )
    return ShortcutResult(ok=True, action="uninstalled", shortcut_path=target)
