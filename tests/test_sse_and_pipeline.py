# SPDX-License-Identifier: Apache-2.0
"""Tests for SSE parsing, broadened OpenAI normalizer, and pipeline fallback."""

import json
import time
from pce_core.normalizer.sse import is_sse_text, assemble_sse_response, assemble_anthropic_sse, assemble_any_sse
from pce_core.normalizer.openai import OpenAIChatNormalizer
from pce_core.normalizer.conversation import ConversationNormalizer
from pce_core.normalizer.pipeline import _normalize_network_intercept, _try_generic_normalize


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------

def test_is_sse_text():
    assert is_sse_text('data: {"choices":[{"delta":{"content":"Hi"}}]}\ndata: [DONE]')
    assert is_sse_text('\ndata: something\n')
    assert not is_sse_text('{"choices": []}')
    assert not is_sse_text('')
    assert not is_sse_text(None)
    print("[PASS] test_is_sse_text")


def test_assemble_sse_basic():
    sse = "\n".join([
        'data: {"id":"1","model":"deepseek-chat","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"id":"1","model":"deepseek-chat","choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"id":"1","model":"deepseek-chat","choices":[{"delta":{"content":" world!"}}]}',
        'data: [DONE]',
    ])
    result = assemble_sse_response(sse)
    assert result is not None
    assert result["model"] == "deepseek-chat"
    msg = result["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hello world!"
    print("[PASS] test_assemble_sse_basic")


def test_assemble_sse_with_reasoning():
    sse = "\n".join([
        'data: {"model":"deepseek-r1","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"model":"deepseek-r1","choices":[{"delta":{"reasoning_content":"Let me think..."}}]}',
        'data: {"model":"deepseek-r1","choices":[{"delta":{"content":"The answer is 42."}}]}',
        'data: [DONE]',
    ])
    result = assemble_sse_response(sse)
    assert result is not None
    msg = result["choices"][0]["message"]
    assert msg["content"] == "The answer is 42."
    assert msg["reasoning_content"] == "Let me think..."
    print("[PASS] test_assemble_sse_with_reasoning")


def test_assemble_sse_empty():
    assert assemble_sse_response("") is None
    assert assemble_sse_response("data: [DONE]") is None
    assert assemble_sse_response("not sse at all") is None
    print("[PASS] test_assemble_sse_empty")


# ---------------------------------------------------------------------------
# OpenAI normalizer can_handle (broadened)
# ---------------------------------------------------------------------------

def test_assemble_anthropic_sse():
    sse = "\n".join([
        'event: message_start',
        'data: {"type":"message_start","message":{"model":"claude-sonnet-4-20250514","role":"assistant"}}',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" from Claude!"}}',
        'event: message_delta',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}}',
    ])
    result = assemble_anthropic_sse(sse)
    assert result is not None
    assert result["model"] == "claude-sonnet-4-20250514"
    assert result["content"][0]["text"] == "Hello from Claude!"
    assert result["usage"]["output_tokens"] == 10
    print("[PASS] test_assemble_anthropic_sse")


def test_assemble_any_sse_autodetect():
    # OpenAI format
    openai_sse = 'data: {"model":"gpt-4","choices":[{"delta":{"content":"Hi"}}]}\ndata: [DONE]'
    r1 = assemble_any_sse(openai_sse)
    assert r1 is not None
    assert r1["choices"][0]["message"]["content"] == "Hi"

    # Anthropic format
    anthropic_sse = "\n".join([
        'data: {"type":"message_start","message":{"model":"claude-3"}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hey"}}',
    ])
    r2 = assemble_any_sse(anthropic_sse)
    assert r2 is not None
    assert r2["content"][0]["text"] == "Hey"
    print("[PASS] test_assemble_any_sse_autodetect")


def test_openai_can_handle_broadened():
    n = OpenAIChatNormalizer()
    # Exact paths
    assert n.can_handle("openai", "api.openai.com", "/v1/chat/completions")
    # Regex path variants
    assert n.can_handle("deepseek", "chat.deepseek.com", "/api/v0/chat/completions")
    assert n.can_handle("unknown", "unknown.com", "/api/v2/chat_completions")
    assert n.can_handle("openai", "chatgpt.com", "/backend-api/conversation")
    # Known hosts
    assert n.can_handle("deepseek", "chat.deepseek.com", "/some/random/path")
    assert n.can_handle("xai", "grok.com", "/api/chat")
    assert n.can_handle("mistral", "chat.mistral.ai", "/api/chat")
    # Known providers
    assert n.can_handle("openai", "custom-proxy.com", "/custom/path")
    assert n.can_handle("groq", "custom-proxy.com", "/custom/path")
    # Unknown should NOT match (falls through to ConversationNormalizer)
    assert not n.can_handle("totally_new", "brand-new-ai.com", "/api/v1/generate")
    print("[PASS] test_openai_can_handle_broadened")


# ---------------------------------------------------------------------------
# OpenAI normalizer with SSE response
# ---------------------------------------------------------------------------

def test_openai_normalize_sse_response():
    n = OpenAIChatNormalizer()
    req = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]})
    sse_resp = "\n".join([
        'data: {"model":"gpt-4","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"model":"gpt-4","choices":[{"delta":{"content":"Hi"}}]}',
        'data: {"model":"gpt-4","choices":[{"delta":{"content":" there!"}}]}',
        'data: [DONE]',
    ])
    result = n.normalize(req, sse_resp, provider="openai", host="api.openai.com", path="/v1/chat/completions")
    assert result is not None
    assert len(result.messages) == 2  # user + assistant
    assert result.messages[0].role == "user"
    assert result.messages[0].content_text == "Hello"
    assert result.messages[1].role == "assistant"
    assert result.messages[1].content_text == "Hi there!"
    print("[PASS] test_openai_normalize_sse_response")


# ---------------------------------------------------------------------------
# ConversationNormalizer catch-all
# ---------------------------------------------------------------------------

def test_conversation_catch_all():
    n = ConversationNormalizer()
    # Should handle known providers
    assert n.can_handle("deepseek", "chat.deepseek.com", "/chat/xxx")
    # Should also handle completely unknown providers (catch-all)
    assert n.can_handle("brand_new_ai", "new-ai.com", "/chat")
    # But normalize should only succeed if body has messages array
    result = n.normalize('{"not_messages": []}', '{}', provider="unknown", host="x.com", path="/")
    assert result is None
    result = n.normalize(
        json.dumps({"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]}),
        '{}', provider="new_ai", host="new-ai.com", path="/chat"
    )
    assert result is not None
    assert len(result.messages) == 2
    print("[PASS] test_conversation_catch_all")


# ---------------------------------------------------------------------------
# Pipeline: _normalize_network_intercept with SSE
# ---------------------------------------------------------------------------

def test_pipeline_network_intercept_sse():
    req_body = json.dumps({"model": "deepseek-chat", "messages": [{"role": "user", "content": "What is 2+2?"}]})
    sse_resp = "\n".join([
        'data: {"model":"deepseek-chat","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"model":"deepseek-chat","choices":[{"delta":{"content":"4"}}]}',
        'data: [DONE]',
    ])
    wrapper = json.dumps({
        "request_body": req_body,
        "response_body": sse_resp,
        "url": "https://chat.deepseek.com/api/v0/chat/completions",
        "is_streaming": True,
    })
    capture_row = {
        "body_text_or_json": wrapper,
        "direction": "network_intercept",
        "provider": "deepseek",
        "host": "chat.deepseek.com",
        "path": "/",
        "pair_id": "test-pair",
        "created_at": time.time(),
    }
    result = _normalize_network_intercept(capture_row)
    assert result is not None
    assert result.provider in ("deepseek", "openai")  # OpenAI normalizer handles it
    assert any(m.role == "user" and "2+2" in m.content_text for m in result.messages)
    assert any(m.role == "assistant" and "4" in m.content_text for m in result.messages)
    print("[PASS] test_pipeline_network_intercept_sse")


# ---------------------------------------------------------------------------
# Pipeline: _try_generic_normalize fallback
# ---------------------------------------------------------------------------

def test_generic_fallback():
    req = json.dumps({"model": "brand-new-model", "messages": [{"role": "user", "content": "Test"}]})
    resp = json.dumps({"model": "brand-new-model", "choices": [{"message": {"role": "assistant", "content": "OK"}}]})
    req_row = {"body_text_or_json": req, "provider": "new_provider", "host": "new-ai.com", "path": "/api/chat", "created_at": time.time()}
    resp_row = {"body_text_or_json": resp, "provider": "new_provider", "host": "new-ai.com", "path": "/api/chat", "created_at": time.time()}
    result = _try_generic_normalize(req_row, resp_row, "new_provider")
    assert result is not None
    assert result.provider == "new_provider"
    assert len(result.messages) == 2
    assert result.messages[0].content_text == "Test"
    assert result.messages[1].content_text == "OK"
    print("[PASS] test_generic_fallback")


if __name__ == "__main__":
    test_is_sse_text()
    test_assemble_sse_basic()
    test_assemble_sse_with_reasoning()
    test_assemble_sse_empty()
    test_assemble_anthropic_sse()
    test_assemble_any_sse_autodetect()
    test_openai_can_handle_broadened()
    test_openai_normalize_sse_response()
    test_conversation_catch_all()
    test_pipeline_network_intercept_sse()
    test_generic_fallback()
    print("\n=== ALL TESTS PASSED ===")
