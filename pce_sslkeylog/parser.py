# SPDX-License-Identifier: Apache-2.0
"""Parser for tshark Elastic-style (-T ek) NDJSON HTTP/HTTP2 events.

The tshark command we use is approximately::

    tshark -i <iface> -f "host <hosts>" -o tls.keylog_file:<path> \\
           -Y "http or http2" -T ek

This emits two kinds of NDJSON lines:

1. ``index`` lines: ``{"index": {"_index": "packets-...", "_type": "doc"}}``
   — skipped by the parser, they're Elasticsearch bulk-format metadata.

2. ``source`` lines: ``{"timestamp": "...", "layers": {...}}`` — these
   carry the actual decoded packet data. We're interested in events
   whose ``layers`` include ``http`` or ``http2`` sub-objects.

Output of this parser:

- ``parse_ek_line(line: str) -> EkRecord | None`` — convert one NDJSON
  line into a typed record; returns None for index lines + malformed
  rows.
- ``TsharkEvent`` — the structured event we extract from each record
  (host / path / direction / headers / body / pair-key).
- ``build_capture_from_pair(req, resp) -> dict`` — given a matched
  request + response event, build a kwargs dict ready to hand to
  ``pce_core.db.insert_capture``.

Why not use scapy / cryptography / h2 directly: tshark already handles
TLS state machine + HTTP/2 frame reassembly + WebSocket. Rolling our
own would be ~50-80h with subtle bugs (RFC 7540 stream multiplexing,
TLS 1.3 0-RTT, etc.). tshark is the industry-standard implementation;
we wrap it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("pce.sslkeylog.parser")


# ---------------------------------------------------------------------------
# Low-level NDJSON line parsing
# ---------------------------------------------------------------------------


@dataclass
class EkRecord:
    """One decoded NDJSON source line from ``tshark -T ek``."""

    timestamp: str            # ISO 8601-ish from tshark, e.g. "May 15, 2026 10:00:00.123456"
    layers: dict[str, Any]    # everything tshark put under "layers"

    @property
    def is_http(self) -> bool:
        return "http" in self.layers or "http2" in self.layers

    @property
    def is_http2(self) -> bool:
        return "http2" in self.layers

    @property
    def has_request_line(self) -> bool:
        # Either HTTP/1.1 (http.request.method) or HTTP/2 (http2.headers.method)
        if "http" in self.layers:
            return any(
                k.endswith("request_line") or k.endswith("request_method") or
                k.endswith("request_uri")
                for k in _flat_keys(self.layers["http"])
            )
        if "http2" in self.layers:
            h2 = self.layers["http2"]
            return any(
                ":method" in str(v).lower() or k.endswith("headers_method")
                for k, v in _flat_kvs(h2)
            )
        return False

    @property
    def has_response_status(self) -> bool:
        if "http" in self.layers:
            return any(
                k.endswith("response_code") or k.endswith("response_phrase")
                for k in _flat_keys(self.layers["http"])
            )
        if "http2" in self.layers:
            return any(
                ":status" in str(v).lower() or k.endswith("headers_status")
                for k, v in _flat_kvs(self.layers["http2"])
            )
        return False


def parse_ek_line(line: str) -> Optional[EkRecord]:
    """Parse one line of tshark -T ek output.

    Returns None for:
    - blank lines
    - index lines (``{"index":{...}}``)
    - JSON parse errors
    - source records without ``layers``

    Otherwise returns an ``EkRecord`` with timestamp + layers.
    """
    s = line.strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        logger.debug("ek parse: bad JSON line of len %d", len(s))
        return None
    if not isinstance(obj, dict):
        return None
    # Skip "index" metadata lines
    if "index" in obj and "layers" not in obj:
        return None
    layers = obj.get("layers")
    if not isinstance(layers, dict):
        return None
    ts = obj.get("timestamp") or ""
    if not isinstance(ts, str):
        ts = str(ts)
    return EkRecord(timestamp=ts, layers=layers)


# ---------------------------------------------------------------------------
# Structured event extraction
# ---------------------------------------------------------------------------


@dataclass
class TsharkEvent:
    """Structured HTTP event extracted from one tshark EK record.

    Either a request or a response; ``direction`` field discriminates.
    """

    direction: str                  # "request" or "response"
    host: str                       # ``Host:`` or HTTP/2 ``:authority``
    path: str                       # request path (or empty on response)
    method: str                     # GET/POST/... (only on request, otherwise "")
    status_code: Optional[int]      # only on response
    headers: dict[str, str]         # decoded headers, lowercase keys
    body: bytes                     # reassembled body (may be b"" if absent)
    stream_id: Optional[str]        # HTTP/2 stream id when known; else None
    tcp_stream: Optional[str]       # TCP "tcp.stream" index — sticky per TCP connection
    timestamp: str                  # propagated from the source record
    is_http2: bool                  # True if HTTP/2 frame
    pair_key: str = field(init=False)  # set in __post_init__

    def __post_init__(self) -> None:
        # pair_key joins request+response that belong to the same exchange:
        # - HTTP/2: (tcp_stream, stream_id)
        # - HTTP/1: (tcp_stream, ts_window) — best-effort; we use tcp_stream
        #   for now and let the caller dedup by host+path proximity.
        parts: list[str] = []
        if self.tcp_stream:
            parts.append(f"tcp{self.tcp_stream}")
        if self.stream_id:
            parts.append(f"s{self.stream_id}")
        if not parts:
            # Worst-case fallback: hash of timestamp+host+path
            parts.append(
                hashlib.sha256(
                    f"{self.timestamp}|{self.host}|{self.path}".encode("utf-8")
                ).hexdigest()[:16]
            )
        self.pair_key = ":".join(parts)


def _flat_keys(obj: Any, prefix: str = "") -> list[str]:
    """Flatten nested dict keys for substring matching."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            out.append(full)
            if isinstance(v, (dict, list)):
                out.extend(_flat_keys(v, full))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flat_keys(v, f"{prefix}[{i}]"))
    return out


def _flat_kvs(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten nested dict to a list of (full_key, value) tuples."""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.extend(_flat_kvs(v, full))
            else:
                out.append((full, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                out.extend(_flat_kvs(v, f"{prefix}[{i}]"))
            else:
                out.append((f"{prefix}[{i}]", v))
    return out


def _get_field(d: dict[str, Any], *candidates: str) -> Optional[str]:
    """Best-effort getter that tries multiple keys (tshark field names vary
    by version)."""
    for c in candidates:
        if c in d:
            v = d[c]
            if isinstance(v, list):
                v = v[0] if v else None
            if v is not None:
                return str(v)
    return None


_SUBSTANTIVE_HTTP_KEYS: frozenset[str] = frozenset({
    "http_http_request_method", "http.request.method", "http_request_method",
    "http_http_response_code", "http.response.code", "http_response_code",
    "http_http_host", "http.host", "http_host",
    "http_http_request_uri", "http.request.uri", "http_request_uri",
})
# For HTTP/2, only HEADERS frames (type=1) carry request/response info.
# tshark batches SETTINGS / WINDOW_UPDATE / PING frames into the same packet
# as HEADERS, so just having a ``http2_http2_streamid`` is not enough — we
# need actual header data (parallel ``header_name`` array OR an individual
# ``headers_method`` / ``headers_status`` field).
_SUBSTANTIVE_HTTP2_KEYS: frozenset[str] = frozenset({
    "http2_http2_header_name", "http2.header.name",
    "http2_http2_headers_method", "http2_http2_headers_status",
    "http2_http2_headers_authority", "http2_http2_headers_path",
})


def _is_substantive(d: dict[str, Any], substantive_keys: frozenset[str]) -> bool:
    return any(k in d for k in substantive_keys)


def _layer_as_dict(v: Any, substantive_keys: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Normalise a tshark layer value into a single dict.

    tshark ``-T ek`` often emits a layer as a *list of dicts* — most
    commonly when a packet has been reassembled through an HTTPS
    ``CONNECT`` tunnel. In that case the list looks like::

        [
            {"http_http_proxy_connect_port": "443",
             "http_http_proxy_connect_host": "api.anthropic.com"},   # tunnel meta
            {"http_http_request_method": "GET",
             "http_http_host": "api.anthropic.com", ...}              # inner HTTP
        ]

    We want the second dict (the actual decrypted HTTP). If a
    ``substantive_keys`` set is provided, we scan the list and return
    the first item containing any of those keys; otherwise we fall back
    to the first non-empty dict. Callers that need to emit *multiple*
    events from one record should use :func:`_layer_as_dicts` instead.
    """
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        if substantive_keys:
            for item in v:
                if isinstance(item, dict) and _is_substantive(item, substantive_keys):
                    return item
        for item in v:
            if isinstance(item, dict) and item:
                return item
    return {}


def _layer_as_dicts(v: Any) -> list[dict[str, Any]]:
    """Return *every* dict in a tshark layer value, dropping empties.

    For multi-message packets we may need to emit one event per dict;
    callers iterate and call :func:`event_from_record` per item.
    """
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [item for item in v if isinstance(item, dict) and item]
    return []


def event_from_record(rec: EkRecord) -> Optional[TsharkEvent]:
    """Convert an ``EkRecord`` to a structured ``TsharkEvent``.

    Returns None if the record doesn't contain a recognisable HTTP
    request or response (e.g. it's a TLS-only frame, or HTTP/2
    SETTINGS frame).
    """
    layers = rec.layers
    tcp_stream = _get_field(_layer_as_dict(layers.get("tcp", {})),
                             "tcp_tcp_stream", "tcp.stream")

    # IMPORTANT: check HTTP/2 BEFORE HTTP/1. When a CONNECT tunnel carries
    # HTTP/2 inside, tshark emits BOTH a "http" layer (the outer CONNECT
    # metadata) AND a "http2" layer (the decrypted inner traffic). If we
    # match "http" first, we'd return a CONNECT-meta-only event and skip
    # the real HTTP/2 content. Process HTTP/2 first; fall through to
    # HTTP/1 only if there's no substantive HTTP/2 frame.
    if "http2" in layers:
        h2_event = _extract_http2_event(layers, tcp_stream, rec.timestamp)
        if h2_event is not None:
            return h2_event

    if "http" in layers:
        # When tshark sees a CONNECT tunnel + decrypted inner HTTP/1
        # in the same packet, ``layers["http"]`` is a list whose first
        # element is the tunnel metadata (``http_http_proxy_connect_*``)
        # and second is the real inner request. Pick the substantive one.
        h = _layer_as_dict(layers["http"], _SUBSTANTIVE_HTTP_KEYS)
        # HTTP/1.x: request side has "http_http_request_method" or similar
        method = _get_field(
            h, "http_http_request_method", "http.request.method",
            "http_request_method",
        )
        status_code = _get_field(
            h, "http_http_response_code", "http.response.code",
            "http_response_code",
        )
        host = _get_field(h, "http_http_host", "http.host", "http_host") or ""
        path = _get_field(
            h, "http_http_request_uri", "http.request.uri", "http_request_uri",
        ) or ""
        # Body (decoded if tshark could): "http_http_file_data" or
        # "http.file_data"; tshark returns it as a string (already decoded
        # from chunked / gzip when the SSLKEYLOGFILE lets it decrypt).
        body_text = _get_field(
            h, "http_http_file_data", "http.file_data", "http_file_data",
        ) or ""
        body = body_text.encode("utf-8", errors="replace") if body_text else b""
        # Headers: tshark flattens HTTP/1 headers into "http_http_header_*"
        headers = _extract_http1_headers(h)
        if method:
            return TsharkEvent(
                direction="request", host=host, path=path, method=method,
                status_code=None, headers=headers, body=body,
                stream_id=None, tcp_stream=tcp_stream,
                timestamp=rec.timestamp, is_http2=False,
            )
        if status_code:
            try:
                sc = int(status_code)
            except (TypeError, ValueError):
                sc = None
            return TsharkEvent(
                direction="response", host=host, path="", method="",
                status_code=sc, headers=headers, body=body,
                stream_id=None, tcp_stream=tcp_stream,
                timestamp=rec.timestamp, is_http2=False,
            )
        return None

    return None


def _extract_http2_event(
    layers: dict[str, Any], tcp_stream: Optional[str], timestamp: str,
) -> Optional[TsharkEvent]:
    """Extract one HTTP/2 request/response event from a layers dict.

    Returns None when the packet contains only non-HEADERS frames
    (SETTINGS, WINDOW_UPDATE, PING, etc.) or only the HTTP/2 magic
    preface. Called *before* the HTTP/1.x branch in ``event_from_record``
    because a CONNECT-tunnel HTTP/2 packet also has a ``http`` layer
    (the outer CONNECT metadata) — we must give HTTP/2 priority.
    """
    h2 = _layer_as_dict(layers["http2"], _SUBSTANTIVE_HTTP2_KEYS)
    if not h2:
        return None
    h2_headers = _extract_http2_headers(h2)
    if not h2_headers:
        # No real HEADERS frame (only SETTINGS / WINDOW_UPDATE / etc.)
        return None
    stream_id = _get_field(h2, "http2_http2_streamid", "http2.streamid",
                            "http2_streamid")
    method = h2_headers.get(":method")
    status = h2_headers.get(":status")
    host = h2_headers.get(":authority") or h2_headers.get("host", "")
    path = h2_headers.get(":path", "")
    # HTTP/2 reassembled body — same as http.file_data when present,
    # else look at the http2.body field
    body_text = _get_field(
        h2, "http2_http2_body_fragment", "http2.body.fragment",
        "http2_body_fragment",
    ) or ""
    if not body_text and "http" in layers:
        body_text = _get_field(
            _layer_as_dict(layers["http"], _SUBSTANTIVE_HTTP_KEYS),
            "http_http_file_data", "http.file_data",
        ) or ""
    body = body_text.encode("utf-8", errors="replace") if body_text else b""
    # Normalize HTTP/2 headers (drop :pseudo-headers from the captured
    # set since we already pull them out as fields above)
    norm_headers = {k.lower(): v for k, v in h2_headers.items()
                     if not k.startswith(":")}

    if method:
        return TsharkEvent(
            direction="request", host=host, path=path, method=method,
            status_code=None, headers=norm_headers, body=body,
            stream_id=stream_id, tcp_stream=tcp_stream,
            timestamp=timestamp, is_http2=True,
        )
    if status:
        try:
            sc = int(status)
        except (TypeError, ValueError):
            sc = None
        return TsharkEvent(
            direction="response", host=host, path="", method="",
            status_code=sc, headers=norm_headers, body=body,
            stream_id=stream_id, tcp_stream=tcp_stream,
            timestamp=timestamp, is_http2=True,
        )
    return None


def _extract_http1_headers(h: dict[str, Any]) -> dict[str, str]:
    """Pull HTTP/1.x headers out of the flat tshark dict.

    tshark exposes HTTP/1 headers as keys like ``http_http_header_*`` or
    a parallel ``http.header`` array. We unify both.
    """
    headers: dict[str, str] = {}
    for k, v in h.items():
        kl = k.lower()
        if not (kl.startswith("http_http_") or kl.startswith("http.")):
            continue
        # Skip the request_method / response_code keys we already picked out
        if any(suffix in kl for suffix in (
            "request_method", "request_uri", "request_line", "request_version",
            "response_code", "response_phrase", "response_line", "response_version",
            "file_data", "host",
        )):
            continue
        # Header name in tshark is the trailing token after the prefix
        name = kl.rsplit("_", 1)[-1] if "_" in kl else kl.rsplit(".", 1)[-1]
        val = v if isinstance(v, str) else (v[0] if isinstance(v, list) and v else "")
        if isinstance(val, str) and val:
            headers[name] = val
    return headers


def _extract_http2_headers(h2: dict[str, Any]) -> dict[str, str]:
    """Pull HTTP/2 headers out of tshark's exposed fields.

    Supports two tshark output styles:
      1. Parallel ``http2_http2_header_name`` / ``http2_http2_header_value``
         arrays (older / one-frame-per-event format).
      2. Individual ``http2_http2_headers_<name>`` fields where ``<name>`` is
         the header name with dashes replaced by underscores (newer tshark).
         Pseudo-headers come through as ``http2_http2_headers_method``,
         ``http2_http2_headers_authority``, etc. — *without* the leading
         colon. We re-add the colon for those.
    """
    headers: dict[str, str] = {}

    # Style 1: parallel arrays
    names = h2.get("http2_http2_header_name") or h2.get("http2.header.name")
    values = h2.get("http2_http2_header_value") or h2.get("http2.header.value")
    if isinstance(names, str):
        names = [names]
    if isinstance(values, str):
        values = [values]
    if isinstance(names, list) and isinstance(values, list):
        for n, v in zip(names, values):
            if isinstance(n, str) and isinstance(v, str):
                headers[n.lower()] = v

    # Style 2: individual ``http2_http2_headers_<name>`` fields
    _PSEUDO = frozenset({"method", "authority", "path", "scheme", "status"})
    for k, v in h2.items():
        if not isinstance(k, str) or not k.startswith("http2_http2_headers_"):
            continue
        if not isinstance(v, (str, list)):
            continue
        # Take first if list
        if isinstance(v, list):
            v = v[0] if v else ""
        if not isinstance(v, str):
            continue
        name = k[len("http2_http2_headers_"):]  # e.g. "method", "user_agent"
        # Pseudo-header re-decoration: "method" -> ":method"
        if name in _PSEUDO:
            name = ":" + name
        else:
            # Convert underscores back to dashes for normal headers
            name = name.replace("_", "-")
        if name and name not in headers:
            headers[name.lower()] = v
    return headers


# ---------------------------------------------------------------------------
# Pair construction (for downstream insert_capture)
# ---------------------------------------------------------------------------


def build_capture_from_pair(
    req: Optional[TsharkEvent],
    resp: Optional[TsharkEvent],
    *,
    pair_id: str,
    source_id: str = "sslkeylog-default",
) -> list[dict[str, Any]]:
    """Construct kwargs dicts for ``pce_core.db.insert_capture``.

    Returns 0, 1, or 2 kwargs dicts (request side, response side) ready
    to be passed via ``**kwargs`` to ``insert_capture``. Each carries
    ``source_id``, the shared ``pair_id`` joining them, and the
    structured body/headers from tshark.
    """
    out: list[dict[str, Any]] = []
    for ev in (req, resp):
        if ev is None:
            continue
        body_text = ev.body.decode("utf-8", errors="replace") if ev.body else ""
        # Decide body_format: JSON if body parses, else text
        body_fmt = "text"
        if body_text:
            stripped = body_text.lstrip()
            if stripped.startswith(("{", "[")):
                body_fmt = "json"
        out.append(dict(
            direction=ev.direction,
            pair_id=pair_id,
            host=ev.host,
            path=ev.path,
            method=ev.method,
            provider=_provider_from_host(ev.host),
            status_code=ev.status_code,
            body_text_or_json=body_text,
            body_format=body_fmt,
            headers_redacted_json=_serialize_headers(ev.headers),
            source_id=source_id,
            meta_json=_build_meta(ev),
        ))
    return out


def _provider_from_host(host: str) -> str:
    """Same mapping as pce_proxy/addon.py::_provider_from_host."""
    if not host:
        return ""
    h = host.lower()
    if "anthropic" in h or "claude.ai" in h:
        return "anthropic"
    if "openai" in h or "chatgpt" in h:
        return "openai"
    if "google" in h or "gemini" in h:
        return "google"
    if "x.ai" in h or "grok.com" in h:
        return "xai"
    if "perplexity" in h:
        return "perplexity"
    return ""


def _serialize_headers(headers: dict[str, str]) -> str:
    """Drop secret-bearing headers, JSON-serialize the rest."""
    redacted = {
        k: ("[REDACTED]" if k.lower() in (
            "authorization", "cookie", "set-cookie", "x-api-key",
            "anthropic-version", "openai-organization",
        ) else v)
        for k, v in headers.items()
    }
    return json.dumps(redacted, ensure_ascii=False)


def _build_meta(ev: TsharkEvent) -> str:
    """Per-row metadata for raw_captures.meta_json."""
    return json.dumps({
        "tshark_timestamp": ev.timestamp,
        "tcp_stream": ev.tcp_stream,
        "http2_stream_id": ev.stream_id,
        "is_http2": ev.is_http2,
        "evidence_tier": "V-GREEN",  # see §1.0 of REDUNDANCY-AUDIT-MATRIX
        "compliance_path": "A2_SSLKEYLOGFILE",
    }, ensure_ascii=False)
