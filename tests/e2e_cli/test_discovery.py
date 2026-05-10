# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_cli_wrapper.discovery``.

Hermetic — does not assume any specific CLI agent is installed on the
host. Tests focus on the public-API contract: filter behaviour,
shape of ``ShimTarget``, ``summarise`` output, and the variant
expansion rules used by the install path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pce_cli_wrapper.discovery import (
    ShimTarget,
    _expand_variants,
    discover,
    known_targets,
    summarise,
)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


class TestCatalogue:
    def test_catalogue_is_non_empty(self) -> None:
        targets = known_targets()
        assert isinstance(targets, list)
        assert len(targets) >= 1

    def test_catalogue_has_claude_code_v0(self) -> None:
        ids = {t.target_id for t in known_targets()}
        assert "claude-code" in ids

    def test_catalogue_specs_are_well_formed(self) -> None:
        for spec in known_targets():
            assert spec.target_id and isinstance(spec.target_id, str)
            assert spec.command_name and isinstance(spec.command_name, str)
            assert spec.provider and isinstance(spec.provider, str)


# ---------------------------------------------------------------------------
# discover() filter behaviour
# ---------------------------------------------------------------------------


class TestDiscoverFilter:
    def test_unknown_filter_returns_empty(self) -> None:
        assert discover(target_filter=["does-not-exist"]) == []

    def test_filter_is_case_insensitive(self) -> None:
        result = discover(target_filter=["Claude-Code"])
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].target_id == "claude-code"

    def test_empty_filter_scans_everything(self) -> None:
        a = discover(target_filter=[])
        b = discover(target_filter=None)
        assert isinstance(a, list)
        assert isinstance(b, list)
        assert len(a) == len(b) == len(known_targets())


# ---------------------------------------------------------------------------
# ShimTarget shape — synthesised, not host-dependent
# ---------------------------------------------------------------------------


class TestShimTargetShape:
    def test_minimal_construction(self) -> None:
        t = ShimTarget(
            target_id="claude-code",
            command_name="claude",
            provider="anthropic",
            description="test",
            shim_path=None,
        )
        assert t.shim_variants == []
        assert t.version is None
        assert t.npm_prefix is None

    def test_full_construction(self, tmp_path: Path) -> None:
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off\r\n", encoding="utf-8")
        t = ShimTarget(
            target_id="claude-code",
            command_name="claude",
            provider="anthropic",
            description="test",
            shim_path=shim,
            shim_variants=[shim],
            version="2.1.59",
            npm_prefix=tmp_path,
        )
        assert t.shim_path == shim
        assert t.shim_variants == [shim]
        assert t.version == "2.1.59"
        assert t.npm_prefix == tmp_path


# ---------------------------------------------------------------------------
# _expand_variants
# ---------------------------------------------------------------------------


class TestExpandVariants:
    def test_returns_three_paths_in_same_dir(self, tmp_path: Path) -> None:
        shim = tmp_path / "claude.cmd"
        variants = _expand_variants(shim, "claude")
        names = [v.name for v in variants]
        assert names == ["claude", "claude.cmd", "claude.ps1"]
        assert all(v.parent == tmp_path for v in variants)

    def test_works_for_bare_shim(self, tmp_path: Path) -> None:
        shim = tmp_path / "claude"
        variants = _expand_variants(shim, "claude")
        assert {v.name for v in variants} == {"claude", "claude.cmd", "claude.ps1"}


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------


class TestSummarise:
    def test_empty_summary(self) -> None:
        out = summarise([])
        assert out["count"] == 0
        assert out["targets"] == []
        assert out["platform"] == sys.platform

    def test_summary_with_uninstalled_target(self) -> None:
        out = summarise([
            ShimTarget(
                target_id="claude-code",
                command_name="claude",
                provider="anthropic",
                description="test",
                shim_path=None,
            ),
        ])
        assert out["count"] == 0
        assert len(out["targets"]) == 1
        row = out["targets"][0]
        assert row["installed"] is False
        assert row["shim_path"] is None
        assert row["version"] is None

    def test_summary_with_installed_target(self, tmp_path: Path) -> None:
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off\r\n", encoding="utf-8")
        out = summarise([
            ShimTarget(
                target_id="claude-code",
                command_name="claude",
                provider="anthropic",
                description="test",
                shim_path=shim,
                shim_variants=[shim],
                version="2.1.59",
                npm_prefix=tmp_path,
            ),
        ])
        assert out["count"] == 1
        row = out["targets"][0]
        assert row["installed"] is True
        assert row["shim_path"] == str(shim)
        assert row["version"] == "2.1.59"
        assert row["npm_prefix"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Idempotency — back-to-back calls return equal data
# ---------------------------------------------------------------------------


class TestIdempotent:
    def test_two_calls_in_a_row_are_equal(self) -> None:
        a = summarise(discover())
        b = summarise(discover())
        assert a == b
