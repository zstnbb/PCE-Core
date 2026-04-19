# SPDX-License-Identifier: Apache-2.0
"""P5.A-5 — Onboarding status aggregator + /verify endpoint tests.

The aggregator composes five independent probes (CA trust, system proxy,
Electron apps, bypass list, captures/pinning DB counts). These tests
mostly exercise the composition seams — the underlying modules have
their own dedicated suites — plus the partial-failure behaviour (one
sub-probe raising must not bring down the whole snapshot).
"""
from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reloaded_server(tmp_path: Path, monkeypatch):
    """Reload the server + DB + supporting modules with an isolated DATA_DIR.

    Yields ``(server_module, db_module, onboarding_module, app_bypass_module)``
    so tests can seed DB rows / bypass entries / trust-store fakes
    against the same reloaded state.
    """
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("PCE_REDACT_PATTERNS", raising=False)

    import pce_core.app_bypass as _bypass
    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.onboarding as _onboarding
    import pce_core.otel_exporter as _otel
    import pce_core.redact as _redact
    import pce_core.server as _server

    importlib.reload(_cfg)
    importlib.reload(_db)
    importlib.reload(_bypass)
    importlib.reload(_redact)
    importlib.reload(_onboarding)
    _otel._initialised = False
    _otel._enabled = False
    importlib.reload(_server)
    _db.init_db()
    return _server, _db, _onboarding, _bypass


def _install_stub_probes(monkeypatch, *,
                          cert_count=0,
                          proxy_enabled=False,
                          detected_apps=None,
                          known_apps=None):
    """Replace the cert / proxy / electron probes with deterministic fakes.

    Letting the real probes hit the host OS makes CI non-deterministic
    (different Windows agents return different cert lists). Each test
    opts in to the overrides it needs.
    """
    from types import SimpleNamespace

    import pce_core.onboarding as _onboarding

    # Rebuild the lazily-imported modules exactly as build_status() does,
    # then stub the individual functions it calls.
    from pce_core import cert_wizard as cw
    from pce_core import proxy_toggle as pt
    from pce_core import electron_proxy as ep

    class _FakePlatform:
        value = "windows"

    class _FakeCert:
        def __init__(self, i):
            self.subject = f"CN=mitmproxy-{i}"
            self.thumbprint_sha1 = f"{i:040x}"
            self.store_id = "CurrentUser"
            self.source_path = "C:/fake/ca.pem"
            self.not_after_iso = "2030-01-01T00:00:00Z"

    monkeypatch.setattr(cw, "detect_platform", lambda: _FakePlatform())
    monkeypatch.setattr(cw, "list_ca", lambda: [_FakeCert(i) for i in range(cert_count)])
    monkeypatch.setattr(cw, "default_ca_path", lambda: Path("C:/fake/ca.pem"))

    fake_state = SimpleNamespace(
        platform=_FakePlatform(),
        enabled=proxy_enabled,
        host="127.0.0.1" if proxy_enabled else None,
        port=8180 if proxy_enabled else None,
        bypass=[],
        raw={},
    )
    monkeypatch.setattr(pt, "get_proxy_state", lambda: fake_state)

    monkeypatch.setattr(
        ep, "detect_installed_apps",
        lambda: list(detected_apps if detected_apps is not None else []),
    )
    if known_apps is not None:
        monkeypatch.setattr(ep, "KNOWN_APPS", tuple(known_apps))


# ---------------------------------------------------------------------------
# build_status() — shape + readiness
# ---------------------------------------------------------------------------

class TestBuildStatus:
    def test_empty_db_minimal_system(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=0, proxy_enabled=False)

        status = _onboarding.build_status()

        # Every section is present even when nothing is set up.
        for key in ("ca", "proxy", "apps", "captures", "pinning",
                    "ready", "generated_at_epoch"):
            assert key in status, f"missing section: {key}"

        assert status["ca"]["ok"] is False
        assert status["ca"]["installed_count"] == 0
        assert status["proxy"]["ok"] is False
        assert status["captures"]["total"] == 0
        assert status["captures"]["recent_count"] == 0
        assert status["pinning"]["has_warnings"] is False
        assert status["ready"] is False

    def test_ready_true_when_ca_and_proxy_both_ok(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=1, proxy_enabled=True)

        status = _onboarding.build_status()
        assert status["ca"]["ok"] is True
        assert status["ca"]["installed_count"] == 1
        assert status["proxy"]["ok"] is True
        assert status["proxy"]["host"] == "127.0.0.1"
        assert status["proxy"]["port"] == 8180
        assert status["ready"] is True

    def test_ready_false_when_only_ca_ok(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=1, proxy_enabled=False)
        assert _onboarding.build_status()["ready"] is False

    def test_ready_false_when_only_proxy_ok(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=0, proxy_enabled=True)
        assert _onboarding.build_status()["ready"] is False

    def test_detected_apps_annotated_with_bypass_flag(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server

        _bypass.save_bypass(["chatgpt-desktop"])
        _install_stub_probes(
            monkeypatch,
            cert_count=1, proxy_enabled=True,
            detected_apps=[
                {"name": "chatgpt-desktop",
                 "display_name": "ChatGPT Desktop",
                 "path": "C:/fake/chatgpt.exe",
                 "ai_domains": ["chatgpt.com"]},
                {"name": "cursor",
                 "display_name": "Cursor",
                 "path": "C:/fake/cursor.exe",
                 "ai_domains": ["api.openai.com"]},
            ],
        )

        status = _onboarding.build_status()
        apps = {a["name"]: a for a in status["apps"]["detected"]}
        assert apps["chatgpt-desktop"]["bypassed"] is True
        assert apps["cursor"]["bypassed"] is False
        assert status["apps"]["detected_count"] == 2
        assert status["apps"]["bypassed_count"] == 1

    def test_missing_apps_surfaced(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        from pce_core.electron_proxy import KNOWN_APPS

        _install_stub_probes(
            monkeypatch, cert_count=1, proxy_enabled=True,
            detected_apps=[],  # nothing on disk
        )

        status = _onboarding.build_status()
        missing_names = {a["name"] for a in status["apps"]["missing"]}
        # Every KNOWN_APP should appear in "missing" since detected=[].
        for known in KNOWN_APPS:
            assert known.name in missing_names

    def test_recent_captures_counted(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=1, proxy_enabled=True)

        _db.insert_capture(
            direction="request",
            pair_id="pair-onb-1",
            host="api.openai.com",
            path="/v1/chat",
            method="POST",
            provider="openai",
            headers_redacted_json="{}",
            body_text_or_json="{}",
            body_format="json",
        )

        status = _onboarding.build_status()
        assert status["captures"]["total"] >= 1
        assert status["captures"]["recent_count"] >= 1
        assert status["captures"]["has_recent"] is True
        assert status["captures"]["latest_ts_epoch"] is not None

    def test_pinning_warnings_aggregated(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=1, proxy_enabled=True)

        # 3 client-side TLS failures on the same host ≥ default threshold
        for _ in range(3):
            _db.record_tls_failure(
                host="pinned.example.com",
                error_category="client_rejected_cert",
                error_message="TLSV1_ALERT_UNKNOWN_CA",
            )

        status = _onboarding.build_status(
            pinning_window_hours=1.0, pinning_min_failures=3,
        )
        assert status["pinning"]["suspected_hosts_last_hour"] == 1
        assert status["pinning"]["has_warnings"] is True

    def test_cert_probe_failure_degrades_gracefully(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=0, proxy_enabled=True)

        from pce_core import cert_wizard as cw

        def _boom():
            raise RuntimeError("simulated trust-store I/O failure")

        monkeypatch.setattr(cw, "list_ca", _boom)

        status = _onboarding.build_status()
        # CA section carries the error but other sections still populate.
        assert status["ca"]["ok"] is False
        assert "error" in status["ca"]
        assert "simulated trust-store" in status["ca"]["error"]
        assert status["proxy"]["ok"] is True  # unaffected
        assert "captures" in status
        assert status["ready"] is False


# ---------------------------------------------------------------------------
# verify_capture_since() — poll-friendly check
# ---------------------------------------------------------------------------

class TestVerifyCaptureSince:
    def test_no_captures_returns_false(self, reloaded_server):
        _server, _db, _onboarding, _bypass = reloaded_server
        r = _onboarding.verify_capture_since(time.time())
        assert r["captured"] is False
        assert r["count"] == 0

    def test_capture_after_timestamp_counted(self, reloaded_server):
        _server, _db, _onboarding, _bypass = reloaded_server
        # Use a timestamp from the past to ensure the insert lands after it.
        t0 = time.time() - 60.0

        _db.insert_capture(
            direction="request",
            pair_id="pair-verify-1",
            host="api.openai.com",
            path="/v1/chat",
            method="POST",
            provider="openai",
            headers_redacted_json="{}",
            body_text_or_json="{}",
            body_format="json",
        )
        r = _onboarding.verify_capture_since(t0)
        assert r["captured"] is True
        assert r["count"] >= 1
        assert r["latest_ts_epoch"] is not None

    def test_capture_before_timestamp_excluded(self, reloaded_server):
        _server, _db, _onboarding, _bypass = reloaded_server

        _db.insert_capture(
            direction="request",
            pair_id="pair-verify-2",
            host="api.openai.com",
            path="/v1/chat",
            method="POST",
            provider="openai",
            headers_redacted_json="{}",
            body_text_or_json="{}",
            body_format="json",
        )
        # Poll at "now" — nothing inserted *after* this instant.
        r = _onboarding.verify_capture_since(time.time() + 1)
        assert r["captured"] is False

    def test_negative_since_coerced_to_zero(self, reloaded_server):
        _server, _db, _onboarding, _bypass = reloaded_server
        r = _onboarding.verify_capture_since(-123.0)
        # Should not raise + returns a valid shape. since_epoch normalised.
        assert r["since_epoch"] == 0
        assert "captured" in r


# ---------------------------------------------------------------------------
# HTTP endpoints: /api/v1/onboarding/status + /verify
# ---------------------------------------------------------------------------

class TestOnboardingHTTPAPI:
    def test_status_shape(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=1, proxy_enabled=True)

        from fastapi.testclient import TestClient
        client = TestClient(_server.app)
        with client:
            r = client.get("/api/v1/onboarding/status")
            assert r.status_code == 200
            body = r.json()
            assert body["ready"] is True
            assert body["ca"]["ok"] is True
            assert body["proxy"]["ok"] is True

    def test_verify_query_param_required(self, reloaded_server):
        _server, _db, _onboarding, _bypass = reloaded_server
        from fastapi.testclient import TestClient
        client = TestClient(_server.app)
        with client:
            r = client.get("/api/v1/onboarding/verify")
            assert r.status_code == 422  # since_epoch is required

    def test_verify_returns_true_after_capture(self, reloaded_server):
        _server, _db, _onboarding, _bypass = reloaded_server
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        t0 = time.time() - 60.0
        with client:
            _db.insert_capture(
                direction="request",
                pair_id="pair-http-verify-1",
                host="api.openai.com",
                path="/v1/chat",
                method="POST",
                provider="openai",
                headers_redacted_json="{}",
                body_text_or_json="{}",
                body_format="json",
            )
            r = client.get(f"/api/v1/onboarding/verify?since_epoch={t0}")
            assert r.status_code == 200
            body = r.json()
            assert body["captured"] is True
            assert body["count"] >= 1

    def test_status_pinning_window_bounds_enforced(self, reloaded_server, monkeypatch):
        _server, _db, _onboarding, _bypass = reloaded_server
        _install_stub_probes(monkeypatch, cert_count=1, proxy_enabled=True)
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        with client:
            r = client.get("/api/v1/onboarding/status?pinning_window_hours=0")
            assert r.status_code == 422
            r = client.get("/api/v1/onboarding/status?pinning_min_failures=0")
            assert r.status_code == 422
