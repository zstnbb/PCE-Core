# SPDX-License-Identifier: Apache-2.0
"""PCE Manual Capture Test Suite.

Run each test function independently to verify a specific capture channel.
Requires PCE Core server running on http://127.0.0.1:9800.

Usage:
    # Check server is up
    python tests/manual_capture_test.py check

    # Test SDK capture (OpenAI)
    python tests/manual_capture_test.py sdk-openai

    # Test SDK capture (Anthropic)
    python tests/manual_capture_test.py sdk-anthropic

    # Test direct API ingest
    python tests/manual_capture_test.py ingest

    # Test Electron app detection
    python tests/manual_capture_test.py electron

    # Test local model hook (requires Ollama running)
    python tests/manual_capture_test.py local-hook

    # Test all non-interactive checks
    python tests/manual_capture_test.py all
"""

import json
import sys
import time
import urllib.request
import urllib.error

PCE_URL = "http://127.0.0.1:9800"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_get(path: str) -> dict:
    req = urllib.request.Request(f"{PCE_URL}{path}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def api_post(path: str, data: dict) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{PCE_URL}{path}", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def print_ok(msg: str):
    print(f"  ✅ {msg}")


def print_fail(msg: str):
    print(f"  ❌ {msg}")


def print_info(msg: str):
    print(f"  ℹ️  {msg}")


# ---------------------------------------------------------------------------
# Test: Server health check
# ---------------------------------------------------------------------------

def test_check():
    """Verify PCE Core server is running and responsive."""
    print("\n🔍 Test: Server Health Check")
    print("-" * 40)

    try:
        health = api_get("/api/v1/health")
        print_ok(f"Server is up: v{health.get('version', '?')}")
        print_info(f"DB: {health.get('db_path')}")
        print_info(f"Total captures: {health.get('total_captures', 0)}")
    except urllib.error.URLError:
        print_fail("Server not reachable at http://127.0.0.1:9800")
        print_info("Start it with: python -m pce_app --no-tray")
        return False

    try:
        ch = api_get("/api/v1/capture-health")
        print_ok(f"Capture health: {ch.get('captures_24h', 0)} captures in 24h")
        sources = ch.get("source_activity", {})
        if sources:
            for src, info in sources.items():
                print_info(f"  Source '{src}': {info.get('count', 0)} captures")
    except Exception as e:
        print_info(f"Capture health endpoint: {e}")

    return True


# ---------------------------------------------------------------------------
# Test: Direct API ingest
# ---------------------------------------------------------------------------

def test_ingest():
    """Send a test capture directly to the Ingest API."""
    print("\n📥 Test: Direct API Ingest")
    print("-" * 40)

    # Get baseline count
    try:
        before = api_get("/api/v1/health").get("total_captures", 0)
    except Exception:
        print_fail("Server not reachable")
        return False

    # Send a fake conversation capture
    payload = {
        "source_type": "browser_extension",
        "source_name": "manual-test",
        "direction": "conversation",
        "provider": "test",
        "host": "test.example.com",
        "path": "/test",
        "method": "POST",
        "model_name": "test-model",
        "body_json": json.dumps({
            "messages": [
                {"role": "user", "content": "Hello, this is a PCE capture test."},
                {"role": "assistant", "content": "Hi! This is a test response from the PCE manual test suite."},
            ]
        }),
        "body_format": "json",
        "session_hint": f"test-session-{int(time.time())}",
        "meta": {"test": True, "timestamp": time.time()},
    }

    try:
        result = api_post("/api/v1/captures", payload)
        print_ok(f"Capture inserted: ID={result.get('capture_id', '?')[:12]}")
        if result.get("session_id"):
            print_ok(f"Auto-normalized to session: {result['session_id'][:12]}")
    except Exception as e:
        print_fail(f"Ingest failed: {e}")
        return False

    # Verify count increased
    try:
        after = api_get("/api/v1/health").get("total_captures", 0)
        if after > before:
            print_ok(f"Capture count: {before} → {after}")
        else:
            print_fail(f"Count didn't increase: {before} → {after}")
    except Exception:
        pass

    return True


# ---------------------------------------------------------------------------
# Test: SDK Capture (OpenAI)
# ---------------------------------------------------------------------------

def test_sdk_openai():
    """Test OpenAI SDK monkey-patch capture."""
    print("\n🐍 Test: OpenAI SDK Capture")
    print("-" * 40)

    try:
        import openai
        print_ok(f"openai package found: v{openai.__version__}")
    except ImportError:
        print_fail("openai package not installed. Run: pip install openai")
        return False

    # Check if API key is set
    api_key = None
    try:
        import os
        api_key = os.environ.get("OPENAI_API_KEY")
    except Exception:
        pass

    if not api_key:
        print_info("OPENAI_API_KEY not set - testing patch installation only")
        print_info("Set it to test full capture: $env:OPENAI_API_KEY='sk-...'")

    # Apply the monkey-patch
    try:
        from pce_core.sdk_capture import patch_openai, _patched
        _patched["openai"] = False  # Reset to allow re-patching
        patch_openai()
        print_ok("OpenAI SDK patched successfully")
    except Exception as e:
        print_fail(f"Patch failed: {e}")
        return False

    # Verify the patch is in place
    try:
        from openai.resources.chat.completions import Completions
        if "patched" in str(Completions.create.__qualname__).lower() or Completions.create.__name__ == "_patched_create":
            print_ok("Completions.create is patched")
        else:
            print_info(f"Patch function: {Completions.create.__name__}")
    except Exception:
        print_info("Could not verify patch installation")

    if api_key:
        print_info("Making a real API call...")
        try:
            client = openai.OpenAI()
            before = api_get("/api/v1/health").get("total_captures", 0)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Say 'PCE capture test OK' in exactly those words."}],
                max_tokens=20,
            )
            print_ok(f"API response: {resp.choices[0].message.content}")
            time.sleep(2)  # Wait for background capture thread
            after = api_get("/api/v1/health").get("total_captures", 0)
            if after > before:
                print_ok(f"Capture recorded! ({before} → {after})")
            else:
                print_fail(f"Capture not recorded ({before} → {after})")
        except Exception as e:
            print_fail(f"API call failed: {e}")

    return True


# ---------------------------------------------------------------------------
# Test: SDK Capture (Anthropic)
# ---------------------------------------------------------------------------

def test_sdk_anthropic():
    """Test Anthropic SDK monkey-patch capture."""
    print("\n🐍 Test: Anthropic SDK Capture")
    print("-" * 40)

    try:
        import anthropic
        print_ok(f"anthropic package found: v{anthropic.__version__}")
    except ImportError:
        print_fail("anthropic package not installed. Run: pip install anthropic")
        return False

    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print_info("ANTHROPIC_API_KEY not set - testing patch installation only")

    try:
        from pce_core.sdk_capture import patch_anthropic, _patched
        _patched["anthropic"] = False
        patch_anthropic()
        print_ok("Anthropic SDK patched successfully")
    except Exception as e:
        print_fail(f"Patch failed: {e}")
        return False

    if api_key:
        print_info("Making a real API call...")
        try:
            client = anthropic.Anthropic()
            before = api_get("/api/v1/health").get("total_captures", 0)
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=20,
                messages=[{"role": "user", "content": "Say 'PCE capture test OK' in exactly those words."}],
            )
            print_ok(f"API response: {resp.content[0].text}")
            time.sleep(2)
            after = api_get("/api/v1/health").get("total_captures", 0)
            if after > before:
                print_ok(f"Capture recorded! ({before} → {after})")
            else:
                print_fail(f"Capture not recorded ({before} → {after})")
        except Exception as e:
            print_fail(f"API call failed: {e}")

    return True


# ---------------------------------------------------------------------------
# Test: Electron app detection
# ---------------------------------------------------------------------------

def test_electron():
    """Test Electron AI app detection."""
    print("\n💻 Test: Electron App Detection")
    print("-" * 40)

    try:
        from pce_core.electron_proxy import detect_installed_apps, get_proxy_env, get_ca_cert_path
    except ImportError as e:
        print_fail(f"Import failed: {e}")
        return False

    apps = detect_installed_apps()
    if apps:
        print_ok(f"Found {len(apps)} Electron AI app(s):")
        for a in apps:
            print_info(f"  {a['display_name']} → {a['path']}")
            print_info(f"    AI domains: {', '.join(a['ai_domains'])}")
    else:
        print_info("No known Electron AI apps detected on this machine")

    env = get_proxy_env()
    print_ok("Proxy env vars generated:")
    for k, v in env.items():
        if k.isupper():
            print_info(f"  {k}={v}")

    ca = get_ca_cert_path()
    if ca:
        print_ok(f"CA cert found: {ca}")
    else:
        print_info("mitmproxy CA cert not found (run mitmproxy once to generate)")

    return True


# ---------------------------------------------------------------------------
# Test: Local model hook scanner
# ---------------------------------------------------------------------------

def test_local_hook():
    """Test local model server detection."""
    print("\n🏠 Test: Local Model Server Detection")
    print("-" * 40)

    try:
        from pce_core.local_hook.scanner import scan_known_ports
    except ImportError as e:
        print_fail(f"Import failed: {e}")
        return False

    print_info("Scanning known ports (11434, 1234, 8000, 8080, 5000, 3000)...")
    servers = scan_known_ports()
    if servers:
        print_ok(f"Found {len(servers)} local model server(s):")
        for s in servers:
            print_info(f"  {s.provider} on port {s.port} — models: {s.models}")
    else:
        print_info("No local model servers detected")
        print_info("Start Ollama/LM Studio/vLLM first, then re-run")

    return True


# ---------------------------------------------------------------------------
# Test: Capture health & stats
# ---------------------------------------------------------------------------

def test_stats():
    """Show current capture statistics."""
    print("\n📊 Test: Capture Statistics")
    print("-" * 40)

    try:
        stats = api_get("/api/v1/stats")
        print_ok(f"Total captures: {stats.get('total_captures', 0)}")
        print_info(f"Total sessions: {stats.get('total_sessions', 0)}")
        print_info(f"Total messages: {stats.get('total_messages', 0)}")
    except Exception as e:
        print_fail(f"Stats failed: {e}")
        return False

    try:
        sessions = api_get("/api/v1/sessions?limit=5")
        if isinstance(sessions, list) and sessions:
            print_ok(f"Recent sessions ({len(sessions)}):")
            for s in sessions[:5]:
                provider = s.get("provider", "?")
                title = (s.get("title_hint") or "untitled")[:50]
                msg_count = s.get("message_count", "?")
                print_info(f"  [{provider}] {title} ({msg_count} msgs)")
        else:
            print_info("No sessions yet")
    except Exception:
        pass

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_TESTS = {
    "check": test_check,
    "ingest": test_ingest,
    "sdk-openai": test_sdk_openai,
    "sdk-anthropic": test_sdk_anthropic,
    "electron": test_electron,
    "local-hook": test_local_hook,
    "stats": test_stats,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("PCE Manual Capture Test Suite")
        print("=" * 40)
        print(f"\nUsage: python {sys.argv[0]} <test-name>")
        print("\nAvailable tests:")
        for name, fn in ALL_TESTS.items():
            print(f"  {name:16s} — {fn.__doc__}")
        print(f"  {'all':16s} — Run all non-interactive tests")
        print(f"\nPre-requisite: PCE Core running on {PCE_URL}")
        return

    target = sys.argv[1]

    if target == "all":
        print("=" * 50)
        print("  PCE CAPTURE SYSTEM — FULL DIAGNOSTIC")
        print("=" * 50)
        results = {}
        for name in ["check", "stats", "ingest", "sdk-openai", "sdk-anthropic", "electron", "local-hook"]:
            try:
                results[name] = ALL_TESTS[name]()
            except Exception as e:
                print_fail(f"Test '{name}' crashed: {e}")
                results[name] = False

        print("\n" + "=" * 50)
        print("  SUMMARY")
        print("=" * 50)
        for name, ok in results.items():
            status = "✅ PASS" if ok else "❌ FAIL"
            print(f"  {name:16s} {status}")
    elif target in ALL_TESTS:
        ALL_TESTS[target]()
    else:
        print(f"Unknown test: {target}")
        print(f"Available: {', '.join(ALL_TESTS.keys())}, all")


if __name__ == "__main__":
    main()
