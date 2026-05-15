# SPDX-License-Identifier: Apache-2.0
"""PCE Proxy – mitmproxy addon.

This addon sits inside the mitmproxy pipeline and:
1. Checks every request against the AI-domain allowlist.
2. For matching requests, records the request *before* forwarding.
3. After the response arrives, records the response.
4. All persistence is fail-safe – errors are logged, never raised.

Usage:
    mitmdump -s pce_proxy/addon.py -p 8080 --set stream_large_bodies=1m
"""

import json
import logging
import time
from typing import Optional

from mitmproxy import http

from .config import ALLOWED_HOSTS
from .db import init_db, insert_capture, new_pair_id
from .redact import redact_headers_json, safe_body_text
from pce_core.normalizer.pipeline import try_normalize_pair
from pce_core.db import SOURCE_PROXY, record_pipeline_error, record_tls_failure
from pce_core.config import CAPTURE_MODE, CaptureMode
from pce_core.logging_config import configure_logging, log_event
from pce_core.redact import redact_body_secrets

# Install PCE's structured logger. Idempotent – a no-op if the server
# process has already configured logging.
configure_logging()
logger = logging.getLogger("pce.addon")

# Ensure DB is ready when the addon is loaded
init_db()

log_event(logger, "proxy.addon_loaded", capture_mode=CAPTURE_MODE.value)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_host(flow: http.HTTPFlow) -> str:
    """Extract the effective hostname from a flow."""
    candidate = flow.request.pretty_host
    if candidate:
        return candidate
    return flow.request.headers.get("Host", "").split(":")[0]


def _is_allowlisted(host: str) -> bool:
    """Check if host is on the static allowlist or dynamic custom domains."""
    if host in ALLOWED_HOSTS:
        return True
    # Check dynamic custom domains (loaded from DB via server API)
    try:
        from pce_core.db import get_custom_domains
        return host in get_custom_domains()
    except Exception:
        return False


def _resolve_host(flow: http.HTTPFlow) -> str | None:
    """Return the target host if it should be captured, else None.

    Behaviour depends on CAPTURE_MODE:
      - ALLOWLIST: only static allowlist + custom domains
      - SMART: allowlist + heuristic AI detection
      - ALL: capture everything
    """
    host = _get_host(flow)
    if not host:
        return None

    # Always capture allowlisted hosts
    if _is_allowlisted(host):
        return host

    # In SMART mode, defer decision — return None here, heuristic runs in request()
    # In ALL mode, capture everything
    if CAPTURE_MODE == CaptureMode.ALL:
        return host

    return None


def _provider_from_host(host: str) -> str:
    """Derive a short provider label from hostname."""
    if "openai" in host:
        return "openai"
    if "anthropic" in host:
        return "anthropic"
    if "googleapis" in host:
        return "google"
    return host


def _extract_model(body_bytes: bytes) -> Optional[str]:
    """Best-effort extraction of the model name from a JSON request body."""
    try:
        data = json.loads(body_bytes)
        return data.get("model")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# TLS failure categorisation — pinning detection (P5.A-6, UCS §3.2)
# ---------------------------------------------------------------------------

def _categorize_tls_error(error: str) -> str:
    """Map a raw TLS error string to a stable category slug.

    Categories are consumed by ``/api/v1/health/pinning`` to group hosts
    by failure kind, so the slugs must stay stable even as OpenSSL error
    text varies across versions. Unknown errors fall through to
    ``"unknown"`` — the UI treats anything unrecognised as "likely a TLS
    config issue, investigate the raw message".
    """
    if not error:
        return "unknown"
    e = error.lower()
    # Order matters: more specific matches first.
    if "pinning" in e or "pinned" in e or "pin verification" in e:
        return "pinning_explicit"
    if "certificate_unknown" in e or "unknown_ca" in e or "unknown ca" in e:
        return "cert_unknown_ca"
    if "certificate" in e and ("reject" in e or "invalid" in e or "verify" in e):
        return "cert_verify_failed"
    if "bad certificate" in e or "bad_certificate" in e:
        return "cert_rejected"
    if "handshake_failure" in e or "handshake failure" in e:
        return "handshake_failure"
    if "timeout" in e or "timed out" in e:
        return "handshake_timeout"
    if "protocol" in e and "version" in e:
        return "protocol_version"
    if "ssl" in e or "tls" in e:
        return "tls_other"
    return "unknown"


def _extract_tls_host(tls_data: object) -> str:
    """Best-effort host extraction from a mitmproxy ``tls.TlsData`` object.

    mitmproxy exposes the SNI on ``data.conn.sni`` (bytes since 10.x,
    str in 11.x). We defensively decode either shape and fall back to
    the peer address only if no SNI is available — peer IP is less
    useful for pinning attribution but better than nothing.
    """
    try:
        conn = getattr(tls_data, "conn", None)
        if conn is not None:
            sni = getattr(conn, "sni", None)
            if isinstance(sni, bytes):
                try:
                    sni = sni.decode("ascii", errors="replace")
                except Exception:
                    sni = ""
            if sni:
                return str(sni)
        # Fallback: peername may be on conn or on context.client
        context = getattr(tls_data, "context", None)
        client = getattr(context, "client", None) if context else None
        peername = getattr(client, "peername", None) if client else None
        if peername and len(peername) >= 1:
            return str(peername[0])
    except Exception:  # pragma: no cover — defensive
        pass
    return ""


# ---------------------------------------------------------------------------
# SMART mode: auto-register newly discovered AI domains
# ---------------------------------------------------------------------------
_discovered_domains: set[str] = set()  # in-memory cache to avoid repeated DB writes


def _register_discovered_domain(host: str, confidence: str, reasons: list[str]) -> None:
    """Record a newly discovered AI domain (SMART mode)."""
    if host in _discovered_domains or _is_allowlisted(host):
        return
    _discovered_domains.add(host)
    log_event(
        logger, "smart.domain_discovered",
        host=host, confidence=confidence, reasons=reasons,
    )
    try:
        from pce_core.db import add_custom_domain
        add_custom_domain(host, source="smart_heuristic", confidence=confidence, reason=", ".join(reasons))
    except Exception:
        logger.exception("Failed to register discovered domain %s", host)


# ---------------------------------------------------------------------------
# State kept per-flow to link request ↔ response
# ---------------------------------------------------------------------------
_flow_meta: dict[str, dict] = {}

# WebSocket session state — populated on first websocket_message for an
# allowlisted flow, cleared on websocket_end. One pair_id per WS session
# so all frames in the session are queryable by pair_id.
_ws_state: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Per-host streaming (HARVEST-SESSION-S1 F3 — replaces global
# `--set stream_large_bodies=1k` which broke other apps' gRPC streams).
#
# Default mitmproxy buffers the entire response before forwarding to the
# client. Streaming clients (SSE, gRPC-web, Connect) refuse to function
# under buffering — observed: Cursor chat reports
# "Streaming responses are being buffered by a proxy" and aborts.
#
# Setting `flow.response.stream` to a callable (or True) tells mitmproxy
# to forward chunks as they arrive. The callable variant lets us tee
# each chunk into a list so the full body is still recorded after the
# stream completes, giving Cursor a working chat AND PCE a complete
# fixture body.
#
# We opt in on two axes:
#   (a) Content-Type: SSE / gRPC / Connect protocols always stream.
#   (b) Host allowlist: known IDE-class hosts whose responses are
#       streaming even when they return Content-Type: application/json
#       (some gRPC-over-h2 implementations don't set the gRPC content-type).
# ---------------------------------------------------------------------------

# Content types that ALWAYS imply incremental delivery. Lower-cased, prefix-matched.
STREAMING_CONTENT_TYPE_PREFIXES = (
    "text/event-stream",         # Classic SSE — OpenAI, Anthropic, Cursor agent, Claude
    "application/grpc",          # gRPC (covers grpc, grpc-web, grpc+proto)
    "application/connect",       # Connect / Connect-Web (cursor.sh uses this)
    "application/x-ndjson",      # Newline-delimited JSON (Ollama, some others)
)

# Hosts where responses are streamed regardless of advertised content-type.
# Discovered empirically from HARVEST-SESSION-S1; safer than global setting
# because non-listed hosts (e.g. Windsurf Cascade's own gRPC stream) keep
# the default buffered behaviour and are unaffected.
STREAMING_HOSTS: set[str] = {
    "agent.api5.cursor.sh",
    "server.codeium.com",
    "server.self-serve.windsurf.com",
    "inference.codeium.com",
    "chatgpt.com",
    "chat.openai.com",
    "claude.ai",
    "api.openai.com",
    "api.anthropic.com",
}


def _decompress_streamed_body(body: bytes, content_encoding: str) -> bytes:
    """Decompress a body whose raw bytes were teed from the streaming hook.

    The streaming `flow.response.stream = _tee` callback in
    `responseheaders()` captures bytes as they leave the wire — i.e.
    pre-decompression. By contrast, `flow.response.content` would have
    applied Content-Encoding automatically. To keep storage uniform
    (plaintext JSON / SSE in body_text_or_json), we apply the same
    decoding manually for the streaming branch.

    Supports gzip / deflate / br / zstd; raises on unknown encoding so
    the caller can fall back to raw bytes + log a warning.
    """
    enc = (content_encoding or "").lower().strip()
    if not body or not enc or enc in ("identity", "none"):
        return body
    if enc == "gzip":
        import gzip
        return gzip.decompress(body)
    if enc == "deflate":
        import zlib
        # RFC 2616 "deflate" is ambiguous; try raw deflate first, then zlib-wrapped
        try:
            return zlib.decompress(body, -zlib.MAX_WBITS)
        except zlib.error:
            return zlib.decompress(body)
    if enc == "br":
        import brotli  # type: ignore[import-not-found]
        return brotli.decompress(body)
    if enc == "zstd":
        import zstandard  # type: ignore[import-not-found]
        return zstandard.ZstdDecompressor().decompress(body)
    raise ValueError(f"unsupported content-encoding: {enc}")


def _should_stream(host: str, content_type: str) -> bool:
    """Decide whether to stream a response chunk-by-chunk.

    Args:
        host: Hostname (already lower-cased by mitmproxy).
        content_type: Raw Content-Type response header value.

    Returns:
        True if mitmproxy should pass chunks through as they arrive
        (and tee them into our own buffer for capture). False keeps
        the default buffered behaviour.
    """
    if host in STREAMING_HOSTS:
        return True
    ct = content_type.lower().split(";", 1)[0].strip()
    return any(ct.startswith(p) for p in STREAMING_CONTENT_TYPE_PREFIXES)


class PCEAddon:
    """mitmproxy addon that captures AI traffic into local SQLite."""

    # --- request phase ----------------------------------------------------

    def request(self, flow: http.HTTPFlow) -> None:
        host = _resolve_host(flow)

        # SMART mode: if not on allowlist, run heuristics on request
        if host is None and CAPTURE_MODE == CaptureMode.SMART:
            host = _get_host(flow)
            if host:
                from .heuristic import detect_ai_request
                body_raw = flow.request.content or b""
                confidence, reasons = detect_ai_request(
                    host, flow.request.path, flow.request.method, body_raw,
                )
                if confidence:
                    log_event(
                        logger, "smart.request_detected",
                        host=host, path=flow.request.path,
                        confidence=confidence, reasons=reasons,
                    )
                    # Auto-register discovered domain
                    _register_discovered_domain(host, confidence, reasons)
                else:
                    # Not detected as AI at request phase — still track for
                    # response-phase heuristics in SMART mode
                    _flow_meta[flow.id] = {
                        "pair_id": None,
                        "request_time": time.time(),
                        "smart_pending": True,
                        "host": host,
                    }
                    return
            else:
                return

        if host is None:
            return

        pair_id = new_pair_id()
        _flow_meta[flow.id] = {
            "pair_id": pair_id,
            "request_time": time.time(),
            "smart_pending": False,
            "host": host,
        }

        try:
            headers_json = redact_headers_json(dict(flow.request.headers))
            body_raw = flow.request.content or b""
            body_text, body_fmt = safe_body_text(body_raw)
            # P5.A-10: scrub in-body secrets (Bearer, JWT, sk-*, etc.) on
            # top of header redaction. Model extraction runs against the
            # *pre-scrub* bytes so we don't accidentally match inside a
            # literal redaction token.
            model = _extract_model(body_raw)
            body_text = redact_body_secrets(body_text)

            rid = insert_capture(
                direction="request",
                pair_id=pair_id,
                host=host,
                path=flow.request.path,
                method=flow.request.method,
                provider=_provider_from_host(host),
                model_name=model,
                headers_redacted_json=headers_json,
                body_text_or_json=body_text,
                body_format=body_fmt,
            )
            if rid is None:
                record_pipeline_error(
                    "ingest", "proxy request insert returned None",
                    source_id=SOURCE_PROXY, pair_id=pair_id,
                    details={"direction": "request", "host": host, "path": flow.request.path},
                )
            log_event(
                logger, "capture.request_recorded",
                pair_id=pair_id[:8], host=host, path=flow.request.path,
                method=flow.request.method, provider=_provider_from_host(host),
                model_name=model,
            )
        except Exception as exc:
            logger.exception("request capture failed – letting request through")
            record_pipeline_error(
                "ingest", f"proxy request capture exception: {type(exc).__name__}: {exc}",
                source_id=SOURCE_PROXY, pair_id=pair_id,
                details={"direction": "request", "host": host},
            )

    # --- response header phase (per-host streaming decision) -------------
    #
    # Fires AFTER the response status line + headers are read upstream but
    # BEFORE the body. This is the only hook where we can flip
    # `flow.response.stream` and have it take effect.

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        meta = _flow_meta.get(flow.id)
        if meta is None:
            return  # not tracked, default behaviour applies
        try:
            host = meta.get("host") or _get_host(flow)
            content_type = flow.response.headers.get("content-type", "")
            if not _should_stream(host, content_type):
                return

            # Tee chunks: pass each through unchanged AND keep a copy so
            # the regular `response()` hook can persist the full body.
            chunks: list[bytes] = []

            def _tee(chunk: bytes) -> bytes:
                chunks.append(chunk)
                return chunk

            flow.response.stream = _tee
            meta["captured_chunks"] = chunks
            log_event(
                logger, "proxy.streaming_enabled",
                pair_id=(meta.get("pair_id") or "")[:8],
                host=host,
                content_type=content_type.split(";", 1)[0].strip(),
            )
        except Exception:
            logger.exception("responseheaders streaming hook failed — falling back to buffered")

    # --- response phase ---------------------------------------------------

    def response(self, flow: http.HTTPFlow) -> None:
        meta = _flow_meta.pop(flow.id, None)
        if meta is None:
            return  # not a tracked flow

        # SMART mode: response-phase heuristic for flows not yet confirmed
        if meta.get("smart_pending"):
            host = meta["host"]
            content_type = flow.response.headers.get("content-type", "")
            # Prefer teed chunks (set in responseheaders for streaming hosts)
            captured = meta.get("captured_chunks")
            if captured is not None:
                body_raw = b"".join(captured)
            else:
                body_raw = flow.response.content or b""

            from .heuristic import detect_ai_response
            confidence, reasons = detect_ai_response(body_raw, content_type)

            if not confidence:
                return  # not AI traffic, skip

            logger.info(
                "SMART detected AI response: %s %s (confidence=%s, reasons=%s)",
                host, flow.request.path, confidence, reasons,
            )
            _register_discovered_domain(host, confidence, reasons)

            # Now capture both request and response retroactively
            pair_id = new_pair_id()
            try:
                req_headers = redact_headers_json(dict(flow.request.headers))
                req_body_raw = flow.request.content or b""
                req_body_text, req_fmt = safe_body_text(req_body_raw)
                model = _extract_model(req_body_raw)
                req_body_text = redact_body_secrets(req_body_text)

                insert_capture(
                    direction="request",
                    pair_id=pair_id,
                    host=host,
                    path=flow.request.path,
                    method=flow.request.method,
                    provider=_provider_from_host(host),
                    model_name=model,
                    headers_redacted_json=req_headers,
                    body_text_or_json=req_body_text,
                    body_format=req_fmt,
                    meta_json=json.dumps({"smart_detected": True, "confidence": confidence, "reasons": reasons}),
                )
            except Exception:
                logger.exception("SMART retroactive request capture failed")

            # Fall through to normal response capture with this pair_id
            meta["pair_id"] = pair_id

        host = meta.get("host") or _get_host(flow)
        pair_id = meta["pair_id"]
        if pair_id is None:
            return

        latency = (time.time() - meta["request_time"]) * 1000  # ms

        try:
            headers_json = redact_headers_json(dict(flow.response.headers))
            # Streaming hosts populate meta['captured_chunks'] via the tee
            # callback set in responseheaders(); for non-streaming flows we
            # fall back to mitmproxy's default buffered content.
            captured = meta.get("captured_chunks")
            if captured is not None:
                body_raw = b"".join(captured)
                # The streaming `_tee` callback receives RAW wire bytes —
                # i.e. still gzip / deflate / br compressed if the upstream
                # set Content-Encoding. mitmproxy's flow.response.content
                # accessor auto-decodes, but the teed chunks bypass that
                # path. Decode here so the normalizer pipeline sees plain
                # text (a.k.a. the actual JSON / SSE body), not garbled
                # binary that SQLite then stores as ~3 chars of mangled
                # UTF-8.
                #
                # Caught during P5.D.1 W4-T6 (api.anthropic.com chat via
                # mitmproxy): request body was 106 KB plaintext but the
                # response body landed as 3 chars because gzip bytes
                # starting with 0x1f 0x8b mangle into invalid UTF-8.
                content_encoding = (
                    flow.response.headers.get("content-encoding", "")
                    .lower().strip()
                )
                if content_encoding in ("gzip", "deflate", "br", "zstd"):
                    try:
                        body_raw = _decompress_streamed_body(body_raw, content_encoding)
                    except Exception as exc:
                        logger.warning(
                            "streamed body decompress failed (%s, %d B): %s — "
                            "falling back to raw bytes",
                            content_encoding, len(body_raw), exc,
                        )
            else:
                body_raw = flow.response.content or b""
            body_text, body_fmt = safe_body_text(body_raw)
            body_text = redact_body_secrets(body_text)

            rid = insert_capture(
                direction="response",
                pair_id=pair_id,
                host=host,
                path=flow.request.path,
                method=flow.request.method,
                provider=_provider_from_host(host),
                status_code=flow.response.status_code,
                latency_ms=round(latency, 2),
                headers_redacted_json=headers_json,
                body_text_or_json=body_text,
                body_format=body_fmt,
            )
            if rid is None:
                record_pipeline_error(
                    "ingest", "proxy response insert returned None",
                    source_id=SOURCE_PROXY, pair_id=pair_id,
                    details={"direction": "response", "host": host, "status_code": flow.response.status_code},
                )
            log_event(
                logger, "capture.response_recorded",
                pair_id=pair_id[:8], host=host, path=flow.request.path,
                method=flow.request.method, provider=_provider_from_host(host),
                status_code=flow.response.status_code,
                latency_ms=round(latency, 2),
            )

            # Auto-normalize the completed pair into sessions + messages
            try:
                try_normalize_pair(pair_id, source_id=SOURCE_PROXY, created_via="proxy")
            except Exception as exc:
                logger.exception("auto-normalization failed for pair %s – non-fatal", pair_id[:8])
                record_pipeline_error(
                    "normalize",
                    f"try_normalize_pair(proxy): {type(exc).__name__}: {exc}",
                    source_id=SOURCE_PROXY, pair_id=pair_id,
                    details={"host": host, "status_code": flow.response.status_code},
                )

        except Exception as exc:
            logger.exception("response capture failed – letting response through")
            record_pipeline_error(
                "ingest", f"proxy response capture exception: {type(exc).__name__}: {exc}",
                source_id=SOURCE_PROXY, pair_id=pair_id,
                details={"direction": "response", "host": host},
            )


    # ── WebSocket frame capture (P5.B.5 — Cowork uses WS, not /completion) ─
    #
    # mitmproxy upgrades any allowlisted HTTP(S) flow that completes a 101
    # handshake into a WebSocket flow. ``websocket_message`` then fires for
    # every frame (text or binary) in either direction. We allocate ONE
    # pair_id per WS session so all frames are queryable together via
    # ``WHERE pair_id = ?``. Direction is ``ws_send`` (client → server)
    # or ``ws_recv`` (server → client). Body format is ``ws_text`` or
    # ``ws_binary``.
    #
    # Without this hook Cowork chat traffic is invisible to the L1 axis —
    # discovered 2026-05-11 during P5.B.5 RECON when 0 ``/completion``
    # rows materialised despite Claude Desktop responding to a multi-step
    # prompt over a persistent WS connection.

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        try:
            ws = getattr(flow, "websocket", None)
            if ws is None or not getattr(ws, "messages", None):
                return

            # Initialise WS session state on first message
            ws_state = _ws_state.get(flow.id)
            if ws_state is None:
                host = _resolve_host(flow)
                if host is None:
                    return  # not allowlisted, skip
                pair_id = new_pair_id()
                ws_state = {
                    "pair_id": pair_id,
                    "host": host,
                    "path": flow.request.path,
                    "first_seen": time.time(),
                    "frame_count": 0,
                }
                _ws_state[flow.id] = ws_state
                log_event(
                    logger, "capture.ws_session_start",
                    pair_id=pair_id[:8], host=host, path=flow.request.path,
                )

            msg = ws.messages[-1]
            from_client = bool(getattr(msg, "from_client", True))
            direction = "ws_send" if from_client else "ws_recv"

            content = getattr(msg, "content", None)
            if content is None:
                content = b""
            if isinstance(content, str):
                content = content.encode("utf-8", errors="replace")

            # Detect text vs binary — mitmproxy 10+ exposes ``is_text``,
            # older versions use ``type`` enum. Default to text if unsure.
            is_text = getattr(msg, "is_text", None)
            if is_text is None:
                msg_type = getattr(msg, "type", None)
                is_text = (str(msg_type).lower().endswith("text") if msg_type else True)

            if is_text:
                try:
                    body_text = content.decode("utf-8", errors="replace")
                except Exception:
                    body_text = ""
                body_fmt = "ws_text"
            else:
                # Binary — store as length marker; full bytes are too noisy
                # for a SQLite TEXT column and v1.1 normaliser doesn't read
                # them anyway. If a use case emerges, store base64 here.
                body_text = f"<binary {len(content)}B>"
                body_fmt = "ws_binary"

            body_text = redact_body_secrets(body_text)
            ws_state["frame_count"] += 1

            insert_capture(
                direction=direction,
                pair_id=ws_state["pair_id"],
                host=ws_state["host"],
                path=ws_state["path"],
                method="WS",
                provider=_provider_from_host(ws_state["host"]),
                body_text_or_json=body_text,
                body_format=body_fmt,
            )
        except Exception:
            logger.exception("websocket_message capture failed — dropping frame")

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        ws_state = _ws_state.pop(flow.id, None)
        if ws_state is not None:
            try:
                log_event(
                    logger, "capture.ws_session_end",
                    pair_id=ws_state["pair_id"][:8],
                    host=ws_state["host"],
                    frame_count=ws_state["frame_count"],
                    duration_s=round(time.time() - ws_state["first_seen"], 1),
                )
            except Exception:
                pass

    # ── TLS failure hooks (P5.A-6) ────────────────────────────────────────
    #
    # When the proxied application refuses our MITM-injected certificate
    # mid-handshake, that's the canonical signature of TLS / certificate
    # pinning. We log the failure to ``tls_failures`` for aggregation via
    # ``GET /api/v1/health/pinning``.
    #
    # mitmproxy fires ``tls_failed_client`` for client-side handshake
    # failures (the pinning case) and ``tls_failed_server`` for upstream
    # failures (usually user-side misconfiguration). We track both but
    # only the client-side failures are treated as pinning evidence by
    # the aggregation endpoint — the dashboard surfaces the distinction.

    def tls_failed_client(self, data) -> None:
        """Client refused our MITM cert — the canonical pinning signal."""
        try:
            host = _extract_tls_host(data)
            if not host:
                return
            error = ""
            conn = getattr(data, "conn", None)
            if conn is not None:
                err_obj = getattr(conn, "error", None)
                if err_obj:
                    error = str(err_obj)
            category = _categorize_tls_error(error)
            record_tls_failure(
                host=host,
                error_category=category,
                error_message=error,
            )
            log_event(
                logger, "proxy.tls_client_failed",
                host=host, error_category=category,
                error=error[:200] if error else "",
            )
        except Exception:
            logger.exception("tls_failed_client hook error — swallowing")

    def tls_failed_server(self, data) -> None:
        """Upstream server TLS failed. Logged as diagnostic, not pinning."""
        try:
            host = _extract_tls_host(data)
            if not host:
                return
            error = ""
            conn = getattr(data, "conn", None)
            if conn is not None:
                err_obj = getattr(conn, "error", None)
                if err_obj:
                    error = str(err_obj)
            log_event(
                logger, "proxy.tls_server_failed",
                host=host, error=error[:200] if error else "",
            )
        except Exception:
            logger.exception("tls_failed_server hook error — swallowing")


# mitmproxy picks up addon instances via the `addons` module-level list.
addons = [PCEAddon()]
