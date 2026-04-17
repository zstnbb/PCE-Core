"""Smoke: schema migration framework.

Verifies:
1. Fresh DB migrates to EXPECTED_SCHEMA_VERSION.
2. Legacy DB (tables exist but no schema_meta) gets stamped and fully
   migrated without data loss.
3. Already-up-to-date DB is a no-op on re-apply.
4. A DB at a version newer than the code refuses to open (downgrade
   protection).
5. Migration history is recorded for each applied step.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pce_core import db as pce_db
from pce_core.migrations import (
    EXPECTED_SCHEMA_VERSION,
    apply_migrations,
    get_current_version,
    get_migration_history,
    discover_migrations,
)


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def test_discovered_migrations_are_ordered_and_unique():
    versions = [m.version for m in discover_migrations()]
    assert versions == sorted(versions), "Migrations must be in ascending order"
    assert len(versions) == len(set(versions)), "No duplicate versions"
    assert versions[0] == 1, "Baseline must be version 1"
    assert versions[-1] == EXPECTED_SCHEMA_VERSION


def test_fresh_db_reaches_expected_version(tmp_path: Path):
    db = tmp_path / "fresh.db"
    pce_db.init_db(db)

    conn = _open(db)
    try:
        assert get_current_version(conn) == EXPECTED_SCHEMA_VERSION

        # All expected tables exist
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "sources", "raw_captures", "sessions", "messages",
            "messages_fts", "snippets", "custom_domains",
            "pipeline_errors", "schema_meta", "schema_migrations",
        }
        missing = required - tables
        assert not missing, f"Fresh DB missing tables: {missing}"

        # Every migration must have a history row
        history = get_migration_history(conn)
        assert {h["version"] for h in history} == {
            m.version for m in discover_migrations()
        }
    finally:
        conn.close()


def test_legacy_db_without_schema_meta_is_adopted(tmp_path: Path):
    """Simulate an old install: tables exist but no schema_meta row yet."""
    db = tmp_path / "legacy.db"
    conn = _open(db)
    try:
        # Partial pre-P0 schema (sources + raw_captures + messages only)
        conn.executescript(
            """
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                source_type TEXT,
                tool_name TEXT,
                install_mode TEXT DEFAULT 'complete',
                active INTEGER DEFAULT 1,
                notes TEXT
            );
            CREATE TABLE raw_captures (
                id TEXT PRIMARY KEY,
                created_at REAL,
                source_id TEXT,
                direction TEXT CHECK (direction IN ('request','response','conversation')),
                pair_id TEXT,
                host TEXT, path TEXT, method TEXT, provider TEXT,
                model_name TEXT, status_code INTEGER, latency_ms REAL,
                headers_redacted_json TEXT, body_text_or_json TEXT,
                body_format TEXT, error TEXT, session_hint TEXT
            );
            INSERT INTO raw_captures (id, created_at, source_id, direction, pair_id, host, provider)
            VALUES ('legacy-cap', 1700000000.0, 'legacy-src', 'request', 'pair-legacy', 'api.openai.com', 'openai');
            INSERT INTO sources (id, source_type) VALUES ('legacy-src', 'proxy');
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Apply migrations
    pce_db.init_db(db)

    conn = _open(db)
    try:
        assert get_current_version(conn) == EXPECTED_SCHEMA_VERSION

        # Legacy row survives
        row = conn.execute("SELECT id, provider FROM raw_captures WHERE id='legacy-cap'").fetchone()
        assert row == ("legacy-cap", "openai"), f"Legacy capture lost: {row}"

        # New columns added by baseline
        cols = {r[1] for r in conn.execute("PRAGMA table_info(raw_captures)").fetchall()}
        assert {"schema_version", "meta_json"} <= cols

        # CHECK constraint now accepts network_intercept + clipboard
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_captures'"
        ).fetchone()[0]
        assert "'network_intercept'" in table_sql
        assert "'clipboard'" in table_sql
    finally:
        conn.close()


def test_reapply_is_idempotent(tmp_path: Path):
    db = tmp_path / "idemp.db"
    pce_db.init_db(db)

    conn = _open(db)
    try:
        first = get_current_version(conn)
        first_history = get_migration_history(conn)
        # Re-apply: should be a no-op
        second = apply_migrations(conn)
        conn.commit()
        second_history = get_migration_history(conn)
        assert first == second == EXPECTED_SCHEMA_VERSION
        assert first_history == second_history
    finally:
        conn.close()


def test_future_schema_refuses_to_open(tmp_path: Path):
    """If the DB is at a newer version than the code, init_db must fail."""
    db = tmp_path / "future.db"
    pce_db.init_db(db)

    conn = _open(db)
    try:
        conn.execute(
            "UPDATE schema_meta SET version = ?",
            (EXPECTED_SCHEMA_VERSION + 99,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer than this build"):
        pce_db.init_db(db)


def test_pipeline_errors_table_is_writable(tmp_path: Path):
    """Migration 0002 must have created the pipeline_errors table."""
    db = tmp_path / "errors.db"
    pce_db.init_db(db)

    pce_db.record_pipeline_error(
        "normalize", "synthetic-error",
        source_id="proxy-default", pair_id="pair-xyz",
        details={"reason": "test"},
        db_path=db,
    )
    counts = pce_db.get_pipeline_error_counts(db_path=db)
    assert counts.get("normalize", {}).get("ERROR", 0) == 1
