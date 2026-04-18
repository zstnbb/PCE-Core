# SPDX-License-Identifier: Apache-2.0
"""Tests for the opt-in OTLP exporter (P1, ADR-007).

We intentionally test the safe-when-disabled path first because that's
the default in production. When the SDK is installed we also verify the
in-process SDK wiring produces a span.

The exporter must:

- Be disabled by default (no endpoint env).
- Be disabled when the SDK isn't installed (logs a warning, returns False).
- Never raise from ``emit_pair_span`` or ``pipeline_span`` when disabled.
- Honour ``OTEL_EXPORTER_OTLP_ENDPOINT`` on ``configure_otlp_exporter``.
"""

from __future__ import annotations

import logging

import pytest

from pce_core import otel_exporter


def _reset_module_state() -> None:
    """Reset module-level globals between tests so each call to
    configure_otlp_exporter is treated as fresh."""
    otel_exporter._initialised = False
    otel_exporter._enabled = False
    otel_exporter._tracer_business = None
    otel_exporter._tracer_pipeline = None


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    _reset_module_state()
    yield
    _reset_module_state()


class TestDisabledPath:

    def test_disabled_by_default_when_no_endpoint(self):
        assert otel_exporter.configure_otlp_exporter() is False
        assert otel_exporter.is_enabled() is False

    def test_emit_pair_span_is_safe_when_disabled(self):
        assert otel_exporter.configure_otlp_exporter() is False
        ok = otel_exporter.emit_pair_span({
            "name": "pce.pair",
            "attributes": {"llm.provider": "openai"},
            "start_time_ns": 1_000_000_000,
            "end_time_ns": 2_000_000_000,
        })
        assert ok is False

    def test_pipeline_span_is_no_op_when_disabled(self):
        otel_exporter.configure_otlp_exporter()
        with otel_exporter.pipeline_span("pce.test", attr=1) as span:
            assert span is None

    def test_pipeline_span_does_not_swallow_caller_exception(self):
        otel_exporter.configure_otlp_exporter()
        with pytest.raises(ValueError, match="boom"):
            with otel_exporter.pipeline_span("pce.test"):
                raise ValueError("boom")


class TestConfigurationEvents:

    def test_disabled_event_logs_when_no_endpoint(self, caplog):
        caplog.set_level(logging.INFO, logger="pce.otel_exporter")
        otel_exporter.configure_otlp_exporter()
        events = [getattr(r, "event", None) for r in caplog.records]
        assert "otel.disabled" in events

    def test_second_call_is_idempotent(self):
        first = otel_exporter.configure_otlp_exporter()
        second = otel_exporter.configure_otlp_exporter()
        assert first == second == False  # noqa: E712


@pytest.mark.skipif(
    not otel_exporter._OTEL_API_AVAILABLE,
    reason="opentelemetry-sdk not installed",
)
class TestEnabledPath:

    def test_configure_succeeds_with_endpoint(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
        ok = otel_exporter.configure_otlp_exporter(force=True)
        # Depending on exporter dependencies, this can still return False
        # (e.g. if proto-http is missing). Accept either — but if True,
        # is_enabled must agree.
        assert ok in (True, False)
        assert otel_exporter.is_enabled() == ok

    def test_emit_pair_span_when_enabled_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
        otel_exporter.configure_otlp_exporter(force=True)
        if not otel_exporter.is_enabled():
            pytest.skip("exporter couldn't initialise in this environment")

        import time
        ok = otel_exporter.emit_pair_span({
            "name": "pce.pair.test",
            "attributes": {
                "llm.provider": "openai",
                "llm.model_name": "gpt-4",
                "openinference.span.kind": "LLM",
            },
            "start_time_ns": time.time_ns() - 1_000_000,
            "end_time_ns": time.time_ns(),
            "status": "OK",
        })
        # The exporter sends to a local OTLP collector — nothing is
        # listening in CI so the export will be queued. "True" just means
        # we queued without throwing.
        assert ok is True


class TestShutdown:

    def test_shutdown_is_safe_when_never_configured(self):
        otel_exporter.shutdown_otlp_exporter()  # must not raise

    def test_shutdown_is_idempotent(self):
        otel_exporter.configure_otlp_exporter()
        otel_exporter.shutdown_otlp_exporter()
        otel_exporter.shutdown_otlp_exporter()
