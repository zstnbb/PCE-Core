# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for PCE Proxy PoC.

Spins up a mock AI server and a mitmproxy reverse proxy, sends a request
through the proxy, then verifies the capture landed in SQLite with headers
properly redacted.

Usage:
    python tests/test_e2e.py
"""

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MOCK_PORT = 19999
PROXY_PORT = 18080


def wait_for_port(port, host="127.0.0.1", timeout=15):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect((host, port))
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    # Use a temp data dir so we don't pollute the real DB
    tmp_data = tempfile.mkdtemp(prefix="pce_test_")
    db_path = os.path.join(tmp_data, "pce.db")
    env = os.environ.copy()
    env["PCE_DATA_DIR"] = tmp_data

    procs = []
    try:
        # 1. Start mock AI server
        print("[1/5] Starting mock AI server on port", MOCK_PORT)
        mock_proc = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "tests" / "mock_ai_server.py")],
            env={**env, "MOCK_PORT": str(MOCK_PORT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        procs.append(mock_proc)

        if not wait_for_port(MOCK_PORT):
            print("FAIL: mock server did not start")
            return 1

        # 2. Start mitmproxy in reverse mode pointing at mock server
        #    In reverse mode, mitmproxy acts as an HTTP server that forwards
        #    to the upstream. The addon will see the Host header we set.
        print("[2/5] Starting mitmproxy reverse proxy on port", PROXY_PORT)
        proxy_env = {**env, "PCE_EXTRA_HOSTS": "127.0.0.1"}
        proxy_proc = subprocess.Popen(
            [
                "mitmdump",
                "--mode", f"reverse:http://127.0.0.1:{MOCK_PORT}",
                "-p", str(PROXY_PORT),
                "-s", str(PROJECT_ROOT / "run_proxy.py"),
                "--set", "stream_large_bodies=1m",
            ],
            env=proxy_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        procs.append(proxy_proc)

        if not wait_for_port(PROXY_PORT):
            print("FAIL: proxy did not start")
            return 1
        # Give addon a moment to finish init
        time.sleep(1)

        # 3. Send a test request through the proxy
        print("[3/5] Sending test request through proxy")
        url = f"http://127.0.0.1:{PROXY_PORT}/v1/chat/completions"
        body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello PCE test"}],
        }).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Host": "api.openai.com",
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-secret-test-key-12345",
                "X-Api-Key": "xai-another-secret",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        resp_body = resp.read().decode()
        print(f"     Response status: {resp.status}")
        print(f"     Response body:   {resp_body[:120]}")
        assert resp.status == 200, f"Expected 200, got {resp.status}"

        # Give the async write a moment
        time.sleep(2)

        # 4. Verify captures in SQLite
        print("[4/5] Verifying captures in SQLite")
        assert os.path.exists(db_path), f"DB not found at {db_path}"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM raw_captures ORDER BY created_at").fetchall()
        rows = [dict(r) for r in rows]
        conn.close()

        assert len(rows) >= 2, f"Expected at least 2 captures (req+resp), got {len(rows)}"

        req_rows = [r for r in rows if r["direction"] == "request"]
        resp_rows = [r for r in rows if r["direction"] == "response"]
        assert len(req_rows) >= 1, "No request capture found"
        assert len(resp_rows) >= 1, "No response capture found"

        # Verify pair linkage
        assert req_rows[-1]["pair_id"] == resp_rows[-1]["pair_id"], "Pair IDs don't match"

        # Verify host captured (in reverse mode this is 127.0.0.1)
        assert req_rows[-1]["host"] is not None

        # Verify model extraction
        assert req_rows[-1]["model_name"] == "gpt-4", f"Expected model gpt-4, got {req_rows[-1]['model_name']}"

        # Verify redaction
        for r in rows:
            hdrs = r["headers_redacted_json"]
            assert "sk-secret-test-key-12345" not in hdrs, "API key leaked in headers!"
            assert "xai-another-secret" not in hdrs, "X-Api-Key leaked in headers!"
            if "Authorization" in hdrs:
                assert "REDACTED" in hdrs, "Authorization not redacted"

        # Verify response status code captured
        assert resp_rows[-1]["status_code"] == 200

        # Verify latency recorded
        assert resp_rows[-1]["latency_ms"] is not None and resp_rows[-1]["latency_ms"] > 0

        print("     Captures:        OK")
        print("     Pair linkage:    OK")
        print("     Provider:        OK")
        print("     Model:           OK")
        print("     Redaction:       OK")
        print("     Status code:     OK")
        print("     Latency:         OK")

        # 5. Test inspect CLI
        print("[5/5] Testing inspect CLI")
        result = subprocess.run(
            [sys.executable, "-m", "pce_proxy", "--last", "5", "--db", db_path],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env,
        )
        assert result.returncode == 0, f"Inspect CLI failed: {result.stderr}"
        assert "127.0.0.1" in result.stdout or "api.openai.com" in result.stdout, "Inspect output missing host"
        print("     Inspect CLI:     OK")

        result2 = subprocess.run(
            [sys.executable, "-m", "pce_proxy", "--stats", "--db", db_path],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env,
        )
        assert result2.returncode == 0
        assert result2.stdout.strip(), "Stats output is empty"
        print("     Inspect --stats: OK")

        print("\n=== All end-to-end tests passed ===")
        return 0

    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()
        shutil.rmtree(tmp_data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
