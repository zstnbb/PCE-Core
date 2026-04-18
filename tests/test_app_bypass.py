# SPDX-License-Identifier: Apache-2.0
"""P5.A-7 — Per-app proxy bypass tests.

Covers:

- ``pce_core.app_bypass`` — load/save round-trip, forgiving reads of
  missing / corrupt / typo'd files, atomic writes, normalisation
  (dedup + case), toggle helpers.
- Launcher integration — a bypassed app launches without the proxy env
  vars (lazily patched so we don't actually spawn a process).
- HTTP endpoints — ``GET`` reflects KNOWN_APPS + current state,
  ``PUT`` normalises input and drops unknown slugs.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_bypass_dir(tmp_path, monkeypatch):
    """Reload app_bypass / config with a tmp DATA_DIR so on-disk state is
    isolated per test. Returns the reloaded app_bypass module."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))

    import pce_core.config as _cfg
    import pce_core.app_bypass as _bypass

    importlib.reload(_cfg)
    importlib.reload(_bypass)
    return _bypass


@pytest.fixture
def reloaded_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import pce_core.app_bypass as _bypass
    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.otel_exporter as _otel
    import pce_core.server as _server

    importlib.reload(_cfg)
    importlib.reload(_db)
    importlib.reload(_bypass)
    _otel._initialised = False
    _otel._enabled = False
    importlib.reload(_server)
    _db.init_db()
    return _bypass, _server


# ---------------------------------------------------------------------------
# load / save / defaults
# ---------------------------------------------------------------------------

class TestLoadSave:
    def test_missing_file_returns_empty_state(self, isolated_bypass_dir):
        state = isolated_bypass_dir.load_bypass()
        assert state == {"bypassed": [], "updated_at": 0.0}

    def test_save_then_load_round_trip(self, isolated_bypass_dir):
        isolated_bypass_dir.save_bypass(["chatgpt-desktop", "claude-desktop"])
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == ["chatgpt-desktop", "claude-desktop"]
        assert state["updated_at"] > 0

    def test_save_normalises_case_and_dedup(self, isolated_bypass_dir):
        isolated_bypass_dir.save_bypass([
            "ChatGPT-Desktop",
            "chatgpt-desktop",
            "  CLAUDE-desktop  ",
        ])
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == ["chatgpt-desktop", "claude-desktop"]

    def test_corrupt_file_degrades_to_empty(self, isolated_bypass_dir, tmp_path):
        # Write invalid JSON
        path = tmp_path / "app_bypass.json"
        path.write_text("{not valid json", encoding="utf-8")
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == []

    def test_non_dict_root_degrades_to_empty(self, tmp_path, isolated_bypass_dir):
        path = tmp_path / "app_bypass.json"
        path.write_text('["not", "a", "dict"]', encoding="utf-8")
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == []

    def test_non_list_bypassed_field_degrades(self, tmp_path, isolated_bypass_dir):
        path = tmp_path / "app_bypass.json"
        path.write_text(
            json.dumps({"bypassed": "not-a-list"}), encoding="utf-8",
        )
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == []

    def test_non_string_entries_filtered(self, tmp_path, isolated_bypass_dir):
        path = tmp_path / "app_bypass.json"
        path.write_text(
            json.dumps({"bypassed": [123, None, "good-app", {}, "  "]}),
            encoding="utf-8",
        )
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == ["good-app"]

    def test_atomic_write_leaves_no_tmp_files(self, isolated_bypass_dir, tmp_path):
        isolated_bypass_dir.save_bypass(["chatgpt-desktop"])
        leftovers = [p for p in tmp_path.glob("app_bypass-*.tmp.json")]
        assert leftovers == []

    def test_save_explicit_path_argument(self, tmp_path, isolated_bypass_dir):
        alt = tmp_path / "alt" / "bypass.json"
        isolated_bypass_dir.save_bypass(["cursor"], path=alt)
        assert alt.exists()
        state = isolated_bypass_dir.load_bypass(path=alt)
        assert state["bypassed"] == ["cursor"]

    def test_save_empty_list_clears_state(self, isolated_bypass_dir):
        isolated_bypass_dir.save_bypass(["chatgpt-desktop"])
        isolated_bypass_dir.save_bypass([])
        state = isolated_bypass_dir.load_bypass()
        assert state["bypassed"] == []


# ---------------------------------------------------------------------------
# is_app_bypassed / set_app_bypassed
# ---------------------------------------------------------------------------

class TestHelperApi:
    def test_is_app_bypassed_true_after_save(self, isolated_bypass_dir):
        isolated_bypass_dir.save_bypass(["chatgpt-desktop"])
        assert isolated_bypass_dir.is_app_bypassed("chatgpt-desktop") is True
        assert isolated_bypass_dir.is_app_bypassed("ChatGPT-Desktop") is True

    def test_is_app_bypassed_false_when_absent(self, isolated_bypass_dir):
        isolated_bypass_dir.save_bypass(["chatgpt-desktop"])
        assert isolated_bypass_dir.is_app_bypassed("claude-desktop") is False

    def test_is_app_bypassed_empty_name_returns_false(self, isolated_bypass_dir):
        assert isolated_bypass_dir.is_app_bypassed("") is False

    def test_set_app_bypassed_toggle_on(self, isolated_bypass_dir):
        state = isolated_bypass_dir.set_app_bypassed("chatgpt-desktop", True)
        assert "chatgpt-desktop" in state["bypassed"]
        # Check on-disk persisted
        state2 = isolated_bypass_dir.load_bypass()
        assert state2["bypassed"] == ["chatgpt-desktop"]

    def test_set_app_bypassed_toggle_off(self, isolated_bypass_dir):
        isolated_bypass_dir.save_bypass(["chatgpt-desktop", "claude-desktop"])
        state = isolated_bypass_dir.set_app_bypassed("chatgpt-desktop", False)
        assert "chatgpt-desktop" not in state["bypassed"]
        assert "claude-desktop" in state["bypassed"]

    def test_set_app_bypassed_name_is_required(self, isolated_bypass_dir):
        with pytest.raises(ValueError):
            isolated_bypass_dir.set_app_bypassed("", True)

    def test_set_app_bypassed_off_when_absent_is_noop(self, isolated_bypass_dir):
        state = isolated_bypass_dir.set_app_bypassed("never-seen", False)
        assert state["bypassed"] == []


# ---------------------------------------------------------------------------
# Launcher integration
# ---------------------------------------------------------------------------

class TestLauncherBypass:
    def test_bypassed_app_launches_without_proxy_env(
        self, tmp_path, monkeypatch, isolated_bypass_dir,
    ):
        """When an app is on the bypass list, launch_app must call Popen
        with an env that has no PCE proxy vars. We replace subprocess.Popen
        with a capture shim so no process actually spawns."""
        import pce_core.electron_proxy as ep
        importlib.reload(ep)

        # Mark chatgpt-desktop as bypassed on the isolated DATA_DIR.
        isolated_bypass_dir.save_bypass(["chatgpt-desktop"])

        # Pretend ChatGPT Desktop is "installed" so launch_app proceeds.
        monkeypatch.setattr(
            ep,
            "detect_installed_apps",
            lambda: [{
                "name": "chatgpt-desktop",
                "display_name": "ChatGPT Desktop",
                "path": str(tmp_path / "fake-chatgpt"),
                "ai_domains": ["chatgpt.com"],
            }],
        )

        captured = {}

        class FakePopen:
            def __init__(self, cmd, env=None):
                captured["env"] = env
                self.pid = 12345

        monkeypatch.setattr(ep.subprocess, "Popen", FakePopen)
        # Inject a parent-env proxy var to prove the launcher strips it.
        monkeypatch.setenv("HTTPS_PROXY", "http://evil-parent:8080")

        ep.launch_app("chatgpt-desktop")

        env = captured["env"] or {}
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                    "NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED"):
            assert var not in env, (
                f"bypassed app should not see {var!r}; got {env.get(var)!r}"
            )

    def test_non_bypassed_app_gets_proxy_env(
        self, tmp_path, monkeypatch, isolated_bypass_dir,
    ):
        import pce_core.electron_proxy as ep
        importlib.reload(ep)

        # NOT bypassed.
        isolated_bypass_dir.save_bypass([])

        monkeypatch.setattr(
            ep,
            "detect_installed_apps",
            lambda: [{
                "name": "cursor",
                "display_name": "Cursor",
                "path": str(tmp_path / "fake-cursor"),
                "ai_domains": ["api.openai.com"],
            }],
        )

        captured = {}

        class FakePopen:
            def __init__(self, cmd, env=None):
                captured["env"] = env
                self.pid = 99

        monkeypatch.setattr(ep.subprocess, "Popen", FakePopen)

        ep.launch_app("cursor")

        env = captured["env"] or {}
        assert "HTTPS_PROXY" in env
        assert env["HTTPS_PROXY"].startswith("http://")


# ---------------------------------------------------------------------------
# HTTP API — GET + PUT /api/v1/bypass/apps
# ---------------------------------------------------------------------------

class TestBypassHTTPAPI:
    def test_get_returns_all_known_apps_with_false_flags_by_default(
        self, reloaded_server,
    ):
        _bypass, _server = reloaded_server
        from fastapi.testclient import TestClient
        from pce_core.electron_proxy import KNOWN_APPS

        client = TestClient(_server.app)
        with client:
            r = client.get("/api/v1/bypass/apps")
            assert r.status_code == 200
            body = r.json()

            names = [e["name"] for e in body["apps"]]
            for app in KNOWN_APPS:
                assert app.name in names
            assert body["bypassed_count"] == 0
            assert all(e["bypassed"] is False for e in body["apps"])

    def test_put_then_get_reflects_change(self, reloaded_server):
        _bypass, _server = reloaded_server
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        with client:
            r = client.put(
                "/api/v1/bypass/apps",
                json={"bypassed": ["chatgpt-desktop", "claude-desktop"]},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["bypassed_count"] == 2

            # Cross-check the on-disk state via the helper.
            state = _bypass.load_bypass()
            assert set(state["bypassed"]) == {
                "chatgpt-desktop", "claude-desktop",
            }

            # GET after PUT
            r2 = client.get("/api/v1/bypass/apps")
            by_name = {e["name"]: e for e in r2.json()["apps"]}
            assert by_name["chatgpt-desktop"]["bypassed"] is True
            assert by_name["claude-desktop"]["bypassed"] is True
            assert by_name["cursor"]["bypassed"] is False

    def test_put_drops_unknown_slugs(self, reloaded_server):
        _bypass, _server = reloaded_server
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        with client:
            r = client.put(
                "/api/v1/bypass/apps",
                json={"bypassed": [
                    "chatgpt-desktop",
                    "not-a-real-app",
                    "another-phantom",
                ]},
            )
            assert r.status_code == 200
            state = _bypass.load_bypass()
            assert state["bypassed"] == ["chatgpt-desktop"]

    def test_put_empty_clears_all(self, reloaded_server):
        _bypass, _server = reloaded_server
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        with client:
            client.put("/api/v1/bypass/apps",
                       json={"bypassed": ["chatgpt-desktop"]})
            r = client.put("/api/v1/bypass/apps", json={"bypassed": []})
            assert r.status_code == 200
            assert r.json()["bypassed_count"] == 0

    def test_put_rejects_non_string_entries_via_pydantic(self, reloaded_server):
        _bypass, _server = reloaded_server
        from fastapi.testclient import TestClient

        client = TestClient(_server.app)
        with client:
            r = client.put(
                "/api/v1/bypass/apps",
                json={"bypassed": [123, None, "chatgpt-desktop"]},
            )
            # Pydantic v2 coerces list[str] by default-rejecting non-string
            # elements; this asserts the 422 behaviour rather than silent drop.
            assert r.status_code == 422
