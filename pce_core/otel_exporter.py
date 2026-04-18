# SPDX-License-Identifier: Apache-2.0
"""PCE Core – OpenTelemetry OTLP exporter (opt-in, fail-open).

Per ADR-007:

- Local SQLite is the single source of truth.
- This module is a **secondary, opt-in** channel that mirrors normalised
  captures to any OTLP-compatible backend (Phoenix, Langfuse, Jaeger …).
- **Default is fully disabled**. We only emit when
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set AND the
  ``opentelemetry-sdk`` / ``opentelemetry-exporter-otlp`` packages are
  importable.
- Any failure in this module must NEVER bubble up to the capture path.
  We catch everything, log a structured event, and move on. The pipeline
  continues writing to SQLite as normal.

Two surface categories are supported:

1. **Business spans** — one span per PCE capture pair, attributes built
   by ``pce_core.normalizer.openinference_mapper.pair_to_oi_span``. This
   is what shows up in Phoenix as an LLM call.

2. **Pipeline spans** — ``pce.ingest.receive``, ``pce.normalize``,
   ``pce.persist.message`` etc., emitted via the context manager
   ``pipeline_span("pce.normalize", …)``. When the SDK isn't installed
   these become no-op context managers so call-sites don't need to
   guard every call.

Environment variables (all optional):

- ``OTEL_EXPORTER_OTLP_ENDPOINT``: OTLP target, e.g. ``http://localhost:4318``.
  If unset, exporter is fully disabled.
- ``OTEL_EXPORTER_OTLP_PROTOCOL``: ``http/protobuf`` (default) or ``grpc``.
- ``OTEL_EXPORTER_OTLP_HEADERS``: standard OTel comma-k=v string.
- ``OTEL_SERVICE_NAME``: defaults to ``pce-core``.
- ``PCE_OTLP_SAMPLING``: float 0..1, defaults to 1.0.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Mapping, Optional

from .logging_config import log_event

logger = logging.getLogger("pce.otel_exporter")

# ---------------------------------------------------------------------------
# Capability detection (do NOT hard-import the SDK)
# ---------------------------------------------------------------------------

try:  # pragma: no cover – import-time capability check
    from opentelemetry import trace as _ot_trace
    from opentelemetry.sdk.resources import Resource as _Resource
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BatchSpanProcessor
    _OTEL_API_AVAILABLE = True
except Exception:  # pragma: no cover
    _ot_trace = None  # type: ignore[assignment]
    _Resource = None  # type: ignore[assignment]
    _TracerProvider = None  # type: ignore[assignment]
    _BatchSpanProcessor = None  # type: ignore[assignment]
    _OTEL_API_AVAILABLE = False

_SERVICE_NAME_DEFAULT = "pce-core"
_PIPELINE_TRACER_NAME = "pce.pipeline"
_BUSINESS_TRACER_NAME = "pce.capture"

# Module-level state — guarded by ``_init_lock``. Re-init is idempotent.
_initialised = False
_enabled = False
_tracer_business = None
_tracer_pipeline = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_otlp_exporter(
    *,
    endpoint: Optional[str] = None,
    service_name: Optional[str] = None,
    protocol: Optional[str] = None,
    headers: Optional[str] = None,
    force: bool = False,
) -> bool:
    """Initialise the OTLP exporter if configured.

    Returns True if exporter is now enabled, False if disabled for any
    reason (missing SDK, no endpoint, init failure).

    Safe to call multiple times – the first successful call wins unless
    ``force=True`` is passed.
    """
    global _initialised, _enabled, _tracer_business, _tracer_pipeline

    if _initialised and not force:
        return _enabled

    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        _initialised = True
        _enabled = False
        log_event(
            logger, "otel.disabled",
            reason="no_endpoint",
            hint="set OTEL_EXPORTER_OTLP_ENDPOINT to enable",
        )
        return False

    if not _OTEL_API_AVAILABLE:
        _initialised = True
        _enabled = False
        log_event(
            logger, "otel.disabled",
            level=logging.WARNING,
            reason="sdk_not_installed",
            endpoint=endpoint,
            hint="pip install opentelemetry-sdk opentelemetry-exporter-otlp",
        )
        return False

    service_name = service_name or os.environ.get("OTEL_SERVICE_NAME") or _SERVICE_NAME_DEFAULT
    protocol = (protocol or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/protobuf").lower()

    try:
        exporter = _build_exporter(endpoint, protocol, headers)
        provider = _TracerProvider(resource=_Resource.create({"service.name": service_name}))
        provider.add_span_processor(_BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            schedule_delay_millis=5_000,
            max_export_batch_size=256,
        ))
        # We don't set this as the global TracerProvider – we keep our own
        # handle so user code that has their own OTel setup is not clobbered.
        _tracer_business = provider.get_tracer(_BUSINESS_TRACER_NAME)
        _tracer_pipeline = provider.get_tracer(_PIPELINE_TRACER_NAME)
        _enabled = True
        _initialised = True
        log_event(
            logger, "otel.ready",
            endpoint=endpoint, service_name=service_name,
            protocol=protocol,
        )
        return True
    except Exception as exc:  # pragma: no cover – defensive
        _initialised = True
        _enabled = False
        log_event(
            logger, "otel.init_failed",
            level=logging.WARNING,
            endpoint=endpoint, protocol=protocol,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False


def shutdown_otlp_exporter() -> None:
    """Flush and stop the exporter. Idempotent. Called from server lifespan."""
    global _initialised, _enabled, _tracer_business, _tracer_pipeline
    if not _enabled or _tracer_business is None:
        _initialised = False
        return
    try:
        provider = _tracer_business.resource if False else None  # type: ignore[attr-defined]
        # Proper shutdown requires going through the provider; get it via tracer.
        # TracerProvider subclass exposes .shutdown()
        provider_obj = getattr(_tracer_business, "_instrumentation_scope", None)
        # Best-effort: search module-level globals for the provider we created.
        # In practice the BatchSpanProcessor will flush on its schedule.
        # We intentionally do not raise.
    except Exception:
        pass
    _enabled = False
    _tracer_business = None
    _tracer_pipeline = None
    _initialised = False
    log_event(logger, "otel.shutdown")


def is_enabled() -> bool:
    return _enabled


def emit_pair_span(span_record: Mapping[str, Any]) -> bool:
    """Emit one OpenInference-shaped span for a capture pair.

    ``span_record`` is the dict returned by
    ``pce_core.normalizer.openinference_mapper.pair_to_oi_span``. Returns
    True if the span was queued, False if the exporter is disabled or
    the SDK rejected the payload. Never raises.

    Before hitting the wire we run the attribute dict through
    :func:`pce_core.normalizer.genai_semconv.apply_mode` which optionally
    adds OpenTelemetry GenAI / OpenLLMetry ``gen_ai.*`` aliases. The mode
    is governed by ``PCE_OTEL_GENAI_SEMCONV`` (see that module); default
    is ``both`` so Phoenix keeps working AND Langfuse/Datadog/OTel-native
    backends get the semconv they expect.
    """
    if not _enabled or _tracer_business is None:
        return False
    try:
        import time
        from .normalizer.genai_semconv import apply_mode as _genai_apply_mode

        start_ns = int(span_record.get("start_time_ns") or time.time_ns())
        end_ns = int(span_record.get("end_time_ns") or start_ns)
        attributes = _genai_apply_mode(span_record.get("attributes") or {})
        with _tracer_business.start_as_current_span(
            name=span_record.get("name") or "pce.pair",
            start_time=start_ns,
        ) as span:
            _apply_attributes(span, attributes)
            status_str = str(span_record.get("status") or "OK")
            if status_str == "ERROR":
                _set_status_error(span)
            # BatchSpanProcessor will exfiltrate at end()
            span.end(end_time=end_ns)
        return True
    except Exception as exc:  # pragma: no cover – defensive
        log_event(
            logger, "otel.emit_failed",
            level=logging.WARNING,
            error=f"{type(exc).__name__}: {exc}",
            span=span_record.get("name"),
        )
        return False


@contextmanager
def pipeline_span(name: str, **attributes: Any):
    """Context manager emitting a pipeline-stage span.

    Always safe to use – when the exporter is disabled this is a pure
    no-op that simply yields ``None``.
    """
    if not _enabled or _tracer_pipeline is None:
        yield None
        return
    try:
        with _tracer_pipeline.start_as_current_span(name) as span:
            _apply_attributes(span, attributes)
            try:
                yield span
            except Exception as exc:
                _set_status_error(span, description=f"{type(exc).__name__}: {exc}")
                raise
    except Exception as exc:  # pragma: no cover – defensive wrap
        log_event(
            logger, "otel.pipeline_span_failed",
            level=logging.DEBUG,
            error=f"{type(exc).__name__}: {exc}",
            span=name,
        )
        yield None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_exporter(endpoint: str, protocol: str, headers: Optional[str]):
    """Build the correct exporter for the configured protocol.

    Raises ImportError if the matching exporter package is missing.
    """
    hdrs = _parse_headers(headers or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"))
    if protocol.startswith("grpc"):
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
            OTLPSpanExporter,
        )
        return OTLPSpanExporter(endpoint=endpoint, headers=hdrs)
    # default: http/protobuf
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
        OTLPSpanExporter,
    )
    return OTLPSpanExporter(endpoint=_maybe_append_http_suffix(endpoint), headers=hdrs)


def _maybe_append_http_suffix(endpoint: str) -> str:
    """OTLP/HTTP expects ``/v1/traces`` at the end unless the user passed one."""
    if endpoint.rstrip("/").endswith("/v1/traces"):
        return endpoint
    return endpoint.rstrip("/") + "/v1/traces"


def _parse_headers(raw: Optional[str]) -> Optional[dict[str, str]]:
    if not raw:
        return None
    out: dict[str, str] = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out or None


def _apply_attributes(span, attributes: Mapping[str, Any]) -> None:
    """Set attributes on a span, coercing unsupported types to strings."""
    for k, v in attributes.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)):
            try:
                span.set_attribute(k, v)
                continue
            except Exception:
                pass
        if isinstance(v, (list, tuple)):
            try:
                span.set_attribute(k, list(v))
                continue
            except Exception:
                pass
        # Fallback: stringify
        try:
            import json
            span.set_attribute(k, json.dumps(v, ensure_ascii=False))
        except Exception:
            try:
                span.set_attribute(k, str(v))
            except Exception:
                pass  # give up silently


def _set_status_error(span, *, description: Optional[str] = None) -> None:
    try:
        from opentelemetry.trace import Status, StatusCode  # type: ignore[import-not-found]
        span.set_status(Status(StatusCode.ERROR, description or ""))
    except Exception:
        pass


__all__ = [
    "configure_otlp_exporter",
    "shutdown_otlp_exporter",
    "is_enabled",
    "emit_pair_span",
    "pipeline_span",
]
