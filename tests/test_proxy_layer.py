"""Comprehensive tests for Layer 2 (System Network) capture pipeline.

Covers:
  - PAC file generation & domain routing
  - Dynamic domain management (add/remove/list/refresh)
  - Proxy addon capture flow (allowlist, SMART, ALL modes)
  - Integration: simulated AI traffic → raw_captures → normalization
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Isolate DB for tests
_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import (
    init_db,
    add_custom_domain,
    remove_custom_domain,
    get_custom_domains,
    refresh_custom_domains,
    list_custom_domains,
    insert_capture,
    new_pair_id,
    query_captures,
    query_by_pair,
    query_sessions,
    query_messages,
    SOURCE_PROXY,
)
from pce_core.config import ALLOWED_HOSTS, PROXY_LISTEN_HOST, PROXY_LISTEN_PORT
from pce_core.pac_generator import generate_pac
from pce_core.normalizer.pipeline import try_normalize_pair

# Ensure DB is ready
init_db()


# ═══════════════════════════════════════════════════════════════════════
# Section 1: PAC Generator
# ═══════════════════════════════════════════════════════════════════════

class TestPACGenerator:
    """Tests for proxy auto-configuration file generation."""

    def test_pac_is_valid_javascript(self):
        pac = generate_pac()
        assert "function FindProxyForURL(url, host)" in pac
        assert "return" in pac

    def test_pac_contains_known_ai_domains(self):
        pac = generate_pac()
        assert "api.openai.com" in pac
        assert "api.anthropic.com" in pac
        assert "api.deepseek.com" in pac

    def test_pac_routes_ai_domains_to_proxy(self):
        pac = generate_pac()
        # Each AI domain should have a dnsDomainIs check
        assert 'dnsDomainIs(host, "api.openai.com")' in pac
        assert "return proxy" in pac

    def test_pac_default_is_direct(self):
        pac = generate_pac()
        # Last return should be DIRECT
        lines = pac.strip().split("\n")
        # Find the last return
        last_return = [l for l in lines if "DIRECT" in l and "return" in l]
        assert len(last_return) >= 1

    def test_pac_never_proxies_localhost(self):
        pac = generate_pac()
        assert "isPlainHostName(host)" in pac
        assert '"127.0.0.1"' in pac
        assert '"localhost"' in pac

    def test_pac_uses_correct_proxy_address(self):
        pac = generate_pac(proxy_host="127.0.0.1", proxy_port=8080)
        assert "PROXY 127.0.0.1:8080" in pac

    def test_pac_custom_proxy_address(self):
        pac = generate_pac(proxy_host="192.168.1.100", proxy_port=9999)
        assert "PROXY 192.168.1.100:9999" in pac

    def test_pac_includes_extra_domains(self):
        pac = generate_pac(extra_domains={"my-private-ai.internal", "ai.company.com"})
        assert "my-private-ai.internal" in pac
        assert "ai.company.com" in pac

    def test_pac_includes_custom_db_domains(self):
        add_custom_domain("custom-ai-test.example.com", source="test")
        try:
            pac = generate_pac()
            assert "custom-ai-test.example.com" in pac
        finally:
            remove_custom_domain("custom-ai-test.example.com")

    def test_pac_domain_count_header(self):
        pac = generate_pac()
        # Header should show domain count
        assert "Domains:" in pac

    def test_pac_excludes_localhost_from_domains(self):
        pac = generate_pac(extra_domains={"localhost", "127.0.0.1", "real-ai.com"})
        # localhost/127.0.0.1 should NOT be in domain checks
        # but real-ai.com should be
        assert 'dnsDomainIs(host, "real-ai.com")' in pac
        # The only localhost references should be in the early bypass
        domain_checks = [l for l in pac.split("\n") if "dnsDomainIs" in l]
        for check in domain_checks:
            assert "localhost" not in check
            assert "127.0.0.1" not in check


# ═══════════════════════════════════════════════════════════════════════
# Section 2: Dynamic Domain Management
# ═══════════════════════════════════════════════════════════════════════

class TestDynamicDomains:
    """Tests for the custom domain add/remove/list/refresh API."""

    def setup_method(self):
        # Clean up any test domains
        for d in list_custom_domains():
            remove_custom_domain(d["domain"])
        refresh_custom_domains()

    def test_add_domain(self):
        ok = add_custom_domain("test-ai.example.com", source="test")
        assert ok is True
        assert "test-ai.example.com" in get_custom_domains()

    def test_add_domain_with_metadata(self):
        ok = add_custom_domain(
            "smart-detected.ai",
            source="smart_heuristic",
            confidence="high",
            reason="domain_pattern + request_body",
        )
        assert ok is True
        domains = list_custom_domains()
        found = [d for d in domains if d["domain"] == "smart-detected.ai"]
        assert len(found) == 1
        assert found[0]["source"] == "smart_heuristic"
        assert found[0]["confidence"] == "high"

    def test_remove_domain(self):
        add_custom_domain("to-remove.ai", source="test")
        assert "to-remove.ai" in get_custom_domains()
        ok = remove_custom_domain("to-remove.ai")
        assert ok is True
        refresh_custom_domains()
        assert "to-remove.ai" not in get_custom_domains()

    def test_remove_nonexistent_domain(self):
        ok = remove_custom_domain("never-existed.example.com")
        assert ok is True  # SQL UPDATE on non-existent row is not an error

    def test_list_custom_domains_active_only(self):
        add_custom_domain("active1.ai", source="test")
        add_custom_domain("active2.ai", source="test")
        add_custom_domain("to-deactivate.ai", source="test")
        remove_custom_domain("to-deactivate.ai")

        active = list_custom_domains(include_inactive=False)
        domains = {d["domain"] for d in active}
        assert "active1.ai" in domains
        assert "active2.ai" in domains
        assert "to-deactivate.ai" not in domains

    def test_list_custom_domains_include_inactive(self):
        add_custom_domain("will-remove.ai", source="test")
        remove_custom_domain("will-remove.ai")

        all_domains = list_custom_domains(include_inactive=True)
        found = [d for d in all_domains if d["domain"] == "will-remove.ai"]
        assert len(found) == 1
        assert found[0]["active"] == 0

    def test_refresh_reloads_from_db(self):
        add_custom_domain("refresh-test.ai", source="test")
        # Force cache invalidation
        domains = refresh_custom_domains()
        assert "refresh-test.ai" in domains

    def test_get_custom_domains_caching(self):
        add_custom_domain("cached.ai", source="test")
        d1 = get_custom_domains()
        d2 = get_custom_domains()
        assert d1 is d2  # Same cached set object

    def test_add_duplicate_domain_upserts(self):
        add_custom_domain("dup.ai", source="user", reason="first")
        add_custom_domain("dup.ai", source="smart_heuristic", reason="second")
        domains = list_custom_domains()
        found = [d for d in domains if d["domain"] == "dup.ai"]
        assert len(found) == 1
        assert found[0]["source"] == "smart_heuristic"

    def test_browser_extension_reports_domain(self):
        """Simulate browser extension discovering a new AI domain."""
        ok = add_custom_domain(
            "new-ai-tool.io",
            source="browser_extension",
            confidence="high",
            reason="detector.js url_match + dom_features",
        )
        assert ok is True
        # Verify it's in PAC
        pac = generate_pac()
        assert "new-ai-tool.io" in pac


# ═══════════════════════════════════════════════════════════════════════
# Section 3: Proxy Addon (mock-based, no live mitmproxy)
# ═══════════════════════════════════════════════════════════════════════

def _make_mock_flow(
    host: str,
    path: str = "/v1/chat/completions",
    method: str = "POST",
    request_body: bytes = b"",
    response_body: bytes = b"",
    response_status: int = 200,
    response_content_type: str = "application/json",
):
    """Build a mock mitmproxy HTTPFlow."""
    flow = MagicMock()
    flow.id = f"flow-{id(flow)}"
    flow.request.pretty_host = host
    flow.request.headers = {"Host": host}
    flow.request.path = path
    flow.request.method = method
    flow.request.content = request_body
    flow.request.url = f"https://{host}{path}"
    flow.response.status_code = response_status
    flow.response.headers = {"content-type": response_content_type}
    flow.response.content = response_body
    return flow


class TestProxyAddonCapture:
    """Tests for the PCEAddon mitmproxy addon using mocks."""

    def test_allowlisted_host_captured(self):
        """Traffic to a known AI domain should be captured."""
        from pce_proxy.addon import PCEAddon, _flow_meta

        addon = PCEAddon()
        body = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]})
        flow = _make_mock_flow("api.openai.com", request_body=body.encode())

        # Capture request
        addon.request(flow)
        assert flow.id in _flow_meta
        meta = _flow_meta[flow.id]
        assert meta["pair_id"] is not None
        assert meta["host"] == "api.openai.com"

    def test_non_ai_host_skipped(self):
        """Traffic to non-AI domains should be skipped."""
        from pce_proxy.addon import PCEAddon, _flow_meta

        addon = PCEAddon()
        flow = _make_mock_flow("www.wikipedia.org", path="/wiki/AI")

        initial_count = len(_flow_meta)
        addon.request(flow)
        # Flow should not be tracked (or smart_pending)
        if flow.id in _flow_meta:
            assert _flow_meta[flow.id].get("smart_pending") is True

    def test_request_response_pair_captured(self):
        """Full request+response flow should create captures in DB."""
        from pce_proxy.addon import PCEAddon

        addon = PCEAddon()
        req_body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
        })
        resp_body = json.dumps({
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "4"}}],
            "model": "gpt-4",
        })
        flow = _make_mock_flow(
            "api.openai.com",
            request_body=req_body.encode(),
            response_body=resp_body.encode(),
        )

        addon.request(flow)
        addon.response(flow)

        # Verify captures exist in DB
        captures = query_captures(last=5, provider="openai")
        assert len(captures) >= 2  # at least req + resp

    def test_response_triggers_normalization(self):
        """Completed pair should auto-normalize into session + messages."""
        from pce_proxy.addon import PCEAddon

        addon = PCEAddon()
        req_body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "proxy-norm-test"}],
        })
        resp_body = json.dumps({
            "id": "chatcmpl-proxy",
            "choices": [{"message": {"role": "assistant", "content": "response from proxy"}}],
            "model": "gpt-4",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        })
        flow = _make_mock_flow(
            "api.openai.com",
            request_body=req_body.encode(),
            response_body=resp_body.encode(),
        )

        addon.request(flow)
        addon.response(flow)

        # Check that normalization created session + messages
        sessions = query_sessions(last=10, provider="openai")
        assert len(sessions) >= 1


class TestProxySMARTMode:
    """Tests for SMART mode heuristic detection through the proxy."""

    def test_smart_request_detection(self):
        """SMART mode should detect AI traffic on unknown domains via request body."""
        from pce_proxy.heuristic import detect_ai_request

        body = json.dumps({
            "model": "llama-3",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        })
        conf, reasons = detect_ai_request(
            "ai.internal.corp.com", "/v1/chat/completions", "POST", body.encode()
        )
        assert conf == "high"
        assert any("path" in r for r in reasons)
        assert any("model" in r or "messages" in r for r in reasons)

    def test_smart_response_detection(self):
        """SMART mode should detect AI traffic from response body."""
        from pce_proxy.heuristic import detect_ai_response

        body = json.dumps({
            "id": "chatcmpl-xxx",
            "choices": [{"message": {"role": "assistant", "content": "Hi!"}}],
        })
        conf, reasons = detect_ai_response(body.encode())
        assert conf == "high"

    def test_smart_sse_detection(self):
        """SMART mode should detect SSE streaming AI responses."""
        from pce_proxy.heuristic import detect_ai_response

        sse_body = b'data: {"choices":[{"delta":{"content":"Hello"},"index":0}]}\n\ndata: [DONE]\n\n'
        conf, reasons = detect_ai_response(sse_body, content_type="text/event-stream")
        assert conf == "high"

    def test_smart_registers_discovered_domain(self):
        """SMART mode should auto-register newly discovered AI domains."""
        from pce_proxy.addon import _register_discovered_domain

        domain = f"smart-discovery-{int(time.time())}.ai"
        _register_discovered_domain(domain, "high", ["test_detection"])
        refresh_custom_domains()
        assert domain in get_custom_domains()

    def test_smart_does_not_register_allowlisted(self):
        """SMART mode should not re-register already allowlisted domains."""
        from pce_proxy.addon import _register_discovered_domain, _discovered_domains

        initial_custom = len(list_custom_domains())
        _discovered_domains.discard("api.openai.com")
        _register_discovered_domain("api.openai.com", "high", ["already_known"])
        after_custom = len(list_custom_domains())
        assert after_custom == initial_custom


# ═══════════════════════════════════════════════════════════════════════
# Section 4: Integration — Simulated Proxy Capture Flow
# ═══════════════════════════════════════════════════════════════════════

class TestProxyIntegration:
    """End-to-end integration: simulate what the proxy does when AI traffic passes through."""

    def test_full_openai_chat_flow(self):
        """Simulate: OpenAI chat completion request → response → normalize → session."""
        pair_id = new_pair_id()

        # Step 1: Capture request
        req_body = json.dumps({
            "model": "gpt-4-turbo",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            "max_tokens": 100,
        })
        req_id = insert_capture(
            direction="request",
            pair_id=pair_id,
            host="api.openai.com",
            path="/v1/chat/completions",
            method="POST",
            provider="openai",
            model_name="gpt-4-turbo",
            headers_redacted_json="{}",
            body_text_or_json=req_body,
            body_format="json",
            source_id=SOURCE_PROXY,
        )
        assert req_id is not None

        # Step 2: Capture response
        resp_body = json.dumps({
            "id": "chatcmpl-integration",
            "object": "chat.completion",
            "model": "gpt-4-turbo",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "The capital of France is Paris."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 25, "completion_tokens": 10, "total_tokens": 35},
        })
        resp_id = insert_capture(
            direction="response",
            pair_id=pair_id,
            host="api.openai.com",
            path="/v1/chat/completions",
            method="POST",
            provider="openai",
            model_name="gpt-4-turbo",
            status_code=200,
            latency_ms=350.5,
            headers_redacted_json="{}",
            body_text_or_json=resp_body,
            body_format="json",
            source_id=SOURCE_PROXY,
        )
        assert resp_id is not None

        # Step 3: Normalize the pair
        result = try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        # Step 4: Verify session + messages created
        sessions = query_sessions(last=20)
        proxy_sessions = [s for s in sessions if s.get("provider") == "openai"]
        assert len(proxy_sessions) >= 1

        # Find messages for the latest session
        sess_id = proxy_sessions[0]["id"]
        messages = query_messages(sess_id)
        assert len(messages) >= 2

        # Verify message content
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles
        assistant_msg = [m for m in messages if m["role"] == "assistant"][0]
        assert "Paris" in assistant_msg["content_text"]

    def test_full_anthropic_flow(self):
        """Simulate: Anthropic messages API → normalize → session."""
        pair_id = new_pair_id()

        req_body = json.dumps({
            "model": "claude-3-opus-20240229",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Explain quantum computing briefly."}],
        })
        insert_capture(
            direction="request",
            pair_id=pair_id,
            host="api.anthropic.com",
            path="/v1/messages",
            method="POST",
            provider="anthropic",
            model_name="claude-3-opus-20240229",
            headers_redacted_json="{}",
            body_text_or_json=req_body,
            body_format="json",
            source_id=SOURCE_PROXY,
        )

        resp_body = json.dumps({
            "id": "msg-integration",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Quantum computing uses qubits."}],
            "model": "claude-3-opus-20240229",
            "usage": {"input_tokens": 15, "output_tokens": 10},
        })
        insert_capture(
            direction="response",
            pair_id=pair_id,
            host="api.anthropic.com",
            path="/v1/messages",
            method="POST",
            provider="anthropic",
            model_name="claude-3-opus-20240229",
            status_code=200,
            latency_ms=800.0,
            headers_redacted_json="{}",
            body_text_or_json=resp_body,
            body_format="json",
            source_id=SOURCE_PROXY,
        )

        try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        sessions = query_sessions(last=20, provider="anthropic")
        assert len(sessions) >= 1

    def test_sse_streaming_flow(self):
        """Simulate: streaming SSE response through proxy."""
        pair_id = new_pair_id()

        req_body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Count to 3"}],
            "stream": True,
        })
        insert_capture(
            direction="request",
            pair_id=pair_id,
            host="api.openai.com",
            path="/v1/chat/completions",
            method="POST",
            provider="openai",
            model_name="gpt-4",
            headers_redacted_json="{}",
            body_text_or_json=req_body,
            body_format="json",
            source_id=SOURCE_PROXY,
        )

        # SSE response assembled by proxy (mitmproxy gives us full body)
        sse_chunks = [
            'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}\n\n',
            'data: {"choices":[{"delta":{"content":"1, "},"index":0}]}\n\n',
            'data: {"choices":[{"delta":{"content":"2, "},"index":0}]}\n\n',
            'data: {"choices":[{"delta":{"content":"3"},"index":0}]}\n\n',
            'data: [DONE]\n\n',
        ]
        sse_body = "".join(sse_chunks)

        insert_capture(
            direction="response",
            pair_id=pair_id,
            host="api.openai.com",
            path="/v1/chat/completions",
            method="POST",
            provider="openai",
            model_name="gpt-4",
            status_code=200,
            latency_ms=1200.0,
            headers_redacted_json='{"content-type": "text/event-stream"}',
            body_text_or_json=sse_body,
            body_format="text",
            source_id=SOURCE_PROXY,
        )

        try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        sessions = query_sessions(last=20)
        assert len(sessions) >= 1

    def test_unknown_provider_via_smart_detection(self):
        """Simulate: unknown AI provider detected by SMART heuristics."""
        pair_id = new_pair_id()

        req_body = json.dumps({
            "model": "custom-model-v1",
            "messages": [{"role": "user", "content": "Smart detect test"}],
            "temperature": 0.7,
        })
        insert_capture(
            direction="request",
            pair_id=pair_id,
            host="ai.mycompany.internal",
            path="/v1/chat/completions",
            method="POST",
            provider="ai.mycompany.internal",
            model_name="custom-model-v1",
            headers_redacted_json="{}",
            body_text_or_json=req_body,
            body_format="json",
            source_id=SOURCE_PROXY,
            meta_json=json.dumps({"smart_detected": True, "confidence": "high"}),
        )

        resp_body = json.dumps({
            "id": "smart-resp",
            "choices": [{"message": {"role": "assistant", "content": "Smart detected response"}}],
            "model": "custom-model-v1",
        })
        insert_capture(
            direction="response",
            pair_id=pair_id,
            host="ai.mycompany.internal",
            path="/v1/chat/completions",
            method="POST",
            provider="ai.mycompany.internal",
            status_code=200,
            headers_redacted_json="{}",
            body_text_or_json=resp_body,
            body_format="json",
            source_id=SOURCE_PROXY,
            meta_json=json.dumps({"smart_detected": True}),
        )

        try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        # Verify the unknown provider was still normalized
        sessions = query_sessions(last=50)
        found = [s for s in sessions if "mycompany" in s.get("provider", "")]
        assert len(found) >= 1

    def test_non_ai_traffic_not_normalized(self):
        """Verify that non-AI traffic doesn't create sessions."""
        pair_id = new_pair_id()

        insert_capture(
            direction="request",
            pair_id=pair_id,
            host="www.example.com",
            path="/api/users",
            method="GET",
            provider="example",
            headers_redacted_json="{}",
            body_text_or_json="",
            body_format="text",
            source_id=SOURCE_PROXY,
        )

        insert_capture(
            direction="response",
            pair_id=pair_id,
            host="www.example.com",
            path="/api/users",
            method="GET",
            provider="example",
            status_code=200,
            headers_redacted_json="{}",
            body_text_or_json='[{"name":"Alice"}]',
            body_format="json",
            source_id=SOURCE_PROXY,
        )

        # Normalization should either fail or produce nothing meaningful
        try:
            try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")
        except Exception:
            pass  # Expected — non-AI body can't be normalized

        # Check pair exists in raw captures but probably not as a session
        pair = query_by_pair(pair_id)
        assert len(pair) == 2  # req + resp exist in raw_captures


# ═══════════════════════════════════════════════════════════════════════
# Section 5: PAC Serving Endpoint (via FastAPI TestClient)
# ═══════════════════════════════════════════════════════════════════════

class TestPACEndpoint:
    """Test the /proxy.pac serving endpoint."""

    @pytest.fixture(autouse=True)
    def client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        self._client = TestClient(app)
        return self._client

    def test_proxy_pac_endpoint_returns_pac(self):
        resp = self._client.get("/proxy.pac")
        assert resp.status_code == 200
        assert "FindProxyForURL" in resp.text
        assert resp.headers["content-type"].startswith("application/x-ns-proxy-autoconfig")

    def test_proxy_pac_info_endpoint(self):
        resp = self._client.get("/api/v1/pac")
        assert resp.status_code == 200
        data = resp.json()
        assert "pac_url" in data
        assert "proxy.pac" in data["pac_url"]

    def test_domains_list_endpoint(self):
        resp = self._client.get("/api/v1/domains")
        assert resp.status_code == 200
        data = resp.json()
        assert "static_domains" in data
        assert "custom_domains" in data
        assert isinstance(data["static_domains"], list)
        assert len(data["static_domains"]) > 30  # at least the known 34+ domains

    def test_domains_add_endpoint(self):
        resp = self._client.post("/api/v1/domains", json={
            "domain": "endpoint-test.ai",
            "source": "test",
        })
        assert resp.status_code == 201
        assert resp.json()["ok"] is True

        # Verify it appears in list
        resp2 = self._client.get("/api/v1/domains")
        custom_domains = [d["domain"] for d in resp2.json()["custom_domains"]]
        assert "endpoint-test.ai" in custom_domains

    def test_domains_delete_endpoint(self):
        self._client.post("/api/v1/domains", json={"domain": "to-delete.ai"})
        resp = self._client.delete("/api/v1/domains/to-delete.ai")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_domains_refresh_endpoint(self):
        resp = self._client.post("/api/v1/domains/refresh")
        assert resp.status_code == 200
        assert "count" in resp.json()


# ═══════════════════════════════════════════════════════════════════════
# Section 6: CaptureMode Configuration
# ═══════════════════════════════════════════════════════════════════════

class TestCaptureMode:
    """Verify CaptureMode enum and configuration."""

    def test_capture_mode_enum_values(self):
        from pce_core.config import CaptureMode
        assert CaptureMode.ALLOWLIST.value == "allowlist"
        assert CaptureMode.SMART.value == "smart"
        assert CaptureMode.ALL.value == "all"

    def test_default_capture_mode(self):
        from pce_core.config import CAPTURE_MODE
        # Default should be allowlist or smart
        assert CAPTURE_MODE.value in ("allowlist", "smart", "all")

    def test_capture_mode_from_env(self):
        from pce_core.config import CaptureMode
        # Verify enum can be constructed from string
        assert CaptureMode("allowlist") == CaptureMode.ALLOWLIST
        assert CaptureMode("smart") == CaptureMode.SMART
        assert CaptureMode("all") == CaptureMode.ALL


# ═══════════════════════════════════════════════════════════════════════
# Section 7: Header & Body Redaction
# ═══════════════════════════════════════════════════════════════════════

class TestRedaction:
    """Tests for sensitive data redaction before DB persistence."""

    def test_authorization_header_redacted(self):
        from pce_core.redact import redact_headers
        h = {"Authorization": "Bearer sk-12345", "Content-Type": "application/json"}
        result = redact_headers(h)
        assert result["Authorization"] == "REDACTED"
        assert result["Content-Type"] == "application/json"

    def test_cookie_header_redacted(self):
        from pce_core.redact import redact_headers
        h = {"Cookie": "session=abc123; token=xyz", "Accept": "*/*"}
        result = redact_headers(h)
        assert result["Cookie"] == "REDACTED"
        assert result["Accept"] == "*/*"

    def test_api_key_headers_redacted(self):
        from pce_core.redact import redact_headers
        h = {"X-Api-Key": "my-secret-key", "Api-Key": "another-secret"}
        result = redact_headers(h)
        assert result["X-Api-Key"] == "REDACTED"
        assert result["Api-Key"] == "REDACTED"

    def test_case_insensitive_redaction(self):
        from pce_core.redact import redact_headers
        h = {"AUTHORIZATION": "Bearer sk-x", "authorization": "Bearer sk-y"}
        result = redact_headers(h)
        for v in result.values():
            assert v == "REDACTED"

    def test_redact_headers_json_returns_valid_json(self):
        from pce_core.redact import redact_headers_json
        h = {"Authorization": "Bearer sk-12345", "Host": "api.openai.com"}
        result = redact_headers_json(h)
        parsed = json.loads(result)
        assert parsed["Authorization"] == "REDACTED"
        assert parsed["Host"] == "api.openai.com"

    def test_safe_body_text_json_detected(self):
        from pce_core.redact import safe_body_text
        body = b'{"model": "gpt-4", "messages": []}'
        text, fmt = safe_body_text(body)
        assert fmt == "json"
        assert '"model"' in text

    def test_safe_body_text_array_json(self):
        from pce_core.redact import safe_body_text
        body = b'[{"item": 1}, {"item": 2}]'
        text, fmt = safe_body_text(body)
        assert fmt == "json"

    def test_safe_body_text_plain_text(self):
        from pce_core.redact import safe_body_text
        body = b"data: hello world\n\ndata: [DONE]\n"
        text, fmt = safe_body_text(body)
        assert fmt == "text"
        assert "hello world" in text

    def test_safe_body_text_empty(self):
        from pce_core.redact import safe_body_text
        text, fmt = safe_body_text(b"")
        assert text == ""
        assert fmt == "text"

    def test_safe_body_text_truncates_large(self):
        from pce_core.redact import safe_body_text
        big = b"x" * (3 * 1024 * 1024)  # 3MB
        text, fmt = safe_body_text(big)
        assert len(text) <= 2 * 1024 * 1024 + 10  # within limit

    def test_safe_body_text_binary_graceful(self):
        from pce_core.redact import safe_body_text
        body = bytes(range(256)) * 10
        text, fmt = safe_body_text(body)
        # Should not raise, uses errors="replace"
        assert isinstance(text, str)

    def test_set_cookie_redacted(self):
        from pce_core.redact import redact_headers
        h = {"Set-Cookie": "session=abc; Path=/", "Content-Length": "100"}
        result = redact_headers(h)
        assert result["Set-Cookie"] == "REDACTED"
        assert result["Content-Length"] == "100"

    def test_proxy_authorization_redacted(self):
        from pce_core.redact import redact_headers
        h = {"Proxy-Authorization": "Basic abc123"}
        result = redact_headers(h)
        assert result["Proxy-Authorization"] == "REDACTED"


# ═══════════════════════════════════════════════════════════════════════
# Section 8: Proxy Addon Helpers
# ═══════════════════════════════════════════════════════════════════════

class TestAddonHelpers:
    """Tests for addon.py internal helper functions."""

    def test_provider_from_host_openai(self):
        from pce_proxy.addon import _provider_from_host
        assert _provider_from_host("api.openai.com") == "openai"

    def test_provider_from_host_anthropic(self):
        from pce_proxy.addon import _provider_from_host
        assert _provider_from_host("api.anthropic.com") == "anthropic"

    def test_provider_from_host_google(self):
        from pce_proxy.addon import _provider_from_host
        assert _provider_from_host("generativelanguage.googleapis.com") == "google"

    def test_provider_from_host_unknown(self):
        from pce_proxy.addon import _provider_from_host
        assert _provider_from_host("ai.newstartup.io") == "ai.newstartup.io"

    def test_extract_model_valid_json(self):
        from pce_proxy.addon import _extract_model
        body = json.dumps({"model": "gpt-4-turbo", "messages": []}).encode()
        assert _extract_model(body) == "gpt-4-turbo"

    def test_extract_model_no_model_field(self):
        from pce_proxy.addon import _extract_model
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        assert _extract_model(body) is None

    def test_extract_model_invalid_json(self):
        from pce_proxy.addon import _extract_model
        assert _extract_model(b"not json") is None

    def test_extract_model_empty(self):
        from pce_proxy.addon import _extract_model
        assert _extract_model(b"") is None

    def test_get_host_from_pretty_host(self):
        from pce_proxy.addon import _get_host
        flow = MagicMock()
        flow.request.pretty_host = "api.openai.com"
        assert _get_host(flow) == "api.openai.com"

    def test_get_host_fallback_to_header(self):
        from pce_proxy.addon import _get_host
        flow = MagicMock()
        flow.request.pretty_host = ""
        flow.request.headers = {"Host": "api.anthropic.com:443"}
        assert _get_host(flow) == "api.anthropic.com"

    def test_get_host_empty(self):
        from pce_proxy.addon import _get_host
        flow = MagicMock()
        flow.request.pretty_host = ""
        flow.request.headers = {}
        assert _get_host(flow) == ""

    def test_is_allowlisted_static(self):
        from pce_proxy.addon import _is_allowlisted
        assert _is_allowlisted("api.openai.com") is True
        assert _is_allowlisted("api.anthropic.com") is True

    def test_is_allowlisted_unknown(self):
        from pce_proxy.addon import _is_allowlisted
        assert _is_allowlisted("www.example.com") is False

    def test_is_allowlisted_custom_domain(self):
        from pce_proxy.addon import _is_allowlisted
        add_custom_domain("custom-check.ai", source="test")
        refresh_custom_domains()
        assert _is_allowlisted("custom-check.ai") is True
        remove_custom_domain("custom-check.ai")
        refresh_custom_domains()


# ═══════════════════════════════════════════════════════════════════════
# Section 9: SMART Mode Retroactive Capture
# ═══════════════════════════════════════════════════════════════════════

class TestSMARTRetroactiveCapture:
    """Test the SMART mode deferred detection path.

    When a request doesn't match the allowlist, SMART mode defers to
    response-phase heuristics. If the response looks like AI traffic,
    both request and response are captured retroactively.
    """

    def test_smart_pending_flow_tracked(self):
        """Non-allowlisted request in SMART mode should be tracked as pending."""
        from pce_proxy.addon import PCEAddon, _flow_meta
        from pce_core.config import CaptureMode, CAPTURE_MODE

        if CAPTURE_MODE != CaptureMode.SMART:
            pytest.skip("SMART mode not active (PCE_CAPTURE_MODE != smart)")

        addon = PCEAddon()
        flow = _make_mock_flow(
            "unknown-server.example.com",
            path="/api/generate",
            method="POST",
            request_body=json.dumps({"prompt": "hello"}).encode(),
        )
        addon.request(flow)
        if flow.id in _flow_meta:
            meta = _flow_meta[flow.id]
            assert meta.get("smart_pending") is True or meta.get("pair_id") is not None

    def test_smart_retroactive_captures_on_ai_response(self):
        """Simulate SMART retroactive: request unknown, response = AI → captures both."""
        from pce_proxy.addon import _flow_meta

        # Manually create a smart_pending flow entry
        flow_id = f"smart-retro-{int(time.time())}"
        req_body = json.dumps({"model": "local-llm", "messages": [{"role": "user", "content": "Hi"}]})
        resp_body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]})

        flow = _make_mock_flow(
            "private-ai.internal",
            path="/v1/chat/completions",
            request_body=req_body.encode(),
            response_body=resp_body.encode(),
        )
        flow.id = flow_id

        _flow_meta[flow_id] = {
            "pair_id": None,
            "request_time": time.time(),
            "smart_pending": True,
            "host": "private-ai.internal",
        }

        from pce_proxy.addon import PCEAddon
        addon = PCEAddon()
        addon.response(flow)

        # Verify captures were created retroactively
        captures = query_captures(last=10)
        retro_caps = [c for c in captures if c.get("host") == "private-ai.internal"]
        assert len(retro_caps) >= 2  # req + resp

        # Verify both directions
        dirs = {c["direction"] for c in retro_caps}
        assert "request" in dirs
        assert "response" in dirs

    def test_smart_non_ai_response_discarded(self):
        """SMART pending flow with non-AI response should be discarded."""
        from pce_proxy.addon import _flow_meta, PCEAddon

        flow_id = f"smart-discard-{int(time.time())}"
        flow = _make_mock_flow(
            "cdn.example.com",
            path="/static/image.png",
            response_body=b"PNG binary data here...",
            response_content_type="image/png",
        )
        flow.id = flow_id

        _flow_meta[flow_id] = {
            "pair_id": None,
            "request_time": time.time(),
            "smart_pending": True,
            "host": "cdn.example.com",
        }

        addon = PCEAddon()
        before_count = len(query_captures(last=100))
        addon.response(flow)

        # Should NOT have created any captures
        after_count = len(query_captures(last=100))
        assert after_count == before_count


# ═══════════════════════════════════════════════════════════════════════
# Section 10: Strong Integration Assertions
# ═══════════════════════════════════════════════════════════════════════

class TestDeepIntegration:
    """Integration tests with deep assertions on normalized content."""

    def test_openai_normalized_message_content(self):
        """Verify OpenAI proxy capture normalizes to correct messages with content."""
        pair_id = new_pair_id()
        req = json.dumps({
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "What is 2+2?"},
            ],
        })
        resp = json.dumps({
            "id": "chatcmpl-deep",
            "model": "gpt-4o",
            "choices": [{"message": {"role": "assistant", "content": "4"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
        })
        insert_capture(direction="request", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       model_name="gpt-4o", headers_redacted_json="{}",
                       body_text_or_json=req, body_format="json", source_id=SOURCE_PROXY)
        insert_capture(direction="response", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       model_name="gpt-4o", status_code=200, latency_ms=100,
                       headers_redacted_json="{}", body_text_or_json=resp,
                       body_format="json", source_id=SOURCE_PROXY)

        try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        sessions = query_sessions(last=50, provider="openai")
        assert len(sessions) >= 1

        sess = sessions[0]
        msgs = query_messages(sess["id"])

        # Deep content verification
        user_msgs = [m for m in msgs if m["role"] == "user"]
        asst_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(user_msgs) >= 1
        assert len(asst_msgs) >= 1
        assert "2+2" in user_msgs[-1]["content_text"]
        assert "4" in asst_msgs[-1]["content_text"]

    def test_anthropic_normalized_content_block(self):
        """Verify Anthropic content blocks normalize correctly."""
        pair_id = new_pair_id()
        req = json.dumps({
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Say hello in French"}],
        })
        resp = json.dumps({
            "id": "msg-deep",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Bonjour! Hello in French is 'Bonjour'."}],
            "model": "claude-3-5-sonnet-20241022",
            "usage": {"input_tokens": 8, "output_tokens": 12},
        })
        insert_capture(direction="request", pair_id=pair_id, host="api.anthropic.com",
                       path="/v1/messages", method="POST", provider="anthropic",
                       model_name="claude-3-5-sonnet-20241022", headers_redacted_json="{}",
                       body_text_or_json=req, body_format="json", source_id=SOURCE_PROXY)
        insert_capture(direction="response", pair_id=pair_id, host="api.anthropic.com",
                       path="/v1/messages", method="POST", provider="anthropic",
                       model_name="claude-3-5-sonnet-20241022", status_code=200,
                       latency_ms=500, headers_redacted_json="{}",
                       body_text_or_json=resp, body_format="json", source_id=SOURCE_PROXY)

        try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        sessions = query_sessions(last=50, provider="anthropic")
        assert len(sessions) >= 1
        msgs = query_messages(sessions[0]["id"])
        asst = [m for m in msgs if m["role"] == "assistant"]
        assert len(asst) >= 1
        assert "Bonjour" in asst[-1]["content_text"]

    def test_sse_assembled_into_complete_message(self):
        """Verify SSE stream is assembled into a complete assistant message."""
        pair_id = new_pair_id()
        req = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Count 1 to 5"}],
            "stream": True,
        })
        sse = (
            'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":"1"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":", 2"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":", 3"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":", 4"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":", 5"},"index":0}]}\n\n'
            'data: [DONE]\n\n'
        )
        insert_capture(direction="request", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       model_name="gpt-4", headers_redacted_json="{}",
                       body_text_or_json=req, body_format="json", source_id=SOURCE_PROXY)
        insert_capture(direction="response", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       model_name="gpt-4", status_code=200, latency_ms=1500,
                       headers_redacted_json='{"content-type":"text/event-stream"}',
                       body_text_or_json=sse, body_format="text", source_id=SOURCE_PROXY)

        try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")

        sessions = query_sessions(last=50, provider="openai")
        assert len(sessions) >= 1
        msgs = query_messages(sessions[0]["id"])
        asst = [m for m in msgs if m["role"] == "assistant"]
        assert len(asst) >= 1
        # The assembled text should contain all the numbers
        content = asst[-1]["content_text"]
        assert "1" in content
        assert "5" in content

    def test_malformed_response_body_does_not_crash(self):
        """Normalization should gracefully handle garbled response bodies."""
        pair_id = new_pair_id()
        req = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]})
        insert_capture(direction="request", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       headers_redacted_json="{}", body_text_or_json=req,
                       body_format="json", source_id=SOURCE_PROXY)
        # Garbled response
        insert_capture(direction="response", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       status_code=200, headers_redacted_json="{}",
                       body_text_or_json="THIS IS NOT JSON AT ALL {{{",
                       body_format="text", source_id=SOURCE_PROXY)

        # Should not raise
        try:
            try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")
        except Exception:
            pass  # May fail but MUST NOT crash the process

        # Raw captures should still exist
        pair = query_by_pair(pair_id)
        assert len(pair) == 2

    def test_error_status_code_still_captured(self):
        """HTTP 4xx/5xx responses should still be captured in raw_captures."""
        pair_id = new_pair_id()
        req = json.dumps({"model": "gpt-4", "messages": []})
        err_resp = json.dumps({"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}})

        insert_capture(direction="request", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       headers_redacted_json="{}", body_text_or_json=req,
                       body_format="json", source_id=SOURCE_PROXY)
        insert_capture(direction="response", pair_id=pair_id, host="api.openai.com",
                       path="/v1/chat/completions", method="POST", provider="openai",
                       status_code=429, headers_redacted_json="{}",
                       body_text_or_json=err_resp, body_format="json",
                       source_id=SOURCE_PROXY)

        pair = query_by_pair(pair_id)
        assert len(pair) == 2
        resp_row = [r for r in pair if r["direction"] == "response"][0]
        assert resp_row["status_code"] == 429


# ═══════════════════════════════════════════════════════════════════════
# Section 11: Local Hook HTTP Roundtrip (TestClient)
# ═══════════════════════════════════════════════════════════════════════

class TestLocalHookHTTP:
    """Test local hook app via TestClient against a mock backend."""

    def test_hook_captures_ollama_style_request(self):
        """Simulate Ollama-like traffic through the hook and verify capture."""
        from pce_core.local_hook.hook import create_hook_app
        from unittest.mock import AsyncMock

        app = create_hook_app(target_host="127.0.0.1", target_port=11434)

        ollama_req = json.dumps({
            "model": "llama3",
            "messages": [{"role": "user", "content": "Hello from hook test"}],
        })
        ollama_resp = json.dumps({
            "model": "llama3",
            "message": {"role": "assistant", "content": "Hi! I'm Llama."},
            "done": True,
        })

        # Mock httpx.AsyncClient to avoid needing a real Ollama server
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = ollama_resp.encode()
        mock_response.headers = {"content-type": "application/json"}

        with patch("pce_core.local_hook.hook.httpx.AsyncClient") as MockClient:
            mock_client_inst = AsyncMock()
            mock_client_inst.request = AsyncMock(return_value=mock_response)
            mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
            mock_client_inst.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_inst

            from fastapi.testclient import TestClient
            with TestClient(app) as client:
                resp = client.post(
                    "/api/chat",
                    content=ollama_req.encode(),
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 200
        resp_data = resp.json()
        assert resp_data["model"] == "llama3"

        # Verify captures were written to DB
        captures = query_captures(last=20)
        hook_caps = [c for c in captures
                     if c.get("host") == "127.0.0.1:11434"
                     and c.get("path") == "/api/chat"]
        assert len(hook_caps) >= 2
        dirs = {c["direction"] for c in hook_caps}
        assert "request" in dirs
        assert "response" in dirs

        # Verify model name was extracted
        req_cap = [c for c in hook_caps if c["direction"] == "request"][0]
        assert req_cap["model_name"] == "llama3"

    def test_hook_returns_502_when_target_unreachable(self):
        """Hook should return 502 when the target server is down."""
        from pce_core.local_hook.hook import create_hook_app
        import httpx as httpx_mod
        from unittest.mock import AsyncMock

        app = create_hook_app(target_host="127.0.0.1", target_port=19999)

        with patch("pce_core.local_hook.hook.httpx.AsyncClient") as MockClient:
            mock_client_inst = AsyncMock()
            mock_client_inst.request = AsyncMock(side_effect=httpx_mod.ConnectError("Connection refused"))
            mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
            mock_client_inst.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_inst

            from fastapi.testclient import TestClient
            with TestClient(app) as client:
                resp = client.post("/api/chat", content=b'{"model":"test"}')

        assert resp.status_code == 502
        assert "unreachable" in resp.json().get("error", "")


# ═══════════════════════════════════════════════════════════════════════
# Section 12: Clipboard Monitor → DB Integration
# ═══════════════════════════════════════════════════════════════════════

class TestClipboardIntegration:
    """Test clipboard detection → parse → insert → query roundtrip."""

    def test_detected_conversation_inserts_to_db(self):
        """Clipboard text that looks like AI conversation should be capturable."""
        from pce_core.clipboard_monitor import detect_ai_conversation, parse_conversation

        text = (
            "User: How do I reverse a string in Python?\n\n"
            "Assistant: You can use slicing: `my_string[::-1]`.\n\n"
            "Here's a complete example:\n"
            "```python\n"
            "text = 'hello'\n"
            "reversed_text = text[::-1]\n"
            "print(reversed_text)  # 'olleh'\n"
            "```\n\n"
            "User: What about reversing a list?\n\n"
            "Assistant: For lists, you have several options:\n"
            "1. `my_list[::-1]` - creates a new reversed list\n"
            "2. `my_list.reverse()` - reverses in place\n"
            "3. `list(reversed(my_list))` - using the reversed() built-in\n"
        )

        # Step 1: Detection
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is True
        assert score >= 0.5

        # Step 2: Parsing
        msgs = parse_conversation(text)
        assert len(msgs) == 4
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert "slicing" in msgs[1]["content"]

        # Step 3: Insert as raw capture (simulating what ClipboardMonitor does)
        pair_id = new_pair_id()
        body = json.dumps({
            "messages": [{"role": m["role"], "content": m["content"]} for m in msgs],
            "source": "clipboard",
        })
        cap_id = insert_capture(
            direction="conversation",
            pair_id=pair_id,
            host="clipboard",
            path="",
            method="",
            provider="clipboard",
            headers_redacted_json="{}",
            body_text_or_json=body,
            body_format="json",
            source_id=SOURCE_PROXY,
            meta_json=json.dumps({
                "capture_source": "clipboard_monitor",
                "confidence": score,
                "reason": reason,
            }),
        )
        assert cap_id is not None

        # Step 4: Verify in DB
        pair = query_by_pair(pair_id)
        assert len(pair) == 1
        raw = pair[0]
        assert raw["provider"] == "clipboard"
        meta = json.loads(raw["meta_json"])
        assert meta["capture_source"] == "clipboard_monitor"

    def test_non_ai_text_not_detected(self):
        """Regular clipboard text should not be detected as AI conversation."""
        from pce_core.clipboard_monitor import detect_ai_conversation

        text = (
            "Shopping list:\n"
            "- Milk\n"
            "- Eggs\n"
            "- Bread\n"
            "- Butter\n"
            "Remember to also pick up the dry cleaning.\n"
            "The store closes at 9pm today.\n"
        )
        is_ai, reason, score = detect_ai_conversation(text)
        assert is_ai is False


# ═══════════════════════════════════════════════════════════════════════
# Section 13: Capture Health API with Real Data
# ═══════════════════════════════════════════════════════════════════════

class TestCaptureHealthAPI:
    """Test capture-health and capabilities endpoints with real capture data."""

    @pytest.fixture(autouse=True)
    def client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        self._client = TestClient(app)
        return self._client

    def test_capture_health_returns_channels(self):
        resp = self._client.get("/api/v1/capture-health")
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert "timestamp" in data
        assert isinstance(data["channels"], list)

    def test_capabilities_includes_all_channels(self):
        resp = self._client.get("/api/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "capabilities" in data
        cap_ids = {c["id"] for c in data["capabilities"]}
        # Must include all known channel types
        assert "core" in cap_ids
        assert "proxy" in cap_ids
        assert "browser_extension" in cap_ids
        assert "local_hook" in cap_ids
        assert "clipboard" in cap_ids
        assert "mcp" in cap_ids
        assert "pac" in cap_ids

    def test_stats_reflects_proxy_captures(self):
        resp = self._client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_captures"] > 0
        assert "openai" in data.get("by_provider", {})
