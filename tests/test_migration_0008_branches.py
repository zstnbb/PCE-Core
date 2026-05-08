# SPDX-License-Identifier: Apache-2.0
"""Tests for migration 0008 — branch semantics columns on ``messages``.

Covers the contract from ADR-2026-04-26 §5.1:

- All 3 new columns are present after upgrade, in the spec order.
- The composite ``idx_messages_session_branch`` index exists.
- Upgrade is idempotent (re-running doesn't double-apply or fail).
- Pre-migration rows survive and get backfilled deterministically:
    - ``branch_id`` defaults to ``'0'``
    - ``branch_parent_id`` stays ``NULL``
    - ``turn_index`` is assigned row-by-row within each session,
      ordered by ``(ts, id)``
- ``insert_message`` honours the new kwargs and auto-computes
  ``turn_index`` as ``MAX(turn_index)+1`` within
  ``(session_id, branch_id)`` when the caller omits it.
- ``update_message_enrichment`` can restamp branch fields on an
  existing row without touching the payload.
- ``MessageRecord`` Pydantic model exposes the three new fields and
  round-trips through ``query_messages``.
- End-to-end: a normalizer-produced ``content_json.threading`` with a
  ``branch_id`` gets promoted to the SQL column when ``persist_result``
  writes the row (the link between JSON source-of-truth and SQL
  projection that G2/G3 depend on).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from pce_core import db as pce_db
from pce_core.migrations import (
    EXPECTED_SCHEMA_VERSION,
    apply_migrations,
    discover_migrations,
    get_current_version,
)
from pce_core.models import MessageRecord


EXPECTED_BRANCH_COLUMNS = ["branch_id", "branch_parent_id", "turn_index"]
EXPECTED_INDEX = "idx_messages_session_branch"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _index_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
    }


def _seed_session(db: Path, session_id: str = "sess-1") -> None:
    """Create a minimal session + source row so FK-targeting inserts succeed."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_type) VALUES (?, ?)",
            ("src-1", "proxy"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source_id, started_at, provider, session_key) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, "src-1", time.time(), "openai", f"key-{session_id}"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_expected_schema_version_covers_0008(self):
        # Lower-bound check — later migrations shouldn't "break" this test.
        assert EXPECTED_SCHEMA_VERSION >= 8

    def test_migration_0008_discovered(self):
        versions = [m.version for m in discover_migrations()]
        assert 8 in versions


class TestColumnsAndIndexes:
    def test_fresh_db_has_branch_columns(self, tmp_path: Path):
        db = tmp_path / "fresh.db"
        pce_db.init_db(db)

        conn = sqlite3.connect(db)
        try:
            cols = _column_names(conn, "messages")
            for name in EXPECTED_BRANCH_COLUMNS:
                assert name in cols, f"migration 0008 missing column: {name}"
        finally:
            conn.close()

    def test_fresh_db_has_composite_index(self, tmp_path: Path):
        db = tmp_path / "fresh.db"
        pce_db.init_db(db)

        conn = sqlite3.connect(db)
        try:
            indexes = _index_names(conn, "messages")
            assert EXPECTED_INDEX in indexes
        finally:
            conn.close()

    def test_branch_id_default_is_main_branch(self, tmp_path: Path):
        db = tmp_path / "fresh.db"
        pce_db.init_db(db)
        _seed_session(db)

        # Insert a bare row via raw SQL (no branch kwargs) to exercise
        # the column-level DEFAULT, not the insert_message fast-path.
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO messages (id, session_id, ts, role, content_text) "
                "VALUES (?, ?, ?, ?, ?)",
                ("m-1", "sess-1", time.time(), "user", "hi"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT branch_id, branch_parent_id FROM messages WHERE id = ?",
                ("m-1",),
            ).fetchone()
        finally:
            conn.close()
        assert row == ("0", None)


# ---------------------------------------------------------------------------
# Backfill correctness (legacy DB path)
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_existing_rows_get_deterministic_turn_index(self, tmp_path: Path):
        """Simulate a legacy DB: rows inserted before 0008 must be
        assigned ``turn_index`` by ``ROW_NUMBER() OVER (session, ts)``.
        """
        db = tmp_path / "legacy.db"
        # Apply migrations up to (but not including) 0008.
        pce_db.init_db(db)

        # Strip the three branch columns and the index to simulate a
        # DB that was initialised before 0008 shipped. We also roll
        # schema_meta back to 7 so apply_migrations() will re-run 0008.
        conn = sqlite3.connect(db)
        try:
            for col in EXPECTED_BRANCH_COLUMNS:
                # SQLite < 3.35 can't DROP COLUMN, but test env ships
                # 3.35+ (Python 3.10+). If the DROP fails on older SQLite
                # the test is skipped with a clear reason.
                try:
                    conn.execute(f"ALTER TABLE messages DROP COLUMN {col}")
                except sqlite3.OperationalError as exc:
                    pytest.skip(
                        f"SQLite build cannot DROP COLUMN {col}: {exc}. "
                        f"Upgrade SQLite to ≥3.35 to run this test."
                    )
            conn.execute(f"DROP INDEX IF EXISTS {EXPECTED_INDEX}")
            conn.execute("UPDATE schema_meta SET version = 7 WHERE id = 1")
            conn.execute("DELETE FROM schema_migrations WHERE version = 8")
            conn.commit()
        finally:
            conn.close()

        _seed_session(db)
        # Seed three legacy rows in a single session with distinct ts.
        conn = sqlite3.connect(db)
        try:
            now = 1_700_000_000.0
            for i, (mid, role, text) in enumerate([
                ("m-A", "user", "first"),
                ("m-B", "assistant", "second"),
                ("m-C", "user", "third"),
            ]):
                conn.execute(
                    "INSERT INTO messages (id, session_id, ts, role, content_text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mid, "sess-1", now + i, role, text),
                )
            conn.commit()
        finally:
            conn.close()

        # Re-apply migrations — this runs 0008 again, which backfills
        # turn_index via ROW_NUMBER().
        conn = sqlite3.connect(db)
        try:
            final = apply_migrations(conn)
            conn.commit()
            assert final == EXPECTED_SCHEMA_VERSION

            rows = conn.execute(
                "SELECT id, branch_id, branch_parent_id, turn_index "
                "FROM messages WHERE session_id = ? ORDER BY ts",
                ("sess-1",),
            ).fetchall()
        finally:
            conn.close()

        assert rows == [
            ("m-A", "0", None, 0),
            ("m-B", "0", None, 1),
            ("m-C", "0", None, 2),
        ]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_reapply_is_noop(self, tmp_path: Path):
        db = tmp_path / "idemp.db"
        pce_db.init_db(db)

        conn = sqlite3.connect(db)
        try:
            before = get_current_version(conn)
            # Re-run explicitly; should be a no-op and preserve version.
            after = apply_migrations(conn)
            conn.commit()
        finally:
            conn.close()
        assert before == after == EXPECTED_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# insert_message() contract with the new kwargs
# ---------------------------------------------------------------------------

class TestInsertMessage:
    def test_default_insert_goes_to_main_branch(self, tmp_path: Path):
        db = tmp_path / "ins.db"
        pce_db.init_db(db)
        _seed_session(db)

        msg_id = pce_db.insert_message(
            session_id="sess-1",
            ts=1_700_000_000.0,
            role="user",
            content_text="hello",
            db_path=db,
        )
        assert msg_id

        rows = pce_db.query_messages("sess-1", db_path=db)
        assert len(rows) == 1
        assert rows[0]["branch_id"] == "0"
        assert rows[0]["branch_parent_id"] is None
        # First row in a fresh (session, branch) starts at 0.
        assert rows[0]["turn_index"] == 0

    def test_turn_index_is_monotonic_within_branch(self, tmp_path: Path):
        """Successive inserts on the same (session, branch) get
        turn_index = 0, 1, 2, … without the caller supplying one.
        """
        db = tmp_path / "ins2.db"
        pce_db.init_db(db)
        _seed_session(db)

        now = 1_700_000_000.0
        for i, role in enumerate(["user", "assistant", "user", "assistant"]):
            pce_db.insert_message(
                session_id="sess-1",
                ts=now + i,
                role=role,
                content_text=f"turn-{i}",
                db_path=db,
            )

        rows = pce_db.query_messages("sess-1", db_path=db)
        assert [r["turn_index"] for r in rows] == [0, 1, 2, 3]
        assert {r["branch_id"] for r in rows} == {"0"}

    def test_alternate_branch_has_independent_turn_sequence(self, tmp_path: Path):
        """A second ``branch_id`` starts its own ``turn_index`` at 0 —
        ``1/N ▾`` counts distinct branches at a turn, so per-branch
        sequences must not bleed into each other.
        """
        db = tmp_path / "ins3.db"
        pce_db.init_db(db)
        _seed_session(db)

        now = 1_700_000_000.0
        # Main branch: two messages
        pce_db.insert_message(
            session_id="sess-1", ts=now, role="user",
            content_text="hello", db_path=db,
        )
        pce_db.insert_message(
            session_id="sess-1", ts=now + 1, role="assistant",
            content_text="world-v1", db_path=db,
        )
        # Alternate branch for the regenerated assistant reply
        pce_db.insert_message(
            session_id="sess-1", ts=now + 2, role="assistant",
            content_text="world-v2",
            branch_id="conv:branch:1:abc", branch_parent_id="m-user-1",
            db_path=db,
        )

        by_branch: dict[str, list[int]] = {}
        for r in pce_db.query_messages("sess-1", db_path=db):
            by_branch.setdefault(r["branch_id"], []).append(r["turn_index"])

        assert by_branch["0"] == [0, 1]
        assert by_branch["conv:branch:1:abc"] == [0]

    def test_explicit_turn_index_is_respected(self, tmp_path: Path):
        """Reconciler can pin ``turn_index`` (e.g. to share a slot with
        the default branch it forked from) — the passed value wins.
        """
        db = tmp_path / "ins4.db"
        pce_db.init_db(db)
        _seed_session(db)

        pce_db.insert_message(
            session_id="sess-1", ts=1_700_000_000.0, role="assistant",
            content_text="pinned", branch_id="alt-1", turn_index=4,
            db_path=db,
        )
        rows = pce_db.query_messages("sess-1", db_path=db)
        assert rows[0]["turn_index"] == 4
        assert rows[0]["branch_id"] == "alt-1"


# ---------------------------------------------------------------------------
# update_message_enrichment() late fork detection
# ---------------------------------------------------------------------------

class TestUpdateMessageEnrichment:
    def test_branch_fields_can_be_restamped(self, tmp_path: Path):
        """Later captures may reveal that a row written to the default
        branch actually belongs to an alternate — enrichment must be
        able to correct branch_id / branch_parent_id / turn_index
        without touching content.
        """
        db = tmp_path / "enr.db"
        pce_db.init_db(db)
        _seed_session(db)

        mid = pce_db.insert_message(
            session_id="sess-1", ts=1_700_000_000.0, role="assistant",
            content_text="payload", db_path=db,
        )
        assert mid

        # Row starts on main branch.
        rows = pce_db.query_messages("sess-1", db_path=db)
        assert rows[0]["branch_id"] == "0"

        ok = pce_db.update_message_enrichment(
            mid,
            branch_id="conv:branch:2:xyz",
            branch_parent_id="parent-msg-uuid",
            turn_index=5,
            db_path=db,
        )
        assert ok

        rows = pce_db.query_messages("sess-1", db_path=db)
        assert rows[0]["branch_id"] == "conv:branch:2:xyz"
        assert rows[0]["branch_parent_id"] == "parent-msg-uuid"
        assert rows[0]["turn_index"] == 5
        # Payload unchanged.
        assert rows[0]["content_text"] == "payload"


# ---------------------------------------------------------------------------
# MessageRecord API shape
# ---------------------------------------------------------------------------

class TestMessageRecordModel:
    def test_default_instance_exposes_branch_fields(self):
        rec = MessageRecord(
            id="m1", session_id="s1", ts=0.0, role="user",
        )
        assert rec.branch_id == "0"
        assert rec.branch_parent_id is None
        assert rec.turn_index is None

    def test_query_messages_row_round_trips_through_pydantic(self, tmp_path: Path):
        db = tmp_path / "api.db"
        pce_db.init_db(db)
        _seed_session(db)

        pce_db.insert_message(
            session_id="sess-1", ts=1_700_000_000.0, role="user",
            content_text="hi", branch_id="alt", turn_index=3,
            db_path=db,
        )
        rows = pce_db.query_messages("sess-1", db_path=db)
        # Build the Pydantic record the way the API layer does.
        rec = MessageRecord(**rows[0])
        assert rec.branch_id == "alt"
        assert rec.turn_index == 3


# ---------------------------------------------------------------------------
# JSON → SQL promotion (normalizer integration)
# ---------------------------------------------------------------------------

class TestThreadingPromotion:
    """persist_result() must promote ``content_json.threading.branch_id``
    (and ``parent_message_id``) from the JSON envelope into the native
    columns. This is the contract G2/G3 depend on — the dashboard no
    longer has to parse JSON to know which branch a row belongs to.
    """

    def test_threading_branch_id_lands_in_sql_column(self, tmp_path: Path):
        from pce_core.normalizer.base import NormalizedMessage, NormalizedResult
        from pce_core.normalizer.message_processor import persist_result
        from pce_core.rich_content import build_content_json

        db = tmp_path / "promote.db"
        pce_db.init_db(db)
        # seed just the source row; persist_result creates the session itself
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sources (id, source_type) VALUES (?, ?)",
                ("src-1", "proxy"),
            )
            conn.commit()
        finally:
            conn.close()

        threading_main = None
        threading_alt = {
            "type": "assistant_variant",
            "branch_id": "conv-42:branch:1:abc",
            "branch_group_id": "conv-42:branch:1",
            "current_branch_id": "conv-42:branch:1:abc",
            "parent_message_id": "conv-42:turn:0",
            "source_action": "regenerate",
        }

        msgs = [
            NormalizedMessage(
                role="user",
                content_text="hello",
                content_json=build_content_json(
                    attachments=None, threading=threading_main,
                ),
            ),
            NormalizedMessage(
                role="assistant",
                content_text="alt reply",
                content_json=build_content_json(
                    attachments=None, threading=threading_alt,
                ),
            ),
        ]

        result = NormalizedResult(
            provider="openai",
            tool_family="api-direct",
            model_name="gpt-4o",
            session_key="sess-key-promote",
            title_hint=None,
            messages=msgs,
        )

        session_id = persist_result(
            result,
            pair_id="pair-promote",
            source_id="src-1",
            created_via="test",
            created_at=1_700_000_000.0,
            db_path=db,
        )
        assert session_id is not None

        rows = pce_db.query_messages(session_id, db_path=db)
        by_role = {r["role"]: r for r in rows}

        # User row falls back to default branch.
        assert by_role["user"]["branch_id"] == "0"
        assert by_role["user"]["branch_parent_id"] is None

        # Assistant row's branch_id got promoted out of the JSON blob
        # into the native column. Parent pointer is preserved verbatim
        # (may be synthetic, ADR §5.1 weak-ref clause).
        assert by_role["assistant"]["branch_id"] == "conv-42:branch:1:abc"
        assert by_role["assistant"]["branch_parent_id"] == "conv-42:turn:0"

        # JSON source-of-truth is untouched — dashboard can still read
        # the richer threading contract when it needs variant chips etc.
        stored_json = json.loads(by_role["assistant"]["content_json"])
        assert (
            stored_json.get("threading", {}).get("branch_id")
            == "conv-42:branch:1:abc"
        )
