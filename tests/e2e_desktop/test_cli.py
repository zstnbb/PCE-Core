# SPDX-License-Identifier: Apache-2.0
"""CLI smoke tests — invoke pce_app_launcher subcommands as a subprocess."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pce_app_launcher", *args]
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_cli_no_args_shows_help_and_errors():
    result = _run_cli()
    assert result.returncode != 0
    assert "subcommand" in (result.stderr.lower() + result.stdout.lower())


def test_cli_detect_runs_and_returns_json():
    result = _run_cli("detect")
    # Returns 0 if installed, 1 if not — both are acceptable; both must be valid JSON.
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert "app" in payload and payload["app"] == "claude-desktop"
    assert "found" in payload


def test_cli_status_runs_and_returns_json():
    result = _run_cli("status")
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert "app" in payload
    assert "installed" in payload
    assert "platform" in payload


def test_cli_unsupported_app_rejected():
    result = _run_cli("detect", "--app=cursor")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr.lower() or "cursor" in result.stderr.lower()


def test_cli_install_shortcut_when_no_install():
    """When Claude Desktop is not installed, install-shortcut should fail cleanly."""
    # We can't rely on Claude not being installed on the test machine, so we
    # just verify that the command runs and returns parseable JSON in either
    # outcome.
    result = _run_cli("install-shortcut")
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert "ok" in payload
    assert "action" in payload


def test_cli_uninstall_shortcut_returns_parseable_json():
    result = _run_cli("uninstall-shortcut")
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert "ok" in payload
    assert "action" in payload
