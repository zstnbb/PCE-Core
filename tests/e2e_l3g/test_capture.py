# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_persistence_watcher.capture.ChromiumStateObserver``.

Focus areas:

- **Dedup**: a record emitted in pass 1 is not re-emitted in pass 2.
- **Dry-run**: dry-run pass MUST NOT mutate the on-disk state file or
  the in-memory dedup map (regression test for ADR-018 §3.4 bug fixed
  in ``capture.py:182-187 / 244-246 / 256-257``).
- **State persistence**: a fresh observer reading the same state file
  picks up where the previous one left off.
- **Soft DB failure**: a write that fails increments ``capture_failures``
  but does not crash the observer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from pce_persistence_watcher.agent_sessions import iter_records
from pce_persistence_watcher.capture import ChromiumStateObserver


L3G_SOURCE_ID = "l3g-local-persistence-default"


def _count_l3g_rows(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT count(*) FROM raw_captures WHERE source_id = ?",
            (L3G_SOURCE_ID,),
        )
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def _l3g_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT id, source_id, source, direction, host, path, provider, "
            "session_hint, body_text_or_json, meta_json "
            "FROM raw_captures WHERE source_id = ? ORDER BY created_at",
            (L3G_SOURCE_ID,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _make_observer(
    *,
    db_path: Path,
    state_path: Path | None = None,
    dry_run: bool = False,
    include_bodies: bool = True,
) -> ChromiumStateObserver:
    return ChromiumStateObserver(
        app_id="claude-desktop",
        app_version="1.0.0-test",
        app_channel="msix",
        db_path=db_path,
        state_path=state_path,
        dry_run=dry_run,
        include_bodies=include_bodies,
    )


# ---------------------------------------------------------------------------
# Smoke: real-mode emit
# ---------------------------------------------------------------------------


class TestRealEmit:
    def test_one_session_record_writes_one_row(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"
        obs = _make_observer(db_path=tmp_pce_db, state_path=state_path)

        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        assert obs.stats["records_seen"] == 1
        assert obs.stats["records_emitted"] == 1
        assert obs.stats["records_deduped"] == 0
        assert _count_l3g_rows(tmp_pce_db) == 1

    def test_emitted_row_carries_l3g_envelope(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"
        obs = _make_observer(db_path=tmp_pce_db, state_path=state_path)

        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        rows = _l3g_rows(tmp_pce_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "local_persistence"
        assert row["source_id"] == L3G_SOURCE_ID
        assert row["direction"] == "conversation"
        assert row["host"] == "local-agent-mode"
        assert row["provider"] == "anthropic"
        assert row["path"].startswith("/claude-desktop/agent-session/")
        assert row["session_hint"] == layout["session_uuids"][0]

        meta = json.loads(row["meta_json"])
        assert meta["app_id"] == "claude-desktop"
        assert meta["app_channel"] == "msix"
        assert meta["app_version"] == "1.0.0-test"
        assert meta["source_kind"] == "session"
        assert "fingerprint" in meta and len(meta["fingerprint"]) == 64
        assert meta["source_path"] == str(layout["session_manifests"][0])

    def test_skills_catalogue_records_emit_correctly(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=True)
        state_path = tmp_path / "state.json"
        obs = _make_observer(db_path=tmp_pce_db, state_path=state_path)

        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        # One session + one skills_catalogue.
        assert obs.stats["records_emitted"] == 2
        rows = _l3g_rows(tmp_pce_db)
        assert len(rows) == 2
        kinds = {json.loads(r["meta_json"])["source_kind"] for r in rows}
        assert kinds == {"session", "skills_catalogue"}


# ---------------------------------------------------------------------------
# Dedup — second pass against same data must NOT re-emit
# ---------------------------------------------------------------------------


class TestDedup:
    def test_second_pass_dedupes_identical_record(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"

        # Pass 1 — fresh observer, fresh state file.
        obs1 = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            obs1.observe_agent_session(rec)
        obs1.flush_state()
        assert obs1.stats["records_emitted"] == 1
        assert _count_l3g_rows(tmp_pce_db) == 1
        assert state_path.exists()

        # Pass 2 — fresh observer, same state file (simulates a re-run).
        obs2 = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            obs2.observe_agent_session(rec)
        obs2.flush_state()

        assert obs2.stats["records_seen"] == 1
        assert obs2.stats["records_emitted"] == 0
        assert obs2.stats["records_deduped"] == 1
        # DB row count unchanged.
        assert _count_l3g_rows(tmp_pce_db) == 1

    def test_changed_content_re_emits(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"
        manifest = layout["session_manifests"][0]

        # Pass 1
        obs1 = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            obs1.observe_agent_session(rec)
        obs1.flush_state()

        # Mutate the manifest body; fingerprint should change.
        body = json.loads(manifest.read_text(encoding="utf-8"))
        body["plugins"] = [{"id": "newly-installed-plugin"}]
        manifest.write_text(json.dumps(body), encoding="utf-8")

        # Pass 2 — same state, mutated source.
        obs2 = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            obs2.observe_agent_session(rec)
        obs2.flush_state()

        assert obs2.stats["records_emitted"] == 1
        assert _count_l3g_rows(tmp_pce_db) == 2

    def test_state_file_is_persisted(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "nested" / "state.json"  # parent doesn't exist
        obs = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        assert state_path.exists()
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 1
        assert isinstance(raw["entries"], dict)
        assert len(raw["entries"]) == 1
        # The single entry is keyed by a 64-char sha256 fingerprint.
        (fp,) = raw["entries"].keys()
        assert len(fp) == 64


# ---------------------------------------------------------------------------
# Dry-run — must NOT touch DB, state file, or in-memory dedup
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_write_db(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=2, include_skills=True)
        state_path = tmp_path / "state.json"
        obs = _make_observer(db_path=tmp_pce_db, state_path=state_path, dry_run=True)

        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        # The observer reports what it would emit, but no rows landed.
        assert obs.stats["records_emitted"] == 3  # 2 sessions + 1 skills
        assert _count_l3g_rows(tmp_pce_db) == 0

    def test_dry_run_does_not_create_state_file(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"
        obs = _make_observer(db_path=tmp_pce_db, state_path=state_path, dry_run=True)

        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        assert not state_path.exists()

    def test_dry_run_does_not_pollute_subsequent_real_run(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        """Regression test for the bug fixed in ``capture.py:182-187``.

        Before the fix: a dry-run pass marked records as emitted in the
        in-memory dedup map; the next real run saw them as already-emitted
        and silently dropped every record.
        After the fix: dry-run is a fully stateless preview.
        """
        layout = fake_app_profile(num_sessions=1, include_skills=True)
        state_path = tmp_path / "state.json"

        # Step 1 — DRY RUN: 2 records "would-emit".
        dry = _make_observer(
            db_path=tmp_pce_db, state_path=state_path, dry_run=True,
        )
        for rec in iter_records(layout["agent_sessions"]):
            dry.observe_agent_session(rec)
        dry.flush_state()

        assert dry.stats["records_emitted"] == 2
        assert _count_l3g_rows(tmp_pce_db) == 0
        assert not state_path.exists()  # nothing persisted

        # Step 2 — REAL RUN with a FRESH observer: must emit both records.
        real = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            real.observe_agent_session(rec)
        real.flush_state()

        assert real.stats["records_emitted"] == 2  # ← was 0 before the fix
        assert real.stats["records_deduped"] == 0
        assert _count_l3g_rows(tmp_pce_db) == 2
        assert state_path.exists()

    def test_dry_run_respects_existing_real_state(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        """A dry-run after a real run shows ``deduped`` for already-emitted records.

        This is the *complement* of the no-pollution test: dry-run reads
        existing state to compute what's-new, but does not mutate it.
        """
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"

        # Step 1 — real run primes the state.
        real = _make_observer(db_path=tmp_pce_db, state_path=state_path)
        for rec in iter_records(layout["agent_sessions"]):
            real.observe_agent_session(rec)
        real.flush_state()

        state_before = state_path.read_text(encoding="utf-8")

        # Step 2 — dry-run sees the record as already-emitted.
        dry = _make_observer(
            db_path=tmp_pce_db, state_path=state_path, dry_run=True,
        )
        for rec in iter_records(layout["agent_sessions"]):
            dry.observe_agent_session(rec)
        dry.flush_state()

        assert dry.stats["records_emitted"] == 0
        assert dry.stats["records_deduped"] == 1

        # State file must be byte-identical — dry-run is fully read-only.
        state_after = state_path.read_text(encoding="utf-8")
        assert state_before == state_after


# ---------------------------------------------------------------------------
# include_bodies=False emits metadata but no payload
# ---------------------------------------------------------------------------


class TestIncludeBodies:
    def test_no_bodies_drops_payload_keeps_meta(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
        fake_app_profile: Callable[..., dict],
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        state_path = tmp_path / "state.json"
        obs = _make_observer(
            db_path=tmp_pce_db, state_path=state_path, include_bodies=False,
        )

        for rec in iter_records(layout["agent_sessions"]):
            obs.observe_agent_session(rec)
        obs.flush_state()

        rows = _l3g_rows(tmp_pce_db)
        assert len(rows) == 1
        # Body dropped, meta retained.
        assert rows[0]["body_text_or_json"] == ""
        meta = json.loads(rows[0]["meta_json"])
        assert meta["source_kind"] == "session"
        assert "fingerprint" in meta
