# SPDX-License-Identifier: Apache-2.0
"""G5 — branch backfill (ADR-2026-04-26 §5.5).

Locks in the contract that a session whose messages were originally
inserted on the default branch ``'0'`` — but whose
``content_json.threading`` exposes ``provider_parent_uuid`` — gets
re-stamped onto alternate branches by the one-shot backfill helper.

Coverage:

- ``backfill_session_branches`` mints a new ``branch_id`` for the
  SECOND of two same-role / same-parent messages with different text
  (regenerate, edit-user-message).
- The helper is idempotent — running it twice mints zero additional
  branches.
- It never DOWNGRADES rows already on a non-default branch (DOM
  channel takes precedence).
- Legacy rows with no ``provider_parent_uuid`` stay linear, matching
  the ADR's "best-effort" guarantee.
- ``backfill_all_sessions`` returns a sane summary dict and bumps
  ``branch_count`` on the touched sessions visible via
  ``query_sessions``.
"""

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Isolate from any user data dir before importing pce_core.
_TMP_DIR = tempfile.mkdtemp(prefix="pce_g5_test_")
os.environ.setdefault("PCE_DATA_DIR", _TMP_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core import db as pce_db  # noqa: E402
from pce_core.branch_backfill import (  # noqa: E402
    backfill_all_sessions,
    backfill_session_branches,
)
from pce_core.rich_content import build_content_json  # noqa: E402


def _fresh_db(name: str) -> Path:
    db_path = Path(_TMP_DIR) / f"{name}.db"
    if db_path.exists():
        db_path.unlink()
    pce_db.init_db(db_path)
    return db_path


def _seed_source(db: Path) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_type) VALUES (?, ?)",
            ("src-g5", "proxy"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_session(db: Path, session_id: str) -> None:
    _seed_source(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source_id, started_at, provider, tool_family, created_via) "
            "VALUES (?, 'src-g5', ?, 'anthropic', 'claude-web', 'test')",
            (session_id, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_msg(
    db: Path,
    *,
    session_id: str,
    role: str,
    content_text: str,
    parent_uuid: str | None = None,
    message_uuid: str | None = None,
    branch_id: str = "0",
    ts: float = 0.0,
) -> str:
    """Insert directly via pce_db.insert_message so we exercise the
    same code path the live pipeline uses (turn_index auto-computed,
    branch columns honoured)."""
    threading = {}
    if parent_uuid:
        threading["provider_parent_uuid"] = parent_uuid
    if message_uuid:
        threading["provider_message_uuid"] = message_uuid
    cj = build_content_json(
        attachments=None,
        plain_text=content_text,
        threading=threading or None,
    )
    msg_id = pce_db.insert_message(
        session_id=session_id,
        ts=ts,
        role=role,
        content_text=content_text,
        content_json=cj,
        branch_id=branch_id,
        db_path=db,
    )
    assert msg_id is not None
    return msg_id


# ---------------------------------------------------------------------------
# 1. backfill_session_branches — fork minting
# ---------------------------------------------------------------------------

class TestBackfillSessionBranches:

    def test_regenerate_two_assistant_replies_minted_to_alt_branch(self, tmp_path):
        # Pre-G4 state: both replies stored on default branch '0' with
        # threading metadata pointing at the same parent_uuid. Backfill
        # must re-stamp the SECOND onto a freshly minted branch.
        db = _fresh_db("g5-regen")
        sid = "sess-regen"
        _seed_session(db, sid)
        _insert_msg(db, session_id=sid, role="user",
                    content_text="ask", parent_uuid="root", ts=1.0)
        a1 = _insert_msg(db, session_id=sid, role="assistant",
                         content_text="reply v1",
                         parent_uuid="user-1", message_uuid="asst-1",
                         ts=2.0)
        a2 = _insert_msg(db, session_id=sid, role="assistant",
                         content_text="reply v2",
                         parent_uuid="user-1", message_uuid="asst-2",
                         ts=3.0)

        updated = backfill_session_branches(sid, db_path=db)
        assert updated == 1

        # a1 stays on default; a2 gets a fresh branch_id pointing at a1.
        rows = pce_db.query_messages(sid, branches="expand", db_path=db)
        by_id = {r["id"]: r for r in rows}
        assert by_id[a1]["branch_id"] == "0"
        assert by_id[a2]["branch_id"] != "0"
        assert by_id[a2]["branch_parent_id"] == a1

    def test_idempotent_second_run_mints_nothing(self, tmp_path):
        db = _fresh_db("g5-idem")
        sid = "sess-idem"
        _seed_session(db, sid)
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="v1", parent_uuid="p", ts=1.0)
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="v2", parent_uuid="p", ts=2.0)

        first = backfill_session_branches(sid, db_path=db)
        second = backfill_session_branches(sid, db_path=db)
        assert first == 1
        assert second == 0  # second run is a no-op

    def test_does_not_downgrade_rows_already_on_non_default_branch(self, tmp_path):
        # If the DOM channel (or a previous backfill) already moved a
        # row off branch '0', the backfill must respect that.
        db = _fresh_db("g5-dom-precedence")
        sid = "sess-dom"
        _seed_session(db, sid)
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="v1", parent_uuid="p", ts=1.0)
        # v2 is already on branch 'dom-mint' (e.g. from branch_prev DOM
        # event in conversation.py).
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="v2", parent_uuid="p",
                    branch_id="dom-mint", ts=2.0)

        updated = backfill_session_branches(sid, db_path=db)
        assert updated == 0
        rows = pce_db.query_messages(sid, branches="expand", db_path=db)
        # The DOM-set branch_id is preserved verbatim.
        v2 = next(r for r in rows if r["content_text"] == "v2")
        assert v2["branch_id"] == "dom-mint"

    def test_legacy_rows_without_threading_stay_linear(self, tmp_path):
        # No ``provider_parent_uuid`` in content_json → backfill can't
        # tell whether two same-role messages are a fork or a legitimate
        # multi-turn. ADR §5.5 explicitly says these stay linear.
        db = _fresh_db("g5-legacy")
        sid = "sess-legacy"
        _seed_session(db, sid)
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="v1", ts=1.0)  # no parent_uuid
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="v2", ts=2.0)  # no parent_uuid

        updated = backfill_session_branches(sid, db_path=db)
        assert updated == 0

    def test_idempotent_replay_with_same_text_does_not_fork(self, tmp_path):
        # Same parent_uuid + same role + same text → just a re-ingest,
        # not a fork. Backfill must NOT mint a branch.
        db = _fresh_db("g5-replay")
        sid = "sess-replay"
        _seed_session(db, sid)
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="reply", parent_uuid="p", ts=1.0)
        _insert_msg(db, session_id=sid, role="assistant",
                    content_text="reply", parent_uuid="p", ts=2.0)

        updated = backfill_session_branches(sid, db_path=db)
        assert updated == 0

    def test_edit_user_message_forks_user_row(self, tmp_path):
        db = _fresh_db("g5-edit")
        sid = "sess-edit"
        _seed_session(db, sid)
        _insert_msg(db, session_id=sid, role="user",
                    content_text="APPLES", parent_uuid="root", ts=1.0)
        u2 = _insert_msg(db, session_id=sid, role="user",
                         content_text="BANANAS",
                         parent_uuid="root", ts=2.0)

        updated = backfill_session_branches(sid, db_path=db)
        assert updated == 1
        rows = pce_db.query_messages(sid, branches="expand", db_path=db)
        u2_row = next(r for r in rows if r["id"] == u2)
        assert u2_row["branch_id"] != "0"


# ---------------------------------------------------------------------------
# 2. backfill_all_sessions — summary + branch_count visibility
# ---------------------------------------------------------------------------

class TestBackfillAllSessions:

    def test_summary_counts_match_actual_changes(self, tmp_path):
        db = _fresh_db("g5-all")
        # Session A — has a fork.
        _seed_session(db, "sess-A")
        _insert_msg(db, session_id="sess-A", role="assistant",
                    content_text="v1", parent_uuid="pA", ts=1.0)
        _insert_msg(db, session_id="sess-A", role="assistant",
                    content_text="v2", parent_uuid="pA", ts=2.0)
        # Session B — linear, no parent uuids.
        _seed_session(db, "sess-B")
        _insert_msg(db, session_id="sess-B", role="user",
                    content_text="hi", ts=1.0)
        _insert_msg(db, session_id="sess-B", role="assistant",
                    content_text="hello", ts=2.0)

        summary = backfill_all_sessions(db_path=db)
        assert summary["sessions_scanned"] == 2
        assert summary["sessions_updated"] == 1
        assert summary["rows_updated"] == 1

    def test_branch_count_lifts_to_2_after_backfill(self, tmp_path):
        # The query_sessions branch_count column must reflect the new
        # branch — that's how the dashboard surfaces the chip.
        db = _fresh_db("g5-count")
        _seed_session(db, "sess-count")
        _insert_msg(db, session_id="sess-count", role="assistant",
                    content_text="v1", parent_uuid="p", ts=1.0)
        _insert_msg(db, session_id="sess-count", role="assistant",
                    content_text="v2", parent_uuid="p", ts=2.0)

        before = pce_db.query_sessions(last=10, db_path=db)
        s_before = next(s for s in before if s["id"] == "sess-count")
        assert s_before["branch_count"] == 1

        backfill_all_sessions(db_path=db)

        after = pce_db.query_sessions(last=10, db_path=db)
        s_after = next(s for s in after if s["id"] == "sess-count")
        assert s_after["branch_count"] == 2

    def test_empty_db_returns_zero_summary(self, tmp_path):
        db = _fresh_db("g5-empty")
        summary = backfill_all_sessions(db_path=db)
        assert summary == {
            "sessions_scanned": 0,
            "sessions_updated": 0,
            "rows_updated": 0,
        }
