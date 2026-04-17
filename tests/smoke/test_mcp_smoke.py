"""Smoke: MCP server ``pce_capture`` tool path.

Exercises the underlying tool function end-to-end against an isolated
DB and verifies:
1. A ``conversation`` capture lands as ``source=mcp``.
2. A request/response pair lands and auto-normalises into a session.
3. Structured events (``mcp.tool_called`` / ``mcp.tool_completed``) are
   emitted.
4. Failed ingests record a ``pipeline_errors`` row so the health API
   can surface them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest


# FastMCP's ``@mcp.tool()`` decorator is expected to preserve the underlying
# Python function so we can call it directly in tests. If it ever starts
# wrapping the return value we fall back to ``.fn`` or ``.func``.
def _call_tool(tool, *args, **kwargs):
    for candidate in (tool, getattr(tool, "fn", None), getattr(tool, "func", None)):
        if callable(candidate):
            try:
                return candidate(*args, **kwargs)
            except TypeError:
                continue
    raise RuntimeError("Cannot invoke MCP tool in tests – unexpected shape")


def _patch_db(monkeypatch, db_path: Path):
    """Route all MCP-side DB writes through ``db_path``."""
    from pce_mcp import server as mcp_server
    from pce_core import db as pce_db
    from pce_core.normalizer import pipeline as np_pipeline

    pce_db.init_db(db_path)

    def _ins(*a, **kw):
        kw.setdefault("db_path", db_path)
        return pce_db.insert_capture(*a, **kw)

    def _rec(stage, message, **kw):
        kw.setdefault("db_path", db_path)
        return pce_db.record_pipeline_error(stage, message, **kw)

    def _norm_conv(row, **kw):
        kw.setdefault("db_path", db_path)
        return np_pipeline.normalize_conversation(row, **kw)

    def _try_pair(pair_id, **kw):
        kw.setdefault("db_path", db_path)
        return np_pipeline.try_normalize_pair(pair_id, **kw)

    def _qbp(pid, **kw):
        kw.setdefault("db_path", db_path)
        return pce_db.query_by_pair(pid, **kw)

    monkeypatch.setattr(mcp_server, "insert_capture", _ins)
    monkeypatch.setattr(mcp_server, "record_pipeline_error", _rec)
    monkeypatch.setattr(mcp_server, "normalize_conversation", _norm_conv)
    monkeypatch.setattr(mcp_server, "try_normalize_pair", _try_pair)
    monkeypatch.setattr(mcp_server, "query_by_pair", _qbp)
    # Prevent re-running init_db against the global default path
    mcp_server._db_initialized = True


def test_conversation_capture_persists_and_normalises(tmp_path: Path, monkeypatch, caplog):
    db = tmp_path / "mcp_conv.db"
    _patch_db(monkeypatch, db)

    from pce_mcp.server import pce_capture

    caplog.set_level(logging.INFO, logger="pce.mcp")

    conv_json = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "What is PCE?"},
                {"role": "assistant", "content": "A local AI capture layer."},
            ]
        }
    )
    result = _call_tool(
        pce_capture,
        provider="openai",
        direction="conversation",
        host="chatgpt.com",
        path="/",
        method="GET",
        model_name="gpt-4",
        conversation_json=conv_json,
    )
    assert "OK." in result

    from pce_core.db import query_recent, SOURCE_MCP, query_sessions, query_messages
    rows = query_recent(10, db_path=db)
    assert any(r["source_id"] == SOURCE_MCP for r in rows), \
        f"Expected at least one MCP capture in {rows}"

    # Structured events emitted
    events = [getattr(r, "event", None) for r in caplog.records]
    assert "mcp.tool_called" in events
    assert "mcp.tool_completed" in events


def test_request_response_pair_normalises_into_session(tmp_path: Path, monkeypatch):
    db = tmp_path / "mcp_pair.db"
    _patch_db(monkeypatch, db)

    from pce_mcp.server import pce_capture

    req = json.dumps(
        {"model": "gpt-4", "messages": [{"role": "user", "content": "Write a haiku."}]}
    )
    resp = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "Tiny snow on pine."}}]}
    )
    result = _call_tool(
        pce_capture,
        provider="openai",
        direction="response",
        host="api.openai.com",
        path="/v1/chat/completions",
        method="POST",
        model_name="gpt-4",
        request_body=req,
        response_body=resp,
        status_code=200,
        latency_ms=350.0,
    )
    assert "Captured request" in result and "Captured response" in result

    from pce_core.db import query_sessions, query_messages, SOURCE_MCP

    sessions = query_sessions(last=10, db_path=db)
    assert sessions, "Normalisation should have produced at least one session"

    sess = next(s for s in sessions if s["source_id"] == SOURCE_MCP)
    msgs = query_messages(sess["id"], db_path=db)
    roles = {m["role"] for m in msgs}
    assert "user" in roles and "assistant" in roles


def test_failed_conversation_insert_records_pipeline_error(tmp_path: Path, monkeypatch):
    db = tmp_path / "mcp_fail.db"
    _patch_db(monkeypatch, db)

    from pce_mcp import server as mcp_server
    from pce_mcp.server import pce_capture

    # Force insert_capture to fail so we exercise the error recording path.
    monkeypatch.setattr(mcp_server, "insert_capture", lambda **kw: None)

    conv_json = json.dumps({"messages": [{"role": "user", "content": "..."}]})
    result = _call_tool(
        pce_capture,
        provider="openai",
        direction="conversation",
        host="chatgpt.com",
        conversation_json=conv_json,
    )
    assert result.startswith("Error:")

    from pce_core.db import get_pipeline_error_counts
    counts = get_pipeline_error_counts(db_path=db)
    assert counts.get("ingest", {}).get("ERROR", 0) >= 1, \
        f"Expected an ingest-stage error, got {counts}"
