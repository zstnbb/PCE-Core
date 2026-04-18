# SPDX-License-Identifier: Apache-2.0
"""Tests for the OpenInference / OTel GenAI attribute mapper (P1).

Guarantees covered:

- Message-level mapper produces OI-compliant keys for every provider.
- Role normalisation copes with provider-specific aliases (``model``,
  ``human`` …).
- Pair-level span builder decomposes messages into indexed
  ``llm.input_messages.{i}.*`` and ``llm.output_messages.{i}.*``.
- Token counts roll up correctly.
- ``metadata`` captures PCE-specific fields without polluting OI keys.
- Trace / span IDs are deterministic given the same (session_id, pair_id).
"""

from __future__ import annotations

import json

import pytest

from pce_core.normalizer.openinference_mapper import (
    OI_INPUT_MESSAGES_PREFIX,
    OI_LLM_MODEL_NAME,
    OI_LLM_PROVIDER,
    OI_LLM_TOKEN_COMPLETION,
    OI_LLM_TOKEN_PROMPT,
    OI_LLM_TOKEN_TOTAL,
    OI_METADATA,
    OI_MSG_CONTENT,
    OI_MSG_ROLE,
    OI_OUTPUT_MESSAGES_PREFIX,
    OI_SESSION_ID,
    OI_SPAN_KIND,
    SPAN_KIND_CHAT,
    SPAN_KIND_LLM,
    message_to_oi_attributes,
    pair_to_oi_span,
    session_to_oi_attributes,
)


def _session(**overrides):
    base = {
        "id": "sess-123456",
        "source_id": "proxy-default",
        "provider": "openai",
        "tool_family": "api-direct",
        "model_names": "gpt-4",
        "created_via": "proxy",
        "session_key": "conv-abc",
        "title_hint": "Hello",
        "language": "en",
        "topic_tags": "python,ai",
        "total_tokens": 120,
        "message_count": 3,
        "favorited": 0,
    }
    base.update(overrides)
    return base


def _msg(role, content, **extras):
    base = {
        "role": role,
        "content_text": content,
        "content_json": None,
        "model_name": None,
        "token_estimate": None,
        "capture_pair_id": "pair-xyz",
        "ts": 1700000000.0,
        "oi_input_tokens": None,
        "oi_output_tokens": None,
    }
    base.update(extras)
    return base


# ---------------------------------------------------------------------------
# message_to_oi_attributes
# ---------------------------------------------------------------------------

class TestMessageMapper:

    def test_basic_user_message_has_oi_role_content_provider_model(self):
        out = message_to_oi_attributes(_msg("user", "hello"), _session())
        assert out[OI_MSG_ROLE] == "user"
        assert out[OI_MSG_CONTENT] == "hello"
        assert out[OI_LLM_PROVIDER] == "openai"
        assert out[OI_LLM_MODEL_NAME] == "gpt-4"
        assert out[OI_SESSION_ID] == "sess-123456"
        assert out[OI_SPAN_KIND] == SPAN_KIND_LLM

    @pytest.mark.parametrize(
        "raw,normalised",
        [
            ("user", "user"),
            ("human", "user"),
            ("Human", "user"),
            ("assistant", "assistant"),
            ("ai", "assistant"),
            ("model", "assistant"),
            ("system", "system"),
            ("developer", "system"),
            ("tool", "tool"),
            ("function", "tool"),
            ("observation", "tool"),
        ],
    )
    def test_role_aliases_collapse_to_spec_values(self, raw, normalised):
        out = message_to_oi_attributes(_msg(raw, "x"), _session())
        assert out[OI_MSG_ROLE] == normalised

    def test_metadata_carries_pce_specific_keys(self):
        out = message_to_oi_attributes(
            _msg("user", "hi", token_estimate=5), _session(),
        )
        assert OI_METADATA in out
        meta = json.loads(out[OI_METADATA])
        assert meta["pce.tool_family"] == "api-direct"
        assert meta["pce.source_id"] == "proxy-default"
        assert meta["pce.capture_pair_id"] == "pair-xyz"
        assert meta["pce.role_raw"] == "user"
        assert meta["pce.mapper_version"] >= 1

    def test_token_counts_written_when_present(self):
        out = message_to_oi_attributes(
            _msg("assistant", "reply", oi_output_tokens=42), _session(),
        )
        assert out[OI_LLM_TOKEN_COMPLETION] == 42
        assert OI_LLM_TOKEN_PROMPT not in out

    def test_rich_content_image_emits_indexed_message_content(self):
        content_json = json.dumps({"attachments": [
            {"type": "image_url", "url": "https://example.com/img.png"},
            {"type": "text", "text": "caption"},
        ]})
        out = message_to_oi_attributes(
            _msg("user", "[Image]\ncaption", content_json=content_json), _session(),
        )
        assert out["message.contents.0.message_content.type"] == "image"
        assert out["message.contents.0.message_content.image.image.url"] == "https://example.com/img.png"
        assert out["message.contents.1.message_content.type"] == "text"
        assert out["message.contents.1.message_content.text"] == "caption"


# ---------------------------------------------------------------------------
# session_to_oi_attributes
# ---------------------------------------------------------------------------

class TestSessionMapper:

    def test_session_is_kind_chat(self):
        out = session_to_oi_attributes(_session())
        assert out[OI_SPAN_KIND] == SPAN_KIND_CHAT
        assert out[OI_SESSION_ID] == "sess-123456"
        assert out[OI_LLM_PROVIDER] == "openai"

    def test_topic_tags_are_split_into_list_in_metadata(self):
        out = session_to_oi_attributes(_session(topic_tags="a, b ,c"))
        meta = json.loads(out[OI_METADATA])
        assert meta["pce.topic_tags"] == ["a", "b", "c"]

    def test_model_names_takes_first_when_comma_separated(self):
        out = session_to_oi_attributes(_session(model_names="gpt-4,claude-3"))
        assert out[OI_LLM_MODEL_NAME] == "gpt-4"

    def test_empty_session_survives(self):
        out = session_to_oi_attributes({"id": "only-id"})
        assert out[OI_SESSION_ID] == "only-id"
        assert out[OI_SPAN_KIND] == SPAN_KIND_CHAT
        assert OI_LLM_PROVIDER not in out


# ---------------------------------------------------------------------------
# pair_to_oi_span
# ---------------------------------------------------------------------------

class TestPairSpan:

    def _build(self, **extras):
        sess = _session()
        msgs = [
            _msg("system", "be helpful", ts=1700000000.0),
            _msg("user", "hello", ts=1700000001.0, oi_input_tokens=3),
            _msg("assistant", "world", ts=1700000002.0, oi_output_tokens=5, model_name="gpt-4"),
        ]
        raw_req = {"method": "POST", "host": "api.openai.com", "path": "/v1/chat/completions", "created_at": 1700000000.5}
        raw_resp = {"status_code": 200, "latency_ms": 120.5, "created_at": 1700000002.0}
        return pair_to_oi_span("pair-xyz", sess, msgs, raw_req, raw_resp, **extras)

    def test_basic_pair_shape(self):
        span = self._build()
        assert span["kind"] == SPAN_KIND_LLM
        assert span["status"] == "OK"
        assert span["start_time_ns"] == 1_700_000_000 * 1_000_000_000
        assert span["end_time_ns"] == 1_700_000_002 * 1_000_000_000
        assert span["name"].startswith("openai.")
        assert len(span["trace_id_hex"]) == 32
        assert len(span["span_id_hex"]) == 16
        assert span["parent_span_id"] is None

    def test_input_and_output_messages_are_indexed(self):
        span = self._build()
        attrs = span["attributes"]
        # system + user land in input; assistant in output
        assert attrs[f"{OI_INPUT_MESSAGES_PREFIX}.0.message.role"] == "system"
        assert attrs[f"{OI_INPUT_MESSAGES_PREFIX}.1.message.role"] == "user"
        assert attrs[f"{OI_INPUT_MESSAGES_PREFIX}.1.message.content"] == "hello"
        assert attrs[f"{OI_OUTPUT_MESSAGES_PREFIX}.0.message.role"] == "assistant"
        assert attrs[f"{OI_OUTPUT_MESSAGES_PREFIX}.0.message.content"] == "world"

    def test_token_counts_sum_across_messages(self):
        span = self._build()
        attrs = span["attributes"]
        assert attrs[OI_LLM_TOKEN_PROMPT] == 3
        assert attrs[OI_LLM_TOKEN_COMPLETION] == 5
        assert attrs[OI_LLM_TOKEN_TOTAL] == 8

    def test_http_metadata_goes_into_metadata_blob(self):
        span = self._build()
        meta = json.loads(span["attributes"][OI_METADATA])
        assert meta["pce.pair_id"] == "pair-xyz"
        assert meta["pce.http.method"] == "POST"
        assert meta["pce.http.host"] == "api.openai.com"
        assert meta["pce.http.status_code"] == 200
        assert abs(meta["pce.http.latency_ms"] - 120.5) < 1e-6

    def test_error_status_on_http_5xx(self):
        sess = _session()
        msgs = [_msg("user", "x", ts=1.0, oi_input_tokens=1)]
        raw_req = {"method": "POST", "host": "a"}
        raw_resp = {"status_code": 500, "created_at": 2.0}
        span = pair_to_oi_span("p1", sess, msgs, raw_req, raw_resp)
        assert span["status"] == "ERROR"

    def test_error_status_when_no_assistant(self):
        sess = _session()
        msgs = [_msg("user", "x", ts=1.0)]
        span = pair_to_oi_span("p2", sess, msgs)
        assert span["status"] == "ERROR"

    def test_trace_id_is_deterministic_for_same_session(self):
        sess = _session()
        msgs = [_msg("user", "x", ts=1.0), _msg("assistant", "y", ts=2.0)]
        s1 = pair_to_oi_span("pair-1", sess, msgs)
        s2 = pair_to_oi_span("pair-2", sess, msgs)
        s3 = pair_to_oi_span("pair-1", _session(id="different"), msgs)
        assert s1["trace_id_hex"] == s2["trace_id_hex"], "same session → same trace"
        assert s1["span_id_hex"] != s2["span_id_hex"], "different pair → different span"
        assert s1["trace_id_hex"] != s3["trace_id_hex"], "different session → different trace"

    def test_empty_messages_raises(self):
        with pytest.raises(ValueError):
            pair_to_oi_span("p", _session(), [])

    def test_input_value_is_valid_json(self):
        from pce_core.normalizer.openinference_mapper import OI_INPUT_VALUE
        span = self._build()
        raw = span["attributes"][OI_INPUT_VALUE]
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
        assert {"role": "system", "content": "be helpful"} in parsed
        assert {"role": "user", "content": "hello"} in parsed
