"""Tests for pce_core.normalizer – OpenAI, Anthropic, and pipeline."""

import sys
import os
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import (
    init_db,
    insert_capture,
    new_pair_id,
    query_sessions,
    query_messages,
    SOURCE_PROXY,
)
from pce_core.normalizer.base import normalize_pair
from pce_core.normalizer.openai import OpenAIChatNormalizer
from pce_core.normalizer.anthropic import AnthropicMessagesNormalizer
from pce_core.normalizer.pipeline import try_normalize_pair


def test_openai_normalizer():
    n = OpenAIChatNormalizer()

    # can_handle
    assert n.can_handle("openai", "api.openai.com", "/v1/chat/completions")
    assert n.can_handle("deepseek", "api.deepseek.com", "/v1/chat/completions")
    assert n.can_handle("groq", "api.groq.com", "/v1/chat/completions")
    assert not n.can_handle("anthropic", "api.anthropic.com", "/v1/messages")
    print("[PASS] OpenAI: can_handle routing")

    # Basic normalize
    result = n.normalize(
        request_body='{"model":"gpt-4","messages":[{"role":"user","content":"hello"}]}',
        response_body='{"choices":[{"message":{"role":"assistant","content":"Hi there!"}}],"model":"gpt-4","usage":{"prompt_tokens":5,"completion_tokens":10,"total_tokens":15}}',
        provider="openai",
        host="api.openai.com",
        path="/v1/chat/completions",
        created_at=time.time(),
    )
    assert result is not None
    assert result.provider == "openai"
    assert result.tool_family == "api-direct"
    assert len(result.messages) == 2
    assert result.messages[0].role == "user"
    assert result.messages[0].content_text == "hello"
    assert result.messages[1].role == "assistant"
    assert result.messages[1].content_text == "Hi there!"
    assert result.messages[1].token_estimate == 10
    assert result.title_hint == "hello"
    print("[PASS] OpenAI: basic normalize (user + assistant)")

    # Multi-message with system
    result2 = n.normalize(
        request_body='{"model":"gpt-4","messages":[{"role":"system","content":"Be helpful"},{"role":"user","content":"What is AI?"},{"role":"assistant","content":"AI is..."},{"role":"user","content":"Tell me more"}]}',
        response_body='{"choices":[{"message":{"role":"assistant","content":"Sure, AI stands for..."}}],"model":"gpt-4"}',
        provider="openai",
        host="api.openai.com",
        path="/v1/chat/completions",
    )
    assert result2 is not None
    assert len(result2.messages) == 5  # system + 2 user + assistant(history) + assistant(response)
    assert result2.messages[0].role == "system"
    assert result2.messages[3].role == "user"
    assert result2.messages[4].role == "assistant"
    assert result2.title_hint == "What is AI?"
    print("[PASS] OpenAI: multi-turn with system message")

    # Multimodal content (array format)
    result3 = n.normalize(
        request_body='{"model":"gpt-4o","messages":[{"role":"user","content":[{"type":"text","text":"What is this?"},{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]}]}',
        response_body='{"choices":[{"message":{"role":"assistant","content":"This appears to be..."}}]}',
        provider="openai",
        host="api.openai.com",
        path="/v1/chat/completions",
    )
    assert result3 is not None
    assert result3.messages[0].content_text == "What is this?"
    print("[PASS] OpenAI: multimodal content array")

    # Invalid body
    result4 = n.normalize(
        request_body="not json",
        response_body="also not json",
        provider="openai",
        host="api.openai.com",
        path="/v1/chat/completions",
    )
    assert result4 is None
    print("[PASS] OpenAI: graceful on invalid JSON")

    # Local model (Ollama)
    result5 = n.normalize(
        request_body='{"model":"llama3","messages":[{"role":"user","content":"hi"}]}',
        response_body='{"choices":[{"message":{"role":"assistant","content":"Hello!"}}]}',
        provider="ollama",
        host="localhost",
        path="/v1/chat/completions",
    )
    assert result5 is not None
    assert result5.tool_family == "local-model"
    print("[PASS] OpenAI: local model (localhost)")


def test_anthropic_normalizer():
    n = AnthropicMessagesNormalizer()

    # can_handle
    assert n.can_handle("anthropic", "api.anthropic.com", "/v1/messages")
    assert not n.can_handle("openai", "api.openai.com", "/v1/chat/completions")
    print("[PASS] Anthropic: can_handle routing")

    # Basic normalize
    result = n.normalize(
        request_body='{"model":"claude-sonnet-4-20250514","max_tokens":1024,"system":"Be helpful","messages":[{"role":"user","content":"Hello"}]}',
        response_body='{"content":[{"type":"text","text":"Hi! How can I help?"}],"model":"claude-sonnet-4-20250514","role":"assistant","usage":{"input_tokens":12,"output_tokens":8}}',
        provider="anthropic",
        host="api.anthropic.com",
        path="/v1/messages",
        created_at=time.time(),
    )
    assert result is not None
    assert result.provider == "anthropic"
    assert len(result.messages) == 3  # system + user + assistant
    assert result.messages[0].role == "system"
    assert result.messages[0].content_text == "Be helpful"
    assert result.messages[1].role == "user"
    assert result.messages[1].content_text == "Hello"
    assert result.messages[2].role == "assistant"
    assert result.messages[2].content_text == "Hi! How can I help?"
    assert result.messages[2].token_estimate == 8
    assert result.title_hint == "Hello"
    print("[PASS] Anthropic: basic normalize (system + user + assistant)")

    # Content blocks array in request
    result2 = n.normalize(
        request_body='{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":[{"type":"text","text":"Explain quantum computing"}]}]}',
        response_body='{"content":[{"type":"text","text":"Quantum computing uses..."}],"role":"assistant"}',
        provider="anthropic",
        host="api.anthropic.com",
        path="/v1/messages",
    )
    assert result2 is not None
    assert result2.messages[0].content_text == "Explain quantum computing"
    print("[PASS] Anthropic: content blocks in request")

    # Tool use in response
    result3 = n.normalize(
        request_body='{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"Search for cats"}]}',
        response_body='{"content":[{"type":"text","text":"Let me search..."},{"type":"tool_use","name":"web_search","input":{"query":"cats"}}],"role":"assistant"}',
        provider="anthropic",
        host="api.anthropic.com",
        path="/v1/messages",
    )
    assert result3 is not None
    assert "Let me search" in result3.messages[-1].content_text
    assert "[tool_use: web_search]" in result3.messages[-1].content_text
    print("[PASS] Anthropic: tool_use in response")

    # Invalid body
    result4 = n.normalize(
        request_body="garbage",
        response_body="garbage",
        provider="anthropic",
        host="api.anthropic.com",
        path="/v1/messages",
    )
    assert result4 is None
    print("[PASS] Anthropic: graceful on invalid JSON")


def test_registry_dispatch():
    """Test that normalize_pair dispatches to the correct normalizer."""
    req = {
        "provider": "openai",
        "host": "api.openai.com",
        "path": "/v1/chat/completions",
        "body_text_or_json": '{"model":"gpt-4","messages":[{"role":"user","content":"test"}]}',
        "model_name": "gpt-4",
        "created_at": time.time(),
    }
    resp = {
        "provider": "openai",
        "host": "api.openai.com",
        "path": "/v1/chat/completions",
        "body_text_or_json": '{"choices":[{"message":{"role":"assistant","content":"OK"}}]}',
    }
    result = normalize_pair(req, resp)
    assert result is not None
    assert result.provider == "openai"
    print("[PASS] Registry: dispatches OpenAI correctly")

    req2 = {
        "provider": "anthropic",
        "host": "api.anthropic.com",
        "path": "/v1/messages",
        "body_text_or_json": '{"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"hi"}]}',
        "created_at": time.time(),
    }
    resp2 = {
        "provider": "anthropic",
        "host": "api.anthropic.com",
        "path": "/v1/messages",
        "body_text_or_json": '{"content":[{"type":"text","text":"Hello!"}],"role":"assistant"}',
    }
    result2 = normalize_pair(req2, resp2)
    assert result2 is not None
    assert result2.provider == "anthropic"
    print("[PASS] Registry: dispatches Anthropic correctly")

    # Unknown provider → returns None
    req3 = {
        "provider": "unknown",
        "host": "some.random.host",
        "path": "/api/unknown",
        "body_text_or_json": "{}",
    }
    result3 = normalize_pair(req3, req3)
    assert result3 is None
    print("[PASS] Registry: returns None for unknown provider")


def test_pipeline_integration():
    """Test the full pipeline: insert captures → auto-normalize → query sessions/messages."""
    db_path = Path(_tmp) / "test_pipeline.db"
    init_db(db_path)

    # Insert an OpenAI request/response pair
    pid = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid,
        host="api.openai.com",
        path="/v1/chat/completions",
        method="POST",
        provider="openai",
        model_name="gpt-4",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"gpt-4","messages":[{"role":"system","content":"You are helpful"},{"role":"user","content":"What is Python?"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db_path,
    )
    insert_capture(
        direction="response",
        pair_id=pid,
        host="api.openai.com",
        path="/v1/chat/completions",
        method="POST",
        provider="openai",
        model_name="gpt-4",
        status_code=200,
        latency_ms=500.0,
        headers_redacted_json="{}",
        body_text_or_json='{"choices":[{"message":{"role":"assistant","content":"Python is a programming language."}}],"model":"gpt-4","usage":{"prompt_tokens":20,"completion_tokens":8,"total_tokens":28}}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db_path,
    )

    # Run pipeline
    session_id = try_normalize_pair(pid, source_id=SOURCE_PROXY, db_path=db_path)
    assert session_id is not None
    print(f"[PASS] Pipeline: normalized pair → session {session_id[:8]}")

    # Verify session
    sessions = query_sessions(last=10, db_path=db_path)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["provider"] == "openai"
    assert s["tool_family"] == "api-direct"
    assert s["title_hint"] == "What is Python?"
    assert s["message_count"] == 3  # system + user + assistant
    print("[PASS] Pipeline: session metadata correct")

    # Verify messages
    msgs = query_messages(session_id, db_path=db_path)
    assert len(msgs) == 3
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content_text"] == "You are helpful"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content_text"] == "What is Python?"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content_text"] == "Python is a programming language."
    assert msgs[2]["model_name"] == "gpt-4"
    assert msgs[2]["token_estimate"] == 8
    assert msgs[2]["capture_pair_id"] == pid
    print("[PASS] Pipeline: messages correct (roles, content, tokens, pair linkage)")

    # Insert an Anthropic pair
    pid2 = new_pair_id()
    insert_capture(
        direction="request",
        pair_id=pid2,
        host="api.anthropic.com",
        path="/v1/messages",
        method="POST",
        provider="anthropic",
        headers_redacted_json="{}",
        body_text_or_json='{"model":"claude-sonnet-4-20250514","system":"Be concise","messages":[{"role":"user","content":"What is Rust?"}]}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db_path,
    )
    insert_capture(
        direction="response",
        pair_id=pid2,
        host="api.anthropic.com",
        path="/v1/messages",
        method="POST",
        provider="anthropic",
        status_code=200,
        headers_redacted_json="{}",
        body_text_or_json='{"content":[{"type":"text","text":"Rust is a systems programming language."}],"model":"claude-sonnet-4-20250514","role":"assistant","usage":{"input_tokens":15,"output_tokens":10}}',
        body_format="json",
        source_id=SOURCE_PROXY,
        db_path=db_path,
    )

    session_id2 = try_normalize_pair(pid2, source_id=SOURCE_PROXY, db_path=db_path)
    assert session_id2 is not None

    sessions2 = query_sessions(last=10, db_path=db_path)
    assert len(sessions2) == 2  # one OpenAI + one Anthropic
    print("[PASS] Pipeline: Anthropic pair normalized alongside OpenAI")

    msgs2 = query_messages(session_id2, db_path=db_path)
    assert len(msgs2) == 3  # system + user + assistant
    assert msgs2[0]["role"] == "system"
    assert msgs2[0]["content_text"] == "Be concise"
    assert msgs2[2]["content_text"] == "Rust is a systems programming language."
    assert msgs2[2]["token_estimate"] == 10
    print("[PASS] Pipeline: Anthropic messages correct")

    print("\n=== All normalizer tests passed ===")


def test_all():
    test_openai_normalizer()
    test_anthropic_normalizer()
    test_registry_dispatch()
    test_pipeline_integration()


if __name__ == "__main__":
    test_all()
