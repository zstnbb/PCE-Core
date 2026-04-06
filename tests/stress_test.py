"""PCE Full-System Stress & QA Test.

Simulates a real developer using PCE for an entire workday:
- Chatting with ChatGPT (OpenAI) across multi-turn sessions
- Asking Claude (Anthropic) complex questions
- Running Ollama locally through the Hook
- Using browser extension to capture web conversations
- Hammering MCP tools for stats/queries
- Meanwhile, the dashboard is being polled continuously

Runs as a single script — spins up everything, acts as the user, then
tears down and reports every anomaly found.

Usage:
    python tests/stress_test.py
"""

import concurrent.futures
import json
import os
import random
import shutil
import socket
import sqlite3
import string
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import httpx
import uvicorn

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TMP_DIR = tempfile.mkdtemp(prefix="pce_stress_")
os.environ["PCE_DATA_DIR"] = TMP_DIR
for _pvar in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_pvar, None)
os.environ["NO_PROXY"] = "*"

# Ports
PORT_OPENAI   = 19101
PORT_ANTHROPIC = 19102
PORT_OLLAMA    = 19103
PORT_PCE       = 19900
PORT_HOOK      = 19535

API = f"http://127.0.0.1:{PORT_PCE}/api/v1"
HOOK = f"http://127.0.0.1:{PORT_HOOK}"

# Counters
_pass = 0
_fail = 0
_warn = 0
_issues: list[str] = []


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def ok(label: str):
    global _pass
    _pass += 1
    print(f"  ✓ {label}")

def fail(label: str, detail: str = ""):
    global _fail
    _fail += 1
    msg = f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else "")
    print(msg)
    _issues.append(msg)

def warn(label: str, detail: str = ""):
    global _warn
    _warn += 1
    msg = f"  ⚠ WARN: {label}" + (f" — {detail}" if detail else "")
    print(msg)
    _issues.append(msg)

def http() -> httpx.Client:
    return httpx.Client(timeout=15, trust_env=False)

def wait_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket(); s.settimeout(1)
            s.connect(("127.0.0.1", port)); s.close(); return True
        except OSError:
            time.sleep(0.15)
    return False

def post_pair(c, provider, host, path, model, user_msg, assistant_msg,
              pair_id=None, source_type="proxy", session_key=None):
    """Simulate a full request→response pair via Ingest API. Returns pair_id."""
    import uuid
    pid = pair_id or uuid.uuid4().hex[:16]

    if provider == "anthropic":
        req_body = json.dumps({
            "model": model, "max_tokens": 1024,
            "messages": [{"role": "user", "content": user_msg}],
        }, ensure_ascii=False)
        resp_body = json.dumps({
            "id": f"msg_{uuid.uuid4().hex[:16]}", "type": "message",
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": assistant_msg}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": len(user_msg)//4, "output_tokens": len(assistant_msg)//4},
        }, ensure_ascii=False)
    else:
        # OpenAI-compatible
        req_body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": user_msg}],
            **({"conversation_id": session_key} if session_key else {}),
        }, ensure_ascii=False)
        resp_body = json.dumps({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": assistant_msg}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(user_msg)//4, "completion_tokens": len(assistant_msg)//4, "total_tokens": (len(user_msg)+len(assistant_msg))//4},
        }, ensure_ascii=False)

    r1 = c.post(f"{API}/captures", json={
        "source_type": source_type, "direction": "request", "pair_id": pid,
        "provider": provider, "host": host, "path": path,
        "method": "POST", "model_name": model,
        "body_json": req_body, "body_format": "json",
        "headers_json": '{"content-type":"application/json","authorization":"REDACTED"}',
    })
    assert r1.status_code == 201, f"Request ingest failed: {r1.status_code} {r1.text}"

    r2 = c.post(f"{API}/captures", json={
        "source_type": source_type, "direction": "response", "pair_id": pid,
        "provider": provider, "host": host, "path": path,
        "method": "POST", "model_name": model,
        "status_code": 200, "latency_ms": random.uniform(50, 500),
        "body_json": resp_body, "body_format": "json",
    })
    assert r2.status_code == 201, f"Response ingest failed: {r2.status_code} {r2.text}"
    return pid


# ═══════════════════════════════════════════════════════════════════════════
# Service Setup
# ═══════════════════════════════════════════════════════════════════════════

def boot():
    print("\n[BOOT] Starting all services...")

    from tests.mock_ai_server import create_openai_app, create_anthropic_app, create_ollama_app

    for app_factory, port, name in [
        (create_openai_app, PORT_OPENAI, "mock-openai"),
        (create_anthropic_app, PORT_ANTHROPIC, "mock-anthropic"),
        (create_ollama_app, PORT_OLLAMA, "mock-ollama"),
    ]:
        _start(app_factory(), port, name)

    os.environ["PCE_INGEST_PORT"] = str(PORT_PCE)
    import pce_core.config as cfg
    cfg.INGEST_PORT = PORT_PCE

    from pce_core.server import app as pce_app
    _start(pce_app, PORT_PCE, "pce-core")
    time.sleep(0.3)

    from pce_core.local_hook.hook import create_hook_app
    _start(create_hook_app(target_host="127.0.0.1", target_port=PORT_OLLAMA), PORT_HOOK, "local-hook")

    print("[BOOT] All services ready.\n")

def _start(app, port, name):
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True, name=name).start()
    assert wait_port(port), f"{name} failed to start on {port}"
    print(f"  {name:20s} → :{port}")


# ═══════════════════════════════════════════════════════════════════════════
# S1: Multi-Turn Session Simulation
# ═══════════════════════════════════════════════════════════════════════════

def s1_multi_turn_sessions():
    """Simulate a developer having multi-turn conversations with GPT-4 and Claude."""
    print("━━ S1: Multi-Turn Session Simulation ━━")

    with http() as c:
        # === GPT-4 conversation: 5 turns with same session_key ===
        sk_gpt = "gpt-session-stress-001"
        gpt_turns = [
            ("Help me write a Python web scraper", "Sure! Here's a basic web scraper using requests and BeautifulSoup..."),
            ("Add error handling and retries", "Here's the improved version with exponential backoff retry logic..."),
            ("Now add async support with aiohttp", "Here's the async version using aiohttp and asyncio.gather..."),
            ("Can you add rate limiting?", "I'll add a semaphore-based rate limiter and respect robots.txt..."),
            ("Summarize everything into a README", "# Web Scraper\n\nA production-ready async web scraper with..."),
        ]
        for i, (user, assistant) in enumerate(gpt_turns):
            post_pair(c, "openai", "api.openai.com", "/v1/chat/completions",
                      "gpt-4", user, assistant, session_key=sk_gpt)

        # === Claude conversation: 4 turns ===
        sk_claude = "claude-session-stress-001"
        claude_turns = [
            ("Explain quantum computing to a 10 year old", "Imagine you have a magic coin that can be heads AND tails at the same time..."),
            ("Now explain qubits more precisely", "A qubit is the quantum analog of a classical bit. Unlike a classical bit..."),
            ("What about quantum entanglement?", "Quantum entanglement is when two qubits become connected in a special way..."),
            ("How close are we to useful quantum computers?", "As of 2024, we're in the NISQ era — Noisy Intermediate-Scale Quantum..."),
        ]
        for user, assistant in claude_turns:
            post_pair(c, "anthropic", "api.anthropic.com", "/v1/messages",
                      "claude-3-5-sonnet-20241022", user, assistant)

        # Verify session grouping
        time.sleep(0.3)
        sessions = c.get(f"{API}/sessions?last=100").json()

        # OpenAI turns with same session_key should group into ONE session
        openai_sessions = [s for s in sessions if s["provider"] == "openai"]
        # Each turn creates session_key=None because the normalizer reads from the parsed body
        # and we put conversation_id in the request body. Let's check if they grouped.
        # Actually, the normalizer reads conversation_id from req_data, so sessions with
        # the same session_key should merge. Let's verify.

        # Count total messages across all openai sessions
        openai_msg_total = 0
        for s in openai_sessions:
            msgs = c.get(f"{API}/sessions/{s['id']}/messages").json()
            openai_msg_total += len(msgs)

        # Each turn produces: 1 user msg + 1 assistant msg = 2, times 5 turns = 10
        if openai_msg_total >= 10:
            ok(f"OpenAI multi-turn: {openai_msg_total} messages across {len(openai_sessions)} session(s)")
        else:
            fail(f"OpenAI multi-turn: expected >= 10 msgs, got {openai_msg_total}")

        # Check that GPT session_key grouping worked (should be 1 session for 5 turns)
        gpt_keyed = [s for s in openai_sessions if s.get("session_key") == sk_gpt]
        if len(gpt_keyed) == 1:
            ok(f"GPT-4 session_key grouping: 5 turns → 1 session ({gpt_keyed[0]['message_count']} msgs)")
        elif len(gpt_keyed) > 1:
            warn(f"GPT-4 session_key: expected 1 session, got {len(gpt_keyed)}", "multi-turn grouping may need review")
        else:
            fail("GPT-4 session_key: no session found with expected key")

        # Anthropic: no session_key provided, so each turn creates a new session
        anthropic_sessions = [s for s in sessions if s["provider"] == "anthropic"]
        anthropic_msg_total = sum(s["message_count"] for s in anthropic_sessions)
        if anthropic_msg_total >= 8:  # 4 turns × 2 msgs each
            ok(f"Anthropic multi-turn: {anthropic_msg_total} messages across {len(anthropic_sessions)} session(s)")
        else:
            fail(f"Anthropic multi-turn: expected >= 8 msgs, got {anthropic_msg_total}")


# ═══════════════════════════════════════════════════════════════════════════
# S2: Local Hook Stress (Ollama simulation)
# ═══════════════════════════════════════════════════════════════════════════

def s2_hook_stress():
    """Send 30 concurrent requests through the Local Hook → Mock Ollama."""
    print("━━ S2: Local Hook Concurrency Stress (30 requests) ━━")

    n = 30
    results = {"ok": 0, "fail": 0}

    def _one(i):
        with http() as c:
            r = c.post(f"{HOOK}/api/chat", json={
                "model": "llama3",
                "messages": [{"role": "user", "content": f"Stress test message #{i}: {'x' * random.randint(10, 200)}"}],
            })
            return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_one, i) for i in range(n)]
        for f in concurrent.futures.as_completed(futs):
            try:
                if f.result() == 200:
                    results["ok"] += 1
                else:
                    results["fail"] += 1
            except Exception:
                results["fail"] += 1

    if results["ok"] == n:
        ok(f"Hook concurrency: all {n} requests succeeded")
    elif results["fail"] <= 2:
        warn(f"Hook concurrency: {results['ok']}/{n} ok, {results['fail']} failed")
    else:
        fail(f"Hook concurrency: {results['ok']}/{n} ok, {results['fail']} failed")

    # Verify all captures landed
    time.sleep(1)
    with http() as c:
        caps = c.get(f"{API}/captures?last=500").json()
        hook_caps = [cap for cap in caps
                     if cap.get("host", "").startswith("127.0.0.1:")
                     and "api/chat" in (cap.get("path") or "")]
        # Each hook request creates 2 captures (req + resp)
        expected = n * 2
        if len(hook_caps) >= expected:
            ok(f"Hook captures: {len(hook_caps)} in DB (expected >= {expected})")
        else:
            fail(f"Hook captures: {len(hook_caps)} in DB (expected >= {expected})")


# ═══════════════════════════════════════════════════════════════════════════
# S3: Browser Extension Simulation
# ═══════════════════════════════════════════════════════════════════════════

def s3_browser_extension():
    """Simulate browser extension capturing full page conversations."""
    print("━━ S3: Browser Extension Conversations ━━")

    with http() as c:
        # ChatGPT web conversation (long, multi-turn)
        chatgpt_conv = {
            "messages": [
                {"role": "user", "content": "What are the best practices for React performance optimization?"},
                {"role": "assistant", "content": "Here are key React performance optimization strategies:\n1. **Use React.memo** for expensive components\n2. **useMemo/useCallback** for expensive computations\n3. **Code splitting** with React.lazy\n4. **Virtual scrolling** for long lists\n5. **Avoid unnecessary re-renders** by structuring state properly"},
                {"role": "user", "content": "Can you elaborate on virtual scrolling?"},
                {"role": "assistant", "content": "Virtual scrolling (or windowing) only renders items visible in the viewport. Libraries like react-window or react-virtualized implement this. For a list of 10,000 items, only ~20 visible items are rendered at once."},
            ],
            "model": "gpt-4o",
            "url": "https://chatgpt.com/c/abc123",
        }
        r = c.post(f"{API}/captures", json={
            "source_type": "browser_extension", "direction": "conversation",
            "provider": "openai", "host": "chatgpt.com",
            "body_json": json.dumps(chatgpt_conv), "body_format": "json",
            "meta": {"page_url": "https://chatgpt.com/c/abc123", "tab_id": 42},
        })
        assert r.status_code == 201
        ok("ChatGPT web conversation captured")

        # Claude web conversation
        claude_conv = {
            "messages": [
                {"role": "user", "content": "Write a haiku about programming"},
                {"role": "assistant", "content": "Semicolons fall\nLike autumn leaves on the screen\nCode compiles at last"},
            ],
            "model": "claude-3-5-sonnet",
        }
        r = c.post(f"{API}/captures", json={
            "source_type": "browser_extension", "direction": "conversation",
            "provider": "anthropic", "host": "claude.ai",
            "body_json": json.dumps(claude_conv), "body_format": "json",
            "meta": {"page_url": "https://claude.ai/chat/xyz789"},
        })
        assert r.status_code == 201
        ok("Claude web conversation captured")

        # Verify source_type filter
        ext_caps = c.get(f"{API}/captures?source_type=browser_extension&last=100").json()
        if len(ext_caps) >= 2:
            ok(f"Browser extension filter: {len(ext_caps)} captures")
        else:
            fail(f"Browser extension filter: expected >= 2, got {len(ext_caps)}")


# ═══════════════════════════════════════════════════════════════════════════
# S4: Unicode / i18n / Emoji Stress
# ═══════════════════════════════════════════════════════════════════════════

def s4_unicode_stress():
    """Test with CJK, emoji, RTL, and mixed-script content."""
    print("━━ S4: Unicode / i18n / Emoji Content ━━")

    test_cases = [
        ("Chinese", "用中文解释量子计算", "量子计算是利用量子力学原理来处理信息的一种计算方式。与经典计算机使用比特（0或1）不同，量子计算机使用量子比特。"),
        ("Japanese", "Pythonの非同期処理について説明してください", "Pythonの非同期処理は、asyncio モジュールを使用して実装できます。async/await 構文を使うことで、I/O バウンドな処理を効率的に実行できます。"),
        ("Korean", "머신러닝의 기초를 설명해주세요", "머신러닝은 데이터로부터 학습하는 인공지능의 한 분야입니다. 주요 유형으로는 지도학습, 비지도학습, 강화학습이 있습니다."),
        ("Emoji Heavy", "Tell me about 🐍 Python 🚀 performance 💪", "Python 🐍 can be fast! Use: 1. 🔄 asyncio for I/O, 2. 🧮 NumPy for math, 3. 🔥 PyPy for CPU-bound, 4. 🦀 Rust extensions via PyO3"),
        ("RTL Arabic", "اشرح لي الذكاء الاصطناعي", "الذكاء الاصطناعي هو فرع من علوم الحاسوب يهدف إلى إنشاء أنظمة قادرة على محاكاة الذكاء البشري"),
        ("Mixed Script", "Explain αβγ, Кириллица, and 漢字 in one response",
         "Greek letters αβγδε are used in math/science. Кириллица (Cyrillic) is used across Slavic languages. 漢字 (Kanji/Hanzi) are logographic characters used in Chinese and Japanese."),
    ]

    with http() as c:
        for label, user_msg, assistant_msg in test_cases:
            try:
                post_pair(c, "openai", "api.openai.com", "/v1/chat/completions",
                          "gpt-4", user_msg, assistant_msg)
                # Verify the content survived roundtrip
                caps = c.get(f"{API}/captures?last=2").json()
                found = False
                for cap in caps:
                    body = cap.get("body_text_or_json", "")
                    if user_msg[:20] in body or assistant_msg[:20] in body:
                        found = True
                        break
                if found:
                    ok(f"Unicode ({label}): roundtrip preserved")
                else:
                    fail(f"Unicode ({label}): content lost in roundtrip")
            except Exception as e:
                fail(f"Unicode ({label}): exception", str(e))


# ═══════════════════════════════════════════════════════════════════════════
# S5: Large Payload Stress
# ═══════════════════════════════════════════════════════════════════════════

def s5_large_payloads():
    """Test with progressively larger payloads: 10KB, 100KB, 500KB, 1MB."""
    print("━━ S5: Large Payload Stress ━━")

    sizes = [
        ("10KB", 10_000),
        ("100KB", 100_000),
        ("500KB", 500_000),
        ("1MB", 1_000_000),
    ]

    with http() as c:
        for label, size in sizes:
            try:
                content = "".join(random.choices(string.ascii_letters + " \n", k=size))
                t0 = time.time()
                post_pair(c, "openai", "api.openai.com", "/v1/chat/completions",
                          "gpt-4", content, f"Acknowledged {label} input.")
                elapsed = (time.time() - t0) * 1000
                if elapsed < 5000:
                    ok(f"Large payload ({label}): ingested in {elapsed:.0f}ms")
                else:
                    warn(f"Large payload ({label}): slow at {elapsed:.0f}ms")
            except Exception as e:
                fail(f"Large payload ({label})", str(e))


# ═══════════════════════════════════════════════════════════════════════════
# S6: Concurrent Read+Write Storm
# ═══════════════════════════════════════════════════════════════════════════

def s6_concurrent_rw():
    """100 concurrent writes + 50 concurrent reads simultaneously."""
    print("━━ S6: Concurrent Read+Write Storm (100W + 50R) ━━")

    write_ok = {"n": 0}
    write_fail = {"n": 0}
    read_ok = {"n": 0}
    read_fail = {"n": 0}

    def _write(i):
        try:
            with http() as c:
                post_pair(c, "openai", "api.openai.com", "/v1/chat/completions",
                          "gpt-4", f"Storm write #{i}", f"Storm reply #{i}")
            write_ok["n"] += 1
        except Exception:
            write_fail["n"] += 1

    def _read(_):
        try:
            with http() as c:
                r = c.get(f"{API}/captures?last=50")
                assert r.status_code == 200
                r = c.get(f"{API}/stats")
                assert r.status_code == 200
                r = c.get(f"{API}/sessions?last=50")
                assert r.status_code == 200
            read_ok["n"] += 1
        except Exception:
            read_fail["n"] += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        futs = []
        for i in range(100):
            futs.append(pool.submit(_write, i))
        for i in range(50):
            futs.append(pool.submit(_read, i))
        concurrent.futures.wait(futs)

    total_w = write_ok["n"] + write_fail["n"]
    total_r = read_ok["n"] + read_fail["n"]

    if write_fail["n"] == 0:
        ok(f"Storm writes: {write_ok['n']}/{total_w} succeeded")
    else:
        fail(f"Storm writes: {write_fail['n']}/{total_w} failed")

    if read_fail["n"] == 0:
        ok(f"Storm reads: {read_ok['n']}/{total_r} succeeded")
    else:
        fail(f"Storm reads: {read_fail['n']}/{total_r} failed")


# ═══════════════════════════════════════════════════════════════════════════
# S7: MCP Tool Exercise
# ═══════════════════════════════════════════════════════════════════════════

def s7_mcp_tools():
    """Exercise all 6 MCP tools programmatically."""
    print("━━ S7: MCP Tool Exercise ━━")

    try:
        # Import MCP server tools directly
        os.environ["PCE_INGEST_PORT"] = str(PORT_PCE)
        from pce_mcp.server import (
            pce_capture, pce_query, pce_stats,
            pce_sessions, pce_session_messages, pce_capture_pair,
        )

        # pce_capture: conversation
        result = pce_capture(
            conversation_json=json.dumps({
                "messages": [
                    {"role": "user", "content": "MCP test question"},
                    {"role": "assistant", "content": "MCP test answer"},
                ],
                "model": "gpt-4",
            }),
            provider="openai",
        )
        if "error" not in result.lower():
            ok("MCP pce_capture (conversation): OK")
        else:
            fail("MCP pce_capture (conversation)", result)

        # pce_capture: request + response pair
        result = pce_capture(
            request_body=json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "MCP pair test"}]}),
            response_body=json.dumps({"choices": [{"message": {"role": "assistant", "content": "MCP pair reply"}}], "model": "gpt-4", "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}),
            provider="openai",
            host="api.openai.com",
            path="/v1/chat/completions",
        )
        if "error" not in result.lower():
            ok("MCP pce_capture (req/resp pair): OK")
        else:
            fail("MCP pce_capture (req/resp pair)", result)

        # pce_query
        result = pce_query(last=10)
        if "capture" in result.lower() or "id" in result.lower():
            ok("MCP pce_query: OK")
        else:
            fail("MCP pce_query", result[:200])

        # pce_stats
        result = pce_stats()
        if "total" in result.lower():
            ok("MCP pce_stats: OK")
        else:
            fail("MCP pce_stats", result[:200])

        # pce_sessions
        result = pce_sessions(last=10)
        if "session" in result.lower() or "id" in result.lower():
            ok("MCP pce_sessions: OK")
        else:
            fail("MCP pce_sessions", result[:200])

        # pce_session_messages: get a real session ID
        with http() as c:
            sessions = c.get(f"{API}/sessions?last=1").json()
            if sessions:
                sid = sessions[0]["id"]
                result = pce_session_messages(session_id=sid)
                if "message" in result.lower() or "user" in result.lower() or "assistant" in result.lower():
                    ok("MCP pce_session_messages: OK")
                else:
                    fail("MCP pce_session_messages", result[:200])

                # pce_capture_pair
                with http() as c2:
                    caps = c2.get(f"{API}/captures?last=1").json()
                    if caps:
                        pid = caps[0]["pair_id"]
                        result = pce_capture_pair(pair_id=pid)
                        if "pair" in result.lower() or "direction" in result.lower():
                            ok("MCP pce_capture_pair: OK")
                        else:
                            fail("MCP pce_capture_pair", result[:200])

    except Exception as e:
        fail("MCP tools import/execution", f"{e}\n{traceback.format_exc()}")


# ═══════════════════════════════════════════════════════════════════════════
# S8: Dashboard API Consistency Under Load
# ═══════════════════════════════════════════════════════════════════════════

def s8_dashboard_consistency():
    """After all the data buildup, verify dashboard APIs return consistent data."""
    print("━━ S8: Dashboard API Full Consistency Check ━━")

    time.sleep(0.5)  # Let all writes settle

    with http() as c:
        # Health
        health = c.get(f"{API}/health").json()
        assert health["status"] == "ok"
        total_from_health = health["total_captures"]
        ok(f"Health: {total_from_health} total captures")

        # Stats
        stats = c.get(f"{API}/stats").json()
        if stats["total_captures"] != total_from_health:
            fail("Stats vs Health: total mismatch",
                 f"stats={stats['total_captures']} vs health={total_from_health}")
        else:
            ok("Stats total matches Health total")

        # by_direction should sum to total
        dir_sum = sum(stats["by_direction"].values())
        if dir_sum != stats["total_captures"]:
            fail("Stats by_direction sum mismatch", f"sum={dir_sum} vs total={stats['total_captures']}")
        else:
            ok(f"Stats by_direction sums correctly ({dir_sum})")

        # by_provider should sum to total
        prov_sum = sum(stats["by_provider"].values())
        if prov_sum != stats["total_captures"]:
            fail("Stats by_provider sum mismatch", f"sum={prov_sum} vs total={stats['total_captures']}")
        else:
            ok(f"Stats by_provider sums correctly ({prov_sum})")

        # by_source should sum to total
        src_sum = sum(stats["by_source"].values())
        if src_sum != stats["total_captures"]:
            fail("Stats by_source sum mismatch", f"sum={src_sum} vs total={stats['total_captures']}")
        else:
            ok(f"Stats by_source sums correctly ({src_sum})")

        # Captures list with max limit
        all_caps = c.get(f"{API}/captures?last=500").json()
        if len(all_caps) != min(500, stats["total_captures"]):
            fail("Captures list count mismatch",
                 f"list={len(all_caps)} vs stats={stats['total_captures']}")
        else:
            ok(f"Captures list: {len(all_caps)} matches stats total")

        # Provider filters
        for prov, expected_count in stats["by_provider"].items():
            filtered = c.get(f"{API}/captures?provider={prov}&last=500").json()
            if len(filtered) != expected_count:
                fail(f"Provider filter '{prov}'",
                     f"got {len(filtered)}, expected {expected_count}")
            else:
                ok(f"Provider filter '{prov}': {len(filtered)} correct")

        # Sessions: verify message_count matches actual
        sessions = c.get(f"{API}/sessions?last=500").json()
        ok(f"Sessions: {len(sessions)} total")

        mismatch_count = 0
        for sess in sessions:
            try:
                msgs = c.get(f"{API}/sessions/{sess['id']}/messages").json()
                if sess["message_count"] != len(msgs):
                    mismatch_count += 1
                    warn(f"Session {sess['id'][:8]}: message_count={sess['message_count']} but actual={len(msgs)}")
            except Exception:
                pass

        if mismatch_count == 0:
            ok(f"All {len(sessions)} sessions: message_count matches actual messages")
        else:
            fail(f"Session message_count mismatches: {mismatch_count}/{len(sessions)}")

        # Dashboard HTML
        r = c.get(f"http://127.0.0.1:{PORT_PCE}/")
        if r.status_code == 200 and "PCE" in r.text:
            ok("Dashboard HTML serves correctly")
        else:
            fail("Dashboard HTML", f"status={r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════
# S9: Edge Cases & Fault Tolerance
# ═══════════════════════════════════════════════════════════════════════════

def s9_edge_cases():
    """Test edge cases that real users would hit."""
    print("━━ S9: Edge Cases & Fault Tolerance ━━")

    with http() as c:
        # 1. Completely empty body
        r = c.post(f"{API}/captures", json={
            "source_type": "proxy", "direction": "request",
            "provider": "openai", "body_json": "", "body_format": "text",
        })
        if r.status_code == 201:
            ok("Empty body capture: accepted")
        else:
            fail("Empty body capture", f"status={r.status_code}")

        # 2. Very long host/path
        r = c.post(f"{API}/captures", json={
            "source_type": "proxy", "direction": "request",
            "provider": "unknown", "host": "x" * 500, "path": "/" + "y" * 500,
            "body_json": "{}", "body_format": "json",
        })
        if r.status_code == 201:
            ok("Very long host/path: accepted")
        else:
            fail("Very long host/path", f"status={r.status_code}")

        # 3. Unknown provider (should still store, just not normalize)
        pid_unk = "edge-unknown-001"
        r = c.post(f"{API}/captures", json={
            "source_type": "proxy", "direction": "request", "pair_id": pid_unk,
            "provider": "totally_unknown_provider",
            "host": "api.unknown.com", "path": "/v1/generate",
            "body_json": '{"prompt": "hello"}', "body_format": "json",
        })
        assert r.status_code == 201
        r = c.post(f"{API}/captures", json={
            "source_type": "proxy", "direction": "response", "pair_id": pid_unk,
            "provider": "totally_unknown_provider",
            "host": "api.unknown.com", "path": "/v1/generate",
            "status_code": 200,
            "body_json": '{"output": "world"}', "body_format": "json",
        })
        assert r.status_code == 201
        ok("Unknown provider: captured without crash")

        # 4. Malformed JSON body
        r = c.post(f"{API}/captures", json={
            "source_type": "proxy", "direction": "request",
            "provider": "openai",
            "body_json": '{"model": "gpt-4", "messages": [BROKEN}',
            "body_format": "text",
        })
        if r.status_code == 201:
            ok("Malformed JSON body: accepted as-is")
        else:
            fail("Malformed JSON body", f"status={r.status_code}")

        # 5. Null/None in optional fields
        r = c.post(f"{API}/captures", json={
            "source_type": "proxy", "direction": "request",
            "provider": "openai",
            "host": None, "path": None, "method": None,
            "model_name": None, "status_code": None,
            "body_json": '{"test": true}', "body_format": "json",
        })
        if r.status_code == 201:
            ok("Null optional fields: accepted")
        else:
            fail("Null optional fields", f"status={r.status_code}")

        # 6. Source type not in map (should still work with default)
        r = c.post(f"{API}/captures", json={
            "source_type": "ide_plugin", "direction": "request",
            "provider": "openai",
            "body_json": '{"model": "gpt-4"}', "body_format": "json",
        })
        if r.status_code == 201:
            ok("Unknown source_type 'ide_plugin': accepted")
        else:
            fail("Unknown source_type", f"status={r.status_code}")

        # 7. Service management endpoints in standalone mode
        r = c.get(f"{API}/services")
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "standalone"
        ok("Services endpoint: standalone mode")

        r = c.post(f"{API}/services/proxy/start")
        if r.status_code == 400:
            ok("Service start in standalone: correctly rejected (400)")
        else:
            fail("Service start in standalone", f"expected 400, got {r.status_code}")

        # 8. 404 for nonexistent session messages
        r = c.get(f"{API}/sessions/nonexistent-session-id/messages")
        if r.status_code == 404:
            ok("Nonexistent session: 404")
        else:
            fail("Nonexistent session", f"expected 404, got {r.status_code}")

        # 9. Hook → unreachable target
        from pce_core.local_hook.hook import create_hook_app
        dead_app = create_hook_app(target_host="127.0.0.1", target_port=19999)
        dead_port = 19537
        _start(dead_app, dead_port, "dead-hook")
        r = c.post(f"http://127.0.0.1:{dead_port}/api/chat", json={
            "model": "test", "messages": [{"role": "user", "content": "hi"}],
        })
        if r.status_code == 502:
            ok("Hook target unreachable: 502")
        else:
            fail("Hook target unreachable", f"expected 502, got {r.status_code}")


# ═══════════════════════════════════════════════════════════════════════════
# S10: DB Integrity Final Audit
# ═══════════════════════════════════════════════════════════════════════════

def s10_db_integrity():
    """Direct DB audit after all stress tests."""
    print("━━ S10: DB Integrity Final Audit ━━")

    db_path = os.path.join(TMP_DIR, "pce.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Count totals
    total_caps = conn.execute("SELECT COUNT(*) as n FROM raw_captures").fetchone()["n"]
    total_sessions = conn.execute("SELECT COUNT(*) as n FROM sessions").fetchone()["n"]
    total_msgs = conn.execute("SELECT COUNT(*) as n FROM messages").fetchone()["n"]
    print(f"  DB totals: {total_caps} captures, {total_sessions} sessions, {total_msgs} messages")

    # No orphan captures
    orphan = conn.execute(
        "SELECT COUNT(*) as n FROM raw_captures WHERE source_id NOT IN (SELECT id FROM sources)"
    ).fetchone()["n"]
    if orphan == 0:
        ok("No orphan captures")
    else:
        fail(f"Orphan captures: {orphan}")

    # No orphan messages
    orphan_msgs = conn.execute(
        "SELECT COUNT(*) as n FROM messages WHERE session_id NOT IN (SELECT id FROM sessions)"
    ).fetchone()["n"]
    if orphan_msgs == 0:
        ok("No orphan messages")
    else:
        fail(f"Orphan messages: {orphan_msgs}")

    # Session message_count accuracy
    mismatches = conn.execute("""
        SELECT s.id, s.message_count, COUNT(m.id) as actual
        FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
        GROUP BY s.id HAVING s.message_count != actual
    """).fetchall()
    if len(mismatches) == 0:
        ok("All session message_counts accurate")
    else:
        fail(f"Session message_count mismatches: {len(mismatches)}")
        for m in mismatches[:5]:
            print(f"    session {dict(m)['id'][:8]}: count={dict(m)['message_count']} actual={dict(m)['actual']}")

    # All responses have matching requests (for paired captures)
    unpaired = conn.execute("""
        SELECT pair_id FROM raw_captures
        WHERE direction = 'response'
        AND pair_id NOT IN (
            SELECT pair_id FROM raw_captures WHERE direction = 'request'
        )
    """).fetchall()
    if len(unpaired) == 0:
        ok("All response captures have matching requests")
    else:
        fail(f"Unpaired responses: {len(unpaired)}")

    # No NULL created_at
    null_ts = conn.execute(
        "SELECT COUNT(*) as n FROM raw_captures WHERE created_at IS NULL"
    ).fetchone()["n"]
    if null_ts == 0:
        ok("No NULL timestamps in captures")
    else:
        fail(f"NULL timestamps: {null_ts}")

    # No empty source_id
    empty_src = conn.execute(
        "SELECT COUNT(*) as n FROM raw_captures WHERE source_id IS NULL OR source_id = ''"
    ).fetchone()["n"]
    if empty_src == 0:
        ok("No empty source_ids")
    else:
        fail(f"Empty source_ids: {empty_src}")

    # DB file size sanity
    db_size = os.path.getsize(db_path)
    print(f"  DB file size: {db_size / 1024:.1f} KB")
    if db_size < 100 * 1024 * 1024:  # < 100MB
        ok(f"DB size reasonable ({db_size / 1024:.1f} KB)")
    else:
        warn(f"DB size large: {db_size / 1024 / 1024:.1f} MB")

    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    global _pass, _fail, _warn
    t0 = time.time()

    try:
        boot()

        s1_multi_turn_sessions()
        s2_hook_stress()
        s3_browser_extension()
        s4_unicode_stress()
        s5_large_payloads()
        s6_concurrent_rw()
        s7_mcp_tools()
        s8_dashboard_consistency()
        s9_edge_cases()
        s10_db_integrity()

    except Exception as e:
        fail("FATAL EXCEPTION", f"{e}\n{traceback.format_exc()}")

    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    elapsed = time.time() - t0
    print(f"\n{'═' * 60}")
    print(f"  Stress Test Complete: {_pass} pass, {_fail} fail, {_warn} warn  ({elapsed:.1f}s)")
    print(f"{'═' * 60}")

    if _issues:
        print("\n  Issues found:")
        for iss in _issues:
            print(f"  {iss}")

    if _fail > 0:
        print(f"\n  *** {_fail} FAILURE(S) — needs fixing ***\n")
        return 1
    elif _warn > 0:
        print(f"\n  *** {_warn} WARNING(S) — review recommended ***\n")
        return 0
    else:
        print("\n  === ALL CHECKS PASSED — SYSTEM STABLE ===\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
