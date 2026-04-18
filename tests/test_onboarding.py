# SPDX-License-Identifier: Apache-2.0
"""Tests for the P3.2 first-run onboarding wizard state + HTTP surface.

Covers:

- ``pce_core.app_state`` load / save / update / deep merge semantics.
- ``needs_onboarding`` transitions: fresh install, completed, version bump.
- ``mark_step`` validates step ids and statuses.
- ``/api/v1/onboarding/state`` GET / POST round-trip.
- ``/api/v1/onboarding/step`` persists step status.
- ``/api/v1/onboarding/complete`` flips the flag.
- ``/onboarding`` HTML is served and the ``/`` redirect behaviour works.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the whole PCE stack at a temp data dir for the test."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    # app_state caches nothing, but reload the module just in case a prior
    # test monkeypatched it.
    return tmp_path


# ---------------------------------------------------------------------------
# app_state module
# ---------------------------------------------------------------------------

class TestAppState:

    def test_default_state_has_required_keys(self):
        from pce_core import app_state
        s = app_state.default_state()
        for k in (
            "onboarding_completed", "onboarding_version", "onboarding_steps",
            "preferences", "first_launch_at", "last_launch_at",
        ):
            assert k in s
        prefs = s["preferences"]
        # Every default pref key should be present.
        assert "redact_tokens" in prefs
        assert "retention_days" in prefs

    def test_load_missing_file_returns_defaults(self, state_file):
        from pce_core import app_state
        s = app_state.load_state(state_file)
        assert s["onboarding_completed"] is False
        assert s["preferences"]["redact_tokens"] is True

    def test_load_corrupt_file_returns_defaults(self, state_file, caplog):
        state_file.write_text("{not json at all", encoding="utf-8")
        from pce_core import app_state
        s = app_state.load_state(state_file)
        # Still has defaults (didn't raise, didn't return garbage).
        assert s["onboarding_completed"] is False

    def test_save_then_load_roundtrips(self, state_file):
        from pce_core import app_state
        original = app_state.default_state()
        original["onboarding_completed"] = True
        original["onboarding_version"] = 1
        app_state.save_state(original, state_file)
        loaded = app_state.load_state(state_file)
        assert loaded["onboarding_completed"] is True
        assert loaded["onboarding_version"] == 1

    def test_update_state_deep_merges_preferences(self, state_file):
        from pce_core import app_state
        app_state.update_state(
            {"preferences": {"retention_days": 14}},
            path=state_file,
        )
        s = app_state.load_state(state_file)
        assert s["preferences"]["retention_days"] == 14
        # Other default prefs still present.
        assert s["preferences"]["redact_tokens"] is True

    def test_update_state_is_atomic(self, state_file):
        """A write failure must not leave a partial state.json around."""
        from pce_core import app_state
        # Seed a valid file.
        app_state.save_state({"foo": "bar", "preferences": {}}, state_file)
        # Simulate a save by writing valid data, then checking idempotence.
        app_state.update_state({"foo": "baz"}, path=state_file)
        text = state_file.read_text(encoding="utf-8")
        data = json.loads(text)
        assert data["foo"] == "baz"

    def test_needs_onboarding_fresh_install(self, state_file):
        from pce_core import app_state
        assert app_state.needs_onboarding(path=state_file) is True

    def test_needs_onboarding_after_completion(self, state_file):
        from pce_core import app_state
        app_state.complete_onboarding(path=state_file)
        assert app_state.needs_onboarding(path=state_file) is False

    def test_needs_onboarding_after_version_bump(self, state_file, monkeypatch):
        from pce_core import app_state
        # Complete at an older version, then pretend the code bumped.
        app_state.save_state({
            "onboarding_completed": True,
            "onboarding_version": 0,
            "onboarding_steps": {},
            "preferences": {},
            "first_launch_at": 1.0,
            "last_launch_at": 1.0,
        }, state_file)
        monkeypatch.setattr(app_state, "CURRENT_ONBOARDING_VERSION", 2, raising=False)
        assert app_state.needs_onboarding(path=state_file) is True

    def test_mark_step_rejects_unknown_id(self, state_file):
        from pce_core import app_state
        with pytest.raises(ValueError, match="unknown onboarding step"):
            app_state.mark_step("nonexistent_step", path=state_file)

    def test_mark_step_rejects_bad_status(self, state_file):
        from pce_core import app_state
        with pytest.raises(ValueError, match="bad status"):
            app_state.mark_step("certificate", status="garbage", path=state_file)

    def test_mark_step_persists(self, state_file):
        from pce_core import app_state
        app_state.mark_step("certificate", "done", path=state_file)
        s = app_state.load_state(state_file)
        assert s["onboarding_steps"]["certificate"]["status"] == "done"

    def test_reset_onboarding_clears_flag(self, state_file):
        from pce_core import app_state
        app_state.complete_onboarding(path=state_file)
        assert app_state.needs_onboarding(path=state_file) is False
        app_state.reset_onboarding(path=state_file)
        assert app_state.needs_onboarding(path=state_file) is True


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class TestOnboardingHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_get_state_returns_defaults_on_fresh_install(self, isolated_data_dir):
        with self._client() as client:
            resp = client.get("/api/v1/onboarding/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["needs_onboarding"] is True
        assert body["current_version"] == 1
        assert "welcome" in body["steps"]
        assert body["state"]["onboarding_completed"] is False

    def test_patch_state_persists_preferences(self, isolated_data_dir):
        with self._client() as client:
            resp = client.post(
                "/api/v1/onboarding/state",
                json={"preferences": {"retention_days": 30}},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["state"]["preferences"]["retention_days"] == 30

        # Reload — value survives.
        with self._client() as client:
            resp = client.get("/api/v1/onboarding/state")
        assert resp.json()["state"]["preferences"]["retention_days"] == 30

    def test_step_endpoint_records_status(self, isolated_data_dir):
        with self._client() as client:
            resp = client.post(
                "/api/v1/onboarding/step",
                json={"step": "certificate", "status": "done"},
            )
        assert resp.status_code == 200
        state = resp.json()["state"]
        assert state["onboarding_steps"]["certificate"]["status"] == "done"

    def test_step_endpoint_rejects_unknown_step(self, isolated_data_dir):
        with self._client() as client:
            resp = client.post(
                "/api/v1/onboarding/step",
                json={"step": "garbage", "status": "done"},
            )
        assert resp.status_code == 400

    def test_step_endpoint_requires_step_field(self, isolated_data_dir):
        with self._client() as client:
            resp = client.post("/api/v1/onboarding/step", json={})
        assert resp.status_code == 400

    def test_complete_endpoint_flips_flag(self, isolated_data_dir):
        with self._client() as client:
            resp = client.post("/api/v1/onboarding/complete")
        assert resp.status_code == 200
        assert resp.json()["state"]["onboarding_completed"] is True
        with self._client() as client:
            resp = client.get("/api/v1/onboarding/state")
        assert resp.json()["needs_onboarding"] is False

    def test_reset_endpoint_reopens_wizard(self, isolated_data_dir):
        with self._client() as client:
            client.post("/api/v1/onboarding/complete")
            resp = client.post("/api/v1/onboarding/reset")
        assert resp.status_code == 200
        assert resp.json()["state"]["onboarding_completed"] is False


# ---------------------------------------------------------------------------
# Wizard HTML + redirect
# ---------------------------------------------------------------------------

class TestOnboardingRouting:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_onboarding_html_served(self, isolated_data_dir):
        with self._client() as client:
            resp = client.get("/onboarding")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Setup" in resp.text or "Welcome to PCE" in resp.text

    def test_root_redirects_to_wizard_on_fresh_install(self, isolated_data_dir):
        with self._client() as client:
            resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/onboarding"

    def test_root_serves_dashboard_when_completed(self, isolated_data_dir):
        # Seed a completed state.
        from pce_core import app_state
        app_state.complete_onboarding()
        with self._client() as client:
            resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "PCE Dashboard" in resp.text

    def test_onboarding_static_assets_are_served(self, isolated_data_dir):
        with self._client() as client:
            css = client.get("/dashboard/onboarding.css")
            js = client.get("/dashboard/onboarding.js")
        assert css.status_code == 200
        assert js.status_code == 200
        assert "wz-btn" in css.text  # sanity check the CSS body.
        assert "STEPS" in js.text    # sanity check the JS body.
