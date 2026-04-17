"""Smoke: P1 storage standardisation — end-to-end through the HTTP API.

Checks the TASK-003 acceptance criteria end-to-end:

1. Schema advances to version 3 and ``oi_*`` columns exist.
2. Ingesting a capture populates ``oi_attributes_json`` on messages.
3. ``GET /api/v1/export?format=otlp`` returns valid OpenInference JSONL.
4. ``GET /api/v1/export?format=json`` returns the streaming envelope.
5. ``POST /api/v1/import`` ingests the exported payload.
6. Re-importing is idempotent.
7. ``GET /api/v1/retention`` reports the current policy.
8. ``POST /api/v1/retention/sweep`` executes a sweep with overrides.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path


def _make_client(tmp_path: Path, monkeypatch):
    """Spin a fresh TestClient with an isolated DB via PCE_DATA_DIR."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PCE_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("PCE_RETENTION_MAX_ROWS", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.otel_exporter as _otel
    import pce_core.server as _server
    importlib.reload(_cfg)
    importlib.reload(_db)
    # Reset OTel module state so configure_otlp_exporter runs fresh.
    _otel._initialised = False
    _otel._enabled = False
    importlib.reload(_server)

    from fastapi.testclient import TestClient

    client = TestClient(_server.app)
    client.__enter__()  # fire lifespan → init_db + configure_otlp
    return client, _server


def _ingest_pair(client, *, user_text: str, assistant_text: str) -> str:
    """Post a synthetic request + response pair, return the pair_id."""
    req_body = json.dumps(
        {"model": "gpt-4", "messages": [{"role": "user", "content": user_text}]}
    )
    resp_body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": assistant_text}}]}
    )
    req_payload = {
        "source_type": "proxy", "direction": "request",
        "provider": "openai", "host": "api.openai.com",
        "path": "/v1/chat/completions", "method": "POST",
        "headers_json": "{}", "body_json": req_body, "body_format": "json",
        "schema_version": 2,
    }
    r = client.post("/api/v1/captures", json=req_payload)
    assert r.status_code == 201, r.text
    pair_id = r.json()["pair_id"]

    resp_payload = {
        **req_payload,
        "direction": "response", "body_json": resp_body,
        "status_code": 200, "latency_ms": 120.0,
        "pair_id": pair_id,
    }
    r2 = client.post("/api/v1/captures", json=resp_payload)
    assert r2.status_code == 201, r2.text
    return pair_id


# ---------------------------------------------------------------------------
# Schema & dual-write
# ---------------------------------------------------------------------------

def test_schema_is_at_version_3_with_oi_columns(tmp_path: Path, monkeypatch):
    client, server = _make_client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/v1/health/migrations")
        assert r.status_code == 200
        data = r.json()
        assert data["current_version"] == 3
        assert data["expected_version"] == 3
        assert data["ok"] is True

        # New OI columns exist on messages + sessions
        conn = server.get_connection() if hasattr(server, "get_connection") else None
        # Import via db module for hygiene
        from pce_core.db import get_connection
        c = get_connection()
        try:
            msg_cols = {r[1] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
            sess_cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
        finally:
            c.close()
        assert {"oi_role_raw", "oi_input_tokens", "oi_output_tokens",
                "oi_attributes_json", "oi_schema_version"} <= msg_cols
        assert {"oi_attributes_json", "oi_schema_version"} <= sess_cols
    finally:
        client.__exit__(None, None, None)


def test_ingest_populates_oi_attributes_json(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        _ingest_pair(client, user_text="what is 2+2?", assistant_text="4.")

        # Fetch messages via the API and verify OI cache is populated
        r = client.get("/api/v1/sessions")
        assert r.status_code == 200
        sessions = r.json()
        assert sessions, "Normalisation should have produced one session"
        sid = sessions[0]["id"]

        from pce_core.db import get_connection
        c = get_connection()
        try:
            import sqlite3
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT role, oi_role_raw, oi_attributes_json, oi_input_tokens, oi_output_tokens "
                "FROM messages WHERE session_id = ? ORDER BY ts",
                (sid,),
            ).fetchall()
        finally:
            c.close()
        assert len(rows) == 2
        assert {r["role"] for r in rows} == {"user", "assistant"}
        for row in rows:
            assert row["oi_role_raw"] in ("user", "assistant")
            assert row["oi_attributes_json"], "dual-write must populate oi_attributes_json"
            attrs = json.loads(row["oi_attributes_json"])
            assert attrs["llm.provider"] == "openai"
            assert attrs["llm.model_name"] == "gpt-4"
            assert attrs["session.id"] == sid
            assert attrs["openinference.span.kind"] == "LLM"

        # Session-level cache refreshed too
        c = get_connection()
        try:
            sess_row = c.execute(
                "SELECT oi_attributes_json FROM sessions WHERE id = ?", (sid,),
            ).fetchone()
        finally:
            c.close()
        assert sess_row[0], "session-level OI cache should be populated"
        sess_attrs = json.loads(sess_row[0])
        assert sess_attrs["session.id"] == sid
        assert sess_attrs["openinference.span.kind"] == "CHAT"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Export / Import round-trip via HTTP
# ---------------------------------------------------------------------------

def test_otlp_export_over_http_returns_oi_spans(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        _ingest_pair(client, user_text="hi", assistant_text="hello")
        _ingest_pair(client, user_text="ping", assistant_text="pong")

        r = client.get("/api/v1/export?format=otlp")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        assert r.headers.get("X-PCE-Export-Format") == "otlp"
        lines = [line for line in r.text.splitlines() if line.strip()]
        assert len(lines) == 2, f"Expected 2 spans, got {len(lines)}"
        for line in lines:
            span = json.loads(line)
            attrs = span["attributes"]
            assert attrs["openinference.span.kind"] == "LLM"
            assert attrs["llm.provider"] == "openai"
            assert "session.id" in attrs
            # At least one output message from the assistant
            assert any(
                k.endswith(".message.role") and v == "assistant"
                for k, v in attrs.items()
            )
    finally:
        client.__exit__(None, None, None)


def test_json_envelope_export_contains_full_sessions(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        _ingest_pair(client, user_text="hi", assistant_text="hello")
        r = client.get("/api/v1/export?format=json&include_raw=true")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        doc = json.loads(r.text)
        assert doc["schema"] == "pce.export/v1"
        assert len(doc["sessions"]) == 1
        env = doc["sessions"][0]
        assert env["session"]["provider"] == "openai"
        assert len(env["messages"]) == 2
        assert len(env["raw_captures"]) == 2
    finally:
        client.__exit__(None, None, None)


def test_export_summary_counts_match_reality(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        # Two independent pairs (no shared session_key) → two sessions.
        _ingest_pair(client, user_text="a", assistant_text="b")
        _ingest_pair(client, user_text="c", assistant_text="d")
        r = client.get("/api/v1/export/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["sessions"] == 2, f"expected 2 sessions, got {data}"
        assert data["messages"] == 4
        assert data["pairs"] == 2
    finally:
        client.__exit__(None, None, None)


def test_round_trip_import_is_idempotent(tmp_path: Path, monkeypatch):
    """Export from DB_A, import into DB_B twice — only first import creates rows."""
    client_a, _server_a = _make_client(tmp_path / "a", monkeypatch)
    try:
        _ingest_pair(client_a, user_text="hi", assistant_text="hello")
        _ingest_pair(client_a, user_text="ping", assistant_text="pong")
        exported = client_a.get("/api/v1/export?format=json").text
        assert exported.startswith('{"schema":"pce.export/v1"')
    finally:
        client_a.__exit__(None, None, None)

    client_b, _server_b = _make_client(tmp_path / "b", monkeypatch)
    try:
        # First import — expects 2 sessions (each pair is an independent
        # conversation since they share no session_key) and 4 messages total.
        r1 = client_b.post(
            "/api/v1/import",
            content=exported,
            headers={"content-type": "application/json"},
        )
        assert r1.status_code == 200, r1.text
        summary1 = r1.json()
        assert summary1["sessions_created"] == 2
        assert summary1["messages_created"] == 4
        assert summary1["errors"] == []

        # Re-import — must be a no-op
        r2 = client_b.post(
            "/api/v1/import",
            content=exported,
            headers={"content-type": "application/json"},
        )
        assert r2.status_code == 200
        summary2 = r2.json()
        assert summary2["sessions_created"] == 0
        assert summary2["sessions_matched"] == 2
        assert summary2["messages_created"] == 0
        assert summary2["messages_skipped_duplicate"] == 4

        # Target DB has exactly two sessions with 2 messages each
        r3 = client_b.get("/api/v1/sessions")
        assert r3.status_code == 200
        sessions = r3.json()
        assert len(sessions) == 2
        total_msgs = 0
        for sess in sessions:
            r4 = client_b.get(f"/api/v1/sessions/{sess['id']}/messages")
            assert r4.status_code == 200
            total_msgs += len(r4.json())
        assert total_msgs == 4
    finally:
        client_b.__exit__(None, None, None)


def test_ndjson_import_also_idempotent(tmp_path: Path, monkeypatch):
    client_a, _ = _make_client(tmp_path / "a", monkeypatch)
    try:
        _ingest_pair(client_a, user_text="x", assistant_text="y")
        ndjson = client_a.get("/api/v1/export?format=otlp").text
    finally:
        client_a.__exit__(None, None, None)

    client_b, _ = _make_client(tmp_path / "b", monkeypatch)
    try:
        r = client_b.post(
            "/api/v1/import",
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
        assert r.status_code == 200
        summary = r.json()
        assert summary["sessions_created"] == 1
        assert summary["messages_created"] >= 2
        assert summary["errors"] == []

        # re-import is idempotent
        r2 = client_b.post(
            "/api/v1/import",
            content=ndjson,
            headers={"content-type": "application/x-ndjson"},
        )
        assert r2.status_code == 200
        summary2 = r2.json()
        assert summary2["sessions_created"] == 0
        assert summary2["messages_created"] == 0
    finally:
        client_b.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

def test_retention_status_reports_policy(tmp_path: Path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/v1/retention")
        assert r.status_code == 200
        data = r.json()
        assert data["days"] == 0
        assert data["max_raw_rows"] == 0
        assert data["otel_enabled"] is False
    finally:
        client.__exit__(None, None, None)


def test_retention_sweep_endpoint_trims_old_rows(tmp_path: Path, monkeypatch):
    client, _server = _make_client(tmp_path, monkeypatch)
    try:
        _ingest_pair(client, user_text="hi", assistant_text="hello")

        # Back-date one capture pair so the sweep has something to delete
        from pce_core.db import get_connection
        c = get_connection()
        try:
            old = time.time() - 86400 * 10
            c.execute(
                "UPDATE raw_captures SET created_at = ? "
                "WHERE pair_id = (SELECT pair_id FROM raw_captures LIMIT 1)",
                (old,),
            )
            c.execute(
                "UPDATE messages SET ts = ? WHERE capture_pair_id = "
                "(SELECT DISTINCT capture_pair_id FROM messages LIMIT 1)",
                (old,),
            )
            c.commit()
        finally:
            c.close()

        r = client.post("/api/v1/retention/sweep?days=7")
        assert r.status_code == 200
        summary = r.json()
        assert summary["days"] == 7
        assert summary["raw_captures_deleted"] >= 2  # 1 pair → 2 rows
        assert summary["messages_deleted"] >= 2
        assert summary["skipped"] is False
    finally:
        client.__exit__(None, None, None)


def test_retention_sweep_is_no_op_without_policy(tmp_path: Path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        _ingest_pair(client, user_text="hi", assistant_text="hello")
        r = client.post("/api/v1/retention/sweep")  # no overrides, env unset
        assert r.status_code == 200
        summary = r.json()
        assert summary["skipped"] is True
        # Data untouched
        r2 = client.get("/api/v1/sessions")
        assert r2.status_code == 200
        assert len(r2.json()) == 1
    finally:
        client.__exit__(None, None, None)
