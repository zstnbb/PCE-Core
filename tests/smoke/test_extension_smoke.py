# SPDX-License-Identifier: Apache-2.0
"""Smoke: browser-extension ingest path.

Uses the FastAPI TestClient to POST captures the way the browser
extension does, and verifies:
1. ``conversation`` payloads land as ``source=browser_extension_default``
   and are auto-normalised into a session.
2. ``network_intercept`` payloads are unwrapped and normalised.
3. A 500-failure path records an ``ingest`` pipeline error.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path


def _make_client(tmp_path: Path, monkeypatch):
    """Return a TestClient bound to a fresh server + DB."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    # Reload config/db/server so the new PCE_DATA_DIR takes effect.
    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.server as _server
    importlib.reload(_cfg)
    importlib.reload(_db)
    importlib.reload(_server)

    from fastapi.testclient import TestClient

    client = TestClient(_server.app)
    client.__enter__()  # fire lifespan → init_db
    return client, _server


def test_conversation_capture_lands_and_normalises(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        payload = {
            "source_type": "browser_extension",
            "source_name": "chrome-ext",
            "direction": "conversation",
            "provider": "openai",
            "host": "chatgpt.com",
            "path": "/c/abc123",
            "method": "GET",
            "headers_json": "{}",
            "body_json": json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "hi there"},
                    ]
                }
            ),
            "body_format": "json",
            "session_hint": "abc123",
            "meta": {"page_url": "https://chatgpt.com/c/abc123"},
            "schema_version": 2,
        }
        r = client.post("/api/v1/captures", json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["source_id"] == "browser-extension-default"

        # Verify the capture row exists via the query API.
        r2 = client.get("/api/v1/captures?source_type=browser_extension")
        assert r2.status_code == 200
        captures = r2.json()
        assert any(c["pair_id"] == body["pair_id"] for c in captures)

        # Verify auto-normalisation produced a session with both roles.
        r3 = client.get("/api/v1/sessions")
        assert r3.status_code == 200
        sessions = r3.json()
        assert sessions, "Expected at least one session from the conversation capture"
        session_id = sessions[0]["id"]
        r4 = client.get(f"/api/v1/sessions/{session_id}/messages")
        assert r4.status_code == 200
        messages = r4.json()
        roles = {m["role"] for m in messages}
        assert {"user", "assistant"} <= roles, f"roles={roles}"
    finally:
        client.__exit__(None, None, None)


def test_network_intercept_capture_is_normalised(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        wrapper = {
            "url": "https://api.openai.com/v1/chat/completions",
            "request_body": json.dumps(
                {"model": "gpt-4", "messages": [{"role": "user", "content": "ping"}]}
            ),
            "response_body": json.dumps(
                {
                    "model": "gpt-4",
                    "choices": [
                        {"message": {"role": "assistant", "content": "pong"}}
                    ],
                }
            ),
            "is_streaming": False,
        }
        payload = {
            "source_type": "browser_extension",
            "source_name": "chrome-ext",
            "direction": "network_intercept",
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "headers_json": "{}",
            "body_json": json.dumps(wrapper),
            "body_format": "json",
            "schema_version": 2,
        }
        r = client.post("/api/v1/captures", json=payload)
        assert r.status_code == 201, r.text

        r2 = client.get("/api/v1/sessions")
        assert r2.status_code == 200
        sessions = r2.json()
        assert sessions, "network_intercept should produce a normalised session"
    finally:
        client.__exit__(None, None, None)


def test_health_counts_reflect_extension_activity(tmp_path: Path, monkeypatch):
    """After extension captures, the health API must show browser_extension activity."""
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        payload = {
            "source_type": "browser_extension",
            "direction": "conversation",
            "provider": "anthropic",
            "host": "claude.ai",
            "path": "/chat/xyz",
            "headers_json": "{}",
            "body_json": json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "hi"},
                    ]
                }
            ),
            "schema_version": 2,
        }
        # Send a handful of captures so counters are non-trivial.
        for _ in range(3):
            r = client.post("/api/v1/captures", json=payload)
            assert r.status_code == 201

        r = client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        ext = data["sources"]["browser-extension-default"]
        assert ext["captures_last_1h"] >= 3
        assert ext["captures_last_24h"] >= 3
        assert ext["captures_total"] >= 3
        assert ext["last_capture_ago_s"] is not None
        assert ext["last_capture_ago_s"] < 10.0
    finally:
        client.__exit__(None, None, None)


def test_ingest_failure_records_pipeline_error(tmp_path: Path, monkeypatch):
    """Force insert_capture to return None and expect an ingest error row."""
    client, server = _make_client(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(server, "insert_capture", lambda **kw: None)

        payload = {
            "source_type": "browser_extension",
            "direction": "conversation",
            "provider": "openai",
            "host": "chatgpt.com",
            "headers_json": "{}",
            "body_json": "{}",
            "schema_version": 2,
        }
        r = client.post("/api/v1/captures", json=payload)
        assert r.status_code == 500

        r2 = client.get("/api/v1/health")
        assert r2.status_code == 200
        data = r2.json()
        ext = data["sources"]["browser-extension-default"]
        assert ext["failures_last_24h"] >= 1
    finally:
        client.__exit__(None, None, None)
