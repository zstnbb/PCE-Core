# SPDX-License-Identifier: Apache-2.0
"""T-1d-a storage tests: ``insert_capture`` + ``CaptureRecord`` expose the
CaptureEvent v2 native columns added by migration 0006 (UCS §5.5).

These tests are deliberately narrow — they verify the contract between the
storage helper and the Query API without touching the v2 ingest endpoint
(which still writes via ``meta_json`` until T-1d-b cuts it over).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reloaded_db(tmp_path: Path, monkeypatch):
    """Reload pce_core with an isolated data dir, apply migrations, yield db module."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PCE_RETENTION_DAYS", raising=False)

    import pce_core.config as _cfg
    import pce_core.db as _db

    importlib.reload(_cfg)
    importlib.reload(_db)

    _db.init_db()
    return _db


# ---------------------------------------------------------------------------
# insert_capture writes native columns
# ---------------------------------------------------------------------------

class TestInsertCaptureWritesNativeColumns:
    def test_v2_kwargs_land_in_native_columns(self, reloaded_db):
        capture_id = reloaded_db.insert_capture(
            direction="response",
            pair_id="pair-native-1",
            host="api.openai.com",
            path="/v1/chat/completions",
            method="POST",
            provider="openai",
            model_name="gpt-4o",
            # v2 native columns
            source="L1_mitm",
            agent_name="pce_proxy",
            agent_version="1.0.0",
            capture_time_ns=1_700_000_000_000_000_000,
            quality_tier="T1_structured",
            fingerprint="f" * 64,
            form_id="F1",
            app_name="chatgpt_web",
            layer_meta_json='{"mitm.flow_id": "flow-1"}',
        )
        assert capture_id is not None

        conn = sqlite3.connect(reloaded_db.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT source, agent_name, agent_version, capture_time_ns, "
                "quality_tier, fingerprint, form_id, app_name, layer_meta_json "
                "FROM raw_captures WHERE id = ?",
                (capture_id,),
            ).fetchone()
            assert row is not None
            assert row["source"] == "L1_mitm"
            assert row["agent_name"] == "pce_proxy"
            assert row["agent_version"] == "1.0.0"
            assert row["capture_time_ns"] == 1_700_000_000_000_000_000
            assert row["quality_tier"] == "T1_structured"
            assert row["fingerprint"] == "f" * 64
            assert row["form_id"] == "F1"
            assert row["app_name"] == "chatgpt_web"
            assert row["layer_meta_json"] == '{"mitm.flow_id": "flow-1"}'
        finally:
            conn.close()

    def test_quality_tier_default_when_omitted(self, reloaded_db):
        """insert_capture default for quality_tier should mirror the DB
        default so v1 callers don't end up with NULL."""
        capture_id = reloaded_db.insert_capture(
            direction="request",
            pair_id="pair-native-default",
            host="",
            path="",
            method="",
            provider="openai",
        )
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT quality_tier, source FROM raw_captures WHERE id = ?",
                (capture_id,),
            ).fetchone()
            assert row["quality_tier"] == "T1_structured"
            # v1 callers that don't set source → NULL in the column
            assert row["source"] is None
        finally:
            conn.close()

    def test_legacy_v1_insert_still_works_without_v2_kwargs(self, reloaded_db):
        """Existing v1 producers (proxy addon, browser ext, mcp) don't pass v2
        kwargs. That path must continue to succeed and return a capture_id."""
        capture_id = reloaded_db.insert_capture(
            direction="request",
            pair_id="pair-legacy-1",
            host="api.anthropic.com",
            path="/v1/messages",
            method="POST",
            provider="anthropic",
            headers_redacted_json="{}",
            body_text_or_json='{"model":"claude-3","messages":[]}',
        )
        assert capture_id is not None

    def test_all_12_v2_source_values_round_trip(self, reloaded_db):
        sources = [
            "L0_kernel", "L1_mitm", "L2_frida",
            "L3a_browser_ext", "L3b_electron_preload", "L3c_vscode_ext",
            "L3d_cdp", "L3e_litellm", "L3f_otel",
            "L4a_clipboard", "L4b_accessibility", "L4c_ocr",
        ]
        # The storage layer uses the v1 direction vocabulary; the v2
        # ``pair`` direction is converted to ``conversation`` by the
        # server-level adapter before it reaches insert_capture (see
        # _v2_event_to_insert_kwargs in pce_core/server.py).
        for idx, source in enumerate(sources):
            cid = reloaded_db.insert_capture(
                direction="conversation",
                pair_id=f"pair-src-{idx}",
                host="", path="", method="", provider="unknown",
                source=source,
                agent_name=f"test-agent-{idx}",
                agent_version="0.0.1",
                capture_time_ns=1_700_000_000_000_000_000 + idx,
            )
            assert cid is not None

        conn = sqlite3.connect(reloaded_db.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT source FROM raw_captures WHERE source IS NOT NULL "
                "ORDER BY capture_time_ns"
            ).fetchall()
            stored = [r["source"] for r in rows]
            assert stored == sources
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Query API surfaces the new fields
# ---------------------------------------------------------------------------

class TestQueryAPIExposesNativeColumns:
    def test_pair_endpoint_returns_v2_columns_when_present(
        self, tmp_path, monkeypatch
    ):
        # Reload server with isolated DB so we hit a freshly-migrated one
        monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
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
        with client:
            cid = _db.insert_capture(
                direction="response",
                pair_id="pair-api-1",
                host="api.openai.com",
                path="/v1/chat/completions",
                method="POST",
                provider="openai",
                source="L1_mitm",
                agent_name="pce_proxy",
                agent_version="1.0.0",
                capture_time_ns=1_700_000_000_123_456_789,
                quality_tier="T1_structured",
                fingerprint="a" * 64,
                form_id="F1",
                app_name="chatgpt_web",
                layer_meta_json='{"mitm.flow_id":"flow-1"}',
            )
            assert cid is not None

            r = client.get("/api/v1/captures/pair/pair-api-1")
            assert r.status_code == 200
            rows = r.json()
            assert len(rows) == 1
            row = rows[0]

            # v2 fields are now first-class — no meta_json parsing required
            assert row["source"] == "L1_mitm"
            assert row["agent_name"] == "pce_proxy"
            assert row["agent_version"] == "1.0.0"
            assert row["capture_time_ns"] == 1_700_000_000_123_456_789
            assert row["quality_tier"] == "T1_structured"
            assert row["fingerprint"] == "a" * 64
            assert row["form_id"] == "F1"
            assert row["app_name"] == "chatgpt_web"
            assert row["layer_meta_json"] == '{"mitm.flow_id":"flow-1"}'

    def test_pair_endpoint_returns_v2_columns_for_v1_upgraded_rows(
        self, tmp_path, monkeypatch
    ):
        """Post-T-1d-b2: v1 /api/v1/captures rows are 'auto-upgraded' —
        source/agent_name/agent_version/capture_time_ns/fingerprint get
        populated from the v1 payload (see _V1_TO_V2_SOURCE in server.py).
        Only columns without a v1→v2 mapping stay NULL (form_id, app_name)."""
        monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
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
        with client:
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
                    "body_json": '{"model":"gpt-4"}',
                    "body_format": "json",
                    "pair_id": "pair-v1-legacy",
                },
            )
            assert r.status_code == 201

            rows = client.get("/api/v1/captures/pair/pair-v1-legacy").json()
            assert len(rows) == 1
            row = rows[0]

            # v2 columns derivable from a v1 payload are populated
            assert row.get("source") == "L1_mitm"
            assert row.get("agent_name") == "mitmproxy-addon"
            assert row.get("agent_version")  # pce __version__
            assert row.get("capture_time_ns") is not None
            assert row.get("fingerprint") is not None
            assert len(row.get("fingerprint")) == 64
            assert row.get("quality_tier") == "T1_structured"

            # Columns a v1 producer has no way of setting remain NULL —
            # form_id (needs Supervisor) and app_name (needs process hint).
            assert row.get("form_id") is None
            assert row.get("app_name") is None
            # Pure v1 path doesn't use layer_meta either.
            assert row.get("layer_meta_json") is None


# ---------------------------------------------------------------------------
# CaptureRecord pydantic model
# ---------------------------------------------------------------------------

class TestCaptureRecordModel:
    def test_parse_row_with_v2_fields(self):
        from pce_core.models import CaptureRecord

        row = {
            "id": "cap-1", "created_at": 123.4, "source_id": "proxy",
            "direction": "response", "pair_id": "pair-1",
            "source": "L1_mitm", "agent_name": "pce_proxy",
            "agent_version": "1.0.0", "capture_time_ns": 1_700_000_000,
            "quality_tier": "T1_structured",
            "fingerprint": "b" * 64,
            "form_id": "F1", "app_name": "chatgpt_web",
            "layer_meta_json": '{"k":"v"}',
        }
        rec = CaptureRecord(**row)
        assert rec.source == "L1_mitm"
        assert rec.agent_name == "pce_proxy"
        assert rec.capture_time_ns == 1_700_000_000
        assert rec.fingerprint == "b" * 64

    def test_parse_row_without_v2_fields_keeps_defaults(self):
        from pce_core.models import CaptureRecord

        row = {
            "id": "cap-1", "created_at": 123.4, "source_id": "proxy",
            "direction": "response", "pair_id": "pair-1",
        }
        rec = CaptureRecord(**row)
        assert rec.source is None
        assert rec.agent_name is None
        assert rec.capture_time_ns is None
        assert rec.quality_tier is None
        assert rec.fingerprint is None


# ---------------------------------------------------------------------------
# Indexes from migration 0006 are queryable
# ---------------------------------------------------------------------------

class TestIndexUsable:
    def test_fingerprint_lookup_finds_row(self, reloaded_db):
        fp = "deadbeef" + "0" * 56
        cid = reloaded_db.insert_capture(
            direction="conversation", pair_id="idx-pair",
            host="", path="", method="", provider="unknown",
            source="L1_mitm", agent_name="x", agent_version="1",
            capture_time_ns=1, fingerprint=fp,
        )
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            row = conn.execute(
                "SELECT id FROM raw_captures WHERE fingerprint = ?", (fp,)
            ).fetchone()
            assert row is not None and row[0] == cid
        finally:
            conn.close()

    def test_source_filter_finds_all_l2_frida_rows(self, reloaded_db):
        for i in range(3):
            reloaded_db.insert_capture(
                direction="conversation", pair_id=f"frida-{i}",
                host="", path="", method="", provider="unknown",
                source="L2_frida", agent_name="pce_agent_frida",
                agent_version="0.1", capture_time_ns=i,
            )
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM raw_captures WHERE source = ?",
                ("L2_frida",),
            ).fetchone()[0]
            assert count == 3
        finally:
            conn.close()
