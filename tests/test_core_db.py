# SPDX-License-Identifier: Apache-2.0
"""Tests for pce_core.db – verifies Tier 0 + Tier 1 schema and operations."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import (
    get_connection,
    init_db,
    insert_capture,
    insert_message,
    insert_session,
    new_pair_id,
    query_by_pair,
    query_captures,
    query_messages,
    query_recent,
    query_sessions,
    get_stats,
    SOURCE_PROXY,
    SOURCE_BROWSER_EXT,
    SOURCE_MCP,
)
from pce_core.redact import redact_headers, redact_headers_json, safe_body_text


def test_all():
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test_core.db"

    # 1. DB init – all tables created
    init_db(db_path)
    conn = get_connection(db_path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    conn.close()

    assert "raw_captures" in tables, f"Missing raw_captures, got {tables}"
    assert "sources" in tables, f"Missing sources, got {tables}"
    assert "sessions" in tables, f"Missing sessions, got {tables}"
    assert "messages" in tables, f"Missing messages, got {tables}"
    print("[PASS] DB init: all 4 tables created")

    # 2. Default sources seeded
    conn = get_connection(db_path)
    conn.row_factory = lambda c, r: r[0]
    source_ids = conn.execute("SELECT id FROM sources").fetchall()
    conn.close()
    assert SOURCE_PROXY in source_ids
    assert SOURCE_BROWSER_EXT in source_ids
    assert SOURCE_MCP in source_ids
    print("[PASS] Default sources seeded (proxy, browser_ext, mcp)")

    # 3. Redaction still works
    h = redact_headers({
        "Authorization": "Bearer sk-secret",
        "Content-Type": "application/json",
        "X-Api-Key": "key-456",
    })
    assert h["Authorization"] == "REDACTED"
    assert h["X-Api-Key"] == "REDACTED"
    assert h["Content-Type"] == "application/json"
    print("[PASS] Redaction: sensitive headers replaced")

    # 4. Body parsing
    text, fmt = safe_body_text(b'{"model": "gpt-4"}')
    assert fmt == "json"
    text2, fmt2 = safe_body_text(b"plain text here")
    assert fmt2 == "text"
    print("[PASS] Body parsing: format detection works")

    # 5. Tier 0: insert + query captures
    pid = new_pair_id()
    rid = insert_capture(
        direction="request", pair_id=pid, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4",
        headers_redacted_json=redact_headers_json({"Authorization": "Bearer sk-xxx"}),
        body_text_or_json='{"model":"gpt-4","messages":[{"role":"user","content":"hello"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db_path,
    )
    assert rid is not None

    resp_id = insert_capture(
        direction="response", pair_id=pid, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4", status_code=200, latency_ms=342.5,
        headers_redacted_json=redact_headers_json({"Content-Type": "application/json"}),
        body_text_or_json='{"choices":[{"message":{"content":"Hi!"}}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db_path,
    )
    assert resp_id is not None
    print("[PASS] Tier 0: request + response inserted")

    # 6. query_recent
    rows = query_recent(10, db_path=db_path)
    assert len(rows) == 2
    print("[PASS] query_recent: 2 rows returned")

    # 7. query_by_pair
    pair_rows = query_by_pair(pid, db_path=db_path)
    assert len(pair_rows) == 2
    assert pair_rows[0]["pair_id"] == pair_rows[1]["pair_id"] == pid
    print("[PASS] query_by_pair: pair linkage OK")

    # 8. query_captures with filter
    filtered = query_captures(last=10, provider="openai", db_path=db_path)
    assert len(filtered) == 2
    filtered_none = query_captures(last=10, provider="anthropic", db_path=db_path)
    assert len(filtered_none) == 0
    print("[PASS] query_captures: filtering works")

    # 9. get_stats
    stats = get_stats(db_path=db_path)
    assert stats["total_captures"] == 2
    assert stats["by_provider"]["openai"] == 2
    assert stats["by_direction"]["request"] == 1
    assert stats["by_direction"]["response"] == 1
    print("[PASS] get_stats: correct counts")

    # 10. meta_json field (new in pce_core)
    pid2 = new_pair_id()
    meta_id = insert_capture(
        direction="conversation", pair_id=pid2, host="chatgpt.com",
        path="/", method="GET", provider="openai",
        headers_redacted_json="{}",
        body_text_or_json='{"user":"hi","assistant":"hello"}',
        body_format="json",
        meta_json='{"page_url":"https://chatgpt.com/c/abc123"}',
        source_id=SOURCE_BROWSER_EXT,
        db_path=db_path,
    )
    assert meta_id is not None
    print("[PASS] meta_json + conversation direction + browser_ext source")

    # 11. Tier 1: sessions
    now = time.time()
    sess_id = insert_session(
        source_id=SOURCE_PROXY,
        started_at=now,
        provider="openai",
        tool_family="api-direct",
        session_key="test-session-001",
        title_hint="Test conversation",
        created_via="proxy",
        db_path=db_path,
    )
    assert sess_id is not None
    print("[PASS] Tier 1: session inserted")

    # 12. Tier 1: messages
    msg1 = insert_message(
        session_id=sess_id, ts=now, role="user",
        content_text="hello", capture_pair_id=pid,
        db_path=db_path,
    )
    msg2 = insert_message(
        session_id=sess_id, ts=now + 1, role="assistant",
        content_text="Hi there!", model_name="gpt-4",
        capture_pair_id=pid,
        db_path=db_path,
    )
    assert msg1 is not None
    assert msg2 is not None
    print("[PASS] Tier 1: 2 messages inserted")

    # 13. query_sessions
    sessions = query_sessions(last=10, db_path=db_path)
    assert len(sessions) == 1
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["provider"] == "openai"
    print("[PASS] query_sessions: correct count and provider")

    # 14. query_messages
    msgs = query_messages(sess_id, db_path=db_path)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content_text"] == "Hi there!"
    print("[PASS] query_messages: correct order and content")

    # 15. Redaction persisted check
    for r in query_recent(10, db_path=db_path):
        assert "sk-xxx" not in (r.get("headers_redacted_json") or "")
        assert "sk-secret" not in (r.get("headers_redacted_json") or "")
    print("[PASS] No sensitive data in persisted captures")

    print("\n=== All pce_core DB tests passed ===")


if __name__ == "__main__":
    test_all()
