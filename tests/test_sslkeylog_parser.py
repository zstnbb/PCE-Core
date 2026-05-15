# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pce_sslkeylog.parser (tshark NDJSON -> TsharkEvent)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pce_sslkeylog.parser import (
    EkRecord,
    TsharkEvent,
    build_capture_from_pair,
    event_from_record,
    parse_ek_line,
)


# ---------------------------------------------------------------------------
# parse_ek_line — line-level filtering
# ---------------------------------------------------------------------------


def test_parse_ek_line_index_returns_none():
    line = '{"index":{"_index":"packets-2026-05-15","_type":"doc"}}'
    assert parse_ek_line(line) is None


def test_parse_ek_line_blank_returns_none():
    assert parse_ek_line("") is None
    assert parse_ek_line("   \n") is None


def test_parse_ek_line_malformed_returns_none():
    assert parse_ek_line("not a json") is None
    assert parse_ek_line("{broken") is None


def test_parse_ek_line_no_layers_returns_none():
    line = '{"timestamp":"foo"}'
    assert parse_ek_line(line) is None


def test_parse_ek_line_source_returns_record():
    line = json.dumps({
        "timestamp": "May 15, 2026 10:00:00.123456",
        "layers": {"tcp": {"tcp_tcp_stream": "42"}},
    })
    rec = parse_ek_line(line)
    assert rec is not None
    assert rec.timestamp == "May 15, 2026 10:00:00.123456"
    assert rec.layers["tcp"]["tcp_tcp_stream"] == "42"


# ---------------------------------------------------------------------------
# event_from_record — HTTP/1.x
# ---------------------------------------------------------------------------


def _make_http1_request_record():
    return EkRecord(
        timestamp="2026-05-15T10:00:00",
        layers={
            "tcp": {"tcp_tcp_stream": "7"},
            "http": {
                "http_http_request_method": "POST",
                "http_http_host": "api.anthropic.com",
                "http_http_request_uri": "/v1/messages",
                "http_http_file_data": '{"messages":[{"role":"user","content":"hi"}]}',
                "http_http_header_accept": "application/json",
            },
        },
    )


def test_event_from_record_http1_request():
    ev = event_from_record(_make_http1_request_record())
    assert ev is not None
    assert ev.direction == "request"
    assert ev.method == "POST"
    assert ev.host == "api.anthropic.com"
    assert ev.path == "/v1/messages"
    assert ev.status_code is None
    assert b'"messages"' in ev.body
    assert ev.tcp_stream == "7"
    assert ev.is_http2 is False
    assert ev.pair_key.startswith("tcp7")


def test_event_from_record_http1_response():
    rec = EkRecord(
        timestamp="2026-05-15T10:00:01",
        layers={
            "tcp": {"tcp_tcp_stream": "7"},
            "http": {
                "http_http_response_code": "200",
                "http_http_file_data": '{"id":"msg_123","content":"ok"}',
            },
        },
    )
    ev = event_from_record(rec)
    assert ev is not None
    assert ev.direction == "response"
    assert ev.status_code == 200
    assert ev.method == ""
    assert b'msg_123' in ev.body
    assert ev.tcp_stream == "7"


# ---------------------------------------------------------------------------
# event_from_record — HTTP/2
# ---------------------------------------------------------------------------


def _make_http2_request_record():
    return EkRecord(
        timestamp="2026-05-15T10:00:02",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": {
                "http2_http2_streamid": "13",
                "http2_http2_header_name": [":method", ":authority", ":path",
                                            "content-type"],
                "http2_http2_header_value": ["POST", "claude.ai", "/api/chat",
                                             "application/json"],
                "http2_http2_body_fragment": '{"prompt":"What is 2+2?"}',
            },
        },
    )


def test_event_from_record_http2_request():
    ev = event_from_record(_make_http2_request_record())
    assert ev is not None
    assert ev.direction == "request"
    assert ev.method == "POST"
    assert ev.host == "claude.ai"
    assert ev.path == "/api/chat"
    assert b'"prompt"' in ev.body
    assert ev.stream_id == "13"
    assert ev.tcp_stream == "11"
    assert ev.is_http2 is True
    assert ev.pair_key == "tcp11:s13"


def test_event_from_record_http2_response():
    rec = EkRecord(
        timestamp="2026-05-15T10:00:03",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": {
                "http2_http2_streamid": "13",
                "http2_http2_header_name": [":status", "content-type"],
                "http2_http2_header_value": ["200", "text/event-stream"],
                "http2_http2_body_fragment": "data: {\"text\":\"4\"}\n\ndata: [DONE]\n",
            },
        },
    )
    ev = event_from_record(rec)
    assert ev is not None
    assert ev.direction == "response"
    assert ev.status_code == 200
    assert ev.stream_id == "13"
    assert ev.tcp_stream == "11"
    assert ev.pair_key == "tcp11:s13"  # MUST match the request side for pairing


# ---------------------------------------------------------------------------
# event_from_record — edge cases
# ---------------------------------------------------------------------------


def test_event_from_record_tls_only_returns_none():
    """A TLS frame with no HTTP layer should produce no event."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:04",
        layers={"tcp": {"tcp_tcp_stream": "1"}, "tls": {"tls_record_version": "0x0303"}},
    )
    assert event_from_record(rec) is None


def test_event_from_record_http2_settings_returns_none():
    """HTTP/2 SETTINGS frame (no method, no status) → no event."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:05",
        layers={
            "tcp": {"tcp_tcp_stream": "1"},
            "http2": {"http2_http2_streamid": "0", "http2_http2_frame_type": "4"},
        },
    )
    assert event_from_record(rec) is None


def test_event_from_record_http2_individual_field_headers():
    """tshark 4.6+ emits HTTP/2 headers as individual ``http2_http2_headers_<name>``
    fields (not just the parallel name/value arrays). Verify the parser
    extracts pseudo-headers from this format too."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:02",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": {
                "http2_http2_streamid": "13",
                "http2_http2_type": "1",  # HEADERS frame
                "http2_http2_headers_method": "POST",
                "http2_http2_headers_authority": "api.anthropic.com",
                "http2_http2_headers_path": "/v1/messages",
                "http2_http2_headers_scheme": "https",
                "http2_http2_headers_content_type": "application/json",
                "http2_http2_headers_user_agent": "python-httpx/0.28.1",
            },
        },
    )
    ev = event_from_record(rec)
    assert ev is not None, "individual-field HTTP/2 headers should parse"
    assert ev.direction == "request"
    assert ev.method == "POST"
    assert ev.host == "api.anthropic.com"
    assert ev.path == "/v1/messages"
    assert ev.is_http2 is True
    assert ev.headers.get("user-agent") == "python-httpx/0.28.1"


def test_event_from_record_http2_priority_over_http1_in_connect_tunnel():
    """When tshark sees a CONNECT-tunnelled HTTP/2 packet, it emits BOTH
    a ``http`` layer (outer CONNECT metadata: ``http_http_proxy_connect_*``)
    AND a ``http2`` layer (decrypted inner traffic). The parser MUST
    process the http2 side first; otherwise it returns a CONNECT-only
    event and drops the real HTTP/2 content silently."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:02",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http": {
                # Just the CONNECT-tunnel metadata, no real method/status:
                "http_http_proxy_connect_port": "443",
                "http_http_proxy_connect_host": "api.anthropic.com",
            },
            "http2": {
                "http2_http2_streamid": "1",
                "http2_http2_type": "1",
                "http2_http2_header_name": [":method", ":authority", ":path"],
                "http2_http2_header_value": ["GET", "api.anthropic.com", "/"],
            },
        },
    )
    ev = event_from_record(rec)
    assert ev is not None
    assert ev.is_http2 is True, (
        f"expected HTTP/2 event, got HTTP/1 with method={ev.method}: "
        f"the http2 branch must take priority over http when both are present"
    )
    assert ev.method == "GET"
    assert ev.host == "api.anthropic.com"
    assert ev.path == "/"


def test_event_from_record_http2_settings_in_list_skipped():
    """A multi-frame HTTP/2 packet (preface + SETTINGS + WINDOW_UPDATE)
    should produce no event when no HEADERS frame is present."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:02",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": [
                {"http2_http2_magic": "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"},
                {"http2_http2_type": "4", "http2_http2_streamid": "0",
                 "http2_http2_settings_header_table_size": "4096"},
                {"http2_http2_type": "8", "http2_http2_streamid": "0",
                 "http2_http2_window_update_window_size_increment": "16777216"},
            ],
        },
    )
    assert event_from_record(rec) is None


def test_event_from_record_http2_in_frame_list_picked_over_settings():
    """When tshark emits HEADERS + SETTINGS in the same packet (list),
    pick the dict with real header info, not the SETTINGS frame."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:02",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": [
                # SETTINGS first
                {"http2_http2_type": "4", "http2_http2_streamid": "0"},
                # HEADERS second
                {"http2_http2_type": "1", "http2_http2_streamid": "5",
                 "http2_http2_headers_method": "GET",
                 "http2_http2_headers_authority": "claude.ai",
                 "http2_http2_headers_path": "/api/x"},
            ],
        },
    )
    ev = event_from_record(rec)
    assert ev is not None
    assert ev.method == "GET"
    assert ev.host == "claude.ai"
    assert ev.path == "/api/x"
    assert ev.stream_id == "5"


def test_event_pair_key_stable_across_request_response():
    """The same (tcp_stream, http2_stream_id) must produce identical pair_key
    on request and response so pairing works."""
    req_rec = _make_http2_request_record()
    req_ev = event_from_record(req_rec)
    resp_rec = EkRecord(
        timestamp="2026-05-15T10:00:03",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": {
                "http2_http2_streamid": "13",
                "http2_http2_header_name": [":status"],
                "http2_http2_header_value": ["200"],
            },
        },
    )
    resp_ev = event_from_record(resp_rec)
    assert req_ev.pair_key == resp_ev.pair_key


# ---------------------------------------------------------------------------
# build_capture_from_pair
# ---------------------------------------------------------------------------


def test_build_capture_from_pair_request_only():
    """Request without matching response → 1 row kwargs."""
    req_ev = event_from_record(_make_http2_request_record())
    rows = build_capture_from_pair(req_ev, None, pair_id="pair-xyz")
    assert len(rows) == 1
    r = rows[0]
    assert r["direction"] == "request"
    assert r["pair_id"] == "pair-xyz"
    assert r["host"] == "claude.ai"
    assert r["source_id"] == "sslkeylog-default"
    assert r["body_format"] == "json"


def test_build_capture_from_pair_response_only():
    """Orphan response (we joined late, missed the request side)."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:03",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": {
                "http2_http2_streamid": "13",
                "http2_http2_header_name": [":status"],
                "http2_http2_header_value": ["200"],
            },
        },
    )
    resp_ev = event_from_record(rec)
    rows = build_capture_from_pair(None, resp_ev, pair_id="pair-orphan")
    assert len(rows) == 1
    assert rows[0]["direction"] == "response"
    assert rows[0]["status_code"] == 200


def test_build_capture_from_pair_both_sides():
    req_ev = event_from_record(_make_http2_request_record())
    resp_rec = EkRecord(
        timestamp="2026-05-15T10:00:03",
        layers={
            "tcp": {"tcp_tcp_stream": "11"},
            "http2": {
                "http2_http2_streamid": "13",
                "http2_http2_header_name": [":status"],
                "http2_http2_header_value": ["200"],
            },
        },
    )
    resp_ev = event_from_record(resp_rec)
    rows = build_capture_from_pair(req_ev, resp_ev, pair_id="pair-both")
    assert len(rows) == 2
    assert rows[0]["direction"] == "request"
    assert rows[1]["direction"] == "response"
    # Same pair_id binds them
    assert rows[0]["pair_id"] == "pair-both" == rows[1]["pair_id"]
    # Same source_id
    assert rows[0]["source_id"] == "sslkeylog-default"
    assert rows[1]["source_id"] == "sslkeylog-default"


def test_build_capture_provider_inference():
    """provider field is filled from the host."""
    req_ev = event_from_record(_make_http1_request_record())  # api.anthropic.com
    rows = build_capture_from_pair(req_ev, None, pair_id="p")
    assert rows[0]["provider"] == "anthropic"

    # Also test a HTTP/2 capture for claude.ai → anthropic
    rows = build_capture_from_pair(
        event_from_record(_make_http2_request_record()),
        None, pair_id="p",
    )
    assert rows[0]["provider"] == "anthropic"


def test_build_capture_redacts_secret_headers():
    """Authorization / Cookie / x-api-key etc should be redacted in
    headers_redacted_json."""
    rec = EkRecord(
        timestamp="2026-05-15T10:00:00",
        layers={
            "tcp": {"tcp_tcp_stream": "7"},
            "http2": {
                "http2_http2_streamid": "1",
                "http2_http2_header_name": [":method", ":authority", ":path",
                                            "authorization", "x-api-key", "cookie",
                                            "user-agent"],
                "http2_http2_header_value": ["POST", "api.anthropic.com", "/v1/messages",
                                              "Bearer secret123", "sk-secret",
                                              "session=value", "Chrome/148.0"],
            },
        },
    )
    ev = event_from_record(rec)
    rows = build_capture_from_pair(ev, None, pair_id="p")
    headers_json = rows[0]["headers_redacted_json"]
    assert "[REDACTED]" in headers_json
    assert "Bearer secret123" not in headers_json
    assert "sk-secret" not in headers_json
    assert "session=value" not in headers_json
    assert "Chrome/148.0" in headers_json  # non-secret header preserved


def test_build_capture_decompresses_gzip_body():
    """A response whose body is gzip-compressed (Content-Encoding: gzip)
    should land as decoded plaintext in body_text_or_json, not as the
    binary gzip bytes interpreted as UTF-8 (which would be a wall of
    replacement chars)."""
    import gzip
    plaintext = b'{"id":"msg_42","content":"hello world"}'
    gz_body = gzip.compress(plaintext)
    # Build a response event with the gzip body + matching Content-Encoding
    resp_ev = TsharkEvent(
        direction="response", host="", path="", method="",
        status_code=200,
        headers={"content-encoding": "gzip", "content-type": "application/json"},
        body=gz_body,
        stream_id="1", tcp_stream="10",
        timestamp="2026-05-15T10:00:00",
        is_http2=True,
    )
    rows = build_capture_from_pair(None, resp_ev, pair_id="p")
    assert len(rows) == 1
    body_text = rows[0]["body_text_or_json"]
    assert "msg_42" in body_text, (
        f"expected gzip body to be decompressed; got {body_text!r}"
    )
    assert rows[0]["body_format"] == "json"


def test_build_capture_decompress_failure_keeps_raw_bytes():
    """If decompression fails (e.g. truncated gzip), the raw bytes should
    survive into body_text_or_json (as replacement chars). We must not
    drop the row or raise."""
    resp_ev = TsharkEvent(
        direction="response", host="", path="", method="",
        status_code=200,
        headers={"content-encoding": "gzip"},
        body=b"not actually gzip",  # invalid gzip
        stream_id="1", tcp_stream="10",
        timestamp="2026-05-15T10:00:00",
        is_http2=True,
    )
    rows = build_capture_from_pair(None, resp_ev, pair_id="p")
    assert len(rows) == 1  # didn't crash
    body_text = rows[0]["body_text_or_json"]
    assert "not actually gzip" in body_text


def test_build_capture_decompresses_zstd_body_without_content_size():
    """Zstd frames don't always declare their uncompressed size; the
    parser must fall back to stream_reader when ``decompress(body)``
    fails with ZstdError 'could not determine content size in frame
    header'. Regression for a real-world failure in W2.2 live captures
    where Claude.ai / Cloudflare zstd responses produced empty bodies."""
    try:
        import zstandard
    except ImportError:
        pytest.skip("zstandard not installed")
    import io
    plaintext = b'{"text": "hello from zstd"}' * 50  # > a few KB
    # Build a zstd frame WITHOUT declaring content size — this is what
    # streaming servers (Cloudflare, etc.) emit. The ``stream_writer``
    # API doesn't auto-fill content_size unless size is provided up
    # front; equivalent of feeding chunks to a wire pipe.
    cctx = zstandard.ZstdCompressor()
    buf = io.BytesIO()
    with cctx.stream_writer(buf, closefd=False) as writer:
        writer.write(plaintext)
    zstd_bytes = buf.getvalue()
    resp_ev = TsharkEvent(
        direction="response", host="", path="", method="",
        status_code=200,
        headers={"content-encoding": "zstd", "content-type": "application/json"},
        body=zstd_bytes,
        stream_id="1", tcp_stream="10",
        timestamp="2026-05-15T10:00:00",
        is_http2=True,
    )
    rows = build_capture_from_pair(None, resp_ev, pair_id="p")
    body_text = rows[0]["body_text_or_json"]
    assert "hello from zstd" in body_text, (
        f"zstd without content-size header should decompress via "
        f"stream_reader; got {body_text[:80]!r}"
    )


def test_build_capture_identity_encoding_passthrough():
    """Content-Encoding: identity (or empty) should leave the body alone."""
    resp_ev = TsharkEvent(
        direction="response", host="", path="", method="",
        status_code=200,
        headers={"content-encoding": "identity"},
        body=b"plain {\"x\": 1}",
        stream_id="1", tcp_stream="10",
        timestamp="2026-05-15T10:00:00",
        is_http2=True,
    )
    rows = build_capture_from_pair(None, resp_ev, pair_id="p")
    assert rows[0]["body_text_or_json"] == "plain {\"x\": 1}"


def test_build_capture_meta_carries_v_green_tier():
    """meta_json marks the row as V-GREEN clean evidence (A2 path)."""
    req_ev = event_from_record(_make_http2_request_record())
    rows = build_capture_from_pair(req_ev, None, pair_id="p")
    meta = json.loads(rows[0]["meta_json"])
    assert meta["evidence_tier"] == "V-GREEN"
    assert meta["compliance_path"] == "A2_SSLKEYLOGFILE"
    assert meta["is_http2"] is True
    assert meta["http2_stream_id"] == "13"
    assert meta["tcp_stream"] == "11"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
