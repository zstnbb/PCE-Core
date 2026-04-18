# SPDX-License-Identifier: Apache-2.0
"""Contract tests for CaptureEvent v2.

Once this schema is merged to main, it is a public API. These tests lock the
invariants so that any future change that would break a v2 producer fails CI.

Coverage aligns with UCS §5 spec. Any change to this file that weakens an
invariant (e.g. allowing extras, renaming a field, changing defaults) MUST
be accompanied by an ADR discussing the migration path per UCS §5.6.
"""
from __future__ import annotations

import json
import time

import pytest
from pydantic import ValidationError

from pce_core.capture_event import (
    CaptureEventIngestResponse,
    CaptureEventV2,
    compute_fingerprint,
    default_capture_host,
    from_v1_capture,
    new_capture_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CROCKFORD_CHARS = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def _minimal_payload(**overrides):
    """Smallest valid CaptureEventV2 payload (only required fields)."""
    payload = {
        "capture_id": new_capture_id(),
        "source": "L1_mitm",
        "agent_name": "pce_proxy",
        "agent_version": "1.0.0",
        "capture_time_ns": time.time_ns(),
        "capture_host": "dev-laptop#12345",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Construction: happy paths
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_minimal_valid(self):
        ev = CaptureEventV2(**_minimal_payload())
        assert ev.source == "L1_mitm"
        assert ev.direction == "pair"  # default
        assert ev.streaming is False
        assert ev.quality_tier == "T1_structured"
        assert ev.layer_meta == {}

    def test_full_valid(self):
        payload = _minimal_payload(
            pair_id="p1",
            session_hint="conv-abc",
            provider="openai",
            model="gpt-4o-2024-11-20",
            endpoint="/v1/chat/completions",
            direction="response",
            streaming=True,
            request_headers={"x-redacted": "<r>"},
            request_body={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            response_headers={"content-type": "application/json"},
            response_body={"id": "chatcmpl-x"},
            stream_chunks=[{"delta": "hi"}, {"delta": " there"}],
            quality_tier="T1_structured",
            fingerprint="a" * 64,
            layer_meta={"mitm.flow_id": "flow-1"},
            form_id="F1",
            app_name="chatgpt_web",
        )
        ev = CaptureEventV2(**payload)
        assert ev.pair_id == "p1"
        assert ev.streaming is True
        assert ev.layer_meta["mitm.flow_id"] == "flow-1"
        assert ev.form_id == "F1"


# ---------------------------------------------------------------------------
# Enum enforcement
# ---------------------------------------------------------------------------

class TestEnumEnforcement:
    def test_source_rejects_unknown_layer(self):
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(source="L5_not_real"))

    def test_quality_tier_rejects_unknown(self):
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(quality_tier="T9_exotic"))

    def test_direction_rejects_unknown(self):
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(direction="push"))

    @pytest.mark.parametrize(
        "source",
        [
            "L0_kernel", "L1_mitm", "L2_frida",
            "L3a_browser_ext", "L3b_electron_preload", "L3c_vscode_ext",
            "L3d_cdp", "L3e_litellm", "L3f_otel",
            "L4a_clipboard", "L4b_accessibility", "L4c_ocr",
        ],
    )
    def test_all_12_sources_accepted(self, source):
        ev = CaptureEventV2(**_minimal_payload(source=source))
        assert ev.source == source


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------

class TestRequiredFields:
    @pytest.mark.parametrize(
        "missing",
        ["capture_id", "source", "agent_name", "agent_version",
         "capture_time_ns", "capture_host"],
    )
    def test_required_field_missing(self, missing):
        payload = _minimal_payload()
        payload.pop(missing)
        with pytest.raises(ValidationError):
            CaptureEventV2(**payload)

    def test_capture_id_cannot_be_empty_string(self):
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(capture_id=""))

    def test_capture_time_ns_must_be_positive(self):
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(capture_time_ns=0))
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(capture_time_ns=-1))


# ---------------------------------------------------------------------------
# Strict mode: extra='forbid' at top level, free-form layer_meta
# ---------------------------------------------------------------------------

class TestStrictness:
    def test_unknown_top_level_field_rejected(self):
        # Schema is a frozen public API — unknown fields at top level mean a
        # bug or a version mismatch, and must fail loudly.
        with pytest.raises(ValidationError):
            CaptureEventV2(**_minimal_payload(magic_experimental_field="oops"))

    def test_layer_meta_accepts_arbitrary_keys(self):
        meta = {
            "frida.script_version": "1.2.3",
            "frida.hook_name": "SSL_read",
            "custom_nested": {"any": {"depth": [1, 2, 3]}},
            "cdp.frame_id": "F1234",
            "unicode_key_中文": "ok",
        }
        ev = CaptureEventV2(**_minimal_payload(layer_meta=meta))
        assert ev.layer_meta == meta

    def test_layer_meta_default_is_independent_dict(self):
        ev1 = CaptureEventV2(**_minimal_payload())
        ev2 = CaptureEventV2(**_minimal_payload())
        ev1.layer_meta["only_on_ev1"] = True
        assert "only_on_ev1" not in ev2.layer_meta


# ---------------------------------------------------------------------------
# Serialization roundtrip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_json_roundtrip(self):
        original = CaptureEventV2(
            **_minimal_payload(
                pair_id="p1",
                provider="anthropic",
                model="claude-sonnet-4.5",
                layer_meta={"cdp.frame_id": "F99"},
            )
        )
        as_json = original.model_dump_json()
        assert isinstance(as_json, str)
        restored = CaptureEventV2.model_validate_json(as_json)
        assert restored.model_dump() == original.model_dump()

    def test_dict_roundtrip(self):
        original = CaptureEventV2(**_minimal_payload())
        d = original.model_dump()
        # Ensure the dict is JSON-serialisable (nothing exotic like datetime)
        json.dumps(d)
        restored = CaptureEventV2(**d)
        assert restored.capture_id == original.capture_id


# ---------------------------------------------------------------------------
# new_capture_id helper
# ---------------------------------------------------------------------------

class TestNewCaptureId:
    def test_length_is_26(self):
        assert len(new_capture_id()) == 26

    def test_characters_are_crockford_base32(self):
        for _ in range(50):
            cid = new_capture_id()
            assert set(cid) <= _CROCKFORD_CHARS, f"unexpected char in {cid!r}"

    def test_uniqueness_over_1000_iterations(self):
        seen = {new_capture_id() for _ in range(1000)}
        assert len(seen) == 1000

    def test_ids_are_time_ordered(self):
        a = new_capture_id()
        # Sleep enough that at least one millisecond has passed so the
        # ULID timestamp prefix increases. Allow retries for flaky CI.
        for _ in range(10):
            time.sleep(0.002)
            b = new_capture_id()
            if b > a:
                return
        pytest.fail("successive ULIDs should be lexicographically increasing")


# ---------------------------------------------------------------------------
# compute_fingerprint helper
# ---------------------------------------------------------------------------

class TestComputeFingerprint:
    def test_deterministic(self):
        a = compute_fingerprint("p1", "hello world")
        b = compute_fingerprint("p1", "hello world")
        assert a == b

    def test_different_pair_id_changes_fingerprint(self):
        a = compute_fingerprint("p1", "hello world")
        b = compute_fingerprint("p2", "hello world")
        assert a != b

    def test_different_body_changes_fingerprint(self):
        a = compute_fingerprint("p1", "hello world")
        b = compute_fingerprint("p1", "HELLO WORLD")
        assert a != b

    def test_separator_prevents_collision_between_splits(self):
        # ('a', 'bc') and ('ab', 'c') must not hash to the same value
        a = compute_fingerprint("a", "bc")
        b = compute_fingerprint("ab", "c")
        assert a != b

    def test_handles_none_pair_id(self):
        fp = compute_fingerprint(None, "body")
        assert len(fp) == 64  # sha256 hex

    def test_handles_none_body(self):
        fp = compute_fingerprint("p1", None)
        assert len(fp) == 64

    def test_handles_bytes_body(self):
        str_fp = compute_fingerprint("p1", "hello")
        bytes_fp = compute_fingerprint("p1", b"hello")
        assert str_fp == bytes_fp

    def test_prefix_chars_bounds_cost(self):
        base = "x" * 2000
        trimmed = "x" * 1024
        assert compute_fingerprint("p1", base) == compute_fingerprint("p1", trimmed)


# ---------------------------------------------------------------------------
# default_capture_host helper
# ---------------------------------------------------------------------------

class TestDefaultCaptureHost:
    def test_has_hostname_and_pid(self):
        host = default_capture_host()
        assert "#" in host
        hostname, pid = host.rsplit("#", 1)
        assert hostname  # non-empty
        assert pid.isdigit()


# ---------------------------------------------------------------------------
# v1 → v2 adapter
# ---------------------------------------------------------------------------

class TestFromV1Capture:
    def test_minimal_v1_payload(self):
        ev = from_v1_capture(
            {"source_type": "proxy", "direction": "request"}
        )
        assert ev.source == "L1_mitm"
        assert ev.direction == "request"
        assert ev.agent_name == "pce_v1_bridge"
        assert ev.capture_host  # computed default

    def test_source_type_proxy(self):
        ev = from_v1_capture({"source_type": "proxy", "direction": "response"})
        assert ev.source == "L1_mitm"

    def test_source_type_browser_extension(self):
        ev = from_v1_capture({"source_type": "browser_extension", "direction": "pair"})
        assert ev.source == "L3a_browser_ext"

    def test_source_type_mcp(self):
        ev = from_v1_capture({"source_type": "mcp", "direction": "pair"})
        assert ev.source == "L3c_vscode_ext"

    def test_source_type_unknown_falls_back_to_l1(self):
        ev = from_v1_capture({"source_type": "mystery", "direction": "pair"})
        assert ev.source == "L1_mitm"

    def test_direction_conversation_becomes_pair(self):
        ev = from_v1_capture({"source_type": "proxy", "direction": "conversation"})
        assert ev.direction == "pair"

    def test_body_json_with_valid_json_is_parsed_as_dict(self):
        payload = {
            "source_type": "proxy",
            "direction": "request",
            "body_json": '{"model": "gpt-4o"}',
            "body_format": "json",
        }
        ev = from_v1_capture(payload)
        assert ev.request_body == {"model": "gpt-4o"}

    def test_body_json_with_invalid_json_falls_back_to_text(self):
        payload = {
            "source_type": "proxy",
            "direction": "request",
            "body_json": "not-json-at-all",
            "body_format": "json",
        }
        ev = from_v1_capture(payload)
        assert ev.request_body == {"text": "not-json-at-all"}

    def test_body_format_text_is_wrapped_under_text_key(self):
        payload = {
            "source_type": "proxy",
            "direction": "response",
            "body_json": "plain text response",
            "body_format": "text",
        }
        ev = from_v1_capture(payload)
        assert ev.response_body == {"text": "plain text response"}

    def test_pair_direction_places_body_on_response_side(self):
        # Legacy v1 "pair"/"conversation" is ambiguous; adapter chooses
        # response_body so the normalizer still sees the payload.
        payload = {
            "source_type": "proxy",
            "direction": "conversation",
            "body_json": '{"role": "assistant"}',
            "body_format": "json",
        }
        ev = from_v1_capture(payload)
        assert ev.response_body == {"role": "assistant"}
        assert ev.request_body is None

    def test_meta_preserved_in_layer_meta(self):
        payload = {
            "source_type": "browser_extension",
            "source_name": "chrome-ext",
            "direction": "pair",
            "meta": {"page_url": "https://chat.openai.com/c/abc"},
        }
        ev = from_v1_capture(payload)
        assert ev.layer_meta["v1_source_name"] == "chrome-ext"
        assert ev.layer_meta["v1_meta"] == {"page_url": "https://chat.openai.com/c/abc"}


# ---------------------------------------------------------------------------
# Ingest response envelope
# ---------------------------------------------------------------------------

class TestIngestResponse:
    def test_minimal_ok(self):
        resp = CaptureEventIngestResponse(
            capture_id="01HX0000000000000000000001",
            ingested_at_ns=time.time_ns(),
        )
        assert resp.normalized is False
        assert resp.deduped_of is None

    def test_deduped_variant(self):
        resp = CaptureEventIngestResponse(
            capture_id="01HX0000000000000000000002",
            ingested_at_ns=time.time_ns(),
            deduped_of="01HX0000000000000000000001",
        )
        assert resp.deduped_of == "01HX0000000000000000000001"

    def test_forbids_extras(self):
        with pytest.raises(ValidationError):
            CaptureEventIngestResponse(
                capture_id="01HX0000000000000000000003",
                ingested_at_ns=time.time_ns(),
                experimental_key="oops",
            )
