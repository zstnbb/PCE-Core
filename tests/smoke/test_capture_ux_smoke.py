"""P2 capture-UX smoke — hits the FastAPI TestClient end-to-end.

Covers the /api/v1/cert, /proxy, /supervisor and /sdk routes against a
real server lifespan. Platform-specific effects (certutil, networksetup,
gsettings, winreg) are verified only through *dry_run* so the suite never
touches the host's real trust store or proxy config.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture: fresh server per test with a tmp SQLite DB + OTel / retention off.
# ---------------------------------------------------------------------------

def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PCE_RETENTION_DAYS", "0")
    monkeypatch.setenv("PCE_RETENTION_MAX_ROWS", "0")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    # Reload the config + server so the new env is picked up.
    import importlib
    import pce_core.config as _cfg
    import pce_core.server as _srv
    importlib.reload(_cfg)
    importlib.reload(_srv)
    client = TestClient(_srv.app)
    client.__enter__()
    return client, _srv


# ---------------------------------------------------------------------------
# /api/v1/cert
# ---------------------------------------------------------------------------

def test_cert_list_reports_platform_and_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/v1/cert")
        assert r.status_code == 200
        data = r.json()
        assert data["platform"] in {"windows", "macos", "linux", "unknown"}
        assert data["default_ca_path"].endswith("mitmproxy-ca-cert.pem")
        assert isinstance(data["installed"], list)
    finally:
        client.__exit__(None, None, None)


def test_cert_install_dry_run_returns_elevation_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        # Generate a synthetic PEM with "mitmproxy" CN so the safety guard
        # accepts it.
        import base64
        blob = b"DUMMYDER" + b"CN=mitmproxy,\x00" + b"x" * 64
        pem = (b"-----BEGIN CERTIFICATE-----\n"
               + base64.b64encode(blob) + b"\n"
               + b"-----END CERTIFICATE-----\n")
        cert = tmp_path / "ca.pem"
        cert.write_bytes(pem)

        r = client.post("/api/v1/cert/install", json={
            "cert_path": str(cert),
            "dry_run": True,
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["dry_run"] is True
        assert data["ok"] is False
        # Dry-run implies elevation is required on every platform.
        assert data["needs_elevation"] is True
        assert data["elevated_cmd"], "dry_run should surface the elevated command"
    finally:
        client.__exit__(None, None, None)


def test_cert_install_rejects_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/cert/install", json={
            "cert_path": str(tmp_path / "no-such.pem"),
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "not found" in data["message"]
    finally:
        client.__exit__(None, None, None)


def test_cert_uninstall_requires_thumbprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/cert/uninstall", json={})
        assert r.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_cert_export_copies_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        # Stage a fake CA under a fake HOME so the default lookup finds it.
        fake_home = tmp_path / "fakehome"
        (fake_home / ".mitmproxy").mkdir(parents=True)
        import base64
        blob = b"CN=mitmproxy\x00" + b"y" * 64
        pem = (b"-----BEGIN CERTIFICATE-----\n"
               + base64.b64encode(blob) + b"\n"
               + b"-----END CERTIFICATE-----\n")
        (fake_home / ".mitmproxy" / "mitmproxy-ca-cert.pem").write_bytes(pem)
        monkeypatch.setattr(Path, "home",
                            classmethod(lambda cls: fake_home))

        dest = tmp_path / "exported.pem"
        r = client.post("/api/v1/cert/export", json={"dest": str(dest)})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert dest.exists()
    finally:
        client.__exit__(None, None, None)


def test_cert_regenerate_dry_run_is_a_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/cert/regenerate", json={"dry_run": True})
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert "paths" in data["extra"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# /api/v1/proxy
# ---------------------------------------------------------------------------

def test_proxy_state_reports_host_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/v1/proxy")
        assert r.status_code == 200
        data = r.json()
        assert data["platform"] in {"windows", "macos", "linux", "unknown"}
        assert isinstance(data["bypass"], list)
    finally:
        client.__exit__(None, None, None)


def test_proxy_enable_dry_run_does_not_mutate_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/proxy/enable", json={
            "host": "127.0.0.1",
            "port": 8080,
            "dry_run": True,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        # The payload should describe *some* intended effect, shape depends
        # on platform, but ok must be False in dry-run mode.
        assert data["ok"] is False
    finally:
        client.__exit__(None, None, None)


def test_proxy_disable_dry_run_is_harmless(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/proxy/disable", json={"dry_run": True})
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
    finally:
        client.__exit__(None, None, None)


def test_proxy_enable_rejects_bad_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/proxy/enable", json={
            "host": "127.0.0.1",
            "port": 0,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert "invalid" in data["message"].lower()
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# /api/v1/supervisor
# ---------------------------------------------------------------------------

def test_supervisor_status_reports_empty_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/v1/supervisor")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        assert data["processes"] == []
    finally:
        client.__exit__(None, None, None)


def test_supervisor_restart_unknown_returns_409_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/supervisor/ghost/restart")
        assert r.status_code == 409
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# /api/v1/sdk/litellm
# ---------------------------------------------------------------------------

def test_sdk_litellm_status_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.get("/api/v1/sdk/litellm")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        assert data["port"] is None
        assert isinstance(data["litellm_available"], bool)
    finally:
        client.__exit__(None, None, None)


def test_sdk_litellm_start_clean_error_when_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When LiteLLM is not installed, /start returns a structured error."""
    client, srv = _make_client(tmp_path, monkeypatch)
    try:
        # Force the not-installed path so this test runs the same way in CI
        # regardless of the developer machine.
        from pce_core.sdk_capture_litellm import bridge as _bridge_mod
        monkeypatch.setattr(_bridge_mod, "LITELLM_AVAILABLE", False)
        monkeypatch.setattr(_bridge_mod, "_has_litellm_cli", lambda: False)

        r = client.post("/api/v1/sdk/litellm/start", json={"port": 19999})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is False
        assert data["error"] == "litellm_not_installed"
    finally:
        client.__exit__(None, None, None)


def test_sdk_litellm_stop_without_start_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    client, _srv = _make_client(tmp_path, monkeypatch)
    try:
        r = client.post("/api/v1/sdk/litellm/stop")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["running"] is False
    finally:
        client.__exit__(None, None, None)
