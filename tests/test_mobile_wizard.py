# SPDX-License-Identifier: Apache-2.0
"""Tests for the P4.5 mobile onboarding wizard.

Scope:
- ``get_lan_ip`` returns a string (real probe + forced-fallback branch).
- ``get_setup_info`` emits the documented envelope.
- ``render_qr_ascii`` works both with and without the optional
  ``qrcode`` package (monkey-patched probe).
- ``render_qr_png`` surfaces the structured error envelope when deps
  are missing.
- HTTP endpoints: info / qr.txt always 200; qr.png → 200 when deps
  present, 503 when missing.
- CLI ``main()`` in ``--json`` mode prints a parsable payload and exits
  cleanly.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# LAN IP + setup info
# ---------------------------------------------------------------------------

class TestGetLanIp:

    def test_returns_string(self):
        from pce_core.mobile_wizard import get_lan_ip
        ip = get_lan_ip()
        assert isinstance(ip, str) and ip.count(".") == 3

    def test_falls_back_to_localhost_on_socket_error(self, monkeypatch):
        import socket

        class _BrokenSock:
            def __init__(self, *a, **kw): pass
            def connect(self, *a, **kw): raise OSError("no network")
            def getsockname(self): return ("0.0.0.0", 0)
            def close(self): pass

        monkeypatch.setattr(socket, "socket", lambda *a, **kw: _BrokenSock())
        from pce_core.mobile_wizard import get_lan_ip
        assert get_lan_ip() == "127.0.0.1"


class TestGetSetupInfo:

    def test_envelope_keys(self):
        from pce_core.mobile_wizard import get_setup_info
        info = get_setup_info(lan_ip="192.168.1.42", port=8080)
        for key in (
            "proxy_host", "proxy_port", "proxy_endpoint",
            "cert_url", "setup_url", "instructions",
        ):
            assert key in info, f"missing key: {key}"
        assert info["proxy_host"] == "192.168.1.42"
        assert info["proxy_port"] == 8080
        assert info["proxy_endpoint"] == "192.168.1.42:8080"
        assert "mitm.it" in info["cert_url"]
        assert info["setup_url"].startswith("pce-proxy://setup?")
        assert "192.168.1.42" in info["setup_url"]
        assert isinstance(info["instructions"], list)
        assert len(info["instructions"]) >= 3

    def test_defaults_use_configured_port(self):
        from pce_core.mobile_wizard import get_setup_info
        from pce_core.config import PROXY_LISTEN_PORT
        info = get_setup_info()
        assert info["proxy_port"] == PROXY_LISTEN_PORT


# ---------------------------------------------------------------------------
# QR rendering — graceful fallback
# ---------------------------------------------------------------------------

class TestRenderQrAscii:

    def test_returns_fallback_banner_without_qrcode(self, monkeypatch):
        from pce_core import mobile_wizard
        monkeypatch.setattr(mobile_wizard, "_qrcode_available", lambda: False)
        out = mobile_wizard.render_qr_ascii(
            mobile_wizard.get_setup_info(lan_ip="10.0.0.5", port=8080),
        )
        assert "QR rendering disabled" in out
        assert "10.0.0.5:8080" in out
        assert "mitm.it" in out

    def test_uses_qrcode_when_available(self, monkeypatch):
        pytest.importorskip("qrcode")
        from pce_core import mobile_wizard
        monkeypatch.setattr(mobile_wizard, "_qrcode_available", lambda: True)
        out = mobile_wizard.render_qr_ascii(
            mobile_wizard.get_setup_info(lan_ip="10.0.0.5"),
        )
        # ASCII QR art uses block characters (█ or ▀/▄); the banner
        # fallback strings must not appear.
        assert "QR rendering disabled" not in out
        assert out.strip(), "expected non-empty ASCII art"


class TestRenderQrPng:

    def test_returns_structured_error_without_deps(self, monkeypatch):
        from pce_core import mobile_wizard
        monkeypatch.setattr(mobile_wizard, "_qrcode_available", lambda: False)
        monkeypatch.setattr(mobile_wizard, "_pillow_available", lambda: False)
        out = mobile_wizard.render_qr_png(
            mobile_wizard.get_setup_info(lan_ip="10.0.0.5"),
        )
        assert out["ok"] is False
        assert out["error"] == "qrcode_or_pillow_not_installed"
        assert "qrcode" in out["hint"]

    def test_renders_png_when_deps_present(self, monkeypatch):
        pytest.importorskip("qrcode")
        pytest.importorskip("PIL")
        from pce_core import mobile_wizard
        out = mobile_wizard.render_qr_png(
            mobile_wizard.get_setup_info(lan_ip="10.0.0.5"),
        )
        assert out["ok"] is True
        assert out["content_type"] == "image/png"
        assert out["png_bytes"].startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class TestMobileWizardHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_info_endpoint_200(self):
        with self._client() as client:
            resp = client.get("/api/v1/mobile_wizard/info?ip=10.1.2.3&port=8080")
        assert resp.status_code == 200
        body = resp.json()
        assert body["proxy_host"] == "10.1.2.3"
        assert body["proxy_port"] == 8080

    def test_qr_txt_always_200(self):
        with self._client() as client:
            resp = client.get("/api/v1/mobile_wizard/qr.txt?ip=10.1.2.3")
        assert resp.status_code == 200
        assert "10.1.2.3" in resp.text or "pce-proxy" in resp.text

    def test_qr_png_503_when_deps_missing(self, monkeypatch):
        from pce_core import mobile_wizard
        monkeypatch.setattr(mobile_wizard, "_qrcode_available", lambda: False)
        monkeypatch.setattr(mobile_wizard, "_pillow_available", lambda: False)
        with self._client() as client:
            resp = client.get("/api/v1/mobile_wizard/qr.png")
        assert resp.status_code == 503

    def test_qr_png_200_when_deps_present(self):
        pytest.importorskip("qrcode")
        pytest.importorskip("PIL")
        with self._client() as client:
            resp = client.get("/api/v1/mobile_wizard/qr.png?ip=10.1.2.3")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    def test_port_param_validated(self):
        with self._client() as client:
            resp = client.get("/api/v1/mobile_wizard/info?port=99999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:

    def test_json_mode_prints_parsable_payload(self, capsys):
        from pce_core.mobile_wizard import main
        rc = main(["--json", "--ip", "10.1.2.3", "--port", "9999"])
        assert rc == 0
        out = capsys.readouterr().out
        payload: Any = json.loads(out)
        assert payload["proxy_host"] == "10.1.2.3"
        assert payload["proxy_port"] == 9999

    def test_ascii_mode_prints_instructions(self, capsys):
        from pce_core.mobile_wizard import main
        rc = main(["--ip", "10.1.2.3", "--port", "8080"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "PCE mobile capture setup" in out
        assert "10.1.2.3:8080" in out
        assert "mitm.it" in out
