# SPDX-License-Identifier: Apache-2.0
"""Tests for the P3.5 Phoenix integration layer.

Phoenix itself is an optional dependency and requires a subprocess, so
these tests intentionally focus on the lifecycle logic that we own:

- PhoenixManager degrades cleanly when arize-phoenix isn't installed.
- get_status reflects subprocess death / port-in-use properly.
- The HTTP surface returns sensible defaults when no manager exists.
- Starting against an already-bound port attaches to the external server
  instead of spawning (and still tries to wire OTLP).
- Stopping runs without raising even if we never started.

Async tests use the project-wide ``asyncio.new_event_loop().run_until_complete``
pattern (see ``tests/test_supervisor.py``) so the suite runs without a
``pytest-asyncio`` install.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from pathlib import Path

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _DrainingListener:
    """Listening socket with a draining accept thread.

    We background-accept so repeated ``_is_port_in_use`` probes don't
    saturate the kernel backlog (which on Windows would silently drop
    subsequent connect attempts and make tests flaky).

    Usage mirrors ``socket.socket``: call ``close()`` to stop the drain
    thread and free the port.
    """

    def __init__(self, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(16)
        self._sock.settimeout(0.1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
                conn.close()
            except (socket.timeout, OSError):
                if self._stop.is_set():
                    return

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=1.0)


def _bind_listener(port: int) -> _DrainingListener:
    return _DrainingListener(port)


def _run(coro):
    """asyncio.run with a fresh event loop — matches the style used in
    ``tests/test_supervisor.py`` so the suite works without pytest-asyncio."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_otlp_endpoint_for_matches_phoenix_default(self):
        from pce_core.phoenix_integration import otlp_endpoint_for
        assert otlp_endpoint_for("127.0.0.1", 6006) == "http://127.0.0.1:6006/v1/traces"

    def test_is_port_in_use_detects_listener(self):
        from pce_core.phoenix_integration import _is_port_in_use

        port = _find_free_port()
        sock = _bind_listener(port)
        try:
            assert _is_port_in_use("127.0.0.1", port) is True
        finally:
            sock.close()
        # Port closed → probe returns False.
        assert _is_port_in_use("127.0.0.1", port) is False


# ---------------------------------------------------------------------------
# PhoenixManager
# ---------------------------------------------------------------------------

class TestPhoenixManager:

    def test_status_defaults_before_start(self, isolated_data_dir):
        from pce_core.phoenix_integration import PhoenixManager

        mgr = PhoenixManager(port=_find_free_port())
        st = mgr.get_status()
        assert st["running"] is False
        assert st["otlp_wired"] is False
        assert st["ui_url"].startswith("http://127.0.0.1:")
        assert st["otlp_endpoint"].endswith("/v1/traces")

    def test_start_without_phoenix_available_returns_error(
        self, isolated_data_dir, monkeypatch,
    ):
        import pce_core.phoenix_integration as phx
        monkeypatch.setattr(phx, "PHOENIX_AVAILABLE", False, raising=False)
        monkeypatch.setattr(phx, "_has_phoenix_cli", lambda: False, raising=False)

        mgr = phx.PhoenixManager(port=_find_free_port())
        result = _run(mgr.start())
        assert result["ok"] is False
        assert result["error"] == "phoenix_not_installed"
        assert mgr.get_status()["running"] is False

    def test_stop_without_start_is_safe(self, isolated_data_dir):
        from pce_core.phoenix_integration import PhoenixManager

        mgr = PhoenixManager(port=_find_free_port())
        result = _run(mgr.stop())
        assert result["ok"] is True
        assert result["running"] is False

    def test_attaches_to_external_phoenix_on_busy_port(
        self, isolated_data_dir, monkeypatch,
    ):
        """If the port is already bound, we treat it as an external Phoenix."""
        import pce_core.phoenix_integration as phx

        # Pretend Phoenix *is* available so we skip the not-installed path.
        monkeypatch.setattr(phx, "PHOENIX_AVAILABLE", True, raising=False)

        # Stub out OTLP wiring so we don't actually launch anything.
        wired = {"count": 0}
        def fake_configure(endpoint=None, protocol=None, force=False, **_kw):
            wired["count"] += 1
            wired["endpoint"] = endpoint
            return True
        monkeypatch.setattr(
            phx.otel_exporter, "configure_otlp_exporter",
            fake_configure, raising=False,
        )

        port = _find_free_port()
        listener = _bind_listener(port)
        try:
            mgr = phx.PhoenixManager(port=port)
            result = _run(mgr.start())
            assert result["ok"] is True
            assert result.get("attached_to_existing") is True
            assert result["otlp_wired"] is True
            assert wired["count"] == 1
            assert wired["endpoint"].endswith(f":{port}/v1/traces")
        finally:
            listener.close()

    def test_start_twice_is_idempotent(self, isolated_data_dir, monkeypatch):
        import pce_core.phoenix_integration as phx
        monkeypatch.setattr(phx, "PHOENIX_AVAILABLE", True, raising=False)
        monkeypatch.setattr(
            phx.otel_exporter, "configure_otlp_exporter",
            lambda **_: True, raising=False,
        )

        port = _find_free_port()
        listener = _bind_listener(port)
        try:
            mgr = phx.PhoenixManager(port=port)
            r1 = _run(mgr.start())
            r2 = _run(mgr.start())
            assert r1["ok"] is True
            assert r2["ok"] is True
            # Manager remains in attached state.
            assert mgr.get_status()["running"] is True
        finally:
            listener.close()

    def test_reprobe_external_flips_to_false_when_port_closes(
        self, isolated_data_dir, monkeypatch,
    ):
        """Opt-in reprobe detects external Phoenix death. ``get_status`` on its
        own is intentionally side-effect free (see TASK-005 §5.5 rationale)."""
        import pce_core.phoenix_integration as phx
        monkeypatch.setattr(phx, "PHOENIX_AVAILABLE", True, raising=False)
        monkeypatch.setattr(
            phx.otel_exporter, "configure_otlp_exporter",
            lambda **_: True, raising=False,
        )

        port = _find_free_port()
        listener = _bind_listener(port)
        mgr = phx.PhoenixManager(port=port)
        try:
            _run(mgr.start())
            assert mgr.get_status()["running"] is True
            assert mgr.reprobe_external() is True
        finally:
            listener.close()

        # External Phoenix is gone now — get_status is still cached,
        # but the explicit reprobe flips the state.
        assert mgr.reprobe_external() is False
        assert mgr.get_status()["running"] is False


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class TestPhoenixHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_status_returns_defaults_before_any_start_call(self, isolated_data_dir):
        with self._client() as client:
            resp = client.get("/api/v1/phoenix")
        assert resp.status_code == 200
        body = resp.json()
        assert body["running"] is False
        assert body["port"] == 6006
        assert body["ui_url"].startswith("http://")
        assert body["otlp_endpoint"].endswith("/v1/traces")
        assert "phoenix_available" in body

    def test_stop_without_start_is_safe(self, isolated_data_dir):
        with self._client() as client:
            resp = client.post("/api/v1/phoenix/stop")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["running"] is False

    def test_start_when_phoenix_missing_returns_structured_error(
        self, isolated_data_dir, monkeypatch,
    ):
        import pce_core.phoenix_integration as phx
        monkeypatch.setattr(phx, "PHOENIX_AVAILABLE", False, raising=False)
        monkeypatch.setattr(phx, "_has_phoenix_cli", lambda: False, raising=False)

        with self._client() as client:
            resp = client.post("/api/v1/phoenix/start", json={"port": _find_free_port()})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["error"] == "phoenix_not_installed"
        assert body["phoenix_available"] is False

    def test_start_with_external_phoenix_on_port_attaches(
        self, isolated_data_dir, monkeypatch,
    ):
        import pce_core.phoenix_integration as phx

        monkeypatch.setattr(phx, "PHOENIX_AVAILABLE", True, raising=False)
        monkeypatch.setattr(
            phx.otel_exporter, "configure_otlp_exporter",
            lambda **_: True, raising=False,
        )

        port = _find_free_port()
        listener = _bind_listener(port)
        try:
            with self._client() as client:
                resp = client.post("/api/v1/phoenix/start", json={"port": port})
            assert resp.status_code == 200
            body = resp.json()
            assert body["ok"] is True
            assert body.get("attached_to_existing") is True
            assert body["otlp_wired"] is True
            assert body["running"] is True
        finally:
            listener.close()
