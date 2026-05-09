# SPDX-License-Identifier: Apache-2.0
"""Launcher tests — port resolution + readiness probe.

We don't actually launch Claude Desktop here; instead we exercise:
- _port_is_free / _resolve_port (pure socket logic)
- _probe_cdp_ready (HTTP probe against a stub server)
- LauncherHandle lifecycle (mock subprocess)
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from contextlib import closing
from unittest.mock import MagicMock

import pytest

from pce_app_launcher.claude_desktop import launcher
from pce_app_launcher.claude_desktop.detector import ClaudeDesktopInstall


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_port_is_free_returns_true_for_random_port():
    # Random unused port should be free
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # After releasing, port should be free
    assert launcher._port_is_free(port)


def test_port_is_free_false_when_in_use():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        port = s.getsockname()[1]
        assert not launcher._port_is_free(port)
    finally:
        s.close()


def test_resolve_port_picks_alternative_when_busy():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        busy_port = s.getsockname()[1]
        result = launcher._resolve_port(busy_port, auto_pick_port=True)
        assert result != busy_port
        assert isinstance(result, int)
        assert 1024 <= result <= 65535
    finally:
        s.close()


def test_resolve_port_raises_when_busy_and_auto_pick_off():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        busy_port = s.getsockname()[1]
        with pytest.raises(OSError):
            launcher._resolve_port(busy_port, auto_pick_port=False)
    finally:
        s.close()


# ---------------------------------------------------------------------------
# CDP readiness probe — uses a stub HTTP server
# ---------------------------------------------------------------------------


class _CDPStubHandler(http.server.BaseHTTPRequestHandler):
    """Minimal stub of /json/version that Chromium exposes."""

    def log_message(self, *_args):
        pass  # silence

    def do_GET(self):  # noqa: N802
        if self.path == "/json/version":
            payload = json.dumps({
                "Browser": "Chrome/127.0.0.0",
                "Protocol-Version": "1.3",
                "User-Agent": "stub",
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def cdp_stub():
    server = http.server.HTTPServer(("127.0.0.1", 0), _CDPStubHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_cdp_ready_true_when_stub_responds(cdp_stub):
    assert launcher._probe_cdp_ready(cdp_stub) is True


def test_probe_cdp_ready_false_for_unreachable_endpoint():
    # Pick a port that's almost certainly nothing — port 1 is privileged
    assert launcher._probe_cdp_ready("http://127.0.0.1:1") is False


def test_probe_cdp_ready_false_when_response_not_json():
    # Stand up a bare server that returns 200 plain text
    class HTML(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a): pass
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html></html>")
    server = http.server.HTTPServer(("127.0.0.1", 0), HTML)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        assert launcher._probe_cdp_ready(f"http://127.0.0.1:{port}") is False
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# LauncherHandle lifecycle
# ---------------------------------------------------------------------------


def test_launcher_handle_terminate_idempotent():
    proc = MagicMock()
    proc.poll.return_value = 0  # already exited
    install = ClaudeDesktopInstall(
        exe_path=__import__("pathlib").Path(__file__),  # any existing file
        version="x",
        platform="Test",
        install_root=None,
    )
    handle = launcher.LauncherHandle(
        process=proc,
        cdp_endpoint="http://127.0.0.1:9222",
        debug_port=9222,
        install=install,
    )
    # is_running == False because poll returned non-None
    assert handle.is_running() is False
    # terminate must not raise
    handle.terminate()
    handle.terminate()


def test_launcher_handle_terminate_kills_when_terminate_times_out():
    proc = MagicMock()
    poll_results = iter([None, None, 0])  # alive, alive, dead after kill

    def poll_side_effect():
        try:
            return next(poll_results)
        except StopIteration:
            return 0

    proc.poll.side_effect = poll_side_effect
    proc.wait.side_effect = __import__("subprocess").TimeoutExpired(cmd="x", timeout=5)

    install = ClaudeDesktopInstall(
        exe_path=__import__("pathlib").Path(__file__),
        version="x", platform="Test", install_root=None,
    )
    handle = launcher.LauncherHandle(
        process=proc,
        cdp_endpoint="http://127.0.0.1:9222",
        debug_port=9222,
        install=install,
    )
    handle.terminate(timeout=0.1)
    proc.kill.assert_called_once()


def test_launch_raises_when_exe_missing(tmp_path):
    missing = tmp_path / "nope.exe"
    install = ClaudeDesktopInstall(
        exe_path=missing,
        version=None,
        platform="Windows",
        install_root=tmp_path,
    )
    with pytest.raises(FileNotFoundError):
        launcher.launch_claude_desktop(install, ready_timeout=1.0)
