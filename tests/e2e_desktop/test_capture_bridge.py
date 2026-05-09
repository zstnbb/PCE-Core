# SPDX-License-Identifier: Apache-2.0
"""Pure-helper tests for capture_bridge — no Playwright runtime required."""
from __future__ import annotations

import http.server
import json
import re
import threading
from typing import Any

import pytest

from pce_app_launcher.claude_desktop import capture_bridge as cb


# ---------------------------------------------------------------------------
# url_matches / host_path_from_url / provider_from_host
# ---------------------------------------------------------------------------


@pytest.fixture
def anth_patterns():
    return [re.compile(p) for p in cb.DEFAULT_URL_PATTERNS]


def test_url_matches_anthropic(anth_patterns):
    assert cb.url_matches("https://api.anthropic.com/v1/messages", anth_patterns)
    assert cb.url_matches("https://claude.ai/api/foo", anth_patterns)
    assert not cb.url_matches("https://api.openai.com/v1/chat", anth_patterns)
    assert not cb.url_matches("https://example.com/", anth_patterns)


def test_host_path_split():
    assert cb.host_path_from_url("https://api.anthropic.com/v1/messages") == (
        "api.anthropic.com", "/v1/messages"
    )
    assert cb.host_path_from_url("https://x.test/p?q=1") == ("x.test", "/p?q=1")
    assert cb.host_path_from_url("") == ("", "")


def test_provider_from_host():
    assert cb.provider_from_host("api.anthropic.com") == "anthropic"
    assert cb.provider_from_host("claude.ai") == "anthropic"
    assert cb.provider_from_host("api.openai.com") == "openai"
    assert cb.provider_from_host("chatgpt.com") == "openai"
    assert cb.provider_from_host("generativelanguage.googleapis.com") == "google"
    assert cb.provider_from_host("x.example") == "unknown"


# ---------------------------------------------------------------------------
# truncate
# ---------------------------------------------------------------------------


def test_truncate_passes_short_body():
    assert cb.truncate("hello") == "hello"


def test_truncate_clips_long_body():
    big = "x" * (cb.MAX_BODY_BYTES + 100)
    out = cb.truncate(big)
    assert len(out) == cb.MAX_BODY_BYTES


def test_truncate_handles_empty_and_none():
    assert cb.truncate("") == ""
    assert cb.truncate(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_capture_event
# ---------------------------------------------------------------------------


def test_build_capture_event_request_shape():
    ev = cb.build_capture_event(
        direction="request",
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        status_code=None,
        headers={"Content-Type": "application/json", "Authorization": "Bearer x"},
        body='{"messages":[{"role":"user","content":"hi"}]}',
        pair_id="p1",
    )
    assert ev["source_type"] == "desktop_electron"
    assert ev["source_name"] == "pce-app-launcher"
    assert ev["direction"] == "request"
    assert ev["pair_id"] == "p1"
    assert ev["provider"] == "anthropic"
    assert ev["host"] == "api.anthropic.com"
    assert ev["path"] == "/v1/messages"
    assert ev["method"] == "POST"
    assert "status_code" not in ev  # request rows have no status
    parsed_headers = json.loads(ev["headers_json"])
    assert parsed_headers["Authorization"] == "Bearer x"  # bridge does NOT redact;
    assert ev["body_format"] == "json"
    assert ev["meta"]["launcher.app_name"] == "claude-desktop"
    assert ev["meta"]["launcher.url"] == "https://api.anthropic.com/v1/messages"


def test_build_capture_event_response_shape_includes_status():
    ev = cb.build_capture_event(
        direction="response",
        url="https://api.anthropic.com/v1/messages",
        method="POST",
        status_code=200,
        headers={},
        body="not-json",
        pair_id="p2",
    )
    assert ev["direction"] == "response"
    assert ev["status_code"] == 200
    assert ev["body_format"] == "text"


def test_build_capture_event_extra_meta_merged():
    ev = cb.build_capture_event(
        direction="request",
        url="https://api.anthropic.com/v1/x",
        method="GET",
        status_code=None,
        headers={},
        body="",
        pair_id="p3",
        extra_meta={"custom_key": "custom_val"},
    )
    assert ev["meta"]["custom_key"] == "custom_val"
    assert ev["meta"]["launcher.app_name"] == "claude-desktop"


def test_build_capture_event_invalid_direction_raises():
    with pytest.raises(ValueError):
        cb.build_capture_event(
            direction="weird",
            url="x", method="GET", status_code=None,
            headers={}, body="", pair_id="x",
        )


def test_build_capture_event_truncates_huge_body():
    big = "z" * (cb.MAX_BODY_BYTES + 50)
    ev = cb.build_capture_event(
        direction="response",
        url="https://api.anthropic.com/v1/x",
        method="GET",
        status_code=200,
        headers={},
        body=big,
        pair_id="p4",
    )
    assert len(ev["body_json"]) == cb.MAX_BODY_BYTES


# ---------------------------------------------------------------------------
# post_to_pce_core — stub HTTP server
# ---------------------------------------------------------------------------


class _IngestStubHandler(http.server.BaseHTTPRequestHandler):
    received: list[dict] = []

    def log_message(self, *_a): pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"_raw": body}
        type(self).received.append({"path": self.path, "payload": payload})
        resp = json.dumps({"id": "x", "pair_id": payload.get("pair_id", ""), "source_id": "y"}).encode("utf-8")
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


@pytest.fixture
def ingest_stub():
    _IngestStubHandler.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _IngestStubHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield server, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


def test_post_to_pce_core_sends_payload(ingest_stub):
    _server, base_url = ingest_stub
    payload = {"hello": "world", "pair_id": "p1"}
    res = cb.post_to_pce_core(payload, pce_core_url=base_url)
    assert res is not None
    assert res["pair_id"] == "p1"
    received = _IngestStubHandler.received
    assert len(received) == 1
    assert received[0]["payload"] == payload
    assert received[0]["path"] == "/api/v1/captures"


def test_post_to_pce_core_returns_none_on_unreachable():
    res = cb.post_to_pce_core({"x": 1}, pce_core_url="http://127.0.0.1:1", timeout=0.5)
    assert res is None


# ---------------------------------------------------------------------------
# CaptureBridge wiring (stubbed Playwright objects)
# ---------------------------------------------------------------------------


class _StubResponse:
    def __init__(self, *, url: str, status: int, body: str, headers: dict, post_data: str = ""):
        self.url = url
        self.status = status
        self._body = body
        self.headers = headers
        self.request = _StubRequest(url, post_data, headers)

    def text(self) -> str:
        return self._body


class _StubRequest:
    def __init__(self, url: str, post_data: str, headers: dict):
        self.url = url
        self.post_data = post_data
        self.headers = headers
        self.method = "POST"


def test_bridge_response_handler_writes_pair_when_url_matches(monkeypatch, ingest_stub):
    """Drive _on_response with a stubbed Response; verify pair posted."""
    _server, base_url = ingest_stub
    bridge = cb.CaptureBridge(
        cdp_endpoint="http://127.0.0.1:9222",  # not actually used in this test
        pce_core_url=base_url,
    )
    response = _StubResponse(
        url="https://api.anthropic.com/v1/messages",
        status=200,
        body='{"completion":"hi"}',
        headers={"Content-Type": "application/json"},
        post_data='{"messages":[{"role":"user","content":"hi"}]}',
    )
    bridge._on_response(response)

    received = _IngestStubHandler.received
    assert len(received) == 2  # request + response rows
    # Same pair_id on both
    pair_ids = {r["payload"]["pair_id"] for r in received}
    assert len(pair_ids) == 1
    # Both have source_type=desktop_electron
    assert all(r["payload"]["source_type"] == "desktop_electron" for r in received)
    # Stats updated
    snap = bridge.snapshot()
    assert snap["matched"] == 1
    assert snap["written"] == 1
    assert snap["failed"] == 0


def test_bridge_response_handler_skips_unmatched_url(ingest_stub):
    _server, base_url = ingest_stub
    bridge = cb.CaptureBridge(
        cdp_endpoint="http://127.0.0.1:9222",
        pce_core_url=base_url,
    )
    response = _StubResponse(
        url="https://example.com/foo",
        status=200,
        body="",
        headers={},
    )
    bridge._on_response(response)
    snap = bridge.snapshot()
    assert snap["matched"] == 0
    assert snap["written"] == 0
    assert _IngestStubHandler.received == []


def test_bridge_response_handler_marks_failure_on_unreachable_pce_core():
    bridge = cb.CaptureBridge(
        cdp_endpoint="http://127.0.0.1:9222",
        pce_core_url="http://127.0.0.1:1",  # unreachable
    )
    response = _StubResponse(
        url="https://api.anthropic.com/v1/messages",
        status=200,
        body="",
        headers={},
    )
    bridge._on_response(response)
    snap = bridge.snapshot()
    assert snap["matched"] == 1
    assert snap["written"] == 0
    assert snap["failed"] == 1
