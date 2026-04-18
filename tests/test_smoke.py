# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for PCE Proxy PoC – verifies DB, redaction, and inspect CLI."""

import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_proxy.config import REDACT_HEADERS
from pce_proxy.db import init_db, insert_capture, new_pair_id, query_recent, get_connection
from pce_proxy.redact import redact_headers, redact_headers_json, safe_body_text


def test_all():
    # Use a temp DB so we don't pollute real data
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "test.db"

    # 1. DB init
    init_db(db_path)
    conn = get_connection(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "raw_captures" in tables, f"Missing raw_captures table, got {tables}"
    assert "sources" in tables, f"Missing sources table, got {tables}"
    print("[PASS] DB init: tables created")

    # 2. Redaction
    h = redact_headers({
        "Authorization": "Bearer sk-secret123",
        "Content-Type": "application/json",
        "Cookie": "session=abc",
        "X-Api-Key": "key-456",
    })
    assert h["Authorization"] == "REDACTED"
    assert h["Cookie"] == "REDACTED"
    assert h["X-Api-Key"] == "REDACTED"
    assert h["Content-Type"] == "application/json"
    print("[PASS] Redaction: sensitive headers replaced")

    # 3. Body parsing
    text, fmt = safe_body_text(b'{"model": "gpt-4"}')
    assert fmt == "json"
    text2, fmt2 = safe_body_text(b"plain text here")
    assert fmt2 == "text"
    text3, fmt3 = safe_body_text(b"")
    assert fmt3 == "text" and text3 == ""
    print("[PASS] Body parsing: format detection works")

    # 4. Insert and query
    pid = new_pair_id()
    rid = insert_capture(
        direction="request", pair_id=pid, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4",
        headers_redacted_json=redact_headers_json({"Authorization": "Bearer sk-xxx", "Content-Type": "application/json"}),
        body_text_or_json='{"model":"gpt-4","messages":[{"role":"user","content":"hello"}]}',
        body_format="json",
        db_path=db_path,
    )
    assert rid is not None, "Request insert failed"

    resp_id = insert_capture(
        direction="response", pair_id=pid, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4", status_code=200, latency_ms=342.5,
        headers_redacted_json=redact_headers_json({"Content-Type": "application/json"}),
        body_text_or_json='{"choices":[{"message":{"content":"Hi there!"}}]}',
        body_format="json",
        db_path=db_path,
    )
    assert resp_id is not None, "Response insert failed"
    print("[PASS] Insert: request + response written")

    # 5. Query
    rows = query_recent(10, db_path=db_path)
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    directions = {r["direction"] for r in rows}
    assert directions == {"request", "response"}

    # Verify redaction persisted
    for r in rows:
        assert "sk-xxx" not in r["headers_redacted_json"], "API key leaked into DB!"
        if "Authorization" in r["headers_redacted_json"]:
            assert "REDACTED" in r["headers_redacted_json"]
    print("[PASS] Query: data retrieved, no sensitive headers in DB")

    # 6. Pair linkage
    assert rows[0]["pair_id"] == rows[1]["pair_id"] == pid
    print("[PASS] Pair linkage: request and response share pair_id")

    print("\n=== All smoke tests passed ===")


if __name__ == "__main__":
    test_all()
