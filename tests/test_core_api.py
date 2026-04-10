"""Tests for pce_core FastAPI Ingest & Query API using TestClient."""

import sys
import os
import tempfile
from pathlib import Path

# Use a temp DB for testing – must be set BEFORE importing pce_core
_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from pce_core.server import app


def test_all():
    # Use context manager so that the lifespan (init_db) runs
    with TestClient(app) as client:
        _run_health(client)
        _run_ingest_and_query(client)
    print("\n=== All pce_core API tests passed ===")


def _run_health(client: TestClient):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    print("[PASS] GET /api/v1/health")


def _run_ingest_and_query(client: TestClient):
    # 1. Ingest a request capture
    payload = {
        "source_type": "proxy",
        "source_name": "mitmproxy",
        "direction": "request",
        "provider": "openai",
        "host": "api.openai.com",
        "path": "/v1/chat/completions",
        "method": "POST",
        "model_name": "gpt-4",
        "headers_json": '{"Content-Type": "application/json", "Authorization": "REDACTED"}',
        "body_json": '{"model":"gpt-4","messages":[{"role":"user","content":"hello"}]}',
        "body_format": "json",
    }
    resp = client.post("/api/v1/captures", json=payload)
    assert resp.status_code == 201, f"Ingest failed: {resp.text}"
    data = resp.json()
    assert "id" in data
    assert "pair_id" in data
    pair_id = data["pair_id"]
    print(f"[PASS] POST /api/v1/captures (request) → id={data['id'][:8]}")

    # 2. Ingest a response capture with same pair_id
    payload2 = {
        "source_type": "proxy",
        "source_name": "mitmproxy",
        "direction": "response",
        "pair_id": pair_id,
        "provider": "openai",
        "host": "api.openai.com",
        "path": "/v1/chat/completions",
        "method": "POST",
        "model_name": "gpt-4",
        "status_code": 200,
        "latency_ms": 450.2,
        "headers_json": '{"Content-Type": "application/json"}',
        "body_json": '{"choices":[{"message":{"content":"Hi there!"}}]}',
        "body_format": "json",
    }
    resp2 = client.post("/api/v1/captures", json=payload2)
    assert resp2.status_code == 201
    assert resp2.json()["pair_id"] == pair_id
    print("[PASS] POST /api/v1/captures (response) → same pair_id")

    # 3. Ingest from browser extension
    payload3 = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext",
        "direction": "conversation",
        "provider": "anthropic",
        "host": "claude.ai",
        "path": "/",
        "method": "GET",
        "headers_json": "{}",
        "body_json": '{"user":"what is AI?","assistant":"AI is..."}',
        "meta": {"page_url": "https://claude.ai/chat/abc123", "tab_id": 42},
    }
    resp3 = client.post("/api/v1/captures", json=payload3)
    assert resp3.status_code == 201
    assert resp3.json()["source_id"] == "browser-extension-default"
    print("[PASS] POST /api/v1/captures (browser_extension)")

    # 4. GET /api/v1/captures
    resp4 = client.get("/api/v1/captures?last=10")
    assert resp4.status_code == 200
    captures = resp4.json()
    assert len(captures) >= 3
    print(f"[PASS] GET /api/v1/captures → {len(captures)} records")

    # 5. GET /api/v1/captures with filter
    resp5 = client.get("/api/v1/captures?provider=anthropic")
    assert resp5.status_code == 200
    assert len(resp5.json()) >= 1
    assert all(c["provider"] == "anthropic" for c in resp5.json())
    print(f"[PASS] GET /api/v1/captures?provider=anthropic → {len(resp5.json())} record(s)")

    # 6. GET /api/v1/captures with source_type filter
    resp5b = client.get("/api/v1/captures?source_type=browser_extension")
    assert resp5b.status_code == 200
    assert len(resp5b.json()) >= 1
    print(f"[PASS] GET /api/v1/captures?source_type=browser_extension → {len(resp5b.json())} record(s)")

    # 7. GET /api/v1/captures/pair/{pair_id}
    resp6 = client.get(f"/api/v1/captures/pair/{pair_id}")
    assert resp6.status_code == 200
    pair_captures = resp6.json()
    assert len(pair_captures) == 2
    directions = {c["direction"] for c in pair_captures}
    assert directions == {"request", "response"}
    print("[PASS] GET /api/v1/captures/pair/{pair_id} → 2 records")

    # 8. GET /api/v1/stats
    resp7 = client.get("/api/v1/stats")
    assert resp7.status_code == 200
    stats = resp7.json()
    assert stats["total_captures"] >= 3
    assert stats["by_provider"].get("openai", 0) >= 2
    assert stats["by_provider"].get("anthropic", 0) >= 1
    print(f"[PASS] GET /api/v1/stats → total={stats['total_captures']}")

    # 9. 404 for non-existent pair
    resp8 = client.get("/api/v1/captures/pair/nonexistent")
    assert resp8.status_code == 404
    print("[PASS] GET /api/v1/captures/pair/nonexistent → 404")


if __name__ == "__main__":
    test_all()
