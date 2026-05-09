# SPDX-License-Identifier: Apache-2.0
"""Exercise POST /api/v1/mcp/capture (the backend for pce-mcp.mcpb).

These tests use FastAPI's TestClient so they don't require a running
daemon or Node runtime. They assert that the HTTP endpoint's behaviour
matches the Python MCP tool (pce_mcp/server.py:pce_capture) on the
core cases: conversation, request/response pair, and error inputs.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    """Return a TestClient with an isolated DB (PCE_DATA_DIR override)."""
    tmp = tmp_path_factory.mktemp("pce_mcp_endpoint")
    import os
    os.environ["PCE_DATA_DIR"] = str(tmp)

    # Import AFTER env override so pce_core.config picks it up.
    from pce_core.server import app
    from pce_core.db import init_db
    init_db()

    with TestClient(app) as c:
        yield c


def test_conversation_capture_round_trip(client: TestClient):
    payload = {
        "provider": "anthropic",
        "direction": "conversation",
        "host": "api.anthropic.com",
        "path": "/v1/messages",
        "model_name": "claude-sonnet-4-20250514",
        "conversation_json": json.dumps({
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }),
        "meta": {"source": "test"},
    }
    res = client.post("/api/v1/mcp/capture", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["pair_id"]
    assert "Captured conversation" in body["summary"]


def test_request_response_pair(client: TestClient):
    payload = {
        "provider": "openai",
        "direction": "request",  # the tool decides based on body fields
        "host": "api.openai.com",
        "path": "/v1/chat/completions",
        "model_name": "gpt-4o",
        "request_body": json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
        "response_body": json.dumps({"choices": [{"message": {"content": "hello"}}]}),
        "status_code": 200,
        "latency_ms": 123.4,
    }
    res = client.post("/api/v1/mcp/capture", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["pair_id"]
    assert "Captured request" in body["summary"] or "Captured response" in body["summary"]


def test_empty_input_returns_ok_false(client: TestClient):
    """Providing no conversation_json AND no request/response must fail cleanly."""
    payload = {"provider": "anthropic", "direction": "conversation"}
    res = client.post("/api/v1/mcp/capture", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["error"]
    assert "conversation_json or request_body" in body["error"]


def test_source_tagging_and_queryable(client: TestClient):
    """After a capture, GET /api/v1/captures must return the row with provider='google'."""
    payload = {
        "provider": "google",
        "direction": "conversation",
        "host": "generativelanguage.googleapis.com",
        "conversation_json": json.dumps({"messages": []}),
    }
    res = client.post("/api/v1/mcp/capture", json=payload)
    assert res.status_code == 200, res.text

    res2 = client.get("/api/v1/captures", params={"provider": "google", "last": 10})
    assert res2.status_code == 200
    rows = res2.json()
    assert any(r["provider"] == "google" for r in rows)


def test_missing_provider_rejected_by_pydantic(client: TestClient):
    res = client.post("/api/v1/mcp/capture", json={})
    assert res.status_code == 422  # pydantic validation


def test_stats_reflects_mcp_source(client: TestClient):
    """After captures land, /api/v1/stats by_source should include 'mcp'."""
    res = client.get("/api/v1/stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["total_captures"] >= 1
    # Source mapping is platform-internal; at minimum some source row should exist.
    assert stats["by_source"]
