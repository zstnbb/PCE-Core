# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pce_sslkeylog.capture.PairingCaptureSink (pairing
state machine + DB insertion glue)."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pce_sslkeylog.capture import PairingCaptureSink


# ---------------------------------------------------------------------------
# Test fixtures: synthetic tshark NDJSON lines
# ---------------------------------------------------------------------------


def _h2_request_line(stream_id: str, tcp_stream: str, host: str, path: str,
                      body: str = "") -> str:
    return json.dumps({
        "timestamp": "2026-05-15T10:00:00",
        "layers": {
            "tcp": {"tcp_tcp_stream": tcp_stream},
            "http2": {
                "http2_http2_streamid": stream_id,
                "http2_http2_header_name": [":method", ":authority", ":path"],
                "http2_http2_header_value": ["POST", host, path],
                "http2_http2_body_fragment": body,
            },
        },
    })


def _h2_response_line(stream_id: str, tcp_stream: str, status: str = "200",
                      body: str = "") -> str:
    return json.dumps({
        "timestamp": "2026-05-15T10:00:01",
        "layers": {
            "tcp": {"tcp_tcp_stream": tcp_stream},
            "http2": {
                "http2_http2_streamid": stream_id,
                "http2_http2_header_name": [":status"],
                "http2_http2_header_value": [status],
                "http2_http2_body_fragment": body,
            },
        },
    })


# ---------------------------------------------------------------------------
# Test helpers — mock insert_capture / normalize / new_pair_id
# ---------------------------------------------------------------------------


class MockInsertCapture:
    """Stand-in for pce_core.db.insert_capture that records calls."""

    def __init__(self):
        self.calls: list[dict] = []
        self.next_id_counter = 0

    def __call__(self, **kwargs):
        self.next_id_counter += 1
        capture_id = f"mock-cap-{self.next_id_counter:04d}"
        self.calls.append({"id": capture_id, **kwargs})
        return capture_id


class MockNewPairId:
    def __init__(self):
        self.counter = 0

    def __call__(self):
        self.counter += 1
        return f"mock-pair-{self.counter:04d}"


# ---------------------------------------------------------------------------
# Request → Response pairing
# ---------------------------------------------------------------------------


def test_sink_pairs_request_then_response():
    insert = MockInsertCapture()
    new_pid = MockNewPairId()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=new_pid,
        try_normalize_pair_fn=lambda *a, **k: None,
    )

    # Send request first
    sink.handle_line(_h2_request_line("13", "7", "api.anthropic.com",
                                       "/v1/messages", body='{"q":"hi"}'))
    assert sink.stats.events_total == 1
    assert sink.stats.pairs_emitted == 0    # not emitted until response arrives
    assert len(insert.calls) == 0
    # Pending state: 1 request waiting

    # Now send the response
    sink.handle_line(_h2_response_line("13", "7", "200",
                                        body='{"answer":"hello"}'))
    assert sink.stats.events_total == 2
    assert sink.stats.pairs_emitted == 1
    assert len(insert.calls) == 2

    # Both rows share the same pair_id
    pair_ids = {call["pair_id"] for call in insert.calls}
    assert len(pair_ids) == 1, f"expected single pair_id, got {pair_ids}"

    # Directions split
    directions = sorted(call["direction"] for call in insert.calls)
    assert directions == ["request", "response"]

    # source_id correct
    for call in insert.calls:
        assert call["source_id"] == "sslkeylog-default"


def test_sink_orphan_response_emitted_alone():
    """If we miss the request side, the response still gets a row."""
    insert = MockInsertCapture()
    new_pid = MockNewPairId()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=new_pid,
        try_normalize_pair_fn=lambda *a, **k: None,
    )

    sink.handle_line(_h2_response_line("13", "7", "200"))
    assert sink.stats.events_total == 1
    assert sink.stats.pairs_emitted == 1
    assert len(insert.calls) == 1
    assert insert.calls[0]["direction"] == "response"


def test_sink_index_lines_ignored():
    """tshark index metadata lines should be ignored entirely."""
    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
    )
    sink.handle_line('{"index":{"_index":"packets","_type":"doc"}}')
    sink.handle_line('')
    sink.handle_line('garbage not json')
    assert sink.stats.lines_total == 3
    assert sink.stats.lines_parsed == 0
    assert sink.stats.events_total == 0
    assert len(insert.calls) == 0


def test_sink_host_allowlist_filters_out_other_traffic():
    """Captures for non-allowlisted hosts should be dropped silently."""
    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
        host_allowlist=frozenset(["api.anthropic.com"]),
    )
    # Anthropic — pass through
    sink.handle_line(_h2_request_line("1", "10", "api.anthropic.com", "/v1/m"))
    sink.handle_line(_h2_response_line("1", "10"))
    # Bing (not allowed) — filtered out
    sink.handle_line(_h2_request_line("2", "11", "www.bing.com", "/search"))
    sink.handle_line(_h2_response_line("2", "11"))
    assert sink.stats.pairs_emitted == 1
    # Two rows: request (host=api.anthropic.com) + response (host=""; HTTP/2
    # responses don't carry :authority). The request's host is what matters
    # for allowlist filtering; response inherits the pair association.
    hosts = {call["host"] for call in insert.calls}
    assert "api.anthropic.com" in hosts
    assert "www.bing.com" not in hosts


def test_sink_host_allowlist_suffix_match():
    """www.claude.ai should match an allowlist entry of 'claude.ai'."""
    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
        host_allowlist=frozenset(["claude.ai"]),
    )
    sink.handle_line(_h2_request_line("1", "10", "www.claude.ai", "/api"))
    sink.handle_line(_h2_response_line("1", "10"))
    assert sink.stats.pairs_emitted == 1


def test_sink_multiple_parallel_streams_paired_correctly():
    """Two parallel HTTP/2 streams should not mix up their pairs."""
    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
    )
    sink.handle_line(_h2_request_line("1", "10", "api.anthropic.com", "/req1"))
    sink.handle_line(_h2_request_line("3", "10", "api.anthropic.com", "/req2"))
    # Responses arrive out of order
    sink.handle_line(_h2_response_line("3", "10", body="resp2"))
    sink.handle_line(_h2_response_line("1", "10", body="resp1"))

    assert sink.stats.pairs_emitted == 2
    # 2 requests + 2 responses = 4 inserts
    assert len(insert.calls) == 4
    # Group by pair_id, each pair should have 1 req + 1 resp matching paths
    pair_map: dict = {}
    for c in insert.calls:
        pair_map.setdefault(c["pair_id"], []).append(c)
    for pid, rows in pair_map.items():
        assert len(rows) == 2
        roles = {r["direction"] for r in rows}
        assert roles == {"request", "response"}


def test_sink_ttl_expiry_orphan_emission():
    """A request with no response within TTL should be flushed as orphan."""
    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
        pending_ttl_s=0.01,   # very short TTL for the test
    )
    sink.handle_line(_h2_request_line("99", "20", "api.anthropic.com", "/lonely"))
    assert sink.stats.pairs_emitted == 0
    # Wait for TTL
    time.sleep(0.05)
    # Trigger a sweep by sending a no-op line (any new event triggers it)
    sink.handle_line(_h2_request_line("100", "21", "api.anthropic.com", "/other"))
    # The first request should now be orphan-flushed
    assert sink.stats.orphans_emitted >= 1


def test_sink_max_pending_cap_drops_oldest():
    """When max_pending is exceeded, the oldest pending is dropped + emitted
    as orphan."""
    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
        max_pending=2,    # only hold 2 pending at once
        pending_ttl_s=999.0,  # no TTL expiry interference
    )
    sink.handle_line(_h2_request_line("1", "10", "api.anthropic.com", "/req1"))
    sink.handle_line(_h2_request_line("2", "11", "api.anthropic.com", "/req2"))
    # 3rd request — should cause oldest (req1) to flush as orphan
    sink.handle_line(_h2_request_line("3", "12", "api.anthropic.com", "/req3"))
    assert sink.stats.orphans_emitted == 1
    # 1 orphan row inserted (for req1)
    inserted_paths = [c.get("path") for c in insert.calls]
    assert "/req1" in inserted_paths


def test_sink_normalize_called_after_pair_complete():
    """try_normalize_pair should be invoked once a request+response pair
    is complete."""
    normalize_calls: list[tuple] = []

    def fake_normalize(pair_id, **kwargs):
        normalize_calls.append((pair_id, kwargs))

    insert = MockInsertCapture()
    sink = PairingCaptureSink(
        insert_capture_fn=insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=fake_normalize,
    )
    sink.handle_line(_h2_request_line("1", "10", "api.anthropic.com", "/m"))
    sink.handle_line(_h2_response_line("1", "10"))
    assert len(normalize_calls) == 1
    pid, kwargs = normalize_calls[0]
    assert kwargs.get("source_id") == "sslkeylog-default"
    assert kwargs.get("created_via") == "sslkeylog"


def test_sink_insert_failure_does_not_crash():
    """An insert_capture exception should be logged + counted, not propagate."""
    def failing_insert(**kwargs):
        raise RuntimeError("simulated DB failure")

    sink = PairingCaptureSink(
        insert_capture_fn=failing_insert,
        new_pair_id_fn=MockNewPairId(),
        try_normalize_pair_fn=lambda *a, **k: None,
    )
    # Should not raise
    sink.handle_line(_h2_request_line("1", "10", "api.anthropic.com", "/m"))
    sink.handle_line(_h2_response_line("1", "10"))
    # Error should be counted
    assert sink.stats.insert_errors >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
