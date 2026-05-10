# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_cli_wrapper.install``.

Hermetic. Synthesised ``ShimTarget`` values are passed in directly so
the tests exercise the install / uninstall logic regardless of what's
actually on the host.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pce_cli_wrapper.discovery import ShimTarget
from pce_cli_wrapper.install import (
    InstallReport,
    _WRAPPER_MARKER,
    _bin_dir_on_path,
    _looks_like_our_wrapper,
    _path_guidance,
    _wrapper_variants_for,
    install,
    uninstall,
)


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
# install()
# ---------------------------------------------------------------------------


class TestInstall:
    def test_creates_wrapper_files_for_present_target(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)

        report = install(bin_dir=bin_dir, targets=[target])

        # Either Windows variants (.cmd + .ps1) or POSIX (bare).
        if sys.platform == "win32":
            assert (bin_dir / "claude.cmd").exists()
            assert (bin_dir / "claude.ps1").exists()
        else:
            assert (bin_dir / "claude").exists()

        actions = [a for a in report.actions if a.action == "create"]
        assert actions, "expected at least one create action"

    def test_wrapper_carries_the_marker(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)

        install(bin_dir=bin_dir, targets=[target])

        if sys.platform == "win32":
            sample = (bin_dir / "claude.cmd").read_text(encoding="utf-8")
        else:
            sample = (bin_dir / "claude").read_text(encoding="utf-8")
        assert _WRAPPER_MARKER in sample

    def test_wrapper_references_the_real_target(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)
        install(bin_dir=bin_dir, targets=[target])

        if sys.platform == "win32":
            body = (bin_dir / "claude.cmd").read_text(encoding="utf-8")
        else:
            body = (bin_dir / "claude").read_text(encoding="utf-8")
        assert str(real) in body
        assert "pce_cli_wrapper" in body
        assert "relay" in body
        assert "--target" in body

    def test_skip_when_target_not_installed(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        target = _mk_target(shim_path=None)
        report = install(bin_dir=bin_dir, targets=[target])
        assert all(a.action == "skip" for a in report.actions)
        # bin_dir was created (mkdir ok) but no wrapper files.
        assert bin_dir.exists()
        assert not list(bin_dir.iterdir())

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)
        report = install(bin_dir=bin_dir, targets=[target], dry_run=True)

        assert not bin_dir.exists()
        assert all(a.action in ("create", "skip") for a in report.actions)

    def test_idempotent_install(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)
        install(bin_dir=bin_dir, targets=[target])
        first = sorted(p.name for p in bin_dir.iterdir())
        install(bin_dir=bin_dir, targets=[target])
        second = sorted(p.name for p in bin_dir.iterdir())
        assert first == second


# ---------------------------------------------------------------------------
# uninstall()
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_removes_our_wrappers(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)
        install(bin_dir=bin_dir, targets=[target])

        report = uninstall(bin_dir=bin_dir, targets=[target])
        assert any(a.action == "remove" for a in report.actions)
        if sys.platform == "win32":
            assert not (bin_dir / "claude.cmd").exists()
            assert not (bin_dir / "claude.ps1").exists()
        else:
            assert not (bin_dir / "claude").exists()

    def test_preserves_foreign_files(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # A user-placed file that does NOT have our marker.
        foreign = bin_dir / ("claude.cmd" if sys.platform == "win32" else "claude")
        foreign.write_text(
            "@echo off\r\nrem some-other-tool\r\n", encoding="utf-8"
        )
        target = _mk_target(shim_path=foreign)
        report = uninstall(bin_dir=bin_dir, targets=[target])

        assert foreign.exists()
        skip_actions = [a for a in report.actions if a.action == "skip"]
        assert any(
            "not a PCE-generated wrapper" in a.reason for a in skip_actions
        )

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)
        install(bin_dir=bin_dir, targets=[target])

        report = uninstall(bin_dir=bin_dir, targets=[target], dry_run=True)
        assert any(a.action == "remove" for a in report.actions)
        if sys.platform == "win32":
            assert (bin_dir / "claude.cmd").exists()
        else:
            assert (bin_dir / "claude").exists()

    def test_uninstall_sweeps_empty_bin_dir(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        real = tmp_path / "real" / "claude.cmd"
        real.parent.mkdir()
        real.write_text("@echo off\r\n", encoding="utf-8")
        target = _mk_target(shim_path=real)
        install(bin_dir=bin_dir, targets=[target])
        uninstall(bin_dir=bin_dir, targets=[target])
        assert not bin_dir.exists()


# ---------------------------------------------------------------------------
# Helper introspection
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_wrapper_variants_for_windows(self, tmp_path: Path) -> None:
        names = [v.name for v in _wrapper_variants_for(tmp_path, "claude")]
        if sys.platform == "win32":
            assert names == ["claude.cmd", "claude.ps1"]
        else:
            assert names == ["claude"]

    def test_looks_like_our_wrapper_positive(self, tmp_path: Path) -> None:
        f = tmp_path / "x.cmd"
        f.write_text(
            f"@echo off\r\nrem {_WRAPPER_MARKER}\r\n", encoding="utf-8"
        )
        assert _looks_like_our_wrapper(f) is True

    def test_looks_like_our_wrapper_negative(self, tmp_path: Path) -> None:
        f = tmp_path / "x.cmd"
        f.write_text("@echo off\r\nrem foreign\r\n", encoding="utf-8")
        assert _looks_like_our_wrapper(f) is False

    def test_looks_like_our_wrapper_missing(self, tmp_path: Path) -> None:
        assert _looks_like_our_wrapper(tmp_path / "missing.cmd") is False

    def test_path_guidance_returns_lines(self, tmp_path: Path) -> None:
        lines = _path_guidance(tmp_path / "bin")
        assert isinstance(lines, list)
        assert all(isinstance(s, str) for s in lines)
        assert all(str(tmp_path / "bin") in s for s in lines)

    def test_bin_dir_on_path_when_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bin_dir = tmp_path / "pce-bin"
        bin_dir.mkdir()
        # PATH = our bin first.
        monkeypatch.setenv("PATH", str(bin_dir))
        assert _bin_dir_on_path(bin_dir) is True

    def test_bin_dir_on_path_when_overshadowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Some earlier dir on PATH already contains a 'claude' shim:
        # we must report False.
        first = tmp_path / "before"
        first.mkdir()
        if sys.platform == "win32":
            (first / "claude.cmd").write_text("@echo off", encoding="utf-8")
        else:
            (first / "claude").write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8"
            )
        bin_dir = tmp_path / "after"
        bin_dir.mkdir()
        sep = ";" if sys.platform == "win32" else ":"
        monkeypatch.setenv("PATH", f"{first}{sep}{bin_dir}")
        assert _bin_dir_on_path(bin_dir) is False
