# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for ``POST /api/v1/captures/v2`` (UCS §5, T-1b).

These tests go through the full HTTP stack with an isolated SQLite DB and
verify the contract is enforced at the API surface, not just at schema
validation time. The ``raw_captures`` row and ``meta_json`` v2 envelope are
also inspected to prove persistence works.
"""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

from pce_core.capture_event import new_capture_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(tmp_path: Path, monkeypatch):
    """Spin a fresh TestClient with an isolated DB via ``PCE_DATA_DIR``."""
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
    _otel._initialised = False
    _otel._enabled = False
    importlib.reload(_server)

    from fastapi.testclient import TestClient

    client = TestClient(_server.app)
    client.__enter__()
    return client, _server


def _valid_payload(**overrides):
    payload = {
        "capture_id": new_capture_id(),
        "source": "L1_mitm",
        "agent_name": "pce_proxy",
        "agent_version": "1.0.0",
        "capture_time_ns": time.time_ns(),
        "capture_host": "test-host#1",
        "pair_id": f"pair-{new_capture_id()[:10]}",
        "direction": "response",
        "provider": "openai",
        "model": "gpt-4o",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "response_body": {
            "id": "chatcmpl-x",
            "choices": [{"message": {"content": "hi"}}],
        },
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Basic accept / reject
# ---------------------------------------------------------------------------

def test_minimal_valid_payload_returns_201(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/captures/v2", json=_valid_payload())
        assert r.status_code == 201, r.text
        data = r.json()
        assert "capture_id" in data
        assert isinstance(data["ingested_at_ns"], int)
        assert data["ingested_at_ns"] > 0
        assert isinstance(data["normalized"], bool)
        assert data["deduped_of"] is None
    finally:
        client.__exit__(None, None, None)


def test_missing_required_field_returns_422(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        payload = _valid_payload()
        del payload["agent_name"]
        r = client.post("/api/v1/captures/v2", json=payload)
        assert r.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_unknown_source_returns_422(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(source="L9_imaginary"),
        )
        assert r.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_unknown_top_level_extra_returns_422(tmp_path, monkeypatch):
    """Schema is frozen — typos / version mismatches must fail loudly."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        payload = _valid_payload()
        payload["future_experimental_field"] = "should be rejected"
        r = client.post("/api/v1/captures/v2", json=payload)
        assert r.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_invalid_direction_returns_422(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/captures/v2", json=_valid_payload(direction="push"))
        assert r.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_negative_capture_time_ns_returns_422(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post(
            "/api/v1/captures/v2", json=_valid_payload(capture_time_ns=-1)
        )
        assert r.status_code == 422
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Persistence (via legacy raw_captures path until T-1c)
# ---------------------------------------------------------------------------

def test_v2_event_is_visible_in_v1_list_endpoint(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-visible-1"
        r = client.post(
            "/api/v1/captures/v2", json=_valid_payload(pair_id=pair_id)
        )
        assert r.status_code == 201

        listing = client.get("/api/v1/captures?last=10")
        assert listing.status_code == 200
        rows = listing.json()
        matching = [row for row in rows if row.get("pair_id") == pair_id]
        assert len(matching) == 1, f"expected 1 row for {pair_id}, got {rows}"
    finally:
        client.__exit__(None, None, None)


def test_v2_envelope_exposed_via_native_columns(tmp_path, monkeypatch):
    """Post-T-1d-b: v2 fields live in native columns (migration 0006),
    not inside a meta_json envelope. Readers consult them directly via
    the CaptureRecord response fields."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-meta-1"
        capture_id = new_capture_id()
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                capture_id=capture_id,
                pair_id=pair_id,
                source="L3a_browser_ext",
                agent_name="pce_browser_ext",
                agent_version="2.1.0",
                quality_tier="T1_structured",
                form_id="F1",
                app_name="chatgpt_web",
                layer_meta={
                    "dom.selector": "div.conversation-turn",
                    "custom_nested": {"depth": [1, 2]},
                },
            ),
        )
        assert r.status_code == 201

        pair = client.get(f"/api/v1/captures/pair/{pair_id}")
        assert pair.status_code == 200
        rows = pair.json()
        assert len(rows) == 1
        row = rows[0]

        # v2 fields surface as first-class CaptureRecord columns
        assert row["source"] == "L3a_browser_ext"
        assert row["agent_name"] == "pce_browser_ext"
        assert row["agent_version"] == "2.1.0"
        assert row["quality_tier"] == "T1_structured"
        assert row["form_id"] == "F1"
        assert row["app_name"] == "chatgpt_web"
        assert row["fingerprint"] is not None  # auto-computed
        assert len(row["fingerprint"]) == 64  # sha256 hex
        assert row["capture_time_ns"] is not None

        # layer_meta escape hatch now lives in its own column as JSON text
        assert row["layer_meta_json"] is not None
        layer_meta = json.loads(row["layer_meta_json"])
        assert layer_meta == {
            "dom.selector": "div.conversation-turn",
            "custom_nested": {"depth": [1, 2]},
        }
    finally:
        client.__exit__(None, None, None)


def test_endpoint_url_parsed_into_host_and_path(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-url-1"
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                pair_id=pair_id,
                endpoint="https://api.anthropic.com/v1/messages",
            ),
        )
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        assert rows[0]["host"] == "api.anthropic.com"
        assert rows[0]["path"] == "/v1/messages"
    finally:
        client.__exit__(None, None, None)


def test_response_body_stored(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-body-1"
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                pair_id=pair_id,
                response_body={"answer": "forty-two"},
            ),
        )
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        stored_body = rows[0]["body_text_or_json"]
        assert "forty-two" in stored_body
    finally:
        client.__exit__(None, None, None)


def test_pair_direction_stores_response_body_and_maps_to_conversation(
    tmp_path, monkeypatch
):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-conv-1"
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                pair_id=pair_id,
                direction="pair",
                request_body={"prompt": "q"},
                response_body={"answer": "a"},
            ),
        )
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        assert len(rows) == 1
        # pair → v1 storage direction "conversation"
        assert rows[0]["direction"] == "conversation"
        assert "answer" in rows[0]["body_text_or_json"]
    finally:
        client.__exit__(None, None, None)


def test_request_direction_stores_request_body(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-req-1"
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                pair_id=pair_id,
                direction="request",
                request_body={"model": "gpt-4o", "input": "hi"},
                response_body=None,
            ),
        )
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        assert rows[0]["direction"] == "request"
        assert "gpt-4o" in rows[0]["body_text_or_json"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Source coverage — all 12 UCS layers must be accepted by the OSS server
# ---------------------------------------------------------------------------

def test_all_12_ucs_sources_accepted(tmp_path, monkeypatch):
    """OSS gateway must not gate submissions by source — Pro layers depend on
    this (ADR-010). Gating by source would break the Open Core contract."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        sources = [
            "L0_kernel", "L1_mitm", "L2_frida",
            "L3a_browser_ext", "L3b_electron_preload", "L3c_vscode_ext",
            "L3d_cdp", "L3e_litellm", "L3f_otel",
            "L4a_clipboard", "L4b_accessibility", "L4c_ocr",
        ]
        for source in sources:
            r = client.post(
                "/api/v1/captures/v2",
                json=_valid_payload(
                    source=source,
                    pair_id=f"pair-src-{source.lower()}",
                ),
            )
            assert r.status_code == 201, f"{source} rejected: {r.text}"
    finally:
        client.__exit__(None, None, None)


def test_pro_future_agent_simulated_accepted(tmp_path, monkeypatch):
    """Simulate a submission from a future Pro agent (e.g. pce_agent_frida)."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                source="L2_frida",
                agent_name="pce_agent_frida",
                agent_version="0.1.0",
                layer_meta={
                    "frida.script_version": "1.0.0",
                    "frida.hook_name": "SSL_read",
                    "frida.target_pid": 12345,
                },
            ),
        )
        assert r.status_code == 201
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Fingerprint auto-computation
# ---------------------------------------------------------------------------

def test_fingerprint_auto_computed_when_client_omits(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-fp-1"
        payload = _valid_payload(pair_id=pair_id)
        assert "fingerprint" not in payload
        r = client.post("/api/v1/captures/v2", json=payload)
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        # Native column, no meta_json parsing required.
        assert rows[0]["fingerprint"] is not None
        assert len(rows[0]["fingerprint"]) == 64  # sha256 hex
    finally:
        client.__exit__(None, None, None)


def test_client_supplied_fingerprint_preserved(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-fp-2"
        client_fp = "abc123" + "0" * 58  # 64-char placeholder
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(pair_id=pair_id, fingerprint=client_fp),
        )
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        assert rows[0]["fingerprint"] == client_fp
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Streaming / layer-specific fields
# ---------------------------------------------------------------------------

def test_streaming_and_stream_chunks_preserved(tmp_path, monkeypatch):
    """Post-T-1d-b: streaming + stream_chunks + capture_host have no native
    columns yet; they live in meta_json under stable namespaced keys."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-stream-1"
        chunks = [{"delta": "hi"}, {"delta": " there"}, {"delta": "!"}]
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                pair_id=pair_id,
                streaming=True,
                stream_chunks=chunks,
            ),
        )
        assert r.status_code == 201

        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        meta = json.loads(rows[0]["meta_json"])
        assert meta["v2_streaming"] is True
        assert meta["v2_stream_chunks"] == chunks
        assert meta["v2_capture_host"] == "test-host#1"
    finally:
        client.__exit__(None, None, None)


def test_session_hint_preserved(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        pair_id = "pair-session-1"
        r = client.post(
            "/api/v1/captures/v2",
            json=_valid_payload(
                pair_id=pair_id,
                session_hint="conv-abc-123",
            ),
        )
        assert r.status_code == 201
        rows = client.get(f"/api/v1/captures/pair/{pair_id}").json()
        assert rows[0]["session_hint"] == "conv-abc-123"
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# v1 gateway auto-upgrade (UCS §5.1 — v1 endpoint populates v2 native cols)
# ---------------------------------------------------------------------------

def test_v1_endpoint_populates_v2_native_columns(tmp_path, monkeypatch):
    """Post-T-1d-b2 contract: legacy producers hitting /api/v1/captures now
    get their rows tagged with v2 native columns (source, agent_*,
    capture_time_ns, fingerprint) so the Query API treats v1 and v2 rows
    uniformly. This is the 'gateway auto-upgrade' mentioned in UCS §5.1."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post(
            "/api/v1/captures",
            json={
                "source_type": "proxy",
                "source_name": "mitmproxy-addon",
                "direction": "request",
                "provider": "openai",
                "host": "api.openai.com",
                "path": "/v1/chat/completions",
                "method": "POST",
                "headers_json": "{}",
                "body_json": '{"model":"gpt-4o"}',
                "body_format": "json",
                "pair_id": "pair-v1-upgrade",
            },
        )
        assert r.status_code == 201

        rows = client.get("/api/v1/captures/pair/pair-v1-upgrade").json()
        assert len(rows) == 1
        row = rows[0]
        # v2 native columns populated via v1→v2 mapping
        assert row["source"] == "L1_mitm"
        assert row["agent_name"] == "mitmproxy-addon"
        assert row["agent_version"]  # pce __version__ string
        assert row["capture_time_ns"] is not None and row["capture_time_ns"] > 0
        assert row["fingerprint"] is not None
        assert len(row["fingerprint"]) == 64
        assert row["quality_tier"] == "T1_structured"
    finally:
        client.__exit__(None, None, None)


def test_v1_browser_extension_maps_to_l3a(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post(
            "/api/v1/captures",
            json={
                "source_type": "browser_extension",
                "source_name": "chrome-ext",
                "direction": "conversation",
                "provider": "anthropic",
                "host": "claude.ai",
                "path": "/chat",
                "method": "GET",
                "headers_json": "{}",
                "body_json": "{}",
                "body_format": "json",
                "pair_id": "pair-v1-ext-1",
            },
        )
        assert r.status_code == 201

        rows = client.get("/api/v1/captures/pair/pair-v1-ext-1").json()
        assert rows[0]["source"] == "L3a_browser_ext"
        assert rows[0]["agent_name"] == "chrome-ext"


    finally:
        client.__exit__(None, None, None)


def test_v1_unknown_source_type_falls_back_to_l1(tmp_path, monkeypatch):
    """Defensive: an unrecognised source_type shouldn't leave the row
    with a NULL source — it falls back to L1_mitm, matching
    from_v1_capture's behaviour."""
    client, _ = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post(
            "/api/v1/captures",
            json={
                "source_type": "mystery_source",
                "direction": "request",
                "provider": "unknown",
                "host": "",
                "path": "",
                "method": "",
                "headers_json": "{}",
                "body_json": "",
                "body_format": "json",
                "pair_id": "pair-v1-mystery",
            },
        )
        assert r.status_code == 201

        rows = client.get("/api/v1/captures/pair/pair-v1-mystery").json()
        assert rows[0]["source"] == "L1_mitm"
    finally:
        client.__exit__(None, None, None)
