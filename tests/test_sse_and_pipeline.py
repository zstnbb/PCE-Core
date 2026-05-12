# SPDX-License-Identifier: Apache-2.0
"""Tests for SSE parsing, broadened OpenAI normalizer, and pipeline fallback."""

import json
import time
from pathlib import Path
from pce_core.normalizer.sse import (
    is_sse_text,
    assemble_sse_response,
    assemble_anthropic_sse,
    assemble_any_sse,
    assemble_chatgpt_web_f_sse,
)
from pce_core.normalizer.openai import OpenAIChatNormalizer
from pce_core.normalizer.conversation import ConversationNormalizer
from pce_core.normalizer.pipeline import _normalize_network_intercept, _try_generic_normalize

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


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


# ---------------------------------------------------------------------------
# ChatGPT Web /backend-api/f/conversation JSON-patch SSE assembler
# (Stage 4 P2 ChatGPT Desktop closure — see
#  Docs/research/2026-05-12-chatgpt-desktop-f-endpoint-recon-findings.md)
# ---------------------------------------------------------------------------

def test_chatgpt_web_f_sse_minimal():
    """Single-message stream: root-add + appends + bare-v + complete."""
    sse = "\n".join([
        'event: delta_encoding',
        'data: "v1"',
        '',
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"id":"m1","author":{"role":"assistant"},"content":{"content_type":"text","text":""}},"conversation_id":"conv-A"}}',
        '',
        'event: delta',
        'data: {"p":"/message/content/text","o":"append","v":"Hello"}',
        '',
        'event: delta',
        'data: {"v":" "}',
        '',
        'event: delta',
        'data: {"v":"world"}',
        '',
        'data: {"type":"message_stream_complete","conversation_id":"conv-A"}',
        '',
        'data: [DONE]',
        '',
    ])
    result = assemble_chatgpt_web_f_sse(sse)
    assert result is not None
    assert result["conversation_id"] == "conv-A"
    assert len(result["choices"]) == 1
    msg = result["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hello world"
    print("[PASS] test_chatgpt_web_f_sse_minimal")


def test_chatgpt_web_f_sse_bare_v_continuation():
    """A long stream of bare-v deltas after one append must concatenate."""
    deltas = [
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"id":"m1","author":{"role":"assistant"},"content":{"content_type":"text","text":""}}}}',
    ]
    expected = ""
    for tok in ["The", " ", "quick", " ", "brown", " ", "fox"]:
        if not expected:
            deltas += ['', 'event: delta', f'data: {{"p":"/message/content/text","o":"append","v":"{tok}"}}']
        else:
            deltas += ['', 'event: delta', f'data: {{"v":"{tok}"}}']
        expected += tok
    deltas.append('')
    sse = "\n".join(deltas)
    result = assemble_chatgpt_web_f_sse(sse)
    assert result is not None
    assert result["choices"][0]["message"]["content"] == expected
    print("[PASS] test_chatgpt_web_f_sse_bare_v_continuation")


def test_chatgpt_web_f_sse_multiple_messages():
    """Two consecutive `{"p":"","o":"add"}` deltas → 2 messages emitted in order."""
    sse = "\n".join([
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"content_type":"code","text":""}}}}',
        '',
        'event: delta',
        'data: {"p":"/message/content/text","o":"append","v":"{\\"tool\\":\\"x\\"}"}',
        '',
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"author":{"role":"tool"},"content":{"content_type":"text","text":""}}}}',
        '',
        'event: delta',
        'data: {"p":"/message/content/text","o":"append","v":"tool result"}',
        '',
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"content_type":"text","text":""}}}}',
        '',
        'event: delta',
        'data: {"p":"/message/content/text","o":"append","v":"final answer"}',
        '',
        'data: {"type":"message_stream_complete"}',
        '',
        'data: [DONE]',
        '',
    ])
    result = assemble_chatgpt_web_f_sse(sse)
    assert result is not None
    assert len(result["choices"]) == 3
    assert result["choices"][0]["message"]["role"] == "assistant"
    assert result["choices"][0]["message"]["content"] == '{"tool":"x"}'
    assert result["choices"][1]["message"]["role"] == "tool"
    assert result["choices"][1]["message"]["content"] == "tool result"
    assert result["choices"][2]["message"]["role"] == "assistant"
    assert result["choices"][2]["message"]["content"] == "final answer"
    print("[PASS] test_chatgpt_web_f_sse_multiple_messages")


def test_chatgpt_web_f_sse_extracts_model_slug():
    """resolved_model_slug from the metadata is surfaced as result['model']."""
    sse = "\n".join([
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"content_type":"text","text":""},"metadata":{"resolved_model_slug":"i-5-mini","model_slug":"gpt-5-3-mini"}}}}',
        '',
        'event: delta',
        'data: {"p":"/message/content/text","o":"append","v":"hi"}',
        '',
        'data: [DONE]',
        '',
    ])
    result = assemble_chatgpt_web_f_sse(sse)
    assert result is not None
    # resolved_model_slug wins over model_slug (per implementation)
    assert result["model"] == "i-5-mini"
    print("[PASS] test_chatgpt_web_f_sse_extracts_model_slug")


def test_chatgpt_web_f_sse_returns_none_when_no_deltas():
    """Body without any event:delta frames (e.g. 567-byte handoff envelope)
    returns None — caller falls back to standard SSE assembler.
    """
    sse = "\n".join([
        'event: delta_encoding',
        'data: "v1"',
        '',
        'data: {"type":"resume_conversation_token","token":"X","conversation_id":"c"}',
        '',
        'data: {"type":"stream_handoff","conversation_id":"c","options":[{"type":"subscribe_ws_topic","topic_id":"t"}]}',
        '',
        'data: [DONE]',
        '',
    ])
    assert assemble_chatgpt_web_f_sse(sse) is None
    print("[PASS] test_chatgpt_web_f_sse_returns_none_when_no_deltas")


def test_chatgpt_web_f_sse_returns_none_for_empty_or_garbage():
    assert assemble_chatgpt_web_f_sse("") is None
    assert assemble_chatgpt_web_f_sse("not sse at all") is None
    assert assemble_chatgpt_web_f_sse("data: [DONE]") is None
    print("[PASS] test_chatgpt_web_f_sse_returns_none_for_empty_or_garbage")


def test_chatgpt_web_f_sse_multimodal_parts():
    """content_type=multimodal_text + parts list — text parts concatenated."""
    sse = "\n".join([
        'event: delta',
        'data: {"p":"","o":"add","v":{"message":{"author":{"role":"assistant"},"content":{"content_type":"multimodal_text","parts":["alpha"," beta"]}}}}',
        '',
        'data: [DONE]',
        '',
    ])
    result = assemble_chatgpt_web_f_sse(sse)
    assert result is not None
    assert result["choices"][0]["message"]["content"] == "alpha beta"
    print("[PASS] test_chatgpt_web_f_sse_multimodal_parts")


def test_chatgpt_web_f_sse_real_fixture():
    """Real captured 15829-byte body from row 62f1686f... (ChatGPT Desktop, 2026-05-12).

    Empirical: 39 event:delta frames → 3 messages (search tool call,
    tool error, final assistant reply in zh-CN).
    """
    fixture = _FIXTURES_DIR / "chatgpt_f_conversation_response.txt"
    if not fixture.exists():
        print(f"[SKIP] fixture missing: {fixture}")
        return
    body = fixture.read_text(encoding="utf-8")
    result = assemble_chatgpt_web_f_sse(body)
    assert result is not None, "real fixture must assemble"
    assert result["model"] == "i-5-mini" or result["model"] == "gpt-5-3-mini"  # one of the two slugs
    assert result.get("conversation_id") == "69ff3abc-9598-83ea-993f-8f3c5854a2eb"
    assert len(result["choices"]) >= 2  # at least 2 messages assembled
    # Some message should contain the user-visible Chinese reply substring
    final_assistant = [c for c in result["choices"] if c["message"]["role"] == "assistant"]
    assert len(final_assistant) >= 1
    last = final_assistant[-1]["message"]["content"]
    # Real reply contains a markdown structure with Chinese text
    assert len(last) > 100
    assert "Claude" in last or "礼品卡" in last or "明白" in last
    print(f"[PASS] test_chatgpt_web_f_sse_real_fixture (assembled {len(result['choices'])} msgs, "
          f"last assistant len={len(last)})")


def test_chatgpt_web_f_sse_live_fixture_simple_text():
    """Real captured 2992-byte body from live sweep 2026-05-12 23:34 pair
    6a9847ff41134241 ("What is 2+2?" → "\\n\\n2 + 2 = 4.").

    Wire-format variant: single non-streaming root-add (no incremental
    deltas — short reply emitted as one block with
    ``content.parts = ["\\n\\n2 + 2 = 4."]``). Locks in support for the
    `parts: [<str>]` fallback in `_extract_message_text`.
    """
    fixture = _FIXTURES_DIR / "chatgpt_f_conversation_response_simple_text.txt"
    if not fixture.exists():
        print(f"[SKIP] live fixture missing: {fixture}")
        return
    body = fixture.read_text(encoding="utf-8")
    result = assemble_chatgpt_web_f_sse(body)
    assert result is not None
    assert result.get("conversation_id") == "6a034877-cfc4-83e8-9c3f-9ccdde05cf75"
    assert result["model"] == "gpt-5-3"
    assert len(result["choices"]) == 1
    msg = result["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "\n\n2 + 2 = 4."
    print("[PASS] test_chatgpt_web_f_sse_live_fixture_simple_text")


def test_openai_normalizer_session_key_from_response_conversation_id():
    """Bug-regression: new-chat first-message has no conversation_id in
    the request body (only ``parent_message_id="client-created-root"``).
    Without the response-fallback, turn 1 + turn 2 of the same chat
    would land in different session rows. See live sweep
    2026-05-12 23:34 pair 6a9847ff41134241.
    """
    req_path = _FIXTURES_DIR / "chatgpt_f_conversation_request_simple_text.json"
    resp_path = _FIXTURES_DIR / "chatgpt_f_conversation_response_simple_text.txt"
    if not req_path.exists() or not resp_path.exists():
        print(f"[SKIP] live fixtures missing")
        return
    req = req_path.read_text(encoding="utf-8")
    resp = resp_path.read_text(encoding="utf-8")
    # Sanity: confirm the request really has NO conversation_id (the precondition
    # for this regression test — if a future capture pattern changes this we want
    # the test to fail loudly so we re-evaluate).
    import json as _json
    req_obj = _json.loads(req)
    assert "conversation_id" not in req_obj or not req_obj.get("conversation_id"), \
        "live fixture invariant: new-chat request must lack conversation_id"
    n = OpenAIChatNormalizer()
    result = n.normalize(
        request_body=req,
        response_body=resp,
        provider="openai",
        host="chatgpt.com",
        path="/backend-api/f/conversation",
    )
    assert result is not None
    # session_key MUST resolve from the response's conversation_id.
    assert result.session_key == "6a034877-cfc4-83e8-9c3f-9ccdde05cf75", \
        f"session_key fallback failed: got {result.session_key!r}"
    # And the actual content is captured.
    assert any(m.role == "user" and m.content_text == "What is 2+2?"
               for m in result.messages)
    assert any(m.role == "assistant" and m.content_text == "\n\n2 + 2 = 4."
               for m in result.messages)
    print("[PASS] test_openai_normalizer_session_key_from_response_conversation_id")


def test_openai_normalizer_handles_f_conversation_path():
    """End-to-end: OpenAIChatNormalizer.normalize() against the fixture.

    Validates the wire-up in openai.py — when path matches
    /backend-api/f/conversation, the SSE fallback routes to the
    ChatGPT Web assembler instead of the OpenAI-API assembler.
    """
    req_path = _FIXTURES_DIR / "chatgpt_f_conversation_request.json"
    resp_path = _FIXTURES_DIR / "chatgpt_f_conversation_response.txt"
    if not req_path.exists() or not resp_path.exists():
        print(f"[SKIP] fixtures missing")
        return
    req = req_path.read_text(encoding="utf-8")
    resp = resp_path.read_text(encoding="utf-8")
    n = OpenAIChatNormalizer()
    assert n.can_handle("openai", "chatgpt.com", "/backend-api/f/conversation")
    result = n.normalize(
        request_body=req,
        response_body=resp,
        provider="openai",
        host="chatgpt.com",
        path="/backend-api/f/conversation",
    )
    assert result is not None, "f/conversation normalize must succeed"
    assert result.provider == "openai"
    # session_key should come from the request body's conversation_id
    assert result.session_key == "69ff3abc-9598-83ea-993f-8f3c5854a2eb"
    # At minimum 1 user + 1 assistant message
    roles = [m.role for m in result.messages]
    assert "user" in roles and "assistant" in roles
    # Confidence should be reasonable (>= 0.4 — has structured req + resp)
    assert (result.confidence or 0) >= 0.4
    print(f"[PASS] test_openai_normalizer_handles_f_conversation_path "
          f"({len(result.messages)} msgs, confidence={result.confidence:.2f})")


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
    # ChatGPT Web f-conversation JSON-patch SSE (P2 Stage 4 closure)
    test_chatgpt_web_f_sse_minimal()
    test_chatgpt_web_f_sse_bare_v_continuation()
    test_chatgpt_web_f_sse_multiple_messages()
    test_chatgpt_web_f_sse_extracts_model_slug()
    test_chatgpt_web_f_sse_returns_none_when_no_deltas()
    test_chatgpt_web_f_sse_returns_none_for_empty_or_garbage()
    test_chatgpt_web_f_sse_multimodal_parts()
    test_chatgpt_web_f_sse_real_fixture()
    test_chatgpt_web_f_sse_live_fixture_simple_text()
    test_openai_normalizer_session_key_from_response_conversation_id()
    test_openai_normalizer_handles_f_conversation_path()
    print("\n=== ALL TESTS PASSED ===")
