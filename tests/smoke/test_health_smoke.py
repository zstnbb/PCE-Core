# SPDX-License-Identifier: Apache-2.0
"""Smoke: /api/v1/health shape and content guarantees.

Verifies:
1. The detailed health snapshot returns every field that TASK-002 §5.1
   promises.
2. Well-known sources are always present (proxy / browser_extension / mcp).
3. Pipeline error counts surface recorded errors.
4. Latency percentiles compute correctly from recorded responses.
5. Storage counters match real row counts.
"""

from __future__ import annotations

import time
from pathlib import Path

from pce_core import db as pce_db
from pce_core.db import (
    SOURCE_BROWSER_EXT,
    SOURCE_MCP,
    SOURCE_PROXY,
    get_detailed_health,
    insert_capture,
    insert_session,
    insert_message,
    record_pipeline_error,
    new_pair_id,
)
from pce_core.migrations import EXPECTED_SCHEMA_VERSION


def _ingest_pair(
    db: Path,
    source_id: str,
    latency_ms: float,
    host: str = "api.openai.com",
    provider: str = "openai",
) -> str:
    pid = new_pair_id()
    insert_capture(
        direction="request", pair_id=pid, host=host, path="/v1/chat/completions",
        method="POST", provider=provider, model_name="gpt-4",
        headers_redacted_json='{"Authorization":"REDACTED"}',
        body_text_or_json='{"model":"gpt-4","messages":[{"role":"user","content":"hi"}]}',
        body_format="json", source_id=source_id, db_path=db,
    )
    insert_capture(
        direction="response", pair_id=pid, host=host, path="/v1/chat/completions",
        method="POST", provider=provider, model_name="gpt-4", status_code=200,
        latency_ms=latency_ms,
        headers_redacted_json="{}",
        body_text_or_json='{"choices":[{"message":{"role":"assistant","content":"hello"}}]}',
        body_format="json", source_id=source_id, db_path=db,
    )
    return pid


def test_detailed_health_has_required_top_level_fields(fresh_db: Path):
    snap = get_detailed_health(db_path=fresh_db)

    assert snap["status"] == "ok"
    assert snap["schema_version"] == EXPECTED_SCHEMA_VERSION
    assert snap["expected_schema_version"] == EXPECTED_SCHEMA_VERSION
    assert isinstance(snap["capture_payload_version"], int)
    assert isinstance(snap["timestamp"], float)
    assert "sources" in snap and isinstance(snap["sources"], dict)
    assert "pipeline" in snap and isinstance(snap["pipeline"], dict)
    assert "storage" in snap and isinstance(snap["storage"], dict)


def test_well_known_sources_always_present(fresh_db: Path):
    snap = get_detailed_health(db_path=fresh_db)
    required = {SOURCE_PROXY, SOURCE_BROWSER_EXT, SOURCE_MCP}
    present = set(snap["sources"])
    assert required <= present, f"Missing well-known sources: {required - present}"

    for sid in required:
        info = snap["sources"][sid]
        assert info["captures_total"] == 0
        assert info["captures_last_1h"] == 0
        assert info["captures_last_24h"] == 0
        assert info["failures_last_24h"] == 0
        assert info["drop_rate"] == 0.0
        assert info["latency_p50_ms"] is None
        assert info["latency_p95_ms"] is None
        assert info["last_capture_at"] is None


def test_per_source_counters_and_latency(fresh_db: Path):
    # 20 responses at varying latencies so p50/p95 are distinct
    for i in range(20):
        _ingest_pair(fresh_db, SOURCE_PROXY, latency_ms=float(i * 10 + 5))

    snap = get_detailed_health(db_path=fresh_db)
    info = snap["sources"][SOURCE_PROXY]

    assert info["captures_total"] == 40  # 20 pairs × 2 rows
    assert info["captures_last_1h"] == 40
    assert info["captures_last_24h"] == 40

    p50 = info["latency_p50_ms"]
    p95 = info["latency_p95_ms"]
    assert p50 is not None and p95 is not None
    assert 0 < p50 < p95, f"percentiles malformed: p50={p50}, p95={p95}"
    assert info["last_capture_ago_s"] is not None
    assert info["last_capture_ago_s"] < 5.0


def test_ingest_failures_feed_drop_rate(fresh_db: Path):
    # 4 successful captures + 1 recorded ingest failure → drop_rate = 1/5 = 0.2
    for _ in range(4):
        _ingest_pair(fresh_db, SOURCE_MCP, latency_ms=50.0)

    record_pipeline_error(
        "ingest", "simulated insert failure",
        source_id=SOURCE_MCP, pair_id=new_pair_id(),
        details={"reason": "smoke"},
        db_path=fresh_db,
    )

    snap = get_detailed_health(db_path=fresh_db)
    info = snap["sources"][SOURCE_MCP]
    # 4 pairs × 2 rows = 8 captures, 1 failure
    assert info["captures_last_24h"] == 8
    assert info["failures_last_24h"] == 1
    assert info["drop_rate"] == round(1 / 9, 4)


def test_pipeline_error_counts_by_stage(fresh_db: Path):
    for stage in ("normalize", "reconcile", "persist", "fts_index"):
        record_pipeline_error(
            stage, f"synthetic {stage} err",
            source_id=SOURCE_PROXY, db_path=fresh_db,
        )
    record_pipeline_error(
        "normalize", "warning level sample",
        level="WARNING", source_id=SOURCE_PROXY, db_path=fresh_db,
    )

    snap = get_detailed_health(db_path=fresh_db)
    pipe = snap["pipeline"]

    assert pipe["normalizer_errors_last_24h"] == 2  # 1 ERROR + 1 WARNING
    assert pipe["reconciler_errors_last_24h"] == 1
    assert pipe["persist_errors_last_24h"] == 1
    assert pipe["errors_last_24h_by_stage"]["normalize"]["ERROR"] == 1
    assert pipe["errors_last_24h_by_stage"]["normalize"]["WARNING"] == 1


def test_storage_counts_match_reality(fresh_db: Path):
    # Seed a session + a couple of messages
    sid = insert_session(
        source_id=SOURCE_PROXY, started_at=time.time(), provider="openai",
        db_path=fresh_db,
    )
    assert sid is not None
    for role, text in [("user", "hello"), ("assistant", "hi!")]:
        insert_message(
            session_id=sid, ts=time.time(), role=role,
            content_text=text, db_path=fresh_db,
        )
    _ingest_pair(fresh_db, SOURCE_PROXY, latency_ms=20.0)

    snap = get_detailed_health(db_path=fresh_db)
    storage = snap["storage"]
    assert storage["raw_captures_rows"] == 2
    assert storage["sessions_rows"] == 1
    assert storage["messages_rows"] == 2
    assert storage["db_size_bytes"] > 0
    assert storage["db_path"].endswith("smoke.db")


def test_server_health_endpoint_returns_detailed_payload(monkeypatch, tmp_path: Path):
    """The FastAPI /api/v1/health endpoint must surface the detailed snapshot."""
    import os
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))

    # Reimport the server module fresh so DB_PATH/etc. pick up the env.
    import importlib
    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.server as _server
    importlib.reload(_cfg)
    importlib.reload(_db)
    importlib.reload(_server)

    from fastapi.testclient import TestClient

    with TestClient(_server.app) as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        # Legacy-compat fields
        assert data["status"] == "ok"
        assert "version" in data
        assert "db_path" in data
        assert data["total_captures"] == 0
        # New detailed fields
        assert data["schema_version"] == EXPECTED_SCHEMA_VERSION
        assert "sources" in data
        assert "pipeline" in data
        assert "storage" in data

        r2 = client.get("/api/v1/health/migrations")
        assert r2.status_code == 200
        mig = r2.json()
        assert mig["ok"] is True
        assert mig["current_version"] == EXPECTED_SCHEMA_VERSION
        assert len(mig["history"]) >= 1
