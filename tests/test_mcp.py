"""Tests for pce_mcp server – tool invocations via direct function calls."""

import sys
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import init_db
from pce_mcp.server import (
    pce_capture,
    pce_query,
    pce_stats,
    pce_sessions,
    pce_session_messages,
    pce_capture_pair,
    _ensure_db,
)


def test_capture_conversation():
    """Test capturing a full conversation via MCP tool."""
    result = pce_capture(
        provider="openai",
        direction="conversation",
        host="api.openai.com",
        path="/v1/chat/completions",
        model_name="gpt-4",
        conversation_json='{"messages":[{"role":"user","content":"hello"},{"role":"assistant","content":"hi"}]}',
        meta={"source": "mcp-test"},
    )
    assert "OK" in result
    assert "pair_id=" in result
    assert "Captured conversation" in result
    print(f"[PASS] pce_capture (conversation): {result}")


def test_capture_request_response_pair():
    """Test capturing a request/response pair that auto-normalizes."""
    result = pce_capture(
        provider="openai",
        direction="request",
        host="api.openai.com",
        path="/v1/chat/completions",
        method="POST",
        model_name="gpt-4",
        request_body='{"model":"gpt-4","messages":[{"role":"user","content":"What is Python?"}]}',
        response_body='{"choices":[{"message":{"role":"assistant","content":"Python is a programming language."}}],"model":"gpt-4","usage":{"prompt_tokens":10,"completion_tokens":8,"total_tokens":18}}',
        status_code=200,
        latency_ms=350.0,
    )
    assert "OK" in result
    assert "Captured request" in result
    assert "Captured response" in result
    assert "Normalized" in result  # auto-normalization should fire
    print(f"[PASS] pce_capture (req/resp + normalize): {result}")


def test_capture_anthropic():
    """Test Anthropic capture via MCP."""
    result = pce_capture(
        provider="anthropic",
        host="api.anthropic.com",
        path="/v1/messages",
        model_name="claude-sonnet-4-20250514",
        request_body='{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"Explain MCP"}]}',
        response_body='{"content":[{"type":"text","text":"MCP stands for Model Context Protocol..."}],"model":"claude-sonnet-4-20250514","role":"assistant","usage":{"input_tokens":8,"output_tokens":15}}',
        status_code=200,
    )
    assert "OK" in result
    assert "Normalized" in result
    print(f"[PASS] pce_capture (anthropic): {result}")


def test_capture_error_no_body():
    """Test error when no body is provided."""
    result = pce_capture(provider="test")
    assert "Error" in result
    print(f"[PASS] pce_capture (missing body → error): {result}")


def test_query():
    """Test querying captures."""
    result = pce_query(last=20)
    assert "capture(s)" in result
    assert "openai" in result
    assert "anthropic" in result
    print(f"[PASS] pce_query: found captures")

    # Filter by provider
    result2 = pce_query(provider="anthropic")
    assert "anthropic" in result2
    # Should not contain openai captures
    lines = result2.strip().split("\n")
    data_lines = [l for l in lines if l.startswith("- [")]
    for l in data_lines:
        assert "anthropic" in l
    print(f"[PASS] pce_query (provider filter): correct")


def test_stats():
    """Test stats summary."""
    result = pce_stats()
    assert "Total captures:" in result
    assert "By provider:" in result
    assert "openai" in result
    assert "anthropic" in result
    print(f"[PASS] pce_stats: {result.split(chr(10))[0]}")


def test_sessions():
    """Test session listing."""
    result = pce_sessions(last=10)
    assert "session(s)" in result
    # Should have at least 2 sessions (OpenAI + Anthropic from test_capture*)
    print(f"[PASS] pce_sessions: found sessions")


def test_session_messages():
    """Test reading messages from a session."""
    # First get sessions to find an ID
    from pce_core.db import query_sessions as _qs
    sessions = _qs(last=1)
    assert len(sessions) > 0
    sid = sessions[0]["id"]

    result = pce_session_messages(sid)
    assert "message(s)" in result
    assert "user" in result or "assistant" in result
    print(f"[PASS] pce_session_messages: got messages for session {sid[:8]}")


def test_capture_pair():
    """Test retrieving a capture pair."""
    from pce_core.db import query_captures as _qc
    rows = _qc(last=1, direction="request")
    assert len(rows) > 0
    pid = rows[0]["pair_id"]

    result = pce_capture_pair(pid)
    assert "capture(s)" in result
    assert "REQUEST" in result or "RESPONSE" in result or "CONVERSATION" in result
    print(f"[PASS] pce_capture_pair: pair {pid[:8]}")


def test_nonexistent():
    """Test querying non-existent data."""
    r1 = pce_session_messages("nonexistent-id-12345")
    assert "No messages found" in r1

    r2 = pce_capture_pair("nonexistent-pair-12345")
    assert "No captures found" in r2

    r3 = pce_query(provider="nonexistent-provider")
    assert "No captures found" in r3
    print("[PASS] Graceful handling of non-existent data")


def test_all():
    test_capture_conversation()
    test_capture_request_response_pair()
    test_capture_anthropic()
    test_capture_error_no_body()
    test_query()
    test_stats()
    test_sessions()
    test_session_messages()
    test_capture_pair()
    test_nonexistent()
    print("\n=== All PCE MCP tests passed ===")


if __name__ == "__main__":
    test_all()
