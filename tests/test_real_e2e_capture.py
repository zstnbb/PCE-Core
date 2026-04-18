# SPDX-License-Identifier: Apache-2.0
"""Real-server end-to-end integration test for the capture pipeline.

Requires a running PCE Core server at http://127.0.0.1:9800.
Tests the full chain: HTTP ingest → DB insert → normalization → session + messages.

Covers:
  1. DOM-extracted conversation captures (browser extension path)
  2. Network-intercepted captures with JSON responses
  3. Network-intercepted captures with SSE streaming responses
  4. Multiple providers: DeepSeek, OpenAI, Anthropic/Claude, unknown/new
  5. Edge cases: malformed data, empty bodies, unknown provider fallback

Usage:
    # First start the server:
    #   python -m pce_core.server
    # Then run:
    #   python tests/test_real_e2e_capture.py
"""

import json
import sys
import time
import traceback
import requests

BASE = "http://127.0.0.1:9800"
INGEST = f"{BASE}/api/v1/captures"
SESSIONS = f"{BASE}/api/v1/sessions"

results = []  # (test_name, passed: bool, detail: str)


def _post_capture(payload: dict) -> dict:
    """POST to ingest API and return response JSON."""
    resp = requests.post(INGEST, json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()


def _get_sessions(provider: str = None, last: int = 50) -> list:
    params = {"last": last}
    if provider:
        params["provider"] = provider
    resp = requests.get(SESSIONS, params=params, timeout=5)
    resp.raise_for_status()
    return resp.json()


def _get_messages(session_id: str) -> list:
    resp = requests.get(f"{SESSIONS}/{session_id}/messages", timeout=5)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json()


def _record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# ===========================================================================
# Test 1: DOM-extracted conversation capture (DeepSeek)
# ===========================================================================
def test_dom_conversation_deepseek():
    print("\n── Test 1: DOM conversation capture (DeepSeek) ──")
    unique = f"test-dom-ds-{int(time.time()*1000)}"
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext",
        "direction": "conversation",
        "provider": "deepseek",
        "host": "chat.deepseek.com",
        "path": "/chat/test123",
        "method": "GET",
        "model_name": "DeepSeek-V3",
        "headers_json": "{}",
        "body_json": json.dumps({
            "messages": [
                {"role": "user", "content": f"What is 1+1? [{unique}]"},
                {"role": "assistant", "content": "The answer is 2."},
            ],
            "conversation_id": unique,
            "title": "Math Question",
        }),
        "body_format": "json",
        "session_hint": "/chat/test123",
        "meta": {"capture_method": "dom_extraction"},
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None, f"id={result.get('id', '?')[:8]}")

    # Check session was created
    time.sleep(0.3)
    sessions = _get_sessions(provider="deepseek")
    matching = [s for s in sessions if s.get("session_key") == unique]
    _record("session created", len(matching) == 1,
            f"found {len(matching)} session(s) with key={unique[:16]}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) == 2, f"expected 2, got {len(msgs)}")
        roles = [m["role"] for m in msgs]
        _record("roles correct", roles == ["user", "assistant"], f"got {roles}")
        user_msg = next((m for m in msgs if m["role"] == "user"), None)
        _record("user content", user_msg and unique in user_msg.get("content_text", ""),
                f"content contains unique marker")
        asst_msg = next((m for m in msgs if m["role"] == "assistant"), None)
        _record("assistant content", asst_msg and "2" in asst_msg.get("content_text", ""),
                f"content: {asst_msg.get('content_text', '')[:50] if asst_msg else 'N/A'}")


# ===========================================================================
# Test 2: DOM-extracted conversation capture (ChatGPT)
# ===========================================================================
def test_dom_conversation_chatgpt():
    print("\n── Test 2: DOM conversation capture (ChatGPT) ──")
    unique = f"test-dom-gpt-{int(time.time()*1000)}"
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext",
        "direction": "conversation",
        "provider": "openai",
        "host": "chatgpt.com",
        "path": "/c/abc123",
        "method": "GET",
        "model_name": "gpt-4o",
        "headers_json": "{}",
        "body_json": json.dumps({
            "messages": [
                {"role": "user", "content": f"Tell me a joke [{unique}]"},
                {"role": "assistant", "content": "Why did the chicken cross the road? To get to the other side!"},
            ],
            "conversation_id": unique,
        }),
        "body_format": "json",
        "session_hint": "/c/abc123",
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions(provider="openai")
    matching = [s for s in sessions if s.get("session_key") == unique]
    _record("session created", len(matching) == 1, f"found {len(matching)}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) == 2, f"got {len(msgs)}")
        _record("assistant has joke", any("chicken" in m.get("content_text", "") for m in msgs))


# ===========================================================================
# Test 3: Network intercept – JSON response (OpenAI API format)
# ===========================================================================
def test_network_intercept_json():
    print("\n── Test 3: Network intercept – JSON response (OpenAI format) ──")
    unique = f"test-net-json-{int(time.time()*1000)}"
    req_body = json.dumps({
        "model": "gpt-4",
        "messages": [{"role": "user", "content": f"Hello from network test [{unique}]"}],
    })
    resp_body = json.dumps({
        "model": "gpt-4",
        "choices": [{"message": {"role": "assistant", "content": "Hi! I'm responding via network intercept."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
    })
    wrapper = json.dumps({
        "request_body": req_body,
        "response_body": resp_body,
        "url": "https://api.openai.com/v1/chat/completions",
        "is_streaming": False,
    })
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext-network",
        "direction": "network_intercept",
        "provider": "openai",
        "host": "api.openai.com",
        "path": "/v1/chat/completions",
        "method": "POST",
        "model_name": "gpt-4",
        "headers_json": "{}",
        "body_json": wrapper,
        "body_format": "json",
        "session_hint": unique,
        "meta": {"capture_method": "network_intercept"},
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions(provider="openai")
    # Find by title hint (first user message)
    matching = [s for s in sessions if s.get("title_hint") and unique in s["title_hint"]]
    _record("session created", len(matching) >= 1, f"found {len(matching)} matching session(s)")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) == 2, f"expected 2, got {len(msgs)}")
        asst = [m for m in msgs if m["role"] == "assistant"]
        _record("assistant response", len(asst) == 1 and "network intercept" in asst[0].get("content_text", ""))


# ===========================================================================
# Test 4: Network intercept – SSE streaming response (DeepSeek)
# ===========================================================================
def test_network_intercept_sse_deepseek():
    print("\n── Test 4: Network intercept – SSE streaming (DeepSeek) ──")
    unique = f"test-sse-ds-{int(time.time()*1000)}"
    req_body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": f"What is Python? [{unique}]"}],
        "stream": True,
    })
    sse_resp = "\n".join([
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{"content":"Python"}}]}',
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{"content":" is"}}]}',
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{"content":" a"}}]}',
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{"content":" programming"}}]}',
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{"content":" language."}}]}',
        'data: {"id":"chatcmpl-1","model":"deepseek-chat","choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}',
        'data: [DONE]',
    ])
    wrapper = json.dumps({
        "request_body": req_body,
        "response_body": sse_resp,
        "url": "https://chat.deepseek.com/api/v0/chat/completions",
        "is_streaming": True,
    })
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext-network",
        "direction": "network_intercept",
        "provider": "deepseek",
        "host": "chat.deepseek.com",
        "path": "/api/v0/chat/completions",
        "method": "POST",
        "model_name": "deepseek-chat",
        "headers_json": "{}",
        "body_json": wrapper,
        "body_format": "json",
        "session_hint": unique,
        "meta": {"capture_method": "network_intercept"},
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions()
    matching = [s for s in sessions if s.get("title_hint") and unique in s["title_hint"]]
    _record("session created", len(matching) >= 1, f"found {len(matching)}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) == 2, f"expected 2, got {len(msgs)}")
        asst = [m for m in msgs if m["role"] == "assistant"]
        _record("SSE assembled correctly",
                len(asst) == 1 and "Python is a programming language." in asst[0].get("content_text", ""),
                f"got: {asst[0].get('content_text', '')[:80] if asst else 'N/A'}")
        _record("model name propagated",
                asst[0].get("model_name") == "deepseek-chat" if asst else False)


# ===========================================================================
# Test 5: Network intercept – SSE streaming (Anthropic/Claude format)
# ===========================================================================
def test_network_intercept_sse_claude():
    print("\n── Test 5: Network intercept – SSE streaming (Claude) ──")
    unique = f"test-sse-claude-{int(time.time()*1000)}"
    req_body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": f"Hello Claude [{unique}]"}],
        "stream": True,
    })
    sse_resp = "\n".join([
        'event: message_start',
        'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-sonnet-4-20250514","role":"assistant","usage":{"input_tokens":10}}}',
        '',
        'event: content_block_start',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        '',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
        '',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"! I\'m Claude"}}',
        '',
        'event: content_block_delta',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":", nice to meet you."}}',
        '',
        'event: content_block_stop',
        'data: {"type":"content_block_stop","index":0}',
        '',
        'event: message_delta',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":12}}',
        '',
        'event: message_stop',
        'data: {"type":"message_stop"}',
    ])
    wrapper = json.dumps({
        "request_body": req_body,
        "response_body": sse_resp,
        "url": "https://api.anthropic.com/v1/messages",
        "is_streaming": True,
    })
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext-network",
        "direction": "network_intercept",
        "provider": "anthropic",
        "host": "api.anthropic.com",
        "path": "/v1/messages",
        "method": "POST",
        "model_name": "claude-sonnet-4-20250514",
        "headers_json": "{}",
        "body_json": wrapper,
        "body_format": "json",
        "session_hint": unique,
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions(provider="anthropic")
    matching = [s for s in sessions if s.get("title_hint") and unique in s["title_hint"]]
    _record("session created", len(matching) >= 1, f"found {len(matching)}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) >= 2, f"expected >=2, got {len(msgs)}")
        asst = [m for m in msgs if m["role"] == "assistant"]
        _record("Claude SSE assembled",
                len(asst) >= 1 and "I'm Claude" in asst[0].get("content_text", ""),
                f"got: {asst[0].get('content_text', '')[:80] if asst else 'N/A'}")
        _record("model name",
                asst[0].get("model_name") == "claude-sonnet-4-20250514" if asst else False)


# ===========================================================================
# Test 6: Network intercept – Unknown provider (generic fallback)
# ===========================================================================
def test_network_intercept_unknown_provider():
    print("\n── Test 6: Network intercept – Unknown provider (fallback) ──")
    unique = f"test-unknown-{int(time.time()*1000)}"
    req_body = json.dumps({
        "model": "brand-new-model-7B",
        "messages": [{"role": "user", "content": f"Testing new AI [{unique}]"}],
    })
    resp_body = json.dumps({
        "model": "brand-new-model-7B",
        "choices": [{"message": {"role": "assistant", "content": "I'm a brand new AI model!"}}],
    })
    wrapper = json.dumps({
        "request_body": req_body,
        "response_body": resp_body,
        "url": "https://api.brand-new-ai.com/v1/chat",
        "is_streaming": False,
    })
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext-network",
        "direction": "network_intercept",
        "provider": "brand_new_ai",
        "host": "api.brand-new-ai.com",
        "path": "/v1/chat",
        "method": "POST",
        "headers_json": "{}",
        "body_json": wrapper,
        "body_format": "json",
        "session_hint": unique,
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions()
    matching = [s for s in sessions if s.get("title_hint") and unique in s["title_hint"]]
    _record("session created (generic fallback)", len(matching) >= 1, f"found {len(matching)}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) == 2, f"expected 2, got {len(msgs)}")
        asst = [m for m in msgs if m["role"] == "assistant"]
        _record("fallback content correct",
                len(asst) == 1 and "brand new AI" in asst[0].get("content_text", ""),
                f"got: {asst[0].get('content_text', '')[:80] if asst else 'N/A'}")


# ===========================================================================
# Test 7: Edge case – malformed body (should not crash server)
# ===========================================================================
def test_edge_malformed_body():
    print("\n── Test 7: Edge case – malformed body ──")
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext",
        "direction": "conversation",
        "provider": "unknown",
        "host": "unknown.com",
        "headers_json": "{}",
        "body_json": "this is not valid json {{{",
        "body_format": "json",
    }
    result = _post_capture(payload)
    _record("server did not crash", result.get("id") is not None,
            "capture stored even if normalization fails")

    # Verify server is still healthy
    resp = requests.get(f"{BASE}/api/v1/health", timeout=3)
    _record("server still healthy", resp.status_code == 200)


# ===========================================================================
# Test 8: Edge case – empty messages array
# ===========================================================================
def test_edge_empty_messages():
    print("\n── Test 8: Edge case – empty messages array ──")
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext",
        "direction": "conversation",
        "provider": "deepseek",
        "host": "chat.deepseek.com",
        "headers_json": "{}",
        "body_json": json.dumps({"messages": []}),
        "body_format": "json",
    }
    result = _post_capture(payload)
    _record("server accepted empty messages", result.get("id") is not None)

    # Should NOT create a session (no content)
    time.sleep(0.2)
    resp = requests.get(f"{BASE}/api/v1/health", timeout=3)
    _record("server still healthy", resp.status_code == 200)


# ===========================================================================
# Test 9: Network intercept – SSE with reasoning/thinking (DeepSeek-R1)
# ===========================================================================
def test_network_intercept_sse_with_reasoning():
    print("\n── Test 9: SSE with reasoning content (DeepSeek-R1) ──")
    unique = f"test-reasoning-{int(time.time()*1000)}"
    req_body = json.dumps({
        "model": "deepseek-reasoner",
        "messages": [{"role": "user", "content": f"Solve: 15 * 37 [{unique}]"}],
        "stream": True,
    })
    sse_resp = "\n".join([
        'data: {"model":"deepseek-reasoner","choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"model":"deepseek-reasoner","choices":[{"delta":{"reasoning_content":"Let me calculate 15 * 37..."}}]}',
        'data: {"model":"deepseek-reasoner","choices":[{"delta":{"reasoning_content":" 15 * 37 = 15 * 30 + 15 * 7 = 450 + 105 = 555"}}]}',
        'data: {"model":"deepseek-reasoner","choices":[{"delta":{"content":"The answer is 555."}}]}',
        'data: [DONE]',
    ])
    wrapper = json.dumps({
        "request_body": req_body,
        "response_body": sse_resp,
        "url": "https://chat.deepseek.com/api/v0/chat/completions",
        "is_streaming": True,
    })
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext-network",
        "direction": "network_intercept",
        "provider": "deepseek",
        "host": "chat.deepseek.com",
        "path": "/api/v0/chat/completions",
        "method": "POST",
        "model_name": "deepseek-reasoner",
        "headers_json": "{}",
        "body_json": wrapper,
        "body_format": "json",
        "session_hint": unique,
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions()
    matching = [s for s in sessions if s.get("title_hint") and unique in s["title_hint"]]
    _record("session created", len(matching) >= 1, f"found {len(matching)}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages exist", len(msgs) >= 2, f"got {len(msgs)}")
        asst = [m for m in msgs if m["role"] == "assistant"]
        _record("answer content", len(asst) >= 1 and "555" in asst[0].get("content_text", ""),
                f"got: {asst[0].get('content_text', '')[:80] if asst else 'N/A'}")


# ===========================================================================
# Test 10: DOM conversation – completely unknown platform (catch-all)
# ===========================================================================
def test_dom_unknown_platform():
    print("\n── Test 10: DOM conversation – unknown platform (catch-all) ──")
    unique = f"test-unknown-dom-{int(time.time()*1000)}"
    payload = {
        "source_type": "browser_extension",
        "source_name": "chrome-ext",
        "direction": "conversation",
        "provider": "totally_new_ai",
        "host": "chat.totally-new-ai.com",
        "path": "/conversation/xyz",
        "method": "GET",
        "headers_json": "{}",
        "body_json": json.dumps({
            "messages": [
                {"role": "user", "content": f"Hello new AI [{unique}]"},
                {"role": "assistant", "content": "Hi! I'm a new AI platform you've never seen before."},
            ],
            "conversation_id": unique,
        }),
        "body_format": "json",
    }
    result = _post_capture(payload)
    _record("ingest accepted", result.get("id") is not None)

    time.sleep(0.3)
    sessions = _get_sessions()
    matching = [s for s in sessions if s.get("session_key") == unique]
    _record("session created (catch-all)", len(matching) >= 1, f"found {len(matching)}")

    if matching:
        msgs = _get_messages(matching[0]["id"])
        _record("messages count", len(msgs) == 2, f"got {len(msgs)}")


# ===========================================================================
# Main
# ===========================================================================
def main():
    # Pre-flight: check server is up
    print("=" * 60)
    print("PCE Real-Environment Integration Test")
    print("=" * 60)

    try:
        resp = requests.get(f"{BASE}/api/v1/health", timeout=3)
        health = resp.json()
        print(f"Server: v{health['version']}  DB: {health['db_path']}")
        print(f"Existing captures: {health['total_captures']}")
    except Exception as e:
        print(f"\nFATAL: Cannot reach PCE server at {BASE}")
        print(f"  Error: {e}")
        print(f"  Start it with: python -m pce_core.server")
        sys.exit(1)

    # Record initial stats
    initial_stats = requests.get(f"{BASE}/api/v1/stats", timeout=3).json()
    initial_captures = initial_stats["total_captures"]

    # Run all tests
    tests = [
        test_dom_conversation_deepseek,
        test_dom_conversation_chatgpt,
        test_network_intercept_json,
        test_network_intercept_sse_deepseek,
        test_network_intercept_sse_claude,
        test_network_intercept_unknown_provider,
        test_edge_malformed_body,
        test_edge_empty_messages,
        test_network_intercept_sse_with_reasoning,
        test_dom_unknown_platform,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception:
            _record(f"{test_fn.__name__} EXCEPTION", False, traceback.format_exc().split("\n")[-2])

    # Final stats
    final_stats = requests.get(f"{BASE}/api/v1/stats", timeout=3).json()
    new_captures = final_stats["total_captures"] - initial_captures

    # Summary
    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"New captures ingested: {new_captures}")
    print("=" * 60)

    if failed > 0:
        print("\nFailed checks:")
        for name, p, detail in results:
            if not p:
                print(f"  ✗ {name}: {detail}")
        sys.exit(1)
    else:
        print("\n✓ ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
