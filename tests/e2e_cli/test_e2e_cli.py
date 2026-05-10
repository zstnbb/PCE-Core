# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI tests for ``python -m pce_cli_wrapper``.

Exercises ``main()`` with discovery monkey-patched to return a
synthesised target. Verifies the four subcommands (status, install,
uninstall, relay) return correct exit codes and side-effects.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import pce_cli_wrapper.__main__ as main_mod
from pce_cli_wrapper.discovery import ShimTarget


L3H_SOURCE_ID = "l3h-cli-wrapper-default"


def _patch_discover(
    monkeypatch: pytest.MonkeyPatch,
    targets: list[ShimTarget],
) -> None:
    """Replace the ``discover`` symbols imported by the CLI surface."""
    monkeypatch.setattr(main_mod, "discover", lambda **_kw: list(targets))
    # The install module also imports discover() lazily in its
    # ``install()`` / ``uninstall()`` helpers when targets is None.
    import pce_cli_wrapper.install as install_mod
    monkeypatch.setattr(install_mod, "discover", lambda **_kw: list(targets))
    # Relay's _augment_target_meta uses discover() to look up provider.
    import pce_cli_wrapper.relay as relay_mod
    monkeypatch.setattr(relay_mod, "discover", lambda **_kw: list(targets))


def _mk_target(*, shim_path: Path | None) -> ShimTarget:
    return ShimTarget(
        target_id="claude-code",
        command_name="claude",
        provider="anthropic",
        description="test",
        shim_path=shim_path,
        shim_variants=[shim_path] if shim_path else [],
        version="2.1.59",
        npm_prefix=shim_path.parent if shim_path else None,
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        _patch_discover(monkeypatch, [])
        rc = main_mod.main(["status"])
        assert rc == 0

    def test_status_prints_target_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        _patch_discover(monkeypatch, [_mk_target(shim_path=real)])

        rc = main_mod.main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude-code" in out
        assert "claude" in out
        assert "2.1.59" in out

    def test_status_json_is_valid(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        _patch_discover(monkeypatch, [_mk_target(shim_path=real)])

        rc = main_mod.main(["status", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 1
        assert payload["targets"][0]["target_id"] == "claude-code"
        assert "bin_dir" in payload
        assert "wrapper_version" in payload


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


class TestInstallUninstall:
    def test_install_writes_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        _patch_discover(monkeypatch, [_mk_target(shim_path=real)])

        rc = main_mod.main(["install", "--bin-dir", str(bin_dir)])
        assert rc == 0
        if sys.platform == "win32":
            assert (bin_dir / "claude.cmd").exists()
            assert (bin_dir / "claude.ps1").exists()
        else:
            assert (bin_dir / "claude").exists()

    def test_install_dry_run_does_not_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        _patch_discover(monkeypatch, [_mk_target(shim_path=real)])

        rc = main_mod.main(["install", "--bin-dir", str(bin_dir), "--dry-run"])
        assert rc == 0
        assert not bin_dir.exists()

    def test_install_no_targets_returns_3(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bin_dir = tmp_path / "bin"
        _patch_discover(monkeypatch, [_mk_target(shim_path=None)])
        rc = main_mod.main(["install", "--bin-dir", str(bin_dir)])
        # Exit 3 = no actionable targets.
        assert rc == 3

    def test_uninstall_removes_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        _patch_discover(monkeypatch, [_mk_target(shim_path=real)])

        rc = main_mod.main(["install", "--bin-dir", str(bin_dir)])
        assert rc == 0

        rc2 = main_mod.main(["uninstall", "--bin-dir", str(bin_dir)])
        assert rc2 == 0
        # bin_dir is swept when empty.
        assert not bin_dir.exists()

    def test_install_json_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        _patch_discover(monkeypatch, [_mk_target(shim_path=real)])

        rc = main_mod.main(["install", "--bin-dir", str(bin_dir), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "install"
        assert payload["bin_dir"] == str(bin_dir)
        assert isinstance(payload["actions"], list)
        assert isinstance(payload["path_guidance"], list)


# ---------------------------------------------------------------------------
# relay (CLI surface)
# ---------------------------------------------------------------------------


class TestRelay:
    def test_relay_runs_fake_shim_and_writes_row(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        count_l3h_rows,
    ) -> None:
        # Discovery returns nothing — relay falls back to "unknown"
        # provider/target_id. That's fine.
        _patch_discover(monkeypatch, [])
        rc = main_mod.main([
            "relay",
            "--target", str(fake_target),
            "--db-path", str(tmp_pce_db),
            "--",
            str(fake_shim_script), "hello",
        ])
        assert rc == 0
        assert count_l3h_rows(tmp_pce_db) == 1

    def test_relay_missing_target_returns_127(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _patch_discover(monkeypatch, [])
        rc = main_mod.main([
            "relay",
            "--target", str(tmp_path / "missing.exe"),
            "--",
        ])
        assert rc == 127

    def test_relay_propagates_child_exit_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
    ) -> None:
        _patch_discover(monkeypatch, [])
        rc = main_mod.main([
            "relay",
            "--target", str(fake_target),
            "--db-path", str(tmp_pce_db),
            "--",
            str(fake_shim_script), "--exit-code", "5",
        ])
        assert rc == 5

    def test_relay_dry_run_skips_db_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        count_l3h_rows,
    ) -> None:
        _patch_discover(monkeypatch, [])
        rc = main_mod.main([
            "relay",
            "--target", str(fake_target),
            "--db-path", str(tmp_pce_db),
            "--dry-run",
            "--",
            str(fake_shim_script), "hi",
        ])
        assert rc == 0
        assert count_l3h_rows(tmp_pce_db) == 0
