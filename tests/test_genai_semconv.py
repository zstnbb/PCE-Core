# SPDX-License-Identifier: Apache-2.0
"""Tests for the P4.3 OpenTelemetry GenAI / OpenLLMetry alias layer.

The mapper operates on the OpenInference attribute dict produced by
:mod:`pce_core.normalizer.openinference_mapper`. We verify:

- Env-driven mode resolution (`PCE_OTEL_GENAI_SEMCONV`)
- Individual attribute mappings (provider, model, tokens, ids, prompt/
  completion values, indexed messages)
- ``apply_mode`` correctly merges / strips attributes in each mode
- ``emit_pair_span`` wires the mapper in (verified by monkey-patching
  the OTEL SDK)
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def oi_attrs() -> dict[str, Any]:
    """A representative OpenInference attribute dict for an LLM span."""
    return {
        "openinference.span.kind": "LLM",
        "llm.provider": "openai",
        "llm.system": "openai",
        "llm.model_name": "gpt-4o",
        "llm.token_count.prompt": 120,
        "llm.token_count.completion": 450,
        "llm.token_count.total": 570,
        "session.id": "sess-abc",
        "user.id": "u-42",
        "input.value": '[{"role":"user","content":"hi"}]',
        "input.mime_type": "application/json",
        "output.value": '[{"role":"assistant","content":"hello"}]',
        "output.mime_type": "application/json",
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "hi",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "hello",
        "metadata": '{"pce.pair_id":"p-1"}',
    }


# ---------------------------------------------------------------------------
# get_mode()
# ---------------------------------------------------------------------------

class TestGetMode:

    def test_default_is_both(self):
        from pce_core.normalizer.genai_semconv import get_mode, GENAI_MODE_BOTH
        assert get_mode({}) == GENAI_MODE_BOTH

    def test_env_off(self):
        from pce_core.normalizer.genai_semconv import get_mode, GENAI_MODE_OFF
        assert get_mode({"PCE_OTEL_GENAI_SEMCONV": "off"}) == GENAI_MODE_OFF

    def test_env_only(self):
        from pce_core.normalizer.genai_semconv import get_mode, GENAI_MODE_ONLY
        assert get_mode({"PCE_OTEL_GENAI_SEMCONV": "only"}) == GENAI_MODE_ONLY

    def test_env_case_insensitive(self):
        from pce_core.normalizer.genai_semconv import get_mode, GENAI_MODE_OFF
        assert get_mode({"PCE_OTEL_GENAI_SEMCONV": "OFF"}) == GENAI_MODE_OFF

    def test_env_unknown_falls_back_to_both(self):
        from pce_core.normalizer.genai_semconv import get_mode, GENAI_MODE_BOTH
        assert get_mode({"PCE_OTEL_GENAI_SEMCONV": "bogus"}) == GENAI_MODE_BOTH


# ---------------------------------------------------------------------------
# to_genai_aliases()
# ---------------------------------------------------------------------------

class TestAliasMapping:

    def test_empty_input_returns_empty_dict(self):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        assert to_genai_aliases({}) == {}

    def test_operation_name_from_span_kind(self):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        assert to_genai_aliases({"openinference.span.kind": "LLM"})["gen_ai.operation.name"] == "chat"
        assert to_genai_aliases({"openinference.span.kind": "TOOL"})["gen_ai.operation.name"] == "execute_tool"
        # Unknown kinds don't create the key (rather than inventing a value).
        assert "gen_ai.operation.name" not in to_genai_aliases(
            {"openinference.span.kind": "WEIRD"}
        )

    def test_system_from_llm_system(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        assert out["gen_ai.system"] == "openai"

    def test_system_falls_back_to_provider(self):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases({"llm.provider": "anthropic"})
        assert out["gen_ai.system"] == "anthropic"

    def test_model_maps_to_both_request_and_response(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        assert out["gen_ai.request.model"] == "gpt-4o"
        assert out["gen_ai.response.model"] == "gpt-4o"

    def test_token_usage_both_spellings(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        assert out["gen_ai.usage.input_tokens"] == 120
        assert out["gen_ai.usage.output_tokens"] == 450
        assert out["gen_ai.usage.total_tokens"] == 570
        # OpenLLMetry flat spellings
        assert out["llm.usage.prompt_tokens"] == 120
        assert out["llm.usage.completion_tokens"] == 450
        assert out["llm.usage.total_tokens"] == 570

    def test_total_is_derived_when_missing(self):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases({
            "llm.token_count.prompt": 10,
            "llm.token_count.completion": 7,
        })
        assert out["gen_ai.usage.total_tokens"] == 17

    def test_conversation_and_user_ids(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        assert out["gen_ai.conversation.id"] == "sess-abc"
        assert out["gen_ai.user.id"] == "u-42"

    def test_prompt_and_completion_values(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        assert out["gen_ai.prompt"] == oi_attrs["input.value"]
        assert out["gen_ai.completion"] == oi_attrs["output.value"]

    def test_indexed_messages_fold_into_genai_namespace(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        assert out["gen_ai.prompt.0.role"] == "user"
        assert out["gen_ai.prompt.0.content"] == "hi"
        assert out["gen_ai.completion.0.role"] == "assistant"
        assert out["gen_ai.completion.0.content"] == "hello"

    def test_unknown_oi_keys_are_left_untouched(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import to_genai_aliases
        out = to_genai_aliases(oi_attrs)
        # The alias mapping should never contain source keys.
        for k in out:
            assert not k.startswith("llm.token_count.")
            assert not k.startswith("openinference.")
            assert not k.startswith("input.")
            assert not k.startswith("output.")


# ---------------------------------------------------------------------------
# apply_mode()
# ---------------------------------------------------------------------------

class TestApplyMode:

    def test_off_leaves_input_unchanged(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import apply_mode
        out = apply_mode(oi_attrs, mode="off")
        assert out == dict(oi_attrs)

    def test_both_preserves_oi_and_adds_genai(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import apply_mode
        out = apply_mode(oi_attrs, mode="both")
        # OI survives
        for k, v in oi_attrs.items():
            assert out[k] == v
        # gen_ai alias is present
        assert out["gen_ai.system"] == "openai"
        assert out["gen_ai.usage.input_tokens"] == 120

    def test_only_strips_llm_prefixed_oi_keys(self, oi_attrs):
        from pce_core.normalizer.genai_semconv import apply_mode
        out = apply_mode(oi_attrs, mode="only")
        # The primary OI keys are gone…
        assert "llm.model_name" not in out
        assert "llm.token_count.prompt" not in out
        assert "input.value" not in out
        assert "output.value" not in out
        # But context keys (span.kind, session.id, user.id, metadata) stay…
        assert out["openinference.span.kind"] == "LLM"
        assert out["session.id"] == "sess-abc"
        assert out["metadata"] == oi_attrs["metadata"]
        # …and gen_ai aliases are present.
        assert out["gen_ai.request.model"] == "gpt-4o"
        assert out["gen_ai.prompt.0.content"] == "hi"

    def test_only_keeps_openllmetry_usage_even_though_prefix_is_llm(self, oi_attrs):
        """OpenLLMetry ``llm.usage.*`` is an **alias** we emit, not an OI source
        key. It must survive the ``only`` strip so OpenLLMetry dashboards
        still work.
        """
        from pce_core.normalizer.genai_semconv import apply_mode
        out = apply_mode(oi_attrs, mode="only")
        assert out["llm.usage.prompt_tokens"] == 120
        assert out["llm.usage.completion_tokens"] == 450

    def test_mode_falls_back_to_env(self, oi_attrs, monkeypatch):
        from pce_core.normalizer.genai_semconv import apply_mode
        monkeypatch.setenv("PCE_OTEL_GENAI_SEMCONV", "off")
        out = apply_mode(oi_attrs)
        assert out == dict(oi_attrs)


# ---------------------------------------------------------------------------
# Integration with emit_pair_span (otel_exporter)
# ---------------------------------------------------------------------------

class TestEmitPairSpanApplyMode:
    """Verify the exporter actually runs the attribute dict through the
    gen_ai alias layer. We inject a stub span + tracer so we can observe
    exactly what ``set_attribute`` sees, without needing a real OTEL SDK.
    """

    def test_emit_pair_span_calls_apply_mode(self, monkeypatch, oi_attrs):
        from pce_core import otel_exporter

        captured: dict[str, Any] = {}

        class _FakeSpan:
            def __init__(self):
                self.attrs: dict[str, Any] = {}
            def set_attribute(self, k, v):
                self.attrs[k] = v
            def end(self, end_time=None):
                pass
            def set_status(self, *_a, **_kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        class _FakeTracer:
            def start_as_current_span(self, name, start_time=None):
                span = _FakeSpan()
                captured["span"] = span
                captured["name"] = name
                return span

        monkeypatch.setattr(otel_exporter, "_enabled", True, raising=False)
        monkeypatch.setattr(otel_exporter, "_tracer_business", _FakeTracer(), raising=False)

        span_record = {
            "name": "openai.gpt-4o",
            "attributes": oi_attrs,
            "start_time_ns": 1_000_000_000,
            "end_time_ns": 2_000_000_000,
            "status": "OK",
        }
        assert otel_exporter.emit_pair_span(span_record) is True
        seen = captured["span"].attrs
        # Default mode is ``both`` → both llm.* and gen_ai.* on the span.
        assert seen.get("llm.model_name") == "gpt-4o"
        assert seen.get("gen_ai.request.model") == "gpt-4o"
        assert seen.get("gen_ai.usage.input_tokens") == 120

    def test_emit_pair_span_respects_env_off(self, monkeypatch, oi_attrs):
        from pce_core import otel_exporter

        captured: dict[str, Any] = {}

        class _FakeSpan:
            def __init__(self):
                self.attrs: dict[str, Any] = {}
            def set_attribute(self, k, v):
                self.attrs[k] = v
            def end(self, end_time=None):
                pass
            def set_status(self, *_a, **_kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False

        class _FakeTracer:
            def start_as_current_span(self, name, start_time=None):
                span = _FakeSpan()
                captured["span"] = span
                return span

        monkeypatch.setattr(otel_exporter, "_enabled", True, raising=False)
        monkeypatch.setattr(otel_exporter, "_tracer_business", _FakeTracer(), raising=False)
        monkeypatch.setenv("PCE_OTEL_GENAI_SEMCONV", "off")

        span_record = {
            "name": "x",
            "attributes": oi_attrs,
            "start_time_ns": 1,
            "end_time_ns": 2,
            "status": "OK",
        }
        otel_exporter.emit_pair_span(span_record)
        seen = captured["span"].attrs
        # Mode=off → no gen_ai aliases on the wire.
        assert "gen_ai.request.model" not in seen
        assert seen.get("llm.model_name") == "gpt-4o"
