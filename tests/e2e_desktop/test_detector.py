# SPDX-License-Identifier: Apache-2.0
"""Detector tests — filesystem-based, no Claude Desktop install required."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pce_app_launcher.claude_desktop import detector


def test_detect_returns_none_when_nothing_installed(tmp_path, monkeypatch):
    """All candidate paths under a fresh tmp HOME → None."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert detector.detect_claude_desktop() is None


def test_detect_finds_windows_install(tmp_path, monkeypatch):
    """Drop a fake Claude.exe at one of the documented Windows paths."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    install_dir = tmp_path / "AppData" / "Local" / "Programs" / "claude-desktop"
    install_dir.mkdir(parents=True)
    exe = install_dir / "Claude.exe"
    exe.write_bytes(b"MZ\x90\x00")  # minimal PE magic

    result = detector.detect_claude_desktop()
    assert result is not None
    assert result.exe_path == exe
    assert result.platform == "Windows"
    assert result.install_root == install_dir


def test_detect_picks_up_squirrel_version(tmp_path, monkeypatch):
    """Detector should read 'app-1.2.3' Squirrel directory as version."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    install_dir = tmp_path / "AppData" / "Local" / "Programs" / "claude-desktop"
    install_dir.mkdir(parents=True)
    exe = install_dir / "Claude.exe"
    exe.write_bytes(b"MZ")
    # Squirrel layout: parent/Latest/app-<version>/  OR sibling app-<version>/
    (install_dir / "app-1.2.3").mkdir()

    result = detector.detect_claude_desktop()
    assert result is not None
    assert result.version == "1.2.3"


def test_detect_macos_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    bundle = tmp_path / "Applications" / "Claude.app"
    macos_dir = bundle / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    exe = macos_dir / "Claude"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    # Info.plist for version probe
    plist_dir = bundle / "Contents"
    (plist_dir / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        "<key>CFBundleShortVersionString</key><string>0.9.5</string>"
        "</dict></plist>",
        encoding="utf-8",
    )
    # Patch the system-level /Applications check to fail
    real_is_dir = Path.is_dir

    def fake_is_dir(self):
        if str(self) == "/Applications/Claude.app":
            return False
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", real_is_file)

    result = detector.detect_claude_desktop()
    assert result is not None
    assert result.exe_path == exe
    assert result.platform == "Darwin"
    assert result.version == "0.9.5"


def test_detect_linux_uses_which(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    fake_path = tmp_path / "usr" / "bin" / "claude"
    fake_path.parent.mkdir(parents=True)
    fake_path.write_text("#!/bin/sh\n")
    monkeypatch.setattr("shutil.which", lambda name: str(fake_path) if name == "claude" else None)

    result = detector.detect_claude_desktop()
    assert result is not None
    assert result.exe_path == fake_path
    assert result.platform == "Linux"


def test_read_version_from_package_json(tmp_path):
    pkg = tmp_path / "package.json"
    pkg.write_text('{"version":"2.0.0-beta"}', encoding="utf-8")
    assert detector._read_version_from_package_json(pkg) == "2.0.0-beta"


def test_read_version_from_package_json_missing_returns_none(tmp_path):
    assert detector._read_version_from_package_json(tmp_path / "missing.json") is None
