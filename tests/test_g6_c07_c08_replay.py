# SPDX-License-Identifier: Apache-2.0
"""G6 — full-pipeline replay for C07 (edit-user-message) + C08 (regenerate).

These were the two stability gaps documented in
``Docs/stability/CLAUDE-FULL-COVERAGE.md`` under the heading
"raw PASS / session gap":

    C07 — user edits APPLES → BANANAS, the second completion's user
          row was silently dropped, leaving an orphan assistant.
    C08 — user clicks ↻ Retry, the two assistant variants both
          land on the default branch, ``message_count`` reads 3
          instead of 2.

Once G1-G5 are wired together, the same raw captures produce a
session with **two branches** and a default-collapse view that
mirrors what the user actually sees in claude.ai. These tests feed
realistic raw_captures rows through ``try_normalize_pair`` (the same
path the live ingest pipeline uses) and assert the new contract.

Asserted contract per ADR-2026-04-26 §5.4 / §7:

- ``branch_count == 2`` after the second completion lands.
- ``GET /messages?branches=collapse`` returns a linear, non-orphan
  conversation (matches provider UI).
- ``GET /messages?branches=expand`` exposes both branches with their
  ``branch_id`` set, ``branch_parent_id`` pointing at the sibling.
- Replaying the SAME pair twice never bumps branch_count again.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Isolate the test DB before importing pce_core.
_TMP_DIR = tempfile.mkdtemp(prefix="pce_g6_test_")
os.environ.setdefault("PCE_DATA_DIR", _TMP_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core import db as pce_db  # noqa: E402
from pce_core.db import (  # noqa: E402
    SOURCE_BROWSER_EXT,
    insert_capture,
    new_pair_id,
    query_messages,
    query_sessions,
)
from pce_core.normalizer.pipeline import try_normalize_pair  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CLAUDE_HOST = "claude.ai"
COMPLETION_PATH = "/api/organizations/org-1/chat_conversations/conv-1/completion"


def _fresh_db(name: str) -> Path:
    db_path = Path(_TMP_DIR) / f"{name}.db"
    if db_path.exists():
        db_path.unlink()
    pce_db.init_db(db_path)
    # Ensure the source row referenced by SOURCE_BROWSER_EXT exists; the
    # rest of the pipeline assumes it.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_type) VALUES (?, ?)",
            (SOURCE_BROWSER_EXT, "extension"),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _ingest_completion(
    db: Path,
    *,
    prompt: str,
    parent_uuid: str,
    asst_text: str,
    asst_uuid: str,
    session_hint: str,
    created_at: float,
) -> str:
    """Insert a request + response pair mirroring claude.ai web's
    completion endpoint, then run the same try_normalize_pair the
    live ingest path uses. Returns the resulting session_id."""
    pair_id = new_pair_id()
    # ``conversation_id`` is what AnthropicMessagesNormalizer reads as
    # ``session_key``. Without it each pair lands in its OWN session and
    # ``persist_result``'s ``is_existing`` is False on the second pair —
    # branch detection only runs on existing sessions, so the fork
    # would never be minted. Real claude.ai web requests carry this
    # id in the URL; the normalizer's URL-path extraction is a
    # follow-up (see Docs follow-up `fu_anthropic_session_key`).
    request_body = json.dumps({
        "prompt": prompt,
        "parent_message_uuid": parent_uuid,
        "conversation_id": session_hint,
        "timezone": "UTC",
    })
    response_body = json.dumps({
        "uuid": asst_uuid,
        "role": "assistant",
        "model": "claude-sonnet-4",
        "content": [{"type": "text", "text": asst_text}],
        "usage": {"input_tokens": 5, "output_tokens": 12},
    })

    insert_capture(
        direction="request",
        pair_id=pair_id,
        host=CLAUDE_HOST,
        path=COMPLETION_PATH,
        method="POST",
        provider="anthropic",
        body_text_or_json=request_body,
        session_hint=session_hint,
        source_id=SOURCE_BROWSER_EXT,
        db_path=db,
    )
    insert_capture(
        direction="response",
        pair_id=pair_id,
        host=CLAUDE_HOST,
        path=COMPLETION_PATH,
        method="POST",
        provider="anthropic",
        status_code=200,
        body_text_or_json=response_body,
        session_hint=session_hint,
        source_id=SOURCE_BROWSER_EXT,
        db_path=db,
    )

    # Bumping created_at on the request row so chronology lines up the
    # way the wall-clock collapse heuristic expects.
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE raw_captures SET created_at = ? WHERE pair_id = ?",
            (created_at, pair_id),
        )
        conn.commit()
    finally:
        conn.close()

    sid = try_normalize_pair(
        pair_id, source_id=SOURCE_BROWSER_EXT,
        created_via="test", db_path=db,
    )
    assert sid is not None, (
        f"try_normalize_pair returned None for pair {pair_id} — "
        f"check provider/host/path matches AnthropicMessagesNormalizer"
    )
    return sid


# ---------------------------------------------------------------------------
# C08 — Regenerate (assistant retry)
# ---------------------------------------------------------------------------

class TestC08RegenerateReplay:
    """User asks once, clicks ↻ Retry → two assistant texts on the same
    parent_message_uuid. branch_count must hit 2; collapse view stays
    linear with the latest reply winning (matches what the user sees)."""

    def test_two_completions_same_parent_yield_two_branches(self):
        db = _fresh_db("g6-c08-branches")
        sid1 = _ingest_completion(
            db, prompt="What is 2+2?", parent_uuid="root-uuid",
            asst_text="2+2 is 4.", asst_uuid="asst-v1",
            session_hint="conv-c08", created_at=1000.0,
        )
        sid2 = _ingest_completion(
            db, prompt="What is 2+2?", parent_uuid="root-uuid",
            asst_text="2+2 equals four.", asst_uuid="asst-v2",
            session_hint="conv-c08", created_at=1001.0,
        )
        assert sid1 == sid2, "Both completions must share one session"

        sessions = query_sessions(last=10, db_path=db)
        s = next(s for s in sessions if s["id"] == sid1)
        assert s["branch_count"] == 2

    def test_collapse_view_returns_linear_with_latest_reply(self):
        db = _fresh_db("g6-c08-collapse")
        sid = _ingest_completion(
            db, prompt="What is 2+2?", parent_uuid="root",
            asst_text="2+2 is 4.", asst_uuid="asst-v1",
            session_hint="conv-c08c", created_at=1000.0,
        )
        _ingest_completion(
            db, prompt="What is 2+2?", parent_uuid="root",
            asst_text="2+2 equals four.", asst_uuid="asst-v2",
            session_hint="conv-c08c", created_at=1001.0,
        )

        collapsed = query_messages(sid, branches="collapse", db_path=db)
        # No orphan assistant, no duplicate user — exactly one round.
        roles = [m["role"] for m in collapsed]
        assert roles == ["user", "assistant"]
        # Collapse picks the LATEST assistant variant — same as the
        # provider UI after the user clicks the regenerate arrow.
        assert collapsed[-1]["content_text"] == "2+2 equals four."

    def test_expand_view_exposes_both_branches(self):
        db = _fresh_db("g6-c08-expand")
        sid = _ingest_completion(
            db, prompt="ping", parent_uuid="root",
            asst_text="pong v1", asst_uuid="asst-v1",
            session_hint="conv-c08e", created_at=1000.0,
        )
        _ingest_completion(
            db, prompt="ping", parent_uuid="root",
            asst_text="pong v2", asst_uuid="asst-v2",
            session_hint="conv-c08e", created_at=1001.0,
        )

        expanded = query_messages(sid, branches="expand", db_path=db)
        # Distinct branch_ids surface in expand mode — the dashboard's
        # "All branches" toggle relies on this.
        branch_ids = {m["branch_id"] for m in expanded}
        assert len(branch_ids) >= 2
        # The forked assistant carries a non-null branch_parent_id
        # pointing at the sibling row.
        forked = [m for m in expanded if m["branch_id"] != "0" and m["role"] == "assistant"]
        assert forked, "Expected at least one assistant on an alt branch"
        assert all(m["branch_parent_id"] for m in forked)

    def test_idempotent_replay_does_not_grow_branch_count(self):
        # Re-ingest the EXACT same pair twice (proxy retransmit). The
        # second pass must enrich-in-place rather than minting a third
        # branch.
        db = _fresh_db("g6-c08-idem")
        sid = _ingest_completion(
            db, prompt="hi", parent_uuid="root",
            asst_text="hello", asst_uuid="asst-1",
            session_hint="conv-c08i", created_at=1000.0,
        )
        _ingest_completion(
            db, prompt="hi", parent_uuid="root",
            asst_text="hello", asst_uuid="asst-1",
            session_hint="conv-c08i", created_at=1001.0,
        )

        sessions = query_sessions(last=10, db_path=db)
        s = next(s for s in sessions if s["id"] == sid)
        assert s["branch_count"] == 1


# ---------------------------------------------------------------------------
# C07 — Edit user message
# ---------------------------------------------------------------------------

class TestC07EditUserMessageReplay:
    """User sends APPLES → reply, then edits to BANANAS → reply.
    Same parent_message_uuid; user text and assistant text both differ.
    Both completions land on the SAME session, on TWO branches,
    and the collapse view shows the LATEST round."""

    def test_edit_creates_second_branch(self):
        db = _fresh_db("g6-c07-branches")
        sid1 = _ingest_completion(
            db, prompt="What is APPLES?", parent_uuid="root-uuid",
            asst_text="An apple is a fruit.", asst_uuid="asst-apples",
            session_hint="conv-c07", created_at=1000.0,
        )
        sid2 = _ingest_completion(
            db, prompt="What is BANANAS?", parent_uuid="root-uuid",
            asst_text="A banana is a fruit.", asst_uuid="asst-bananas",
            session_hint="conv-c07", created_at=1001.0,
        )
        assert sid1 == sid2

        sessions = query_sessions(last=10, db_path=db)
        s = next(s for s in sessions if s["id"] == sid1)
        assert s["branch_count"] == 2

    def test_collapse_drops_orphan_and_keeps_latest_round(self):
        # The C07 production bug was: user "BANANAS" never made it to
        # the messages table → "asked APPLES, got BANANAS reply" orphan.
        # With branches in place, collapse must show the BANANAS round
        # and NOT show a stranded BANANAS reply paired with an APPLES
        # prompt.
        db = _fresh_db("g6-c07-collapse")
        sid = _ingest_completion(
            db, prompt="What is APPLES?", parent_uuid="root",
            asst_text="apples reply", asst_uuid="asst-1",
            session_hint="conv-c07c", created_at=1000.0,
        )
        _ingest_completion(
            db, prompt="What is BANANAS?", parent_uuid="root",
            asst_text="bananas reply", asst_uuid="asst-2",
            session_hint="conv-c07c", created_at=1001.0,
        )

        collapsed = query_messages(sid, branches="collapse", db_path=db)
        roles = [m["role"] for m in collapsed]
        # Linear: one user + one assistant — no orphan.
        assert roles == ["user", "assistant"]
        # The visible round is the LATEST edit (BANANAS), matching
        # what the user sees in claude.ai after editing.
        assert collapsed[0]["content_text"] == "What is BANANAS?"
        assert collapsed[1]["content_text"] == "bananas reply"

    def test_expand_view_shows_both_user_texts(self):
        # Forensic fidelity (Driver #1): the original APPLES prompt must
        # still be retrievable for audit.
        db = _fresh_db("g6-c07-expand")
        sid = _ingest_completion(
            db, prompt="What is APPLES?", parent_uuid="root",
            asst_text="apples reply", asst_uuid="asst-1",
            session_hint="conv-c07e", created_at=1000.0,
        )
        _ingest_completion(
            db, prompt="What is BANANAS?", parent_uuid="root",
            asst_text="bananas reply", asst_uuid="asst-2",
            session_hint="conv-c07e", created_at=1001.0,
        )

        expanded = query_messages(sid, branches="expand", db_path=db)
        user_texts = {
            m["content_text"] for m in expanded if m["role"] == "user"
        }
        assert "What is APPLES?" in user_texts
        assert "What is BANANAS?" in user_texts
