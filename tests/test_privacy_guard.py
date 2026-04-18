# SPDX-License-Identifier: Apache-2.0
"""P5.A-10 — Privacy middleware tests.

Covers:

- ``PrivacyGuard`` pure-function behaviour: each default pattern scrubs
  its target shape and leaves clean text alone.
- Header-value redaction through the JSON-string entry point, including
  forgiving behaviour on malformed input.
- ``PCE_REDACT_PATTERNS`` env var: valid custom regex is applied, invalid
  regex is logged + skipped (doesn't raise).
- FastAPI dependency wiring: ``/api/v1/captures`` (v1) and
  ``/api/v1/captures/v2`` both run header + body through the active
  guard; ``app.dependency_overrides`` swaps the guard at test time.
- ``redact_body_secrets`` module-level shortcut for non-HTTP callers
  (proxy addon).
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Pure PrivacyGuard behaviour
# ---------------------------------------------------------------------------

class TestPrivacyGuardDefaults:
    def _guard(self):
        from pce_core.redact import PrivacyGuard
        return PrivacyGuard.default()

    def test_bearer_token_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("Authorization: Bearer abcdefgh12345678.xyz")
        assert "abcdefgh12345678" not in out
        assert "Bearer [REDACTED]" in out

    def test_basic_auth_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("basic dXNlcjpwYXNzd29yZA==")
        assert "dXNlcjpwYXNz" not in out
        assert "Basic [REDACTED]" in out

    def test_jwt_scrubbed(self):
        g = self._guard()
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        out = g.scrub_body(f"token={jwt}")
        assert "eyJhbGc" not in out
        assert "[REDACTED_JWT]" in out

    def test_openai_key_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("key=sk-proj-abcdefghijklmnopqrstuv")
        assert "sk-proj-abcdefghijkl" not in out
        assert "[REDACTED_OPENAI_KEY]" in out

    def test_anthropic_key_scrubbed_before_openai(self):
        """Order matters: sk-ant-* must win over the generic sk-* rule."""
        g = self._guard()
        out = g.scrub_body("key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz")
        assert "[REDACTED_ANTHROPIC_KEY]" in out
        assert "[REDACTED_OPENAI_KEY]" not in out

    def test_google_api_key_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("GOOGLE_API_KEY=AIzaSyA-abcdefghijklmnopqrstuvwxyz123")
        assert "AIzaSyA" not in out
        assert "[REDACTED_GOOGLE_API_KEY]" in out

    def test_github_token_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("token=ghp_abcdefghijklmnopqrstu")
        assert "ghp_abcdef" not in out
        assert "[REDACTED_GITHUB_TOKEN]" in out

    def test_github_fine_grained_pat_scrubbed(self):
        g = self._guard()
        out = g.scrub_body(
            "tok=github_pat_11AAABBBB0aaabbbbcccc_ddddeeeeffffgggghhhh"
        )
        assert "github_pat_11AAABBBB" not in out
        assert "[REDACTED_GITHUB_TOKEN]" in out

    def test_stripe_key_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("stripe=sk_live_abcdefghijklmnopqrstuvwx")
        assert "sk_live_abcdefghij" not in out
        assert "[REDACTED_STRIPE_KEY]" in out

    def test_aws_access_key_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert "[REDACTED_AWS_KEY]" in out

    def test_slack_token_scrubbed(self):
        g = self._guard()
        out = g.scrub_body("slack=xoxb-1234567890-abcdefghij")
        assert "xoxb-1234567890" not in out
        assert "[REDACTED_SLACK_TOKEN]" in out

    def test_clean_prose_untouched(self):
        g = self._guard()
        prose = "hello world this is a perfectly normal sentence with no secrets"
        assert g.scrub_body(prose) == prose

    def test_empty_input_returns_empty_string(self):
        g = self._guard()
        assert g.scrub_body("") == ""
        assert g.scrub_body(None) == ""


# ---------------------------------------------------------------------------
# Header JSON redaction
# ---------------------------------------------------------------------------

class TestHeaderRedaction:
    def test_authorization_value_replaced(self):
        from pce_core.redact import redact_headers_json_str
        hjson = json.dumps({"Authorization": "Bearer secret-abc", "X-Trace": "ok"})
        out = json.loads(redact_headers_json_str(hjson))
        assert out["Authorization"] == "REDACTED"
        assert out["X-Trace"] == "ok"

    def test_case_insensitive_header_match(self):
        from pce_core.redact import redact_headers_json_str
        hjson = json.dumps({"authorization": "Bearer x", "CookiE": "session=y"})
        out = json.loads(redact_headers_json_str(hjson))
        assert out["authorization"] == "REDACTED"
        assert out["CookiE"] == "REDACTED"

    def test_none_returns_empty_json_dict(self):
        from pce_core.redact import redact_headers_json_str
        assert redact_headers_json_str(None) == "{}"
        assert redact_headers_json_str("") == "{}"

    def test_malformed_json_returned_unchanged(self):
        from pce_core.redact import redact_headers_json_str
        assert redact_headers_json_str("{not json") == "{not json"

    def test_non_dict_json_returned_unchanged(self):
        from pce_core.redact import redact_headers_json_str
        assert redact_headers_json_str("[1,2,3]") == "[1,2,3]"


# ---------------------------------------------------------------------------
# PCE_REDACT_PATTERNS env var
# ---------------------------------------------------------------------------

class TestEnvExtensibility:
    def test_valid_custom_pattern_applied(self, monkeypatch):
        monkeypatch.setenv("PCE_REDACT_PATTERNS", r"\bINTERNAL-\d{4}")
        import pce_core.redact as redact
        importlib.reload(redact)
        g = redact.PrivacyGuard.default()
        out = g.scrub_body("ticket=INTERNAL-1234 user=bob")
        assert "INTERNAL-1234" not in out
        assert "[REDACTED_CUSTOM]" in out

    def test_invalid_regex_skipped_not_raised(self, monkeypatch, caplog):
        monkeypatch.setenv("PCE_REDACT_PATTERNS", r"[unclosed,\bOK\d+")
        import pce_core.redact as redact
        importlib.reload(redact)
        g = redact.PrivacyGuard.default()
        # The valid half of the comma-separated list should still work.
        out = g.scrub_body("tag=OK42")
        assert "[REDACTED_CUSTOM]" in out

    def test_empty_env_is_noop(self, monkeypatch):
        monkeypatch.delenv("PCE_REDACT_PATTERNS", raising=False)
        import pce_core.redact as redact
        importlib.reload(redact)
        from pce_core.redact import _load_env_patterns
        assert _load_env_patterns() == []


# ---------------------------------------------------------------------------
# FastAPI dependency wiring (v1 + v2)
# ---------------------------------------------------------------------------

@pytest.fixture
def reloaded_server(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("PCE_REDACT_PATTERNS", raising=False)

    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.otel_exporter as _otel
    import pce_core.redact as _redact
    import pce_core.server as _server

    importlib.reload(_cfg)
    importlib.reload(_db)
    importlib.reload(_redact)
    _otel._initialised = False
    _otel._enabled = False
    importlib.reload(_server)
    _db.init_db()
    return _db, _redact, _server


class TestV1EndpointScrubs:
    def test_headers_and_body_scrubbed_before_insert(self, reloaded_server):
        _db, _redact, _server = reloaded_server
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        secret_body = json.dumps({
            "prompt": "hello",
            "api_key_leak": "sk-proj-abcdefghijklmnopqrstuv",
        })
        headers_json = json.dumps({
            "Authorization": "Bearer real-secret-token",
            "X-Request-ID": "req-123",
        })
        with client:
            r = client.post(
                "/api/v1/captures",
                json={
                    "source_type": "proxy",
                    "direction": "request",
                    "provider": "openai",
                    "host": "api.openai.com",
                    "path": "/v1/chat/completions",
                    "method": "POST",
                    "headers_json": headers_json,
                    "body_json": secret_body,
                    "body_format": "json",
                    "pair_id": "pair-redact-1",
                },
            )
            assert r.status_code == 201

            rows = client.get("/api/v1/captures/pair/pair-redact-1").json()
            assert len(rows) == 1
            stored = rows[0]

            stored_headers = json.loads(stored["headers_redacted_json"])
            assert stored_headers["Authorization"] == "REDACTED"
            assert stored_headers["X-Request-ID"] == "req-123"

            stored_body = stored["body_text_or_json"]
            assert "sk-proj-abcdefghijkl" not in stored_body
            assert "[REDACTED_OPENAI_KEY]" in stored_body

    def test_dependency_override_swaps_guard(self, reloaded_server):
        _db, _redact, _server = reloaded_server
        from fastapi.testclient import TestClient
        from pce_core.redact import PrivacyGuard, get_privacy_guard

        # Override with a no-op guard: no patterns, no header redaction.
        noop = PrivacyGuard(patterns=(), replacements=(), redact_headers=False)
        _server.app.dependency_overrides[get_privacy_guard] = lambda: noop

        try:
            client = TestClient(_server.app)
            with client:
                headers_json = json.dumps({"Authorization": "Bearer raw"})
                r = client.post(
                    "/api/v1/captures",
                    json={
                        "source_type": "proxy",
                        "direction": "request",
                        "provider": "openai",
                        "host": "api.openai.com",
                        "path": "/v1/chat/completions",
                        "method": "POST",
                        "headers_json": headers_json,
                        "body_json": "Bearer raw-token-should-survive",
                        "body_format": "json",
                        "pair_id": "pair-noop-1",
                    },
                )
                assert r.status_code == 201
                rows = client.get("/api/v1/captures/pair/pair-noop-1").json()
                stored = rows[0]

                # Headers AND body pass through untouched under the no-op guard.
                assert json.loads(stored["headers_redacted_json"])[
                    "Authorization"
                ] == "Bearer raw"
                assert "Bearer raw-token-should-survive" == stored["body_text_or_json"]
        finally:
            _server.app.dependency_overrides.pop(get_privacy_guard, None)


class TestV2EndpointScrubs:
    def test_v2_headers_and_body_scrubbed(self, reloaded_server):
        _db, _redact, _server = reloaded_server
        from fastapi.testclient import TestClient
        from pce_core.capture_event import new_capture_id

        client = TestClient(_server.app)
        with client:
            payload = {
                "capture_id": new_capture_id(),
                "source": "L1_mitm",
                "agent_name": "pce_proxy",
                "agent_version": "1.0.0",
                "capture_time_ns": 1_700_000_000_000_000_000,
                "capture_host": "test#1",
                "pair_id": "pair-v2-redact-1",
                "direction": "pair",
                "provider": "openai",
                "endpoint": "https://api.openai.com/v1/chat/completions",
                "request_headers": {"Authorization": "Bearer leaking-token"},
                "request_body": {"text": "my key is sk-proj-abcdefghijklmnopqrstuv"},
                "response_body": {"answer": "here"},
            }
            r = client.post("/api/v1/captures/v2", json=payload)
            assert r.status_code == 201

            rows = client.get("/api/v1/captures/pair/pair-v2-redact-1").json()
            stored = rows[0]

            stored_headers = json.loads(stored["headers_redacted_json"])
            assert stored_headers.get("Authorization") == "REDACTED"

            body_text = stored["body_text_or_json"]
            assert "sk-proj-abcdefghijkl" not in body_text

    def test_v2_stream_chunks_scrubbed(self, reloaded_server):
        _db, _redact, _server = reloaded_server
        from fastapi.testclient import TestClient
        from pce_core.capture_event import new_capture_id

        client = TestClient(_server.app)
        with client:
            payload = {
                "capture_id": new_capture_id(),
                "source": "L1_mitm",
                "agent_name": "pce_proxy",
                "agent_version": "1.0.0",
                "capture_time_ns": 1_700_000_000_000_000_000,
                "capture_host": "test#1",
                "pair_id": "pair-stream-redact-1",
                "direction": "pair",
                "provider": "openai",
                "endpoint": "https://api.openai.com/v1/chat/completions",
                "streaming": True,
                "stream_chunks": [
                    {"delta": "hello"},
                    {"delta": "my token is sk-proj-abcdefghijklmnopqrstuv"},
                    {"raw": "plain-text-chunk Bearer hardcoded-abc12345-token"},
                ],
            }
            r = client.post("/api/v1/captures/v2", json=payload)
            assert r.status_code == 201

            rows = client.get(
                "/api/v1/captures/pair/pair-stream-redact-1"
            ).json()
            meta = json.loads(rows[0]["meta_json"])
            chunks = meta["v2_stream_chunks"]

            serialised = json.dumps(chunks)
            assert "sk-proj-abcdefghijkl" not in serialised
            assert "hardcoded-abc12345" not in serialised
            assert "[REDACTED_OPENAI_KEY]" in serialised
            assert "Bearer [REDACTED]" in serialised


# ---------------------------------------------------------------------------
# Module-level shortcut used by pce_proxy/addon.py
# ---------------------------------------------------------------------------

class TestRedactBodySecretsShortcut:
    def test_consistent_with_default_guard(self):
        from pce_core.redact import redact_body_secrets, get_privacy_guard
        sample = "note=sk-proj-abcdefghijklmnopqrstuv"
        assert redact_body_secrets(sample) == get_privacy_guard().scrub_body(sample)

    def test_handles_none(self):
        from pce_core.redact import redact_body_secrets
        assert redact_body_secrets(None) == ""
