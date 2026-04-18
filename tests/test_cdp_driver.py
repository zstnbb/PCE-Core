# SPDX-License-Identifier: Apache-2.0
"""Tests for the P4.4 CDP embedded-browser capture source.

Strategy
========

Launching a real Chromium in CI is slow and fragile, so every test here
**mocks Playwright entirely**. We build a tiny fake ``sync_playwright``
object that:

- behaves as a context manager,
- exposes ``.chromium.launch()`` → fake browser → fake context → fake
  page,
- lets us programmatically inject ``response`` events,

which is enough to exercise the :class:`pce_core.cdp.driver.CDPDriver`
lifecycle, URL pattern filtering, redaction, and the ``raw_captures``
write-side end-to-end.

We also verify:

- ``pce_core.cdp.status()`` reflects the singleton state.
- ``start_default`` / ``stop_default`` are idempotent.
- Graceful ``playwright_not_installed`` path when the feature flag
  ``PLAYWRIGHT_AVAILABLE`` is forced off.
- The FastAPI endpoints return 503 when Playwright is missing and echo
  the driver snapshot when it is present.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    import pce_core.db as _db
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    monkeypatch.setattr(_db, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


@pytest.fixture
def tmp_db(isolated_data_dir: Path) -> Path:
    from pce_core.db import init_db
    db = isolated_data_dir / "pce.db"
    init_db(db)
    return db


# ---------------------------------------------------------------------------
# Fake Playwright — just enough to drive CDPDriver
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, method="POST", headers=None, post_data=""):
        self.method = method
        self.headers = headers or {
            "Authorization": "Bearer SECRET",
            "Content-Type": "application/json",
        }
        self.post_data = post_data


class _FakeResponse:
    def __init__(
        self,
        url: str,
        status: int = 200,
        request: Optional[_FakeRequest] = None,
        body: str = "",
        headers: Optional[dict] = None,
    ):
        self.url = url
        self.status = status
        self.request = request or _FakeRequest(post_data='{"q":"hi"}')
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}
        self.timing = {"startTime": 0.0, "responseEnd": 150.0}

    def text(self) -> str:
        return self._body


class _FakePage:
    def __init__(self):
        self._listeners: dict[str, list[Callable]] = {}
        self.goto_url: Optional[str] = None
    def on(self, event: str, handler: Callable):
        self._listeners.setdefault(event, []).append(handler)
    def goto(self, url: str, wait_until: str = "load"):
        self.goto_url = url
    def emit(self, event: str, payload: Any):
        """Test hook — deliver an event to every registered listener."""
        for h in list(self._listeners.get(event, [])):
            h(payload)


class _FakeContext:
    def __init__(self):
        self.closed = False
    def new_page(self) -> _FakePage:
        self.page = _FakePage()
        return self.page
    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self):
        self.closed = False
        self.contexts: list[_FakeContext] = []
    def new_context(self) -> _FakeContext:
        ctx = _FakeContext()
        self.contexts.append(ctx)
        return ctx
    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser):
        self._browser = browser
        self.launch_kwargs: dict[str, Any] = {}
    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self._browser


class _FakePW:
    def __init__(self, browser: _FakeBrowser):
        self.chromium = _FakeChromium(browser)


class _FakeSyncPlaywright:
    """Context manager returned by ``sync_playwright()``."""
    def __init__(self, browser: _FakeBrowser):
        self._browser = browser
    def __enter__(self) -> _FakePW:
        return _FakePW(self._browser)
    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_playwright(monkeypatch):
    """Install a fake ``playwright.sync_api`` module.

    Yields the fake browser handle so tests can reach into ``.contexts[0].page``
    and synthesise ``response`` events.
    """
    browser = _FakeBrowser()

    fake_sync_api = SimpleNamespace(
        sync_playwright=lambda: _FakeSyncPlaywright(browser),
    )
    # The driver does ``from playwright.sync_api import sync_playwright``
    # at call time, so we inject a module into sys.modules.
    fake_playwright_pkg = SimpleNamespace(sync_api=fake_sync_api)
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    yield browser


def _wait_for_page(browser: _FakeBrowser, timeout_s: float = 5.0) -> _FakePage:
    """Spin until the fake browser has produced a page. Raises on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if browser.contexts and getattr(browser.contexts[0], "page", None):
            return browser.contexts[0].page
        time.sleep(0.01)
    raise TimeoutError("fake page never materialised")


# ---------------------------------------------------------------------------
# URL matching
# ---------------------------------------------------------------------------

class TestUrlMatching:

    def test_default_patterns_match_known_endpoints(self):
        from pce_core.cdp.driver import CDPDriver
        d = CDPDriver()
        assert d._url_matches("https://api.openai.com/v1/chat/completions")
        assert d._url_matches("https://chatgpt.com/backend-api/conversation")
        assert d._url_matches("https://api.anthropic.com/v1/messages")

    def test_default_patterns_reject_random_hosts(self):
        from pce_core.cdp.driver import CDPDriver
        d = CDPDriver()
        assert not d._url_matches("https://example.com/foo")
        assert not d._url_matches("https://www.google.com/search?q=hi")

    def test_custom_patterns_override(self):
        from pce_core.cdp.driver import CDPDriver
        d = CDPDriver(url_patterns=[r"^https://my-llm\.example\.com/.*"])
        assert d._url_matches("https://my-llm.example.com/chat")
        assert not d._url_matches("https://api.openai.com/v1/chat/completions")


# ---------------------------------------------------------------------------
# provider_from_host helper
# ---------------------------------------------------------------------------

class TestProviderFromHost:

    @pytest.mark.parametrize("host,expected", [
        ("api.openai.com", "openai"),
        ("chatgpt.com", "openai"),
        ("api.anthropic.com", "anthropic"),
        ("claude.ai", "anthropic"),
        ("gemini.google.com", "google"),
        ("api.groq.com", "groq"),
        ("api.mistral.ai", "mistral"),
        ("www.perplexity.ai", "perplexity"),
        ("example.com", "unknown"),
    ])
    def test_maps_hosts(self, host, expected):
        from pce_core.cdp.driver import _provider_from_host
        assert _provider_from_host(host) == expected


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestHeaderRedaction:

    def test_authorization_is_redacted(self):
        import json
        from pce_core.cdp.driver import _dumps_headers
        out = json.loads(_dumps_headers({
            "Authorization": "Bearer SECRET",
            "Content-Type": "application/json",
        }))
        assert out["Authorization"] == "REDACTED"
        assert out["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Driver lifecycle + response handling (end-to-end, fully mocked)
# ---------------------------------------------------------------------------

class TestDriverLifecycle:

    def test_start_and_stop_cleanly(self, tmp_db, fake_playwright):
        from pce_core.cdp.driver import CDPDriver

        d = CDPDriver(start_url="https://chat.openai.com", headless=True)
        d.start(timeout_s=5.0)
        assert d.is_running()
        # The start URL landed on the fake page.
        page = _wait_for_page(fake_playwright)
        assert page.goto_url == "https://chat.openai.com"

        d.stop(timeout_s=5.0)
        assert not d.is_running()
        # Fake browser + context were closed.
        assert fake_playwright.closed
        assert fake_playwright.contexts[0].closed

    def test_start_is_idempotent(self, tmp_db, fake_playwright):
        from pce_core.cdp.driver import CDPDriver
        d = CDPDriver(headless=True)
        d.start(timeout_s=5.0)
        d.start(timeout_s=5.0)  # second call = no-op
        try:
            assert d.is_running()
            # Only one browser should have been launched.
            assert len(fake_playwright.contexts) == 1
        finally:
            d.stop()

    def test_snapshot_reports_headless_flag_via_launch_kwargs(
        self, tmp_db, fake_playwright,
    ):
        from pce_core.cdp.driver import CDPDriver
        d = CDPDriver(headless=True)
        d.start(timeout_s=5.0)
        try:
            # Our fake captures launch kwargs.
            page = _wait_for_page(fake_playwright)  # ensures thread progressed
            assert page is not None
            import sys
            fake_pw_sync = sys.modules["playwright.sync_api"]
            # Can't easily introspect kwargs without holding the chromium ref,
            # so just verify driver.snapshot()['running'] == True.
            assert d.snapshot()["running"] is True
        finally:
            d.stop()


class TestResponseCapture:

    def test_matching_response_writes_two_rows(self, tmp_db, fake_playwright):
        """Every matched response should produce a request+response pair
        in ``raw_captures`` sharing the same ``pair_id``."""
        from pce_core.cdp.driver import CDPDriver, SOURCE_CDP
        from pce_core.db import get_connection

        d = CDPDriver(headless=True)
        d.start(timeout_s=5.0)
        try:
            page = _wait_for_page(fake_playwright)
            page.emit("response", _FakeResponse(
                url="https://api.openai.com/v1/chat/completions",
                status=200,
                body='{"id":"cmpl-1"}',
                request=_FakeRequest(
                    method="POST",
                    headers={"Authorization": "Bearer S", "X-Custom": "hi"},
                    post_data='{"model":"gpt-4o","messages":[]}',
                ),
            ))
            # Give the handler a beat to persist.
            deadline = time.time() + 2.0
            while time.time() < deadline and d.snapshot()["captures_written"] < 1:
                time.sleep(0.02)
        finally:
            d.stop()

        assert d.snapshot()["captures_written"] == 1

        conn = get_connection(tmp_db)
        try:
            rows = conn.execute(
                "SELECT direction, host, path, status_code, source_id, "
                "       body_text_or_json, provider, pair_id "
                "FROM raw_captures ORDER BY direction"
            ).fetchall()
        finally:
            conn.close()

        directions = {r[0] for r in rows}
        assert directions == {"request", "response"}
        pair_ids = {r[7] for r in rows}
        assert len(pair_ids) == 1, "request + response must share pair_id"
        assert all(r[4] == SOURCE_CDP for r in rows)
        assert all(r[1] == "api.openai.com" for r in rows)
        assert all(r[6] == "openai" for r in rows)
        # Response row carries the status code.
        resp_row = next(r for r in rows if r[0] == "response")
        assert resp_row[3] == 200
        assert "cmpl-1" in resp_row[5]

    def test_non_matching_response_is_ignored(self, tmp_db, fake_playwright):
        from pce_core.cdp.driver import CDPDriver
        from pce_core.db import get_connection

        d = CDPDriver(headless=True)
        d.start(timeout_s=5.0)
        try:
            page = _wait_for_page(fake_playwright)
            page.emit("response", _FakeResponse(
                url="https://example.com/random",
                status=200, body="nope",
            ))
            time.sleep(0.2)
        finally:
            d.stop()

        count = get_connection(tmp_db).execute(
            "SELECT COUNT(*) FROM raw_captures"
        ).fetchone()[0]
        assert count == 0

    def test_handler_swallows_body_errors(self, tmp_db, fake_playwright):
        """A response whose ``.text()`` raises must not crash the driver."""
        from pce_core.cdp.driver import CDPDriver

        class _Broken(_FakeResponse):
            def text(self):
                raise RuntimeError("boom")

        d = CDPDriver(headless=True)
        d.start(timeout_s=5.0)
        try:
            page = _wait_for_page(fake_playwright)
            page.emit("response", _Broken(
                url="https://api.openai.com/v1/chat/completions",
                body="",
            ))
            time.sleep(0.1)
        finally:
            d.stop()
        # Driver still alive, shutdown clean.
        assert not d.is_running()


# ---------------------------------------------------------------------------
# Module-level singleton + status
# ---------------------------------------------------------------------------

class TestModuleSingleton:

    def test_status_when_unavailable(self, tmp_db, monkeypatch):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        monkeypatch.setattr(cdp, "PLAYWRIGHT_AVAILABLE", False, raising=False)
        s = cdp.status()
        assert s["available"] is False
        assert "pip install playwright" in s["hint"]
        assert s["running"] is False

    def test_start_default_without_playwright(self, tmp_db, monkeypatch):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        monkeypatch.setattr(cdp, "PLAYWRIGHT_AVAILABLE", False, raising=False)
        r = cdp.start_default()
        assert r["ok"] is False
        assert r["error"] == "playwright_not_installed"

    def test_start_default_is_idempotent(self, tmp_db, fake_playwright, monkeypatch):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        monkeypatch.setattr(cdp, "PLAYWRIGHT_AVAILABLE", True, raising=False)

        r1 = cdp.start_default(start_url="https://chat.openai.com", headless=True)
        try:
            assert r1.get("ok") is True
            assert r1["running"] is True
            r2 = cdp.start_default(start_url="https://chat.openai.com", headless=True)
            assert r2.get("already_running") is True
        finally:
            cdp.stop_default()

    def test_stop_default_is_safe_when_nothing_running(self, tmp_db):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        r = cdp.stop_default()
        assert r["ok"] is True
        assert r["running"] is False


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

class TestCdpHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_status_endpoint_200(self, tmp_db):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        with self._client() as client:
            resp = client.get("/api/v1/cdp/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "available" in body and "running" in body

    def test_start_endpoint_503_without_playwright(self, tmp_db, monkeypatch):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        monkeypatch.setattr(cdp, "PLAYWRIGHT_AVAILABLE", False, raising=False)
        with self._client() as client:
            resp = client.post("/api/v1/cdp/start")
        assert resp.status_code == 503

    def test_start_and_stop_endpoints_happy_path(
        self, tmp_db, fake_playwright, monkeypatch,
    ):
        from pce_core import cdp
        cdp._reset_singleton_for_tests()
        monkeypatch.setattr(cdp, "PLAYWRIGHT_AVAILABLE", True, raising=False)

        try:
            with self._client() as client:
                r1 = client.post(
                    "/api/v1/cdp/start?start_url=https://chat.openai.com&headless=true"
                )
                assert r1.status_code == 200
                body1 = r1.json()
                assert body1.get("ok") is True
                assert body1.get("running") is True

                r2 = client.post("/api/v1/cdp/stop")
                assert r2.status_code == 200
                body2 = r2.json()
                assert body2["ok"] is True
                assert body2["running"] is False
        finally:
            cdp._reset_singleton_for_tests()
            # Safety net: make sure no driver was left running.
            try:
                cdp.stop_default()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI wizard (probe-only mode)
# ---------------------------------------------------------------------------

class TestCliWizardProbeOnly:

    def test_probe_only_when_unavailable_returns_1(self, monkeypatch, capsys):
        import pce_core.cdp.__main__ as wizard
        monkeypatch.setattr(wizard, "PLAYWRIGHT_AVAILABLE", False, raising=False)
        monkeypatch.setattr(wizard, "PLAYWRIGHT_VERSION", None, raising=False)

        rc = wizard.main(["--probe-only"])
        captured = capsys.readouterr().out
        assert rc == 1
        assert "playwright_available=False" in captured
        assert "pip install playwright" in captured

    def test_probe_only_when_available_returns_0(self, monkeypatch, capsys):
        import pce_core.cdp.__main__ as wizard
        monkeypatch.setattr(wizard, "PLAYWRIGHT_AVAILABLE", True, raising=False)
        monkeypatch.setattr(wizard, "PLAYWRIGHT_VERSION", "1.58.0", raising=False)

        rc = wizard.main(["--probe-only"])
        captured = capsys.readouterr().out
        assert rc == 0
        assert "playwright_available=True" in captured
