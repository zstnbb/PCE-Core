# SPDX-License-Identifier: Apache-2.0
"""Tests for G2 — branch projection on the query API (ADR-2026-04-26 §5.3).

Covers three contracts:

1. ``pce_core.db.query_messages(branches=...)`` projects rows correctly
   for ``expand`` / ``collapse`` / a specific ``branch_id``.
2. ``pce_core.db.query_sessions`` surfaces ``branch_count`` per session
   so dashboards can decide whether to render a switcher chip.
3. ``GET /api/v1/sessions/{id}/messages`` defaults to ``collapse`` and
   honours ``?branches=expand`` and ``?branch=<id>``.

The collapse semantic implemented in G2 is intentionally minimal
(picks the wall-clock-latest branch + dedups consecutive same-role
rows). Tests pin those behaviours so a future "smarter" collapse can
swap in without breaking dashboard expectations.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Set PCE_DATA_DIR before importing anything from pce_core so the HTTP
# TestClient picks up an isolated DB. Any earlier import (e.g. via
# conftest) of pce_core.server still works because the lifespan re-reads
# the env-var when ``init_db`` runs inside ``with TestClient(app)``.
_PCE_TMP = tempfile.mkdtemp(prefix="pce_g2_")
os.environ.setdefault("PCE_DATA_DIR", _PCE_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core import db as pce_db  # noqa: E402
from pce_core import config as pce_config  # noqa: E402
from pce_core.db import _collapse_branches  # noqa: E402


def _server_db_path() -> Path:
    """The DB the FastAPI server actually reads from.

    ``pce_core.config.DB_PATH`` is frozen at first-import time. If
    another test module imported ``pce_core.config`` before this one
    (regression run), that module's tempdir wins. Seeding into our
    *own* tempdir would then leave the server querying a different
    file → spurious 404s. Reading the live ``DB_PATH`` keeps the two
    sides in lockstep regardless of import order.
    """
    return pce_config.DB_PATH


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed_session(db: Path, session_id: str = "sess-g2") -> None:
    """Create the source + session rows referenced by inserts."""
    pce_db.init_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_type) VALUES (?, ?)",
            ("src-g2", "proxy"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source_id, started_at, provider, session_key) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, "src-g2", time.time(), "openai", f"key-{session_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_linear(db: Path, sid: str) -> list[str]:
    """Plain user / assistant / user / assistant on the default branch."""
    _seed_session(db, sid)
    base = 1_700_000_000.0
    ids = []
    for i, (role, text) in enumerate([
        ("user", "first"),
        ("assistant", "first reply"),
        ("user", "second"),
        ("assistant", "second reply"),
    ]):
        mid = pce_db.insert_message(
            session_id=sid, ts=base + i, role=role,
            content_text=text, db_path=db,
        )
        assert mid
        ids.append(mid)
    return ids


def _seed_forked(db: Path, sid: str) -> dict[str, str]:
    """Plain user → asst → THEN an alternate user (edit) → alternate asst.

    Layout (ts ordering):
        u1 ts=1000  branch='0'
        a1 ts=1001  branch='0'
        u2 ts=1002  branch='alt-1'   (edited prompt)
        a2 ts=1003  branch='alt-1'   (reply on the edited branch)

    The "winning" branch by max-ts is ``alt-1`` so collapse should
    return ``[u1, u2, a2]`` (the shared prefix from main + the entire
    winning branch — no, wait, u1 is main, u2/a2 are alt; main's a1
    came AFTER u1 but BEFORE alt's first row, so collapse keeps a1
    too: ``[u1, a1, u2, a2]``). Then the consecutive-role squash
    leaves it unchanged because no two adjacent rows share a role.
    """
    _seed_session(db, sid)
    base = 1_700_000_000.0
    ids: dict[str, str] = {}
    ids["u1"] = pce_db.insert_message(
        session_id=sid, ts=base, role="user",
        content_text="APPLES", db_path=db,
    )
    ids["a1"] = pce_db.insert_message(
        session_id=sid, ts=base + 1, role="assistant",
        content_text="APPLES_REPLY", db_path=db,
    )
    ids["u2"] = pce_db.insert_message(
        session_id=sid, ts=base + 2, role="user",
        content_text="BANANAS", branch_id="alt-1",
        branch_parent_id=ids["u1"], db_path=db,
    )
    ids["a2"] = pce_db.insert_message(
        session_id=sid, ts=base + 3, role="assistant",
        content_text="BANANAS_REPLY", branch_id="alt-1",
        branch_parent_id=ids["u2"], db_path=db,
    )
    return ids


def _seed_regen(db: Path, sid: str) -> dict[str, str]:
    """Regenerate-on-default-branch case: two consecutive asst rows.

    All on branch '0' because today's normalizer leaves variants on
    main. Collapse must squash them to "the latest reply wins".
    """
    _seed_session(db, sid)
    base = 1_700_000_000.0
    ids: dict[str, str] = {}
    ids["u"] = pce_db.insert_message(
        session_id=sid, ts=base, role="user",
        content_text="hi", db_path=db,
    )
    ids["a_v1"] = pce_db.insert_message(
        session_id=sid, ts=base + 1, role="assistant",
        content_text="reply v1", db_path=db,
    )
    ids["a_v2"] = pce_db.insert_message(
        session_id=sid, ts=base + 2, role="assistant",
        content_text="reply v2", db_path=db,
    )
    return ids


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------

class TestCollapseHelper:
    """``_collapse_branches`` is a pure list-of-dict transform; pin its
    behaviour with synthetic rows so we can change the SQL layer
    without re-deriving the algorithm."""

    def test_empty_input_returns_empty(self):
        assert _collapse_branches([]) == []

    def test_single_branch_passthrough(self):
        rows = [
            {"role": "user", "ts": 1.0, "branch_id": "0", "content_text": "u"},
            {"role": "assistant", "ts": 2.0, "branch_id": "0", "content_text": "a"},
        ]
        assert _collapse_branches(rows) == rows

    def test_consecutive_same_role_keeps_latest(self):
        rows = [
            {"role": "user", "ts": 1.0, "branch_id": "0", "content_text": "u"},
            {"role": "assistant", "ts": 2.0, "branch_id": "0", "content_text": "v1"},
            {"role": "assistant", "ts": 3.0, "branch_id": "0", "content_text": "v2"},
        ]
        out = _collapse_branches(rows)
        assert [r["content_text"] for r in out] == ["u", "v2"]

    def test_alt_branch_wins_when_latest(self):
        rows = [
            {"role": "user", "ts": 1.0, "branch_id": "0", "content_text": "u_orig"},
            {"role": "assistant", "ts": 2.0, "branch_id": "0", "content_text": "a_orig"},
            {"role": "user", "ts": 3.0, "branch_id": "alt", "content_text": "u_edit"},
            {"role": "assistant", "ts": 4.0, "branch_id": "alt", "content_text": "a_edit"},
        ]
        out = _collapse_branches(rows)
        # shared prefix from '0' (rows BEFORE alt's first ts) + alt rows.
        # alt's first ts is 3.0, so u_orig (1.0) and a_orig (2.0) both
        # qualify as shared prefix. Then the alt rows append.
        assert [r["content_text"] for r in out] == [
            "u_orig", "a_orig", "u_edit", "a_edit",
        ]

    def test_alt_branch_with_stale_main_drops_main_only(self):
        # Main is stale (alt's max_ts > main's max_ts) AND alt comes
        # FIRST in time → no shared prefix; only alt rows survive.
        rows = [
            {"role": "user", "ts": 5.0, "branch_id": "alt", "content_text": "u_alt"},
            {"role": "assistant", "ts": 6.0, "branch_id": "alt", "content_text": "a_alt"},
            {"role": "user", "ts": 1.0, "branch_id": "0", "content_text": "u_old"},
        ]
        out = _collapse_branches(rows)
        assert [r["content_text"] for r in out] == ["u_alt", "a_alt"]

    def test_main_wins_when_latest_returns_only_main(self):
        rows = [
            {"role": "user", "ts": 1.0, "branch_id": "alt", "content_text": "stale_u"},
            {"role": "user", "ts": 2.0, "branch_id": "0", "content_text": "main_u"},
            {"role": "assistant", "ts": 3.0, "branch_id": "0", "content_text": "main_a"},
        ]
        out = _collapse_branches(rows)
        assert [r["content_text"] for r in out] == ["main_u", "main_a"]


# ---------------------------------------------------------------------------
# query_messages contract
# ---------------------------------------------------------------------------

class TestQueryMessagesProjection:
    def test_expand_default_returns_every_row(self, tmp_path: Path):
        db = tmp_path / "expand.db"
        ids = _seed_forked(db, "sess-fork")

        rows = pce_db.query_messages("sess-fork", db_path=db)  # default = expand
        assert len(rows) == 4
        assert {r["id"] for r in rows} == set(ids.values())

    def test_collapse_drops_stale_main_branch_rows_via_squash(self, tmp_path: Path):
        """Both main and alt branches survive prefix/squash but the
        regenerated case (two asst on main) collapses to one.
        """
        db = tmp_path / "regen.db"
        ids = _seed_regen(db, "sess-regen")

        all_rows = pce_db.query_messages("sess-regen", branches="expand", db_path=db)
        assert len(all_rows) == 3  # u + a_v1 + a_v2

        collapsed = pce_db.query_messages("sess-regen", branches="collapse", db_path=db)
        assert len(collapsed) == 2
        # The kept asst is the LATEST one (squash keeps the later row).
        kept_asst = next(r for r in collapsed if r["role"] == "assistant")
        assert kept_asst["id"] == ids["a_v2"]

    def test_collapse_on_linear_session_is_noop(self, tmp_path: Path):
        db = tmp_path / "linear.db"
        _seed_linear(db, "sess-linear")
        expand = pce_db.query_messages("sess-linear", branches="expand", db_path=db)
        collapse = pce_db.query_messages("sess-linear", branches="collapse", db_path=db)
        assert [r["id"] for r in expand] == [r["id"] for r in collapse]

    def test_specific_branch_filters_to_that_branch(self, tmp_path: Path):
        db = tmp_path / "specific.db"
        _seed_forked(db, "sess-fork")

        only_main = pce_db.query_messages("sess-fork", branches="0", db_path=db)
        assert {r["branch_id"] for r in only_main} == {"0"}
        assert len(only_main) == 2

        only_alt = pce_db.query_messages("sess-fork", branches="alt-1", db_path=db)
        assert {r["branch_id"] for r in only_alt} == {"alt-1"}
        assert len(only_alt) == 2

    def test_unknown_branch_returns_empty_list(self, tmp_path: Path):
        db = tmp_path / "unknown.db"
        _seed_linear(db, "sess-linear")
        rows = pce_db.query_messages(
            "sess-linear", branches="nope", db_path=db,
        )
        assert rows == []


# ---------------------------------------------------------------------------
# query_sessions: branch_count
# ---------------------------------------------------------------------------

class TestSessionBranchCount:
    def test_linear_session_reports_one_branch(self, tmp_path: Path):
        db = tmp_path / "bcount-linear.db"
        _seed_linear(db, "sess-linear")
        sessions = pce_db.query_sessions(last=10, db_path=db)
        assert len(sessions) == 1
        assert sessions[0]["branch_count"] == 1

    def test_forked_session_reports_two_branches(self, tmp_path: Path):
        db = tmp_path / "bcount-fork.db"
        _seed_forked(db, "sess-fork")
        sessions = pce_db.query_sessions(last=10, db_path=db)
        assert len(sessions) == 1
        assert sessions[0]["branch_count"] == 2

    def test_empty_session_reports_one(self, tmp_path: Path):
        # Edge case: a session row exists but has no messages yet.
        # COALESCE(..., 1) keeps branch_count >= 1 so the dashboard
        # never has to special-case zero.
        db = tmp_path / "bcount-empty.db"
        _seed_session(db, "sess-empty")
        sessions = pce_db.query_sessions(last=10, db_path=db)
        assert len(sessions) == 1
        assert sessions[0]["branch_count"] == 1

    def test_filters_still_work_with_alias(self, tmp_path: Path):
        # Make sure the s.* alias didn't break the WHERE clauses.
        db = tmp_path / "bcount-filter.db"
        _seed_linear(db, "sess-linear")
        rows = pce_db.query_sessions(
            last=10, provider="openai", db_path=db,
        )
        assert len(rows) == 1
        rows = pce_db.query_sessions(
            last=10, provider="anthropic", db_path=db,
        )
        assert rows == []


# ---------------------------------------------------------------------------
# HTTP route contract
# ---------------------------------------------------------------------------

class TestHTTPSessionMessagesRoute:
    """Smoke tests on the FastAPI handler. These reuse the global
    PCE_DATA_DIR set at import time so they share a DB file with the
    rest of this test module (we use unique session ids to avoid
    cross-contamination)."""

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app

        with TestClient(app) as c:
            yield c

    def test_default_is_collapse_with_regenerate_dedup(self, client):
        sid = "http-g2-regen"
        _seed_regen(_server_db_path(), sid)
        resp = client.get(f"/api/v1/sessions/{sid}/messages")
        assert resp.status_code == 200
        body = resp.json()
        # Default = collapse → consecutive asst squashed to the latest.
        assert len(body) == 2
        assert {m["role"] for m in body} == {"user", "assistant"}
        assert body[-1]["content_text"] == "reply v2"

    def test_expand_returns_full_history(self, client):
        sid = "http-g2-regen-expand"
        _seed_regen(_server_db_path(), sid)
        resp = client.get(
            f"/api/v1/sessions/{sid}/messages",
            params={"branches": "expand"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 3  # both asst variants visible

    def test_specific_branch_filter(self, client):
        sid = "http-g2-fork"
        ids = _seed_forked(_server_db_path(), sid)
        resp = client.get(
            f"/api/v1/sessions/{sid}/messages",
            params={"branch": "alt-1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert {m["branch_id"] for m in body} == {"alt-1"}
        assert {m["id"] for m in body} == {ids["u2"], ids["a2"]}

    def test_invalid_branches_value_rejected_by_pattern(self, client):
        sid = "http-g2-invalid"
        _seed_linear(_server_db_path(), sid)
        resp = client.get(
            f"/api/v1/sessions/{sid}/messages",
            params={"branches": "garbage"},
        )
        # FastAPI's Query(pattern=...) yields 422 on validation failure.
        assert resp.status_code == 422

    def test_response_carries_branch_metadata_fields(self, client):
        sid = "http-g2-meta"
        _seed_forked(_server_db_path(), sid)
        resp = client.get(
            f"/api/v1/sessions/{sid}/messages",
            params={"branches": "expand"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all("branch_id" in m for m in body)
        assert all("turn_index" in m for m in body)
        assert any(m["branch_id"] == "alt-1" for m in body)

    def test_404_when_no_messages(self, client):
        # A session that has no messages → 404 (existing contract).
        sid = "http-g2-empty"
        _seed_session(_server_db_path(), sid)
        resp = client.get(f"/api/v1/sessions/{sid}/messages")
        assert resp.status_code == 404
