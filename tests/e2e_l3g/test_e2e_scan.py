# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI tests for ``python -m pce_persistence_watcher``.

These exercise the real ``main()`` entry-point with a synthetic install
plumbed in via monkey-patched ``discover``. The DB is a fresh temp DB
with all migrations applied, so the path from CLI args → discovery →
parsers → observer → ``insert_capture`` is validated end-to-end.

The 3-pass user-machine verification (real Claude Desktop install)
documented in ADR-018 §3.4 covers the *real* ``discover()`` codepath;
these tests cover the CLI plumbing + scan loop in a hermetic way.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

import pce_persistence_watcher.__main__ as main_mod
from pce_persistence_watcher.discovery import AppInstall


L3G_SOURCE_ID = "l3g-local-persistence-default"


def _count_l3g_rows(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute(
            "SELECT count(*) FROM raw_captures WHERE source_id = ?",
            (L3G_SOURCE_ID,),
        ).fetchone()[0])
    finally:
        conn.close()


def _patch_discover_with(monkeypatch: pytest.MonkeyPatch, installs: list[AppInstall]) -> None:
    """Replace the ``discover`` symbol that ``__main__`` imported."""
    monkeypatch.setattr(main_mod, "discover", lambda **_kw: list(installs))


# ---------------------------------------------------------------------------
# discover mode
# ---------------------------------------------------------------------------


class TestDiscoverMode:
    def test_no_installs_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        _patch_discover_with(monkeypatch, [])
        rc = main_mod.main(["discover"])
        assert rc == 0  # discover mode is always 0 even with no installs
        captured = capsys.readouterr()
        assert "no target applications found" in captured.out

    def test_with_install_prints_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, _ = fake_app_install()
        _patch_discover_with(monkeypatch, [install])

        rc = main_mod.main(["discover"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "claude-desktop" in captured.out
        assert "msix" in captured.out
        assert "agent_sessions" in captured.out

    def test_json_output_is_valid_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, _ = fake_app_install()
        _patch_discover_with(monkeypatch, [install])

        rc = main_mod.main(["discover", "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["count"] == 1
        assert payload["installs"][0]["app_id"] == "claude-desktop"
        assert "leveldb_backend" in payload


# ---------------------------------------------------------------------------
# scan mode
# ---------------------------------------------------------------------------


class TestScanMode:
    def test_no_installs_exits_3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Per the CLI contract: scan with nothing discovered ⇒ exit 3.
        _patch_discover_with(monkeypatch, [])
        rc = main_mod.main(["scan"])
        assert rc == 3

    def test_scan_writes_l3g_rows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, layout = fake_app_install(num_sessions=1, include_skills=True)
        _patch_discover_with(monkeypatch, [install])

        state_path = tmp_path / "state.json"
        rc = main_mod.main([
            "scan",
            "--db-path", str(tmp_pce_db),
            "--state-path", str(state_path),
        ])
        assert rc == 0
        # 1 session manifest + 1 skills_catalogue manifest.
        assert _count_l3g_rows(tmp_pce_db) == 2
        assert state_path.exists()

    def test_scan_idempotent_second_pass_emits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, _ = fake_app_install(num_sessions=2, include_skills=True)
        _patch_discover_with(monkeypatch, [install])

        state_path = tmp_path / "state.json"
        common_args = [
            "scan",
            "--db-path", str(tmp_pce_db),
            "--state-path", str(state_path),
        ]

        rc1 = main_mod.main(common_args)
        assert rc1 == 0
        first_count = _count_l3g_rows(tmp_pce_db)
        assert first_count == 3  # 2 sessions + 1 skills

        rc2 = main_mod.main(common_args)
        assert rc2 == 0
        # Idempotent: row count unchanged after a second pass.
        assert _count_l3g_rows(tmp_pce_db) == first_count

    def test_dry_run_does_not_persist_anything(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, _ = fake_app_install(num_sessions=1, include_skills=True)
        _patch_discover_with(monkeypatch, [install])

        state_path = tmp_path / "state.json"
        rc = main_mod.main([
            "scan",
            "--dry-run",
            "--db-path", str(tmp_pce_db),
            "--state-path", str(state_path),
        ])
        assert rc == 0
        assert _count_l3g_rows(tmp_pce_db) == 0
        assert not state_path.exists()

    def test_dry_run_then_real_emits_full_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        """End-to-end regression for the dry-run dedup-pollution bug.

        Reproduces the exact 3-pass sequence from the manual verification
        but goes through the CLI surface, not just the observer API.
        """
        install, _ = fake_app_install(num_sessions=1, include_skills=True)
        _patch_discover_with(monkeypatch, [install])

        state_path = tmp_path / "state.json"
        base = ["--db-path", str(tmp_pce_db), "--state-path", str(state_path)]

        # Dry run first — must NOT prime the state.
        rc1 = main_mod.main(["scan", "--dry-run", *base])
        assert rc1 == 0
        assert _count_l3g_rows(tmp_pce_db) == 0

        # Real run — must emit the full set.
        rc2 = main_mod.main(["scan", *base])
        assert rc2 == 0
        assert _count_l3g_rows(tmp_pce_db) == 2

        # Real run again — must dedup.
        rc3 = main_mod.main(["scan", *base])
        assert rc3 == 0
        assert _count_l3g_rows(tmp_pce_db) == 2

    def test_scan_only_filter_restricts_to_one_category(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, _ = fake_app_install(num_sessions=1, include_skills=True)
        _patch_discover_with(monkeypatch, [install])

        state_path = tmp_path / "state.json"
        rc = main_mod.main([
            "scan",
            "--only", "agent_sessions",
            "--db-path", str(tmp_pce_db),
            "--state-path", str(state_path),
        ])
        assert rc == 0
        # Only the session row was emitted; skills_catalogue was filtered.
        rows = sqlite3.connect(str(tmp_pce_db)).execute(
            "SELECT meta_json FROM raw_captures WHERE source_id = ?",
            (L3G_SOURCE_ID,),
        ).fetchall()
        kinds = {json.loads(r[0])["source_kind"] for r in rows}
        assert kinds == {"session"}


# ---------------------------------------------------------------------------
# JSON summary output
# ---------------------------------------------------------------------------


class TestJsonSummary:
    def test_scan_emits_json_summary_when_requested(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_install: Callable[..., tuple[AppInstall, dict]],
    ) -> None:
        install, _ = fake_app_install(num_sessions=1, include_skills=False)
        _patch_discover_with(monkeypatch, [install])

        rc = main_mod.main([
            "scan", "--json",
            "--db-path", str(tmp_pce_db),
            "--state-path", str(tmp_path / "state.json"),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "installs" in payload
        per_app = payload["installs"]["claude-desktop"]
        assert per_app["records_seen"] == 1
        assert per_app["records_emitted"] == 1
        assert per_app["records_deduped"] == 0
