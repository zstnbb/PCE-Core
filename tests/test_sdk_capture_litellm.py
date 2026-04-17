"""Unit tests for ``pce_core.sdk_capture_litellm``.

We cover the three moving parts without actually running a LiteLLM proxy:

- :mod:`config` — shape of the generated ``litellm_config.yaml``
- :mod:`callback` — payload flattening + provider inference + fire-and-forget
  POST mechanics (with a captured HTTP server stand-in)
- :mod:`bridge` — lifecycle when LiteLLM is NOT installed (clean-error path)

The LiteLLM-installed path is only exercised via a mocked supervisor so no
real ``litellm`` subprocess is spawned by the test suite.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

import pytest

from pce_core.sdk_capture_litellm import (
    LITELLM_AVAILABLE,
    LiteLLMBridge,
    PCECallback,
    build_litellm_config,
    pce_callback_handler,
    write_litellm_config,
)
from pce_core.sdk_capture_litellm.callback import (
    PCE_INGEST_URL_ENV,
    _build_ingest_payload,
    _compute_latency_ms,
    _provider_from_model,
    _response_to_dict,
    _status_code_from_response,
)


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

class TestConfigGenerator:
    def test_contains_pce_callback_in_both_success_and_failure(self):
        raw = build_litellm_config(port=9900)
        data = json.loads(raw)
        ls = data["litellm_settings"]
        assert ls["success_callback"] == [
            "pce_core.sdk_capture_litellm.callback.pce_callback_handler",
        ]
        assert ls["failure_callback"] == ls["success_callback"]

    def test_environment_variables_stamp_ingest_url_and_port(self):
        raw = build_litellm_config(
            pce_ingest_url="http://10.0.0.5:9800/api/v1/captures",
            port=9876,
        )
        data = json.loads(raw)
        env = data["environment_variables"]
        assert env["PCE_INGEST_URL"] == "http://10.0.0.5:9800/api/v1/captures"
        assert env["PCE_LITELLM_PORT"] == "9876"

    def test_master_key_is_none_by_default(self):
        data = json.loads(build_litellm_config())
        assert data["general_settings"]["master_key"] is None

    def test_pass_through_endpoints_cover_main_providers(self):
        data = json.loads(build_litellm_config())
        paths = {e["path"] for e in data["general_settings"]["pass_through_endpoints"]}
        assert {"/openai", "/anthropic", "/gemini"}.issubset(paths)

    def test_write_creates_parents_and_returns_path(self, tmp_path: Path):
        out = tmp_path / "subdir" / "litellm_config.yaml"
        returned = write_litellm_config(out, port=9901)
        assert returned == out
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["environment_variables"]["PCE_LITELLM_PORT"] == "9901"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

class TestProviderInference:
    @pytest.mark.parametrize("model,expected", [
        ("openai/gpt-4", "openai"),
        ("anthropic/claude-3-opus", "anthropic"),
        ("gpt-4-turbo", "openai"),
        ("claude-3-haiku-20240307", "anthropic"),
        ("gemini-1.5-pro", "gemini"),
        ("models/gemini-1.5-pro", "gemini"),
        ("command-r-plus", "cohere"),
        ("mistral-small-latest", "mistral"),
        ("something-weird", "unknown"),
        ("", "unknown"),
    ])
    def test_infers_provider_from_model(self, model: str, expected: str):
        assert _provider_from_model(model) == expected


class TestResponseNormalisation:
    def test_plain_dict_passes_through(self):
        assert _response_to_dict({"a": 1}) == {"a": 1}

    def test_pydantic_v2_model_is_serialised_via_model_dump(self):
        class Fake:
            def model_dump(self):
                return {"x": 42}
        assert _response_to_dict(Fake()) == {"x": 42}

    def test_legacy_object_falls_back_to_vars(self):
        class Legacy:
            def __init__(self):
                self.choices = ["a"]
                self._private = "ignore"
        out = _response_to_dict(Legacy())
        assert out == {"choices": ["a"]}

    def test_none_returns_empty_dict(self):
        assert _response_to_dict(None) == {}

    def test_unserialisable_becomes_repr(self):
        class NoSerialise:
            def __repr__(self): return "NoSerialise()"
        out = _response_to_dict(NoSerialise())
        assert isinstance(out, dict)


class TestLatency:
    def test_datetime_pair(self):
        a = datetime(2026, 1, 1, 12, 0, 0)
        b = datetime(2026, 1, 1, 12, 0, 1)   # +1s
        assert _compute_latency_ms(a, b) == 1000

    def test_float_pair(self):
        assert _compute_latency_ms(1000.0, 1002.5) == 2500

    def test_garbage_returns_zero(self):
        assert _compute_latency_ms("abc", None) == 0


class TestStatusCode:
    def test_exception_response_returns_500_by_default(self):
        assert _status_code_from_response(ValueError("boom")) == 500

    def test_exception_with_status_code_attribute(self):
        class ApiErr(Exception):
            status_code = 429
        assert _status_code_from_response(ApiErr()) == 429

    def test_none_returns_zero(self):
        assert _status_code_from_response(None) == 0


# ---------------------------------------------------------------------------
# Ingest payload shape
# ---------------------------------------------------------------------------

class TestPayloadShape:
    def test_payload_has_all_pce_ingest_fields(self):
        kwargs = {
            "model": "openai/gpt-4",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.1,
        }
        response = {
            "id": "chatcmpl-xyz",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }
        start = datetime(2026, 4, 17, 0, 0, 0)
        end = datetime(2026, 4, 17, 0, 0, 0, 500000)
        payload = _build_ingest_payload(kwargs, response, start, end)
        for key in ("source_type", "source_name", "direction", "provider",
                    "host", "path", "method", "model_name", "status_code",
                    "latency_ms", "headers_json", "body_json", "body_format",
                    "meta"):
            assert key in payload, f"missing key: {key}"
        assert payload["source_type"] == "local_hook"
        assert payload["source_name"] == "sdk-litellm"
        assert payload["direction"] == "network_intercept"
        assert payload["provider"] == "openai"
        assert payload["model_name"] == "openai/gpt-4"
        assert payload["status_code"] == 200
        assert payload["latency_ms"] == 500
        body = json.loads(payload["body_json"])
        assert "request_body" in body
        assert "response_body" in body

    def test_failure_response_payload_uses_500_status(self):
        payload = _build_ingest_payload(
            {"model": "gpt-4", "messages": []},
            ValueError("oops"),
            datetime(2026, 1, 1), datetime(2026, 1, 1),
        )
        assert payload["status_code"] == 500

    def test_streaming_flag_propagates(self):
        payload = _build_ingest_payload(
            {"model": "gpt-4", "messages": [], "stream": True},
            {},
            datetime(2026, 1, 1), datetime(2026, 1, 1),
        )
        assert payload["meta"]["is_streaming"] is True
        body = json.loads(payload["body_json"])
        assert body["is_streaming"] is True


# ---------------------------------------------------------------------------
# Fire-and-forget POST
# ---------------------------------------------------------------------------

class _PayloadCapture:
    """Captures the JSON body the callback would POST to PCE Ingest."""

    def __init__(self) -> None:
        self.payloads: List[dict] = []
        self._evt = threading.Event()
        self._lock = threading.Lock()

    def fake_urlopen(self, req, timeout=5):
        try:
            body = req.data.decode("utf-8") if req.data else ""
            with self._lock:
                self.payloads.append(json.loads(body))
        finally:
            self._evt.set()

        class _Resp:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _Resp()

    def wait(self, timeout: float = 2.0) -> bool:
        return self._evt.wait(timeout)


class TestFireAndForgetPost:
    def test_handler_posts_a_payload_in_background_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        cap = _PayloadCapture()
        monkeypatch.setattr(
            "pce_core.sdk_capture_litellm.callback.urllib_request.urlopen",
            cap.fake_urlopen,
        )
        pce_callback_handler(
            {"model": "openai/gpt-4",
             "messages": [{"role": "user", "content": "hi"}]},
            {"id": "x", "choices": []},
            datetime(2026, 1, 1), datetime(2026, 1, 1),
        )
        assert cap.wait(timeout=3.0), "callback never POSTed"
        assert len(cap.payloads) == 1
        assert cap.payloads[0]["source_name"] == "sdk-litellm"

    def test_handler_does_not_raise_when_ingest_unreachable(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        def explode(*_a, **_kw):
            raise ConnectionRefusedError("no listener")
        monkeypatch.setattr(
            "pce_core.sdk_capture_litellm.callback.urllib_request.urlopen",
            explode,
        )
        # Should not raise.
        pce_callback_handler(
            {"model": "gpt-4", "messages": []}, {},
            datetime(2026, 1, 1), datetime(2026, 1, 1),
        )
        # The background thread dies silently; let it wind down.
        time.sleep(0.1)

    def test_build_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "pce_core.sdk_capture_litellm.callback._build_ingest_payload",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("nope")),
        )
        pce_callback_handler({}, {}, None, None)   # must not raise

    def test_ingest_url_env_var_honoured(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        cap = _PayloadCapture()
        captured_url = {}

        def fake_urlopen(req, timeout=5):
            captured_url["url"] = req.full_url
            return cap.fake_urlopen(req, timeout=timeout)

        monkeypatch.setenv(PCE_INGEST_URL_ENV,
                           "http://example.invalid/api/v1/captures")
        monkeypatch.setattr(
            "pce_core.sdk_capture_litellm.callback.urllib_request.urlopen",
            fake_urlopen,
        )
        pce_callback_handler(
            {"model": "gpt-4", "messages": []}, {},
            datetime(2026, 1, 1), datetime(2026, 1, 1),
        )
        assert cap.wait(timeout=3.0)
        assert captured_url["url"] == "http://example.invalid/api/v1/captures"


# ---------------------------------------------------------------------------
# Class-style PCECallback
# ---------------------------------------------------------------------------

class TestPCECallbackClass:
    def test_instance_can_be_created(self):
        cb = PCECallback()
        # Calling the sync hook should not raise.
        cb.log_success_event({"model": "gpt-4", "messages": []}, {},
                             datetime(2026, 1, 1), datetime(2026, 1, 1))


# ---------------------------------------------------------------------------
# Bridge lifecycle — the no-LiteLLM path
# ---------------------------------------------------------------------------

class TestBridgeWithoutLiteLLM:
    def test_start_returns_clean_error_when_litellm_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "pce_core.sdk_capture_litellm.bridge.LITELLM_AVAILABLE", False,
        )
        monkeypatch.setattr(
            "pce_core.sdk_capture_litellm.bridge._has_litellm_cli",
            lambda: False,
        )
        bridge = LiteLLMBridge(port=19999)
        async def _go():
            return await bridge.start()
        result = asyncio.new_event_loop().run_until_complete(_go())
        assert result == {
            "ok": False, "error": "litellm_not_installed",
            "hint": "pip install litellm[proxy]",
        }

    def test_get_status_before_start_is_idempotent(self):
        bridge = LiteLLMBridge(port=19999)
        status = bridge.get_status()
        assert status["running"] is False
        assert status["port"] is None
        assert isinstance(status["litellm_available"], bool)


class TestBridgeStopWithoutStart:
    def test_stop_is_safe_when_never_started(self):
        bridge = LiteLLMBridge()
        async def _go():
            return await bridge.stop()
        result = asyncio.new_event_loop().run_until_complete(_go())
        assert result["ok"] is True
        assert result["running"] is False
