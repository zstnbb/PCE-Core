# SPDX-License-Identifier: Apache-2.0
"""Tests for the P1 retention sweeper (``pce_core.retention``).

Requirements (TASK-003 §5.4):

- ``days=0`` AND ``max_raw_rows=0`` → pure no-op.
- Rows older than ``days`` are deleted across raw_captures / messages /
  sessions / snippets / pipeline_errors.
- Favorited sessions survive even when empty.
- ``max_raw_rows`` trims oldest captures first; newer ones survive.
- FTS index (``messages_fts``) is kept in sync via the existing triggers.
- Orphan sessions (no messages) are cleaned up after deletion.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from pce_core import db as pce_db
from pce_core.db import (
    SOURCE_PROXY,
    insert_capture,
    insert_message,
    insert_session,
    new_pair_id,
    query_messages,
    query_sessions,
    record_pipeline_error,
)
from pce_core.retention import sweep_once


@pytest.fixture
def db_with_mixed_ages(tmp_path: Path) -> Path:
    db = tmp_path / "retention.db"
    pce_db.init_db(db)
    now = time.time()

    # Session A — all messages older than 10 days
    old_sid = insert_session(
        source_id=SOURCE_PROXY, started_at=now - 86400 * 10, provider="openai", db_path=db,
    )
    old_pair = new_pair_id()
    insert_capture(
        direction="request", pair_id=old_pair, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        body_text_or_json="{}", body_format="json",
        source_id=SOURCE_PROXY, db_path=db,
    )
    # Force created_at to be in the past
    _set_capture_ts(db, old_pair, now - 86400 * 10)
    insert_message(
        session_id=old_sid, ts=now - 86400 * 10, role="user",
        content_text="ancient message", capture_pair_id=old_pair, db_path=db,
    )

    # Session B — messages from today
    new_sid = insert_session(
        source_id=SOURCE_PROXY, started_at=now - 10, provider="openai", db_path=db,
    )
    new_pair = new_pair_id()
    insert_capture(
        direction="request", pair_id=new_pair, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        body_text_or_json="{}", body_format="json",
        source_id=SOURCE_PROXY, db_path=db,
    )
    insert_message(
        session_id=new_sid, ts=now - 5, role="user", content_text="today's work",
        capture_pair_id=new_pair, db_path=db,
    )

    # Pipeline error from 10 days ago
    record_pipeline_error("normalize", "old error", db_path=db)
    _set_pipeline_error_ts(db, now - 86400 * 10)
    # Pipeline error from now
    record_pipeline_error("normalize", "fresh error", db_path=db)

    return db


def _set_capture_ts(db: Path, pair_id: str, ts: float) -> None:
    conn = pce_db.get_connection(db)
    try:
        conn.execute("UPDATE raw_captures SET created_at = ? WHERE pair_id = ?", (ts, pair_id))
        conn.commit()
    finally:
        conn.close()


def _set_pipeline_error_ts(db: Path, ts: float) -> None:
    """Back-date the oldest pipeline_errors row so the test has a clear target."""
    conn = pce_db.get_connection(db)
    try:
        conn.execute(
            "UPDATE pipeline_errors SET ts = ? "
            "WHERE id = (SELECT id FROM pipeline_errors ORDER BY ts ASC LIMIT 1)",
            (ts,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# No-op path
# ---------------------------------------------------------------------------

class TestNoOpPath:

    def test_zero_zero_is_skipped(self, db_with_mixed_ages: Path):
        summary = sweep_once(days=0, max_raw_rows=0, db_path=db_with_mixed_ages)
        assert summary["skipped"] is True
        assert summary["raw_captures_deleted"] == 0
        assert summary["messages_deleted"] == 0
        # Data untouched
        sessions = query_sessions(last=10, db_path=db_with_mixed_ages)
        assert len(sessions) == 2


# ---------------------------------------------------------------------------
# Days-based retention
# ---------------------------------------------------------------------------

class TestDaysRetention:

    def test_deletes_old_rows_and_keeps_recent(self, db_with_mixed_ages: Path):
        summary = sweep_once(days=7, max_raw_rows=0, db_path=db_with_mixed_ages)

        assert summary["raw_captures_deleted"] == 1
        assert summary["messages_deleted"] == 1
        assert summary["pipeline_errors_deleted"] == 1
        # old session became orphan (message deleted) → cleaned up
        assert summary["sessions_deleted"] == 1

        sessions = query_sessions(last=10, db_path=db_with_mixed_ages)
        assert len(sessions) == 1
        msgs = query_messages(sessions[0]["id"], db_path=db_with_mixed_ages)
        assert len(msgs) == 1
        assert msgs[0]["content_text"] == "today's work"

    def test_fts_stays_in_sync_after_delete(self, db_with_mixed_ages: Path):
        sweep_once(days=7, db_path=db_with_mixed_ages)
        conn = pce_db.get_connection(db_with_mixed_ages)
        try:
            fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            conn.close()
        assert fts_count == msg_count

    def test_favorited_empty_session_survives(self, tmp_path: Path):
        db = tmp_path / "fav.db"
        pce_db.init_db(db)
        now = time.time()
        sid = insert_session(
            source_id=SOURCE_PROXY, started_at=now - 86400 * 30,
            provider="openai", db_path=db,
        )
        # Mark favorited
        conn = pce_db.get_connection(db)
        try:
            conn.execute("UPDATE sessions SET favorited = 1 WHERE id = ?", (sid,))
            conn.commit()
        finally:
            conn.close()

        # No messages → would be orphan, but favorited saves it
        summary = sweep_once(days=7, db_path=db)
        assert summary["sessions_deleted"] == 0
        sessions = query_sessions(last=10, db_path=db)
        assert len(sessions) == 1


# ---------------------------------------------------------------------------
# Row-cap retention
# ---------------------------------------------------------------------------

class TestMaxRowsRetention:

    def test_cap_trims_oldest_raw_captures(self, tmp_path: Path):
        db = tmp_path / "cap.db"
        pce_db.init_db(db)
        sid = insert_session(
            source_id=SOURCE_PROXY, started_at=time.time() - 1000,
            provider="openai", db_path=db,
        )
        # Insert 10 captures with increasing timestamps
        for i in range(10):
            pid = new_pair_id()
            insert_capture(
                direction="request", pair_id=pid, host="api.openai.com",
                path="/v1/chat/completions", method="POST",
                provider="openai", body_text_or_json="{}",
                body_format="json", source_id=SOURCE_PROXY, db_path=db,
            )
            _set_capture_ts(db, pid, time.time() - (10 - i) * 10)  # ascending

        summary = sweep_once(days=0, max_raw_rows=3, db_path=db)
        assert summary["raw_captures_deleted"] == 7

        conn = pce_db.get_connection(db)
        try:
            remaining = conn.execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
        finally:
            conn.close()
        assert remaining == 3


# ---------------------------------------------------------------------------
# Async loop smoke
# ---------------------------------------------------------------------------

def test_run_retention_loop_respects_stop_event(tmp_path: Path):
    """Loop exits promptly when the stop event is set."""
    import asyncio

    from pce_core.retention import run_retention_loop

    db = tmp_path / "loop.db"
    pce_db.init_db(db)

    async def driver():
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_retention_loop(interval_hours=0.01, db_path=db, stop_event=stop),
        )
        # Let the first sweep run, then stop
        await asyncio.sleep(0.05)
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
