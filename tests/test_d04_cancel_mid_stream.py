# SPDX-License-Identifier: Apache-2.0
"""Tests for D04 (Claude Desktop chat cancel-mid-stream) + E04 (same
bug surfacing on the inline Code-region tab).

The fix is layered:
  1. ``NormalizedMessage.interaction_kind`` field — propagates through
     ``message_processor.persist_result`` to ``messages.interaction_kind``
     (column added in migration 0010).
  2. ``pipeline.try_normalize_pair_request_only`` — replays a request
     row through the normaliser with an empty response, drops any
     assistant rows, tags survivors with ``interaction_kind='cancelled'``.
  3. ``pipeline.sweep_orphan_request_rows`` — periodic sweeper that
     finds aged-orphan request rows and routes each through (2).
  4. ``db.query_orphan_request_rows`` — the SQL behind (3); excludes
     pairs that already have a ``messages`` row (idempotency).

These tests cover all four layers with real SQLite operations against
an isolated temp database.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import (
    init_db,
    insert_capture,
    new_pair_id,
    query_messages,
    query_messages_by_pair,
    query_orphan_request_rows,
    query_sessions,
    SOURCE_PROXY,
)
from pce_core.normalizer.pipeline import (
    sweep_orphan_request_rows,
    try_normalize_pair,
    try_normalize_pair_request_only,
)


def _fresh_db() -> Path:
    """Create a fresh isolated DB for this test."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Layer 1: query_orphan_request_rows SQL
# ---------------------------------------------------------------------------

def _test_orphan_query_skips_completed_pairs():
    db = _fresh_db()
    pid_done = new_pair_id()
    pid_orphan = new_pair_id()
    now = time.time()

    # Completed pair (request + response, both old enough).
    insert_capture(
        direction="request",
        pair_id=pid_done,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"complete prompt"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    insert_capture(
        direction="response",
        pair_id=pid_done,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        status_code=200,
        headers_redacted_json="{}",
        body_text_or_json='{"content":[{"type":"text","text":"reply"}],"model":"claude-sonnet-4","role":"assistant"}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )

    # Orphan request, old enough (>30s).
    insert_capture(
        direction="request",
        pair_id=pid_orphan,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"cancelled prompt"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    # Force its created_at older than the cutoff.
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE raw_captures SET created_at = ? WHERE pair_id = ?",
            (now - 120.0, pid_orphan),
        )
        conn.execute(
            "UPDATE raw_captures SET created_at = ? WHERE pair_id = ?",
            (now - 120.0, pid_done),
        )
        conn.commit()
    finally:
        conn.close()

    orphans = query_orphan_request_rows(min_age_seconds=30.0, now=now, db_path=db)
    pair_ids = [r["pair_id"] for r in orphans]
    assert pid_orphan in pair_ids, f"orphan request should be returned; got {pair_ids}"
    assert pid_done not in pair_ids, f"completed pair must be excluded; got {pair_ids}"
    print("[PASS] query_orphan_request_rows: skips completed pairs")


def _test_orphan_query_respects_min_age():
    db = _fresh_db()
    pid_fresh = new_pair_id()
    now = time.time()

    insert_capture(
        direction="request",
        pair_id=pid_fresh,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"fresh"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    # created_at defaults to time.time() at insert; should be < cutoff for min_age=30.

    orphans = query_orphan_request_rows(min_age_seconds=30.0, now=now, db_path=db)
    pair_ids = [r["pair_id"] for r in orphans]
    assert pid_fresh not in pair_ids, (
        f"fresh request must NOT be returned (still might get response); got {pair_ids}"
    )

    # Once min_age=0 we should see it.
    orphans2 = query_orphan_request_rows(min_age_seconds=0.0, now=now + 1.0, db_path=db)
    assert pid_fresh in [r["pair_id"] for r in orphans2]
    print("[PASS] query_orphan_request_rows: respects min_age_seconds")


def _test_orphan_query_idempotent_after_message_inserted():
    """Once a messages row references the pair, it must drop out of the orphan list."""
    db = _fresh_db()
    pid = new_pair_id()
    now = time.time()

    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"once"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    # Age it.
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE raw_captures SET created_at = ? WHERE pair_id = ?", (now - 120.0, pid))
        conn.commit()
    finally:
        conn.close()

    # Round 1: orphan visible.
    orphans1 = query_orphan_request_rows(min_age_seconds=30.0, now=now, db_path=db)
    assert pid in [r["pair_id"] for r in orphans1]

    # Recover via request-only.
    sid = try_normalize_pair_request_only(pid, source_id=SOURCE_PROXY, db_path=db)
    assert sid is not None

    # Round 2: orphan must no longer surface.
    orphans2 = query_orphan_request_rows(min_age_seconds=30.0, now=now, db_path=db)
    assert pid not in [r["pair_id"] for r in orphans2], (
        f"after recovery the pair must drop out; still visible: "
        f"{[r['pair_id'] for r in orphans2]}"
    )
    print("[PASS] query_orphan_request_rows: idempotent after message inserted")


# ---------------------------------------------------------------------------
# Layer 2: try_normalize_pair_request_only
# ---------------------------------------------------------------------------

def _test_request_only_anthropic_api_shape():
    """Anthropic /v1/messages request body → user message tagged cancelled."""
    db = _fresh_db()
    pid = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"What is the meaning of life?"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )

    sid = try_normalize_pair_request_only(pid, source_id=SOURCE_PROXY, db_path=db)
    assert sid is not None, "request-only normalize must produce a session"

    msgs = query_messages(sid, db_path=db)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1, f"expected 1 user msg, got {len(user_msgs)}"
    assert user_msgs[0]["content_text"] == "What is the meaning of life?"
    assert user_msgs[0]["interaction_kind"] == "cancelled"
    assert user_msgs[0]["capture_pair_id"] == pid
    print("[PASS] request-only: Anthropic API shape → cancelled user msg")


def _test_request_only_anthropic_web_shape():
    """claude.ai web shape (prompt + parent_message_uuid) → user msg."""
    db = _fresh_db()
    pid = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="claude.ai",
        path="/api/organizations/abc/chat_conversations/0123abcd-ef45-6789-abcd-ef0123456789/completion",
        method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"prompt":"web-shape cancel test","parent_message_uuid":"prev-uuid","model":"claude-sonnet-4"}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )

    sid = try_normalize_pair_request_only(pid, source_id=SOURCE_PROXY, db_path=db)
    assert sid is not None, "claude.ai web shape must produce a session"

    msgs = query_messages(sid, db_path=db)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content_text"] == "web-shape cancel test"
    assert user_msgs[0]["interaction_kind"] == "cancelled"
    print("[PASS] request-only: claude.ai web shape → cancelled user msg")


def _test_request_only_drops_assistants_from_request_history():
    """If a multi-turn request body contains assistant history, those
    rows must be dropped — request-only path can NOT trustworthy report
    any assistant output."""
    db = _fresh_db()
    pid = new_pair_id()
    body = (
        '{"model":"claude-sonnet-4","messages":['
        '{"role":"user","content":"first turn"},'
        '{"role":"assistant","content":"first reply (echoed history)"},'
        '{"role":"user","content":"second turn cancelled"}'
        ']}'
    )
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json=body,
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )

    sid = try_normalize_pair_request_only(pid, source_id=SOURCE_PROXY, db_path=db)
    assert sid is not None
    msgs = query_messages(sid, db_path=db)
    roles = [m["role"] for m in msgs]
    assert "assistant" not in roles, (
        f"assistant history must be dropped on cancel path; got {roles}"
    )
    user_texts = [m["content_text"] for m in msgs if m["role"] == "user"]
    assert "first turn" in user_texts
    assert "second turn cancelled" in user_texts
    # Every kept row carries the cancelled tag.
    for m in msgs:
        assert m["interaction_kind"] == "cancelled", (
            f"every survivor must be tagged cancelled; got {m}"
        )
    print("[PASS] request-only: assistant history dropped, all survivors tagged")


def _test_request_only_skips_when_response_present():
    """If the pair already has a response row, request-only must
    return None (caller should use try_normalize_pair instead)."""
    db = _fresh_db()
    pid = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"x"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    insert_capture(
        direction="response",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        status_code=200,
        headers_redacted_json="{}",
        body_text_or_json='{"content":[{"type":"text","text":"y"}],"role":"assistant","model":"claude-sonnet-4"}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )

    sid = try_normalize_pair_request_only(pid, source_id=SOURCE_PROXY, db_path=db)
    assert sid is None, "must refuse to run on a pair that has a response"
    print("[PASS] request-only: refuses to run when response is present")


def _test_request_only_returns_none_for_unknown_pair():
    db = _fresh_db()
    sid = try_normalize_pair_request_only(
        "nonexistent-pair-id", source_id=SOURCE_PROXY, db_path=db,
    )
    assert sid is None
    print("[PASS] request-only: unknown pair → None")


# ---------------------------------------------------------------------------
# Layer 3: sweep_orphan_request_rows
# ---------------------------------------------------------------------------

def _test_sweep_recovers_one_orphan():
    db = _fresh_db()
    pid_orphan = new_pair_id()
    pid_complete = new_pair_id()
    now = time.time()

    # Orphan.
    insert_capture(
        direction="request",
        pair_id=pid_orphan,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"orphan"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    # Complete pair.
    insert_capture(
        direction="request",
        pair_id=pid_complete,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"complete"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    insert_capture(
        direction="response",
        pair_id=pid_complete,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        status_code=200,
        headers_redacted_json="{}",
        body_text_or_json='{"content":[{"type":"text","text":"r"}],"role":"assistant","model":"claude-sonnet-4"}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    # Persist the complete pair via the normal pipeline so it has messages.
    sid_complete = try_normalize_pair(pid_complete, source_id=SOURCE_PROXY, db_path=db)
    assert sid_complete is not None

    # Age the orphan past min_age.
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE raw_captures SET created_at = ? WHERE pair_id = ?",
            (now - 120.0, pid_orphan),
        )
        conn.commit()
    finally:
        conn.close()

    stats = sweep_orphan_request_rows(min_age_seconds=30.0, db_path=db)
    assert stats["scanned"] == 1, f"expected 1 orphan, got {stats}"
    assert stats["recovered"] == 1, f"expected 1 recovery, got {stats}"
    assert stats["errors"] == 0
    assert len(stats["session_ids"]) == 1

    # Verify the recovered message exists with the cancelled tag.
    recovered = query_messages_by_pair(pid_orphan, db_path=db)
    assert len(recovered) == 1
    assert recovered[0]["role"] == "user"
    assert recovered[0]["content_text"] == "orphan"
    assert recovered[0]["interaction_kind"] == "cancelled"
    print("[PASS] sweep: recovers exactly the one orphan")


def _test_sweep_idempotent():
    db = _fresh_db()
    pid = new_pair_id()
    now = time.time()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"once"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE raw_captures SET created_at = ? WHERE pair_id = ?", (now - 120.0, pid))
        conn.commit()
    finally:
        conn.close()

    s1 = sweep_orphan_request_rows(min_age_seconds=30.0, db_path=db)
    s2 = sweep_orphan_request_rows(min_age_seconds=30.0, db_path=db)
    assert s1["recovered"] == 1
    assert s2["scanned"] == 0, f"second sweep must see 0 orphans, got {s2}"
    assert s2["recovered"] == 0
    print("[PASS] sweep: idempotent on rerun")


def _test_sweep_skips_fresh_orphans():
    db = _fresh_db()
    pid = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com", path="/v1/messages", method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4","messages":[{"role":"user","content":"fresh"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    # No aging — request was just created.

    stats = sweep_orphan_request_rows(min_age_seconds=30.0, db_path=db)
    assert stats["scanned"] == 0, f"fresh orphan must be skipped, got {stats}"
    print("[PASS] sweep: skips fresh (under-min-age) orphans")


# ---------------------------------------------------------------------------
# Layer 4: end-to-end E04 / D04 shape
# ---------------------------------------------------------------------------

def _test_e04_code_region_inline_cancel():
    """E04 mirror: request from inline Code-region tab via api.anthropic.com.

    Same code path as D04 — the fix closes both with one set of changes.
    The inline Code tab uses ``messages.tool_choice`` which we don't care
    about here; the only thing the test needs is for the user prompt to
    surface as a cancelled message."""
    db = _fresh_db()
    pid = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.anthropic.com",
        path="/v1/messages",
        method="POST",
        provider="anthropic",
        headers_redacted_json='{"x-anthropic-source":"claude-code"}',
        body_text_or_json=(
            '{"model":"claude-haiku-4-5","messages":['
            '{"role":"user","content":"run tests"}],'
            '"tools":[{"name":"Bash","description":"shell"}]}'
        ),
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db,
    )
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE raw_captures SET created_at = ? WHERE pair_id = ?",
            (time.time() - 60.0, pid),
        )
        conn.commit()
    finally:
        conn.close()

    stats = sweep_orphan_request_rows(min_age_seconds=30.0, db_path=db)
    assert stats["recovered"] == 1, f"E04 must recover, got {stats}"

    msgs = query_messages_by_pair(pid, db_path=db)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content_text"] == "run tests"
    assert msgs[0]["interaction_kind"] == "cancelled"
    print("[PASS] E04: inline Code-region tab cancel-mid-stream → cancelled user msg")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def test_d04_all():
    _test_orphan_query_skips_completed_pairs()
    _test_orphan_query_respects_min_age()
    _test_orphan_query_idempotent_after_message_inserted()
    _test_request_only_anthropic_api_shape()
    _test_request_only_anthropic_web_shape()
    _test_request_only_drops_assistants_from_request_history()
    _test_request_only_skips_when_response_present()
    _test_request_only_returns_none_for_unknown_pair()
    _test_sweep_recovers_one_orphan()
    _test_sweep_idempotent()
    _test_sweep_skips_fresh_orphans()
    _test_e04_code_region_inline_cancel()
    print("\n=== All D04 / E04 tests passed (12) ===")


if __name__ == "__main__":
    test_d04_all()
