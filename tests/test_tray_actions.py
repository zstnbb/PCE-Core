# SPDX-License-Identifier: Apache-2.0
"""Tests for the P3.3 tray_actions module.

``pce_app.tray_actions`` is the backend-agnostic seam used by both the
pystray and (future) Tauri trays. Tests focus on the parts we can verify
without a running desktop environment:

- ``ActionResult`` shape + ``as_dict`` serialisation.
- ``collect_diagnostics`` produces a real zip (uses the in-process
  diagnose collector, not the HTTP path).
- ``reset_onboarding`` flips the persisted flag.
- ``open_dashboard`` / ``open_onboarding`` tolerate a broken
  ``webbrowser.open`` and return a failing ``ActionResult``.
- ``phoenix_toggle`` short-circuits when the core server isn't running.
- ``check_for_updates`` short-circuits when the core server isn't running.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# ActionResult
# ---------------------------------------------------------------------------

class TestActionResult:

    def test_default_serialisation(self):
        from pce_app.tray_actions import ActionResult
        r = ActionResult(ok=True, title="Hi", message="hello")
        d = r.as_dict()
        assert d == {"ok": True, "title": "Hi", "message": "hello", "payload": {}}

    def test_payload_serialises(self):
        from pce_app.tray_actions import ActionResult
        r = ActionResult(ok=False, title="x", message="y", payload={"code": 1})
        d = r.as_dict()
        assert d["payload"] == {"code": 1}


# ---------------------------------------------------------------------------
# open_* actions
# ---------------------------------------------------------------------------

class TestBrowserOpeners:

    def test_open_dashboard_returns_ok(self, monkeypatch):
        from pce_app import tray_actions
        calls = []
        monkeypatch.setattr(
            tray_actions.webbrowser, "open",
            lambda url: calls.append(url) or True,
        )
        r = tray_actions.open_dashboard()
        assert r.ok is True
        assert calls == ["http://127.0.0.1:9800/"]

    def test_open_onboarding_returns_ok(self, monkeypatch):
        from pce_app import tray_actions
        calls = []
        monkeypatch.setattr(
            tray_actions.webbrowser, "open",
            lambda url: calls.append(url) or True,
        )
        r = tray_actions.open_onboarding()
        assert r.ok is True
        assert calls == ["http://127.0.0.1:9800/onboarding"]

    def test_open_dashboard_reports_failure_if_webbrowser_raises(self, monkeypatch):
        from pce_app import tray_actions
        def _boom(_url):
            raise RuntimeError("no browser")
        monkeypatch.setattr(tray_actions.webbrowser, "open", _boom)
        r = tray_actions.open_dashboard()
        assert r.ok is False
        assert "no browser" in r.message


# ---------------------------------------------------------------------------
# collect_diagnostics
# ---------------------------------------------------------------------------

class TestCollectDiagnostics:

    def test_writes_zip_to_output_dir(self, isolated_data_dir, tmp_path):
        from pce_core import db as pce_db
        from pce_app import tray_actions
        pce_db.init_db(isolated_data_dir / "pce.db")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        result = tray_actions.collect_diagnostics(
            output_dir=out_dir,
            include_logs=False,
            reveal_in_explorer=False,
        )
        assert result.ok is True
        assert result.payload is not None
        path = Path(result.payload["path"])
        assert path.exists()
        assert path.suffix == ".zip"
        assert path.parent == out_dir

    def test_errors_are_surfaced_as_failed_result(self, isolated_data_dir, monkeypatch):
        from pce_app import tray_actions
        def _boom(*a, **kw):
            raise RuntimeError("nope")
        # Patch the concrete function the action looks up.
        import pce_core.diagnose as _diag
        monkeypatch.setattr(_diag, "collect_diagnostics", _boom, raising=False)

        result = tray_actions.collect_diagnostics(reveal_in_explorer=False)
        assert result.ok is False
        assert "nope" in result.message


# ---------------------------------------------------------------------------
# reset_onboarding
# ---------------------------------------------------------------------------

class TestResetOnboarding:

    def test_flips_flag(self, isolated_data_dir):
        from pce_core import app_state
        from pce_app import tray_actions

        app_state.complete_onboarding()
        assert app_state.needs_onboarding() is False

        r = tray_actions.reset_onboarding()
        assert r.ok is True
        assert app_state.needs_onboarding() is True


# ---------------------------------------------------------------------------
# phoenix_toggle / check_for_updates when core is down
# ---------------------------------------------------------------------------

class TestCoreServerDownPaths:

    def test_phoenix_toggle_when_core_is_down(self, monkeypatch):
        from pce_app import tray_actions
        monkeypatch.setattr(tray_actions, "_core_running", lambda *_a, **_kw: False)
        r = tray_actions.phoenix_toggle(port=65530)
        assert r.ok is False
        assert "Core server" in r.message

    def test_check_for_updates_when_core_is_down(self, monkeypatch):
        from pce_app import tray_actions
        monkeypatch.setattr(tray_actions, "_core_running", lambda *_a, **_kw: False)
        r = tray_actions.check_for_updates(port=65530)
        assert r.ok is False
        assert "Core server" in r.message
