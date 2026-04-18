# SPDX-License-Identifier: Apache-2.0
"""Unit tests for pce_proxy.heuristic – AI traffic detection."""

import json
import pytest

from pce_proxy.heuristic import (
    match_domain_pattern,
    check_request_body,
    check_response_body,
    detect_ai_request,
    detect_ai_response,
    _upgrade_confidence,
)


# ── Domain pattern matching ──────────────────────────────────────────────

class TestMatchDomainPattern:
    def test_known_openai_subdomain(self):
        assert match_domain_pattern("api.openai.com") is not None

    def test_known_anthropic_subdomain(self):
        assert match_domain_pattern("api.anthropic.com") is not None

    def test_known_deepseek_subdomain(self):
        assert match_domain_pattern("chat.deepseek.com") is not None

    def test_generic_ai_domain(self):
        assert match_domain_pattern("api.newstartup.ai") is not None

    def test_generic_ml_domain(self):
        assert match_domain_pattern("api.something.ml") is not None

    def test_unrelated_domain_returns_none(self):
        assert match_domain_pattern("www.google.com") is None

    def test_unrelated_domain_amazon(self):
        assert match_domain_pattern("www.amazon.com") is None

    def test_inference_subdomain(self):
        assert match_domain_pattern("inference.example.com") is not None

    def test_completion_subdomain(self):
        assert match_domain_pattern("completion.service.io") is not None

    def test_empty_host(self):
        assert match_domain_pattern("") is None


# ── Request body heuristics ──────────────────────────────────────────────

class TestCheckRequestBody:
    def test_openai_chat_completions_high(self):
        body = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]})
        conf, reason = check_request_body(body.encode())
        assert conf == "high"
        assert "messages" in reason and "model" in reason

    def test_legacy_completions_high(self):
        body = json.dumps({"model": "text-davinci-003", "prompt": "Hello"})
        conf, reason = check_request_body(body.encode())
        assert conf == "high"

    def test_streaming_chat_high(self):
        body = json.dumps({"model": "gpt-4", "messages": [], "stream": True})
        conf, reason = check_request_body(body.encode())
        assert conf == "high"

    def test_messages_only_medium(self):
        body = json.dumps({"messages": [{"role": "user", "content": "Hi"}]})
        conf, reason = check_request_body(body.encode())
        assert conf == "medium"

    def test_prompt_temperature_medium(self):
        body = json.dumps({"prompt": "Hello", "temperature": 0.7})
        conf, reason = check_request_body(body.encode())
        assert conf == "medium"

    def test_many_ai_fields_medium(self):
        body = json.dumps({"model": "x", "max_tokens": 100, "temperature": 0.5})
        conf, reason = check_request_body(body.encode())
        assert conf == "medium"
        assert "ai_fields" in reason

    def test_unrelated_body_none(self):
        body = json.dumps({"username": "alice", "password": "secret"})
        conf, reason = check_request_body(body.encode())
        assert conf is None

    def test_empty_body(self):
        conf, reason = check_request_body(b"")
        assert conf is None

    def test_none_body(self):
        conf, reason = check_request_body(None)
        assert conf is None

    def test_short_body(self):
        conf, reason = check_request_body(b"hi")
        assert conf is None

    def test_invalid_json(self):
        conf, reason = check_request_body(b"not json at all {{{")
        assert conf is None

    def test_json_array_not_dict(self):
        conf, reason = check_request_body(b'[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]')
        assert conf is None

    def test_huggingface_inputs(self):
        body = json.dumps({"inputs": "Translate this to French"})
        conf, reason = check_request_body(body.encode())
        assert conf == "medium"

    def test_embeddings_high(self):
        body = json.dumps({"model": "text-embedding-ada-002", "input": "Hello"})
        conf, reason = check_request_body(body.encode())
        assert conf == "high"


# ── Response body heuristics ─────────────────────────────────────────────

class TestCheckResponseBody:
    def test_openai_response_high(self):
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Hi"}}],
            "model": "gpt-4",
        })
        conf, reason = check_response_body(body.encode())
        assert conf == "high"

    def test_anthropic_response_high(self):
        body = json.dumps({"content": "Hello", "role": "assistant"})
        conf, reason = check_response_body(body.encode())
        assert conf == "high"

    def test_cohere_response_high(self):
        body = json.dumps({"generations": [{"text": "Hello"}]})
        conf, reason = check_response_body(body.encode())
        assert conf == "high"

    def test_huggingface_response_medium(self):
        body = json.dumps({"generated_text": "Hello there"})
        conf, reason = check_response_body(body.encode())
        assert conf == "medium"

    def test_sse_stream_with_choices_high(self):
        body = b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        conf, reason = check_response_body(body, content_type="text/event-stream")
        assert conf == "high"

    def test_sse_stream_with_content_block_high(self):
        body = b'data: {"type":"content_block_delta","delta":{"text":"Hi"}}\n\n'
        conf, reason = check_response_body(body, content_type="text/event-stream")
        assert conf == "high"

    def test_sse_stream_with_model_medium(self):
        body = b'data: {"model":"gpt-4","data":"test","text":"hello"}\n\n'
        conf, reason = check_response_body(body, content_type="text/event-stream")
        assert conf == "medium"

    def test_raw_text_choices_message(self):
        body = b'data: {"choices":[{"message":{"content":"test"}}]}\ndata: [DONE]'
        conf, reason = check_response_body(body, content_type="application/json")
        # JSON parse fails, falls through to raw text check
        assert conf == "high"
        assert "raw_text" in reason

    def test_raw_text_content_block(self):
        body = b'event: content_block_delta\ndata: {"content_block": "test"}\n'
        conf, reason = check_response_body(body, content_type="text/plain")
        assert conf == "high"

    def test_unrelated_response(self):
        body = json.dumps({"status": "ok", "items": []})
        conf, reason = check_response_body(body.encode())
        assert conf is None

    def test_empty_response(self):
        conf, reason = check_response_body(b"")
        assert conf is None

    def test_nested_choices_with_message(self):
        body = json.dumps({
            "id": "chatcmpl-123",
            "choices": [{"message": {"role": "assistant", "content": "Hello"}, "index": 0}],
        })
        conf, reason = check_response_body(body.encode())
        assert conf == "high"

    def test_nested_choices_with_delta(self):
        body = json.dumps({
            "choices": [{"delta": {"content": "chunk"}, "index": 0}],
        })
        conf, reason = check_response_body(body.encode())
        assert conf == "high"


# ── Combined heuristic: detect_ai_request ────────────────────────────────

class TestDetectAIRequest:
    def test_known_pattern_domain_post_with_body(self):
        body = json.dumps({"model": "gpt-4", "messages": []})
        conf, reasons = detect_ai_request("api.openai.com", "/v1/chat/completions", "POST", body.encode())
        assert conf == "high"
        assert len(reasons) >= 2  # domain + path + body

    def test_unknown_domain_with_ai_path(self):
        conf, reasons = detect_ai_request("newai.example.com", "/v1/chat/completions", "POST", b"")
        assert conf == "medium"
        assert any("path" in r for r in reasons)

    def test_unknown_domain_with_ai_body(self):
        body = json.dumps({"model": "llama-2", "messages": [{"role": "user", "content": "Hi"}]})
        conf, reasons = detect_ai_request("myserver.local", "/api/inference", "POST", body.encode())
        assert conf == "high"

    def test_completely_unrelated(self):
        body = json.dumps({"username": "alice"})
        conf, reasons = detect_ai_request("www.example.com", "/login", "POST", body.encode())
        assert conf is None

    def test_get_request_skips_body(self):
        body = json.dumps({"model": "gpt-4", "messages": []})
        conf, reasons = detect_ai_request("www.example.com", "/api/data", "GET", body.encode())
        assert conf is None  # GET body is ignored, no domain/path match

    def test_ollama_api(self):
        body = json.dumps({"model": "llama2", "prompt": "Hello"})
        conf, reasons = detect_ai_request("localhost", "/api/generate", "POST", body.encode())
        assert conf == "high"


# ── detect_ai_response ───────────────────────────────────────────────────

class TestDetectAIResponse:
    def test_openai_response(self):
        body = json.dumps({"choices": [{"message": {"content": "Hello"}}]})
        conf, reasons = detect_ai_response(body.encode())
        assert conf == "high"

    def test_sse_response(self):
        body = b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n'
        conf, reasons = detect_ai_response(body, content_type="text/event-stream")
        assert conf == "high"

    def test_empty_response(self):
        conf, reasons = detect_ai_response(b"")
        assert conf is None
        assert len(reasons) == 0


# ── _upgrade_confidence ──────────────────────────────────────────────────

class TestUpgradeConfidence:
    def test_none_to_medium(self):
        assert _upgrade_confidence(None, "medium") == "medium"

    def test_none_to_high(self):
        assert _upgrade_confidence(None, "high") == "high"

    def test_medium_to_high(self):
        assert _upgrade_confidence("medium", "high") == "high"

    def test_high_stays_high(self):
        assert _upgrade_confidence("high", "medium") == "high"

    def test_medium_stays_medium(self):
        assert _upgrade_confidence("medium", "medium") == "medium"
