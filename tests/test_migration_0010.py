# SPDX-License-Identifier: Apache-2.0
"""Migration 0010 — adds messages.interaction_kind + registers
desktop-electron-default source row (P5.B.2 Phase 3, ADR-016 §3.2/§3.6).

Coverage:
- column added on legacy DBs (pre-0010 schema)
- column already present → no-op
- desktop-electron-default source row inserted
- repeat upgrade is idempotent
- downgrade leaves source row when captures reference it
- downgrade removes source row when no captures reference it
- EXPECTED_SCHEMA_VERSION bumped to 10
- fresh init_db produces interaction_kind column without manual upgrade
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def fresh_conn(tmp_path: Path) -> sqlite3.Connection:
    """Bootstrap a DB at the pre-0010 baseline (apply 0001..0009 only)."""
    from pce_core.migrations import discover_migrations, _ensure_meta_table

    db = tmp_path / "pce_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON;")

    _ensure_meta_table(conn)
    migs = list(discover_migrations())
    pre_0010 = [m for m in migs if m.version < 10]
    for mig in pre_0010:
        mig.upgrade(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (id, version, updated_at) "
            "VALUES (1, ?, ?)",
            (mig.version, time.time()),
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Column add
# ---------------------------------------------------------------------------


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def test_legacy_db_lacks_interaction_kind_before_upgrade(fresh_conn: sqlite3.Connection):
    """Pre-condition for migration 0010: column does not yet exist."""
    assert not _column_exists(fresh_conn, "messages", "interaction_kind")


def test_upgrade_adds_column(fresh_conn: sqlite3.Connection):
    from pce_core.migrations import discover_migrations
    migs = {m.version: m for m in discover_migrations()}
    migs[10].upgrade(fresh_conn)
    assert _column_exists(fresh_conn, "messages", "interaction_kind")


def test_upgrade_is_idempotent(fresh_conn: sqlite3.Connection):
    from pce_core.migrations import discover_migrations
    migs = {m.version: m for m in discover_migrations()}
    migs[10].upgrade(fresh_conn)
    # Run a second time — should not raise.
    migs[10].upgrade(fresh_conn)
    assert _column_exists(fresh_conn, "messages", "interaction_kind")


def test_desktop_electron_source_registered(fresh_conn: sqlite3.Connection):
    from pce_core.migrations import discover_migrations
    migs = {m.version: m for m in discover_migrations()}
    migs[10].upgrade(fresh_conn)
    rows = fresh_conn.execute(
        "SELECT id, source_type, tool_name FROM sources "
        "WHERE id = 'desktop-electron-default'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "desktop_electron"
    assert rows[0][2] == "pce-app-launcher"


def test_messages_can_have_interaction_kind_after_upgrade(fresh_conn: sqlite3.Connection):
    from pce_core.migrations import discover_migrations
    migs = {m.version: m for m in discover_migrations()}
    migs[10].upgrade(fresh_conn)
    fresh_conn.execute(
        "INSERT INTO sessions (id, source_id, started_at, provider, session_key) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess1", "desktop-electron-default", time.time(), "anthropic", "key1"),
    )
    fresh_conn.execute(
        "INSERT INTO messages (id, session_id, ts, role, content_text, interaction_kind) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("msg1", "sess1", time.time(), "assistant", "hello", "tool_call"),
    )
    fresh_conn.commit()
    row = fresh_conn.execute(
        "SELECT interaction_kind FROM messages WHERE id = 'msg1'"
    ).fetchone()
    assert row[0] == "tool_call"


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_removes_source_row_when_unused(fresh_conn: sqlite3.Connection):
    from pce_core.migrations import discover_migrations
    migs = {m.version: m for m in discover_migrations()}
    migs[10].upgrade(fresh_conn)
    assert fresh_conn.execute(
        "SELECT 1 FROM sources WHERE id = 'desktop-electron-default'"
    ).fetchone() is not None

    migs[10].downgrade(fresh_conn)
    assert fresh_conn.execute(
        "SELECT 1 FROM sources WHERE id = 'desktop-electron-default'"
    ).fetchone() is None


def test_downgrade_keeps_source_row_when_in_use(fresh_conn: sqlite3.Connection):
    from pce_core.migrations import discover_migrations
    migs = {m.version: m for m in discover_migrations()}
    migs[10].upgrade(fresh_conn)
    # Insert a fake capture row referencing the source
    fresh_conn.execute(
        "INSERT INTO raw_captures (id, created_at, source_id, direction, pair_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("cap1", time.time(), "desktop-electron-default", "request", "pair1"),
    )
    fresh_conn.commit()

    migs[10].downgrade(fresh_conn)
    # Source row must still be present because captures reference it
    assert fresh_conn.execute(
        "SELECT 1 FROM sources WHERE id = 'desktop-electron-default'"
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Schema-version & init_db integration
# ---------------------------------------------------------------------------


def test_expected_schema_version_is_10():
    from pce_core.migrations import EXPECTED_SCHEMA_VERSION
    assert EXPECTED_SCHEMA_VERSION == 10


def test_fresh_init_db_yields_v10_schema(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    # Force importlib to pick up the env override — easiest via reload.
    import importlib
    import pce_core.config as cfg
    importlib.reload(cfg)
    import pce_core.db as dbmod
    importlib.reload(dbmod)
    from pce_core.migrations import EXPECTED_SCHEMA_VERSION, get_current_version

    dbmod.init_db()
    conn = dbmod.get_connection()
    try:
        # Schema version
        assert get_current_version(conn) == EXPECTED_SCHEMA_VERSION
        # Column present
        cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
        assert "interaction_kind" in cols
        # Source row present
        assert conn.execute(
            "SELECT 1 FROM sources WHERE id = 'desktop-electron-default'"
        ).fetchone() is not None
    finally:
        conn.close()
