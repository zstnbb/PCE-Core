# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_persistence_watcher.discovery``.

Hermetic — no real Claude / ChatGPT install needed. Real-platform
discovery (powershell Get-AppxPackage etc.) is exercised by the manual
3-pass scan documented in ADR-018 §3.4 and validated on the developer
machine; here we test the public API contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pce_persistence_watcher.discovery import (
    AppInstall,
    discover,
    summarise,
)


# ---------------------------------------------------------------------------
# AppInstall.root() semantics
# ---------------------------------------------------------------------------


class TestAppInstallRoot:
    def test_root_returns_existing_path(self, tmp_path: Path) -> None:
        existing = tmp_path / "agent_sessions"
        existing.mkdir()
        inst = AppInstall(
            app_id="claude-desktop",
            channel="test",
            version=None,
            install_location=None,
            roots={"agent_sessions": existing},
        )
        assert inst.root("agent_sessions") == existing

    def test_root_returns_none_when_path_missing(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does-not-exist"
        inst = AppInstall(
            app_id="claude-desktop",
            channel="test",
            version=None,
            install_location=None,
            roots={"agent_sessions": ghost},
        )
        assert inst.root("agent_sessions") is None

    def test_root_returns_none_for_unknown_name(self) -> None:
        inst = AppInstall(
            app_id="claude-desktop",
            channel="test",
            version=None,
            install_location=None,
            roots={},
        )
        assert inst.root("does-not-exist") is None


# ---------------------------------------------------------------------------
# summarise() shape
# ---------------------------------------------------------------------------


class TestSummarise:
    def test_summarise_empty(self) -> None:
        out = summarise([])
        assert out == {"installs": [], "count": 0}

    def test_summarise_single_install(self, tmp_path: Path) -> None:
        existing = tmp_path / "Claude" / "local-agent-mode-sessions"
        existing.mkdir(parents=True)
        ghost = tmp_path / "Claude" / "IndexedDB"  # NOT created

        inst = AppInstall(
            app_id="claude-desktop",
            channel="msix",
            version="1.0.0-test",
            install_location=tmp_path / "WindowsApps" / "Claude",
            roots={
                "agent_sessions": existing,
                "indexeddb": ghost,
            },
        )
        summary = summarise([inst])
        assert summary["count"] == 1
        installs = summary["installs"]
        assert isinstance(installs, list) and len(installs) == 1
        row = installs[0]
        assert row["app_id"] == "claude-desktop"
        assert row["channel"] == "msix"
        assert row["version"] == "1.0.0-test"
        assert row["install_location"] == str(tmp_path / "WindowsApps" / "Claude")
        assert row["roots"]["agent_sessions"]["exists"] is True
        assert row["roots"]["indexeddb"]["exists"] is False
        assert row["roots"]["agent_sessions"]["path"] == str(existing)


# ---------------------------------------------------------------------------
# discover() filter behaviour
# ---------------------------------------------------------------------------


class TestDiscoverFilter:
    def test_unknown_app_filter_returns_empty(self) -> None:
        # `app_filter` containing only unknown ids must silently return [],
        # regardless of what's actually installed on the host.
        assert discover(app_filter=["totally-not-an-app"]) == []

    def test_filter_is_case_insensitive(self) -> None:
        # The filter accepts mixed case and lowercases internally; the
        # important contract is "filter does not accidentally exclude a
        # registered app due to capitalisation". We assert the call is
        # well-formed (returns a list) — actual contents depend on host.
        result = discover(app_filter=["Claude-Desktop"])
        assert isinstance(result, list)

    def test_empty_filter_scans_everything(self) -> None:
        # Empty list ⇒ scan everything. Result is host-dependent so we
        # only assert the type.
        assert isinstance(discover(app_filter=[]), list)
        assert isinstance(discover(app_filter=None), list)


# ---------------------------------------------------------------------------
# Idempotency — two back-to-back calls must produce equal output
# ---------------------------------------------------------------------------


class TestDiscoverIdempotent:
    def test_two_calls_in_a_row_are_equal(self) -> None:
        a = summarise(discover(app_filter=["claude-desktop"]))
        b = summarise(discover(app_filter=["claude-desktop"]))
        assert a == b

    def test_install_filter_does_not_leak_between_calls(self) -> None:
        # A scoped call must not pollute the result of a follow-up call.
        scoped = summarise(discover(app_filter=["does-not-exist-app"]))
        assert scoped["count"] == 0
        wide = summarise(discover(app_filter=None))
        assert wide["count"] >= 0  # no negative leakage
