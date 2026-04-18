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

    # --- response phase ---------------------------------------------------

    def response(self, flow: http.HTTPFlow) -> None:
        meta = _flow_meta.pop(flow.id, None)
        if meta is None:
            return  # not a tracked flow

        # SMART mode: response-phase heuristic for flows not yet confirmed
        if meta.get("smart_pending"):
            host = meta["host"]
            content_type = flow.response.headers.get("content-type", "")
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
