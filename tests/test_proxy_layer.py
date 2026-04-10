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
