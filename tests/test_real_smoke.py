"""Real-environment smoke tests for PCE Core capture pipeline.

End-to-end tests that exercise the full FastAPI app with real HTTP
requests via TestClient.  Validates capture ingest, normalization,
PAC serving, domain CRUD, concurrency, edge cases.

Run:
    pytest tests/test_real_smoke.py -v
"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

# Isolate DB before any pce_core imports
_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import init_db
init_db()

from fastapi.testclient import TestClient
from pce_core.server import app


# ---------------------------------------------------------------------------
# Module-scoped client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_server():
    """Return a TestClient wrapping the real Core API app."""
    with TestClient(app) as client:
        yield client


# ═══════════════════════════════════════════════════════════════════════
# 1. Health & Discovery
# ═══════════════════════════════════════════════════════════════════════

class TestLiveHealth:

    def test_health_endpoint(self, live_server):
        r = live_server.get("/api/v1/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        assert "db_path" in d

    def test_capabilities_endpoint(self, live_server):
        r = live_server.get("/api/v1/capabilities")
        assert r.status_code == 200
        caps = r.json()["capabilities"]
        ids = {c["id"] for c in caps}
        assert "core" in ids
        assert "proxy" in ids

    def test_capture_health(self, live_server):
        r = live_server.get("/api/v1/capture-health")
        assert r.status_code == 200
        assert "channels" in r.json()


# ═══════════════════════════════════════════════════════════════════════
# 2. PAC File Serving (live HTTP)
# ═══════════════════════════════════════════════════════════════════════

class TestLivePAC:

    def test_pac_file_served_over_http(self, live_server):
        r = live_server.get("/proxy.pac")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ns-proxy-autoconfig")
        pac = r.text
        assert "FindProxyForURL" in pac
        assert "api.openai.com" in pac
        # Verify it's valid JS-ish (basic check)
        assert pac.count("function") >= 1
        assert pac.count("return") >= 2

    def test_pac_info_over_http(self, live_server):
        r = live_server.get("/api/v1/pac")
        assert r.status_code == 200
        d = r.json()
        assert "pac_url" in d
        assert d["pac_url"].endswith("/proxy.pac")


# ═══════════════════════════════════════════════════════════════════════
# 3. Domain Management (live HTTP CRUD)
# ═══════════════════════════════════════════════════════════════════════

class TestLiveDomains:

    def test_domains_crud_lifecycle(self, live_server):
        c = live_server

        # List initial
        r = c.get("/api/v1/domains")
        assert r.status_code == 200
        initial = r.json()
        assert "static_domains" in initial
        assert len(initial["static_domains"]) > 30

        # Add
        r = c.post("/api/v1/domains", json={
            "domain": "live-smoke-test.ai",
            "source": "test",
            "confidence": "high",
        })
        assert r.status_code == 201
        assert r.json()["ok"] is True

        # Verify appears in list
        r = c.get("/api/v1/domains")
        custom = [d["domain"] for d in r.json()["custom_domains"]]
        assert "live-smoke-test.ai" in custom

        # Verify appears in PAC
        r = c.get("/proxy.pac")
        assert "live-smoke-test.ai" in r.text

        # Delete
        r = c.delete("/api/v1/domains/live-smoke-test.ai")
        assert r.status_code == 200

        # Refresh
        r = c.post("/api/v1/domains/refresh")
        assert r.status_code == 200

    def test_add_empty_domain_rejected(self, live_server):
        r = live_server.post("/api/v1/domains", json={
            "domain": "  ",
            "source": "test",
        })
        assert r.status_code in (400, 422)


# ═══════════════════════════════════════════════════════════════════════
# 4. Capture Ingest → Normalize (live HTTP)
# ═══════════════════════════════════════════════════════════════════════

class TestLiveCapture:

    def test_openai_capture_and_normalize_live(self, live_server):
        c = live_server

        # Get initial stats
        r0 = c.get("/api/v1/stats")
        initial_total = r0.json()["total_captures"]

        # Ingest request
        req_body = json.dumps({
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the speed of light?"},
            ],
        })
        r1 = c.post("/api/v1/captures", json={
            "direction": "request",
            "source_type": "proxy",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "body_json": req_body,
            "body_format": "json",
        })
        assert r1.status_code == 201
        pair_id = r1.json()["pair_id"]

        # Ingest response (same pair_id)
        resp_body = json.dumps({
            "id": "chatcmpl-smoke",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "The speed of light in a vacuum is approximately 299,792,458 meters per second.",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 18, "total_tokens": 38},
        })
        r2 = c.post("/api/v1/captures", json={
            "direction": "response",
            "source_type": "proxy",
            "pair_id": pair_id,
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "status_code": 200,
            "body_json": resp_body,
            "body_format": "json",
        })
        assert r2.status_code == 201
        assert r2.json()["pair_id"] == pair_id

        # Verify captures are in DB
        r3 = c.get(f"/api/v1/captures/pair/{pair_id}")
        assert r3.status_code == 200
        pair = r3.json()
        assert len(pair) == 2
        dirs = {c_["direction"] for c_ in pair}
        assert dirs == {"request", "response"}

        # Verify auto-normalization created session + messages
        r4 = c.get("/api/v1/sessions", params={"last": 10})
        assert r4.status_code == 200
        sessions = r4.json()
        assert len(sessions) >= 1

        # Find our session (provider=openai, should be recent)
        openai_sess = [s for s in sessions if s.get("provider") == "openai"]
        assert len(openai_sess) >= 1
        sess = openai_sess[0]

        # Verify messages
        r5 = c.get(f"/api/v1/sessions/{sess['id']}/messages")
        assert r5.status_code == 200
        msgs = r5.json()
        assert len(msgs) >= 2

        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles

        # Deep content check
        user_msg = [m for m in msgs if m["role"] == "user"][-1]
        assert "speed of light" in user_msg["content_text"]
        asst_msg = [m for m in msgs if m["role"] == "assistant"][-1]
        assert "299,792,458" in asst_msg["content_text"]

        # Stats should have increased
        r6 = c.get("/api/v1/stats")
        assert r6.json()["total_captures"] > initial_total

    def test_anthropic_capture_live(self, live_server):
        c = live_server

        req_body = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "What is pi?"}],
        })
        r1 = c.post("/api/v1/captures", json={
            "direction": "request",
            "source_type": "proxy",
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "method": "POST",
            "provider": "anthropic",
            "model_name": "claude-3-5-sonnet-20241022",
            "body_json": req_body,
            "body_format": "json",
        })
        assert r1.status_code == 201
        pair_id = r1.json()["pair_id"]

        resp_body = json.dumps({
            "id": "msg-smoke",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Pi is approximately 3.14159265358979."}],
            "model": "claude-3-5-sonnet-20241022",
            "usage": {"input_tokens": 6, "output_tokens": 15},
        })
        r2 = c.post("/api/v1/captures", json={
            "direction": "response",
            "source_type": "proxy",
            "pair_id": pair_id,
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "method": "POST",
            "provider": "anthropic",
            "model_name": "claude-3-5-sonnet-20241022",
            "status_code": 200,
            "body_json": resp_body,
            "body_format": "json",
        })
        assert r2.status_code == 201

        # Verify normalization
        r3 = c.get("/api/v1/sessions", params={"last": 10, "provider": "anthropic"})
        assert r3.status_code == 200
        sessions = r3.json()
        assert len(sessions) >= 1
        msgs = c.get(f"/api/v1/sessions/{sessions[0]['id']}/messages").json()
        asst = [m for m in msgs if m["role"] == "assistant"]
        assert len(asst) >= 1
        assert "3.14159" in asst[-1]["content_text"]

    def test_sse_streaming_capture_live(self, live_server):
        c = live_server

        req_body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Say 'alpha beta gamma'"}],
            "stream": True,
        })
        r1 = c.post("/api/v1/captures", json={
            "direction": "request",
            "source_type": "proxy",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "body_json": req_body,
            "body_format": "json",
        })
        pair_id = r1.json()["pair_id"]

        sse_body = (
            'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":"alpha"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":" beta"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":" gamma"},"index":0}]}\n\n'
            'data: [DONE]\n\n'
        )
        r2 = c.post("/api/v1/captures", json={
            "direction": "response",
            "source_type": "proxy",
            "pair_id": pair_id,
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "status_code": 200,
            "body_json": sse_body,
            "body_format": "text",
            "headers_json": '{"content-type":"text/event-stream"}',
        })
        assert r2.status_code == 201

        # Verify SSE assembled into coherent message
        sessions = c.get("/api/v1/sessions", params={"last": 10, "provider": "openai"}).json()
        assert len(sessions) >= 1
        msgs = c.get(f"/api/v1/sessions/{sessions[0]['id']}/messages").json()
        asst = [m for m in msgs if m["role"] == "assistant"]
        assert len(asst) >= 1
        text = asst[-1]["content_text"]
        assert "alpha" in text
        assert "gamma" in text

    def test_browser_extension_conversation_capture_live(self, live_server):
        """Simulate a DOM-extracted conversation from browser extension."""
        conv_body = json.dumps({
            "messages": [
                {"role": "user", "content": "Explain recursion"},
                {"role": "assistant", "content": "Recursion is when a function calls itself."},
            ],
            "url": "https://chatgpt.com/c/abc-123",
            "title": "Recursion explained",
            "provider": "openai",
        })
        r = live_server.post("/api/v1/captures", json={
            "direction": "conversation",
            "source_type": "browser_extension",
            "host": "chatgpt.com",
            "path": "/c/abc-123",
            "provider": "openai",
            "body_json": conv_body,
            "body_format": "json",
            "session_hint": "abc-123",
        })
        assert r.status_code == 201

    def test_nonexistent_pair_404(self, live_server):
        r = live_server.get("/api/v1/captures/pair/does-not-exist-99999")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# 5. Concurrent Ingest Stability
# ═══════════════════════════════════════════════════════════════════════

class TestLiveConcurrency:

    def test_concurrent_captures_no_crash(self, live_server):
        """Send 20 concurrent capture pairs to verify thread safety."""
        c = live_server
        errors = []

        def send_pair(i):
            try:
                req = json.dumps({
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": f"Concurrent test {i}"}],
                })
                r1 = c.post("/api/v1/captures", json={
                    "direction": "request",
                    "source_type": "proxy",
                    "host": "api.openai.com",
                    "path": "/v1/chat/completions",
                    "method": "POST",
                    "provider": "openai",
                    "body_json": req,
                    "body_format": "json",
                })
                if r1.status_code != 201:
                    errors.append(f"req {i}: {r1.status_code}")
                    return

                pair_id = r1.json()["pair_id"]
                resp = json.dumps({
                    "choices": [{"message": {"role": "assistant", "content": f"Response {i}"}}],
                    "model": "gpt-4",
                })
                r2 = c.post("/api/v1/captures", json={
                    "direction": "response",
                    "source_type": "proxy",
                    "pair_id": pair_id,
                    "host": "api.openai.com",
                    "path": "/v1/chat/completions",
                    "method": "POST",
                    "provider": "openai",
                    "status_code": 200,
                    "body_json": resp,
                    "body_format": "json",
                })
                if r2.status_code != 201:
                    errors.append(f"resp {i}: {r2.status_code}")
            except Exception as e:
                errors.append(f"pair {i}: {e}")

        threads = [threading.Thread(target=send_pair, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Concurrent capture errors: {errors}"

        # Verify server still healthy after burst
        r = live_server.get("/api/v1/health")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# 6. Error Handling & Edge Cases
# ═══════════════════════════════════════════════════════════════════════

class TestLiveEdgeCases:

    def test_empty_body_capture(self, live_server):
        """GET request with empty body should still be accepted."""
        r = live_server.post("/api/v1/captures", json={
            "direction": "request",
            "source_type": "proxy",
            "host": "api.openai.com",
            "path": "/v1/models",
            "method": "GET",
            "provider": "openai",
            "body_json": "",
            "body_format": "text",
        })
        assert r.status_code == 201

    def test_unicode_content_preserved(self, live_server):
        c = live_server
        req = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "翻译：你好世界 🌍"}],
        })
        r1 = c.post("/api/v1/captures", json={
            "direction": "request",
            "source_type": "proxy",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "body_json": req,
            "body_format": "json",
        })
        assert r1.status_code == 201
        pair_id = r1.json()["pair_id"]

        resp = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Hello World 🌍 — 你好世界"}}],
            "model": "gpt-4",
        })
        r2 = c.post("/api/v1/captures", json={
            "direction": "response",
            "source_type": "proxy",
            "pair_id": pair_id,
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "status_code": 200,
            "body_json": resp,
            "body_format": "json",
        })
        assert r2.status_code == 201

        # Verify raw capture preserves unicode (body stored as JSON string,
        # may be ascii-escaped — decode to verify actual content)
        pair = c.get(f"/api/v1/captures/pair/{pair_id}").json()
        req_row = [r_ for r_ in pair if r_["direction"] == "request"][0]
        decoded_body = json.loads(req_row["body_text_or_json"])
        assert "你好世界" in decoded_body["messages"][0]["content"]
        assert "🌍" in decoded_body["messages"][0]["content"]

        # Verify normalized messages preserve unicode
        sessions = c.get("/api/v1/sessions", params={"last": 5, "provider": "openai"}).json()
        if sessions:
            msgs = c.get(f"/api/v1/sessions/{sessions[0]['id']}/messages").json()
            asst = [m for m in msgs if m["role"] == "assistant"]
            if asst:
                assert "你好世界" in asst[-1]["content_text"]

    def test_large_body_capture(self, live_server):
        """Large body (500KB) should be accepted without timeout."""
        big_content = "x" * 500_000
        req = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": big_content}],
        })
        r = live_server.post("/api/v1/captures", json={
            "direction": "request",
            "source_type": "proxy",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "body_json": req,
            "body_format": "json",
        })
        assert r.status_code == 201

    def test_invalid_json_body_accepted(self, live_server):
        """Non-JSON body_json string should still be stored."""
        r = live_server.post("/api/v1/captures", json={
            "direction": "response",
            "source_type": "proxy",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "method": "POST",
            "provider": "openai",
            "status_code": 200,
            "body_json": "data: [DONE]\n\nthis is SSE not JSON",
            "body_format": "text",
        })
        assert r.status_code == 201
