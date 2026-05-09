# SPDX-License-Identifier: Apache-2.0
"""Shortcut install/uninstall tests — platform-aware where needed."""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pce_app_launcher.claude_desktop import shortcut as sh
from pce_app_launcher.claude_desktop.detector import ClaudeDesktopInstall


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------


def test_install_no_op_when_claude_not_installed(monkeypatch):
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: None)
    result = sh.install_shortcut()
    assert not result.ok
    assert result.action == "no-op"
    assert "not installed" in (result.error or "")


def test_unsupported_platform_returns_error(monkeypatch):
    fake_install = ClaudeDesktopInstall(
        exe_path=Path("/fake"),
        version="1",
        platform="OS/2",
        install_root=Path("/fake_root"),
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)
    monkeypatch.setattr("platform.system", lambda: "OS/2")
    result = sh.install_shortcut()
    assert not result.ok
    assert "unsupported" in (result.error or "")


# ---------------------------------------------------------------------------
# Linux — exercise file-based path on any host (no privileged calls)
# ---------------------------------------------------------------------------


def test_linux_install_writes_desktop_file(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    fake_install = ClaudeDesktopInstall(
        exe_path=tmp_path / "claude",
        version="1.0", platform="Linux",
        install_root=tmp_path,
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)

    result = sh.install_shortcut(python_exe="/usr/bin/python3")
    assert result.ok
    assert result.action == "installed"
    assert result.shortcut_path is not None
    assert result.shortcut_path.is_file()
    contents = result.shortcut_path.read_text(encoding="utf-8")
    assert "Type=Application" in contents
    assert "/usr/bin/python3 -m pce_app_launcher run claude-desktop" in contents


def test_linux_reinstall_creates_backup(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    fake_install = ClaudeDesktopInstall(
        exe_path=tmp_path / "claude",
        version="1.0", platform="Linux",
        install_root=tmp_path,
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)

    sh.install_shortcut(python_exe="/usr/bin/python3")
    second = sh.install_shortcut(python_exe="/usr/bin/python3")
    assert second.ok
    assert second.backup_path is not None
    assert second.backup_path.is_file()


def test_linux_uninstall_after_install(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    fake_install = ClaudeDesktopInstall(
        exe_path=tmp_path / "claude",
        version="1.0", platform="Linux",
        install_root=tmp_path,
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)

    sh.install_shortcut(python_exe="/usr/bin/python3")
    result = sh.uninstall_shortcut()
    assert result.ok
    # Without a prior backup, the file is just removed.
    assert result.action == "uninstalled"


# ---------------------------------------------------------------------------
# macOS — file system parts only (no /usr/bin/osascript needed)
# ---------------------------------------------------------------------------


def test_macos_install_creates_app_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake_install = ClaudeDesktopInstall(
        exe_path=tmp_path / "Applications" / "Claude.app" / "Contents" / "MacOS" / "Claude",
        version="0.9", platform="Darwin",
        install_root=tmp_path / "Applications" / "Claude.app",
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)

    result = sh.install_shortcut(python_exe="/usr/bin/python3")
    assert result.ok
    bundle = result.shortcut_path
    assert bundle is not None
    assert bundle.name == "Claude (via PCE).app"
    runner = bundle / "Contents" / "MacOS" / "Claude_via_PCE"
    assert runner.is_file()
    plist = bundle / "Contents" / "Info.plist"
    assert plist.is_file()
    assert "<key>CFBundleName</key>" in plist.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Windows — only run the path/dir logic; powershell call is mocked
# ---------------------------------------------------------------------------


def test_windows_install_falls_back_when_no_powershell(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    fake_install = ClaudeDesktopInstall(
        exe_path=tmp_path / "AppData" / "Local" / "Programs" / "claude-desktop" / "Claude.exe",
        version="1", platform="Windows",
        install_root=tmp_path / "AppData" / "Local" / "Programs" / "claude-desktop",
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)
    monkeypatch.setattr("shutil.which", lambda _name: None)  # no powershell
    result = sh.install_shortcut(python_exe=str(tmp_path / "python.exe"))
    assert not result.ok
    assert result.action == "fallback"
    assert any("Right-click" in line for line in result.fallback_instructions)


def test_windows_install_no_op_when_no_desktop_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Don't create Desktop dir; expect fallback
    fake_install = ClaudeDesktopInstall(
        exe_path=tmp_path / "Claude.exe",
        version="1", platform="Windows",
        install_root=tmp_path,
    )
    monkeypatch.setattr(sh, "detect_claude_desktop", lambda: fake_install)
    result = sh.install_shortcut(python_exe="python.exe")
    assert not result.ok
    assert result.action == "fallback"
