"""PCE Full End-to-End Verification Suite.

Spins up a complete isolated environment with:
- Mock OpenAI / Anthropic / Ollama servers
- PCE Core API server (FastAPI)
- PCE Local Model Hook (reverse-proxy → Mock Ollama)

Then runs 5 test domains:
  T1: Mock server fidelity
  T2: Capture path completeness (Ingest API, Local Hook, Proxy via mitmproxy)
  T3: Normalization correctness (OpenAI + Anthropic → sessions/messages)
  T4: Boundary & fault tolerance (large payload, malformed JSON, concurrency)
  T5: Dashboard API consistency (stats, sessions, captures, services)

Usage:
    python tests/test_e2e_full.py
"""

import concurrent.futures
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

# ---------------------------------------------------------------------------
# Setup paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Isolated temp data dir
TMP_DIR = tempfile.mkdtemp(prefix="pce_e2e_")
os.environ["PCE_DATA_DIR"] = TMP_DIR

# Disable system proxy so httpx talks directly to our test servers
for _pvar in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_pvar, None)
os.environ["NO_PROXY"] = "*"

# Ports – all in 19xxx range to avoid conflicts
PORT_OPENAI = 19001
PORT_ANTHROPIC = 19002
PORT_OLLAMA = 19003
PORT_PCE_CORE = 19800
PORT_LOCAL_HOOK = 19435

# Base URLs
PCE_API = f"http://127.0.0.1:{PORT_PCE_CORE}/api/v1"
HOOK_URL = f"http://127.0.0.1:{PORT_LOCAL_HOOK}"

# Test state
_servers: list[threading.Thread] = []
_pass = 0
_fail = 0
_proxy_proc = None


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def ok(label: str):
    global _pass
    _pass += 1
    print(f"  [PASS] {label}")


def fail(label: str, detail: str = ""):
    global _fail
    _fail += 1
    print(f"  [FAIL] {label}: {detail}")


def wait_for_port(port: int, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def start_uvicorn_thread(app, port: int, name: str):
    """Run a uvicorn server in a daemon thread."""
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    def _run():
        server.run()

    t = threading.Thread(target=_run, daemon=True, name=name)
    t.start()
    _servers.append(t)

    if not wait_for_port(port):
        raise RuntimeError(f"{name} failed to start on port {port}")
    return server


# ═══════════════════════════════════════════════════════════════════════════
# Service Startup
# ═══════════════════════════════════════════════════════════════════════════

def start_all_services():
    """Boot all mock servers, PCE Core, and Local Hook."""
    print("\n[SETUP] Starting services...")

    # 1. Mock AI servers
    from tests.mock_ai_server import create_openai_app, create_anthropic_app, create_ollama_app

    start_uvicorn_thread(create_openai_app(), PORT_OPENAI, "mock-openai")
    print(f"  Mock OpenAI    → :{PORT_OPENAI}")

    start_uvicorn_thread(create_anthropic_app(), PORT_ANTHROPIC, "mock-anthropic")
    print(f"  Mock Anthropic → :{PORT_ANTHROPIC}")

    start_uvicorn_thread(create_ollama_app(), PORT_OLLAMA, "mock-ollama")
    print(f"  Mock Ollama    → :{PORT_OLLAMA}")

    # 2. PCE Core API (need to override port before importing)
    os.environ["PCE_INGEST_PORT"] = str(PORT_PCE_CORE)
    # Force reload config with new port
    import pce_core.config as cfg
    cfg.INGEST_PORT = PORT_PCE_CORE

    from pce_core.server import app as pce_app
    start_uvicorn_thread(pce_app, PORT_PCE_CORE, "pce-core")
    print(f"  PCE Core       → :{PORT_PCE_CORE}")

    # Wait for DB init
    time.sleep(0.5)

    # 3. Local Hook → Mock Ollama
    from pce_core.local_hook.hook import create_hook_app
    hook_app = create_hook_app(target_host="127.0.0.1", target_port=PORT_OLLAMA)
    start_uvicorn_thread(hook_app, PORT_LOCAL_HOOK, "local-hook")
    print(f"  Local Hook     → :{PORT_LOCAL_HOOK} → :{PORT_OLLAMA}")

    print("[SETUP] All services ready.\n")


# ═══════════════════════════════════════════════════════════════════════════
# T1: Mock Server Fidelity
# ═══════════════════════════════════════════════════════════════════════════

def test_t1_mock_servers():
    print("── T1: Mock Server Fidelity ──")

    with httpx.Client(timeout=10, trust_env=False) as c:
        # OpenAI
        r = c.post(f"http://127.0.0.1:{PORT_OPENAI}/v1/chat/completions", json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["model"] == "gpt-4"
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "usage" in data
        ok("OpenAI mock: correct schema")

        # Anthropic
        r = c.post(f"http://127.0.0.1:{PORT_ANTHROPIC}/v1/messages", json={
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert data["content"][0]["type"] == "text"
        assert "usage" in data
        ok("Anthropic mock: correct schema")

        # Ollama
        r = c.post(f"http://127.0.0.1:{PORT_OLLAMA}/api/chat", json={
            "model": "llama3",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["model"] == "llama3"
        assert data["message"]["role"] == "assistant"
        assert data["done"] is True
        ok("Ollama mock: correct schema")


# ═══════════════════════════════════════════════════════════════════════════
# T2: Capture Path Completeness
# ═══════════════════════════════════════════════════════════════════════════

def _post_capture(client: httpx.Client, **kwargs) -> dict:
    """Helper to POST a capture to the Ingest API."""
    r = client.post(f"{PCE_API}/captures", json=kwargs)
    assert r.status_code == 201, f"Ingest failed ({r.status_code}): {r.text}"
    return r.json()


def test_t2_ingest_api_openai():
    """T2a: OpenAI request+response via Ingest API → DB + normalization."""
    print("── T2a: Ingest API – OpenAI Pair ──")

    pair_id = "e2e-openai-pair-001"
    req_body = json.dumps({
        "model": "gpt-4",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is PCE?"},
        ],
    })
    resp_body = json.dumps({
        "id": "chatcmpl-e2e001",
        "object": "chat.completion",
        "model": "gpt-4",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "PCE stands for Personal Cognitive Engine."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 10, "total_tokens": 35},
    })

    with httpx.Client(timeout=10, trust_env=False) as c:
        # Ingest request
        out1 = _post_capture(c,
            source_type="proxy", direction="request", pair_id=pair_id,
            provider="openai", host="api.openai.com", path="/v1/chat/completions",
            method="POST", model_name="gpt-4", body_json=req_body, body_format="json",
            headers_json='{"content-type": "application/json", "authorization": "REDACTED"}',
        )
        assert out1["pair_id"] == pair_id
        ok("OpenAI request ingested")

        # Ingest response (triggers normalization)
        out2 = _post_capture(c,
            source_type="proxy", direction="response", pair_id=pair_id,
            provider="openai", host="api.openai.com", path="/v1/chat/completions",
            method="POST", model_name="gpt-4", status_code=200, latency_ms=150.0,
            body_json=resp_body, body_format="json",
        )
        assert out2["pair_id"] == pair_id
        ok("OpenAI response ingested")


def test_t2_ingest_api_anthropic():
    """T2b: Anthropic request+response via Ingest API."""
    print("── T2b: Ingest API – Anthropic Pair ──")

    pair_id = "e2e-anthro-pair-001"
    req_body = json.dumps({
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Explain quantum computing."}],
    })
    resp_body = json.dumps({
        "id": "msg_e2e001",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [{"type": "text", "text": "Quantum computing uses quantum bits..."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 15, "output_tokens": 8},
    })

    with httpx.Client(timeout=10, trust_env=False) as c:
        _post_capture(c,
            source_type="proxy", direction="request", pair_id=pair_id,
            provider="anthropic", host="api.anthropic.com", path="/v1/messages",
            method="POST", model_name="claude-3-5-sonnet-20241022",
            body_json=req_body, body_format="json",
        )
        ok("Anthropic request ingested")

        _post_capture(c,
            source_type="proxy", direction="response", pair_id=pair_id,
            provider="anthropic", host="api.anthropic.com", path="/v1/messages",
            method="POST", model_name="claude-3-5-sonnet-20241022",
            status_code=200, latency_ms=200.0,
            body_json=resp_body, body_format="json",
        )
        ok("Anthropic response ingested")


def test_t2_ingest_browser_ext():
    """T2c: Browser extension conversation capture via Ingest API."""
    print("── T2c: Ingest API – Browser Extension ──")

    conv = json.dumps({
        "messages": [
            {"role": "user", "content": "Tell me a joke"},
            {"role": "assistant", "content": "Why did the programmer quit? No arrays."},
        ],
        "model": "chatgpt-4o",
        "url": "https://chatgpt.com/c/abc123",
    })

    with httpx.Client(timeout=10, trust_env=False) as c:
        out = _post_capture(c,
            source_type="browser_extension", direction="conversation",
            provider="openai", host="chatgpt.com",
            body_json=conv, body_format="json",
            meta={"page_url": "https://chatgpt.com/c/abc123"},
        )
        assert out["id"]
        ok("Browser extension conversation ingested")


def test_t2_local_hook():
    """T2d: Local Hook captures Ollama traffic transparently."""
    print("── T2d: Local Hook – Ollama via Hook ──")

    with httpx.Client(timeout=10, trust_env=False) as c:
        r = c.post(f"{HOOK_URL}/api/chat", json={
            "model": "llama3",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["model"] == "llama3"
        assert "message" in data
        ok("Local Hook: request forwarded and response received")

    # Give capture time to write
    time.sleep(0.5)

    # Verify captures in DB
    with httpx.Client(timeout=10, trust_env=False) as c:
        captures = c.get(f"{PCE_API}/captures?last=100").json()
        hook_captures = [
            cap for cap in captures
            if cap.get("host", "").startswith("127.0.0.1:")
            and "api/chat" in (cap.get("path") or "")
        ]
        assert len(hook_captures) >= 2, f"Expected >= 2 hook captures, got {len(hook_captures)}"
        directions = {cap["direction"] for cap in hook_captures}
        assert "request" in directions and "response" in directions
        ok("Local Hook: request + response captured in DB")


def test_t2_mitmproxy():
    """T2e: mitmproxy proxy path (reverse mode → Mock OpenAI)."""
    print("── T2e: mitmproxy Proxy Path ──")

    global _proxy_proc
    proxy_port = 19080
    env = os.environ.copy()
    env["PCE_EXTRA_HOSTS"] = "127.0.0.1"

    try:
        _proxy_proc = subprocess.Popen(
            [
                "mitmdump",
                "--mode", f"reverse:http://127.0.0.1:{PORT_OPENAI}",
                "-p", str(proxy_port),
                "-s", str(PROJECT_ROOT / "run_proxy.py"),
                "--set", "flow_detail=0",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )

        if not wait_for_port(proxy_port, timeout=15):
            fail("mitmproxy: failed to start")
            return

        time.sleep(1)  # Let addon init

        # Send request through proxy
        with httpx.Client(timeout=10, trust_env=False) as c:
            r = c.post(
                f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "Hello from proxy E2E test"}],
                },
                headers={
                    "Host": "api.openai.com",
                    "Authorization": "Bearer sk-test-secret-key-e2e-12345",
                    "Content-Type": "application/json",
                },
            )
            assert r.status_code == 200
            ok("mitmproxy: request forwarded, got 200")

        time.sleep(2)  # Let async write complete

        # Verify in DB
        db_path = os.path.join(TMP_DIR, "pce.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM raw_captures WHERE body_text_or_json LIKE '%Hello from proxy E2E test%'"
        ).fetchall()
        rows = [dict(r) for r in rows]
        conn.close()

        if len(rows) >= 1:
            ok("mitmproxy: capture found in DB")
            # Check redaction
            for r in rows:
                hdrs = r.get("headers_redacted_json", "")
                if "sk-test-secret-key-e2e-12345" in hdrs:
                    fail("mitmproxy: API key NOT redacted!")
                    return
            ok("mitmproxy: API key redacted correctly")
        else:
            fail("mitmproxy: no captures found in DB", f"rows={len(rows)}")

    except FileNotFoundError:
        print("  [SKIP] mitmproxy not installed – proxy path skipped")
    finally:
        if _proxy_proc:
            _proxy_proc.terminate()
            try:
                _proxy_proc.wait(timeout=5)
            except Exception:
                _proxy_proc.kill()
            _proxy_proc = None


# ═══════════════════════════════════════════════════════════════════════════
# T3: Normalization Correctness
# ═══════════════════════════════════════════════════════════════════════════

def test_t3_normalization():
    """T3: Verify OpenAI + Anthropic pairs were normalized into sessions."""
    print("── T3: Normalization Correctness ──")

    with httpx.Client(timeout=10, trust_env=False) as c:
        sessions = c.get(f"{PCE_API}/sessions?last=100").json()

        # Should have at least the OpenAI and Anthropic sessions from T2
        assert len(sessions) >= 2, f"Expected >= 2 sessions, got {len(sessions)}"
        ok(f"Sessions created: {len(sessions)}")

        providers = {s["provider"] for s in sessions}
        if "openai" in providers:
            ok("OpenAI session normalized")
        else:
            fail("OpenAI session missing", f"providers={providers}")

        if "anthropic" in providers:
            ok("Anthropic session normalized")
        else:
            fail("Anthropic session missing", f"providers={providers}")

        # Check messages for each session
        for sess in sessions:
            msgs = c.get(f"{PCE_API}/sessions/{sess['id']}/messages").json()
            assert len(msgs) >= 2, f"Session {sess['id'][:8]} has {len(msgs)} msgs, expected >= 2"

            roles = [m["role"] for m in msgs]
            assert "user" in roles, f"Session {sess['id'][:8]}: no user message"
            assert "assistant" in roles, f"Session {sess['id'][:8]}: no assistant message"

            # Check content is not empty
            for m in msgs:
                if m["role"] in ("user", "assistant"):
                    assert m.get("content_text"), f"Empty content in msg {m['id'][:8]}"

            ok(f"Session {sess['id'][:8]} ({sess['provider']}): {len(msgs)} msgs, roles OK")

        # Verify message_count on session matches actual messages
        for sess in sessions:
            msgs = c.get(f"{PCE_API}/sessions/{sess['id']}/messages").json()
            assert sess["message_count"] == len(msgs), \
                f"Session {sess['id'][:8]}: message_count={sess['message_count']} but actual={len(msgs)}"
        ok("Session message_count matches actual messages")


# ═══════════════════════════════════════════════════════════════════════════
# T4: Boundary & Fault Tolerance
# ═══════════════════════════════════════════════════════════════════════════

def test_t4_large_payload():
    """T4a: Large payload (~100KB body) does not crash."""
    print("── T4a: Large Payload ──")

    big_content = "x" * 100_000
    pair_id = "e2e-bigpay-001"

    with httpx.Client(timeout=10, trust_env=False) as c:
        req_body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": big_content}],
        })

        out = _post_capture(c,
            source_type="proxy", direction="request", pair_id=pair_id,
            provider="openai", host="api.openai.com", path="/v1/chat/completions",
            method="POST", body_json=req_body, body_format="json",
        )
        assert out["id"]
        ok(f"Large payload ingested ({len(req_body)} bytes)")


def test_t4_malformed_json():
    """T4b: Malformed JSON body is stored as-is, no crash."""
    print("── T4b: Malformed JSON ──")

    pair_id = "e2e-malform-001"
    bad_body = '{"model": "gpt-4", "messages": [{"role": "user", BROKEN'

    with httpx.Client(timeout=10, trust_env=False) as c:
        out = _post_capture(c,
            source_type="proxy", direction="request", pair_id=pair_id,
            provider="openai", host="api.openai.com", path="/v1/chat/completions",
            method="POST", body_json=bad_body, body_format="text",
        )
        assert out["id"]
        ok("Malformed JSON ingested without crash")


def test_t4_empty_body():
    """T4c: Empty body capture."""
    print("── T4c: Empty Body ──")

    pair_id = "e2e-empty-001"
    with httpx.Client(timeout=10, trust_env=False) as c:
        out = _post_capture(c,
            source_type="proxy", direction="request", pair_id=pair_id,
            provider="openai", host="api.openai.com", path="/v1/chat/completions",
            method="POST", body_json="", body_format="text",
        )
        assert out["id"]
        ok("Empty body ingested without crash")


def test_t4_concurrent():
    """T4d: 50 concurrent requests all land in DB."""
    print("── T4d: Concurrent Requests (50x) ──")

    n = 50
    results = []

    def _send(i):
        with httpx.Client(timeout=15, trust_env=False) as c:
            pair_id = f"e2e-conc-{i:04d}"
            out = _post_capture(c,
                source_type="proxy", direction="request", pair_id=pair_id,
                provider="openai", host="api.openai.com",
                path="/v1/chat/completions", method="POST",
                body_json=json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": f"msg-{i}"}]}),
                body_format="json",
            )
            return out["id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_send, i) for i in range(n)]
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                fail(f"Concurrent request failed", str(e))

    assert len(results) == n, f"Expected {n} results, got {len(results)}"
    assert len(set(results)) == n, "Duplicate IDs detected"
    ok(f"All {n} concurrent requests succeeded with unique IDs")


def test_t4_hook_target_down():
    """T4e: Local Hook returns 502 when target server is unreachable."""
    print("── T4e: Hook Target Unreachable ──")

    # Create a hook pointing at a port with nothing running
    from pce_core.local_hook.hook import create_hook_app
    dead_hook = create_hook_app(target_host="127.0.0.1", target_port=19999)

    dead_port = 19436
    start_uvicorn_thread(dead_hook, dead_port, "dead-hook")

    with httpx.Client(timeout=10, trust_env=False) as c:
        r = c.post(f"http://127.0.0.1:{dead_port}/api/chat", json={
            "model": "test", "messages": [{"role": "user", "content": "hi"}],
        })
        assert r.status_code == 502
        ok("Hook returns 502 when target unreachable")


# ═══════════════════════════════════════════════════════════════════════════
# T5: Dashboard API Consistency
# ═══════════════════════════════════════════════════════════════════════════

def test_t5_dashboard_api():
    """T5: Verify all dashboard API endpoints return consistent data."""
    print("── T5: Dashboard API Consistency ──")

    with httpx.Client(timeout=10, trust_env=False) as c:
        # Health
        health = c.get(f"{PCE_API}/health").json()
        assert health["status"] == "ok"
        assert health["total_captures"] > 0
        ok(f"Health: ok, {health['total_captures']} captures")

        # Stats
        stats = c.get(f"{PCE_API}/stats").json()
        assert stats["total_captures"] == health["total_captures"], \
            f"Stats total ({stats['total_captures']}) != health total ({health['total_captures']})"
        assert "openai" in stats["by_provider"]
        assert "anthropic" in stats["by_provider"]
        ok(f"Stats: consistent total, providers present")

        # Stats breakdown counts should sum to total
        by_dir_sum = sum(stats["by_direction"].values())
        assert by_dir_sum == stats["total_captures"], \
            f"by_direction sum ({by_dir_sum}) != total ({stats['total_captures']})"
        ok("Stats: by_direction sums to total")

        # Captures list
        captures = c.get(f"{PCE_API}/captures?last=500").json()
        assert len(captures) == stats["total_captures"], \
            f"Captures list ({len(captures)}) != stats total ({stats['total_captures']})"
        ok(f"Captures list: {len(captures)} matches stats total")

        # Filter by provider
        openai_caps = c.get(f"{PCE_API}/captures?provider=openai&last=500").json()
        assert len(openai_caps) == stats["by_provider"]["openai"], \
            f"OpenAI filter ({len(openai_caps)}) != stats ({stats['by_provider']['openai']})"
        ok("Captures filter by provider: consistent")

        # Sessions
        sessions = c.get(f"{PCE_API}/sessions?last=100").json()
        assert len(sessions) >= 2
        ok(f"Sessions list: {len(sessions)} sessions")

        # Services endpoint
        services = c.get(f"{PCE_API}/services").json()
        assert services["mode"] == "standalone"
        assert "core" in services["services"]
        ok("Services endpoint: standalone mode reported")

        # Pair lookup
        if captures:
            pair_id = captures[0]["pair_id"]
            pair = c.get(f"{PCE_API}/captures/pair/{pair_id}").json()
            assert len(pair) >= 1
            ok(f"Pair lookup: found {len(pair)} captures for pair {pair_id[:8]}")

        # 404 for non-existent pair
        r = c.get(f"{PCE_API}/captures/pair/nonexistent-pair-id")
        assert r.status_code == 404
        ok("Pair 404: correct for nonexistent pair")

        # Dashboard HTML
        r = c.get(f"http://127.0.0.1:{PORT_PCE_CORE}/")
        assert r.status_code == 200
        assert "PCE" in r.text
        ok("Dashboard HTML: served correctly")


# ═══════════════════════════════════════════════════════════════════════════
# DB Direct Verification
# ═══════════════════════════════════════════════════════════════════════════

def test_db_integrity():
    """Final DB integrity check: foreign keys, no orphans."""
    print("── DB Integrity ──")

    db_path = os.path.join(TMP_DIR, "pce.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # All captures have valid source_id
    orphan_caps = conn.execute(
        "SELECT COUNT(*) as n FROM raw_captures WHERE source_id NOT IN (SELECT id FROM sources)"
    ).fetchone()["n"]
    assert orphan_caps == 0
    ok("No orphan captures (all source_ids valid)")

    # All messages have valid session_id
    orphan_msgs = conn.execute(
        "SELECT COUNT(*) as n FROM messages WHERE session_id NOT IN (SELECT id FROM sessions)"
    ).fetchone()["n"]
    assert orphan_msgs == 0
    ok("No orphan messages (all session_ids valid)")

    # Session message_count matches actual count
    mismatches = conn.execute("""
        SELECT s.id, s.message_count, COUNT(m.id) as actual
        FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
        GROUP BY s.id
        HAVING s.message_count != actual
    """).fetchall()
    assert len(mismatches) == 0, f"Message count mismatches: {[dict(r) for r in mismatches]}"
    ok("Session message_counts all match actual message rows")

    # Pair IDs: for every response there should be a matching request (or conversation)
    pairs_with_response = conn.execute(
        "SELECT DISTINCT pair_id FROM raw_captures WHERE direction = 'response'"
    ).fetchall()
    for row in pairs_with_response:
        pid = row["pair_id"]
        req = conn.execute(
            "SELECT COUNT(*) as n FROM raw_captures WHERE pair_id = ? AND direction = 'request'",
            (pid,),
        ).fetchone()["n"]
        assert req >= 1, f"Response pair {pid} has no matching request"
    ok(f"All {len(pairs_with_response)} response pairs have matching requests")

    conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    global _pass, _fail
    start_time = time.time()

    try:
        start_all_services()

        # T1: Mock server fidelity
        test_t1_mock_servers()

        # T2: All capture paths
        test_t2_ingest_api_openai()
        test_t2_ingest_api_anthropic()
        test_t2_ingest_browser_ext()
        test_t2_local_hook()
        test_t2_mitmproxy()

        # T3: Normalization
        test_t3_normalization()

        # T4: Boundary & fault tolerance
        test_t4_large_payload()
        test_t4_malformed_json()
        test_t4_empty_body()
        test_t4_concurrent()
        test_t4_hook_target_down()

        # T5: Dashboard API
        test_t5_dashboard_api()

        # DB integrity
        test_db_integrity()

    except Exception as e:
        fail("UNEXPECTED EXCEPTION", str(e))
        import traceback
        traceback.print_exc()

    finally:
        # Cleanup
        if _proxy_proc:
            _proxy_proc.terminate()
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"  E2E Results: {_pass} passed, {_fail} failed ({elapsed:.1f}s)")
    print(f"{'=' * 60}")

    if _fail > 0:
        print("\n  *** FAILURES DETECTED – see [FAIL] lines above ***\n")
        return 1
    else:
        print("\n  === ALL E2E TESTS PASSED ===\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
