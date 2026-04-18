# SPDX-License-Identifier: Apache-2.0
"""Tests for the P3.6 update checker (``pce_core.updates``).

The checker must:

- Parse both PCE-native manifests and the GitHub releases subset.
- Report ``update_available=True`` only for strictly-newer versions.
- Never raise on network failures — surface a structured ``error``.
- Honour ``UPDATES_DISABLED`` / empty manifest URL by returning
  ``disabled=True`` without touching the network.
- Cache repeat calls within the TTL window.
- Expose a ``/api/v1/updates/check`` HTTP endpoint that mirrors the model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_cache():
    from pce_core import updates
    updates.clear_cache()
    # Also flip UPDATES_DISABLED off regardless of the CI env so the tests
    # drive the flag themselves.
    yield
    updates.clear_cache()


# ---------------------------------------------------------------------------
# Semver parsing
# ---------------------------------------------------------------------------

class TestSemver:

    def test_is_newer_major(self):
        from pce_core.updates import _is_newer
        assert _is_newer("1.0.0", "0.9.9") is True

    def test_is_newer_minor(self):
        from pce_core.updates import _is_newer
        assert _is_newer("0.2.0", "0.1.99") is True

    def test_is_newer_patch(self):
        from pce_core.updates import _is_newer
        assert _is_newer("0.1.2", "0.1.1") is True

    def test_not_newer_when_equal(self):
        from pce_core.updates import _is_newer
        assert _is_newer("0.1.0", "0.1.0") is False

    def test_not_newer_when_older(self):
        from pce_core.updates import _is_newer
        assert _is_newer("0.0.9", "0.1.0") is False

    def test_v_prefix_is_accepted(self):
        from pce_core.updates import _is_newer
        assert _is_newer("v1.2.3", "1.2.2") is True

    def test_garbage_versions_sort_behind(self):
        from pce_core.updates import _is_newer
        # A non-semver string parses to (-1,-1,-1, raw) which is strictly
        # less than any real version.
        assert _is_newer("garbage", "0.0.1") is False


# ---------------------------------------------------------------------------
# Manifest normalisation
# ---------------------------------------------------------------------------

class TestNormaliseManifest:

    def test_pce_native_shape(self):
        from pce_core.updates import _normalise_manifest
        out = _normalise_manifest({
            "latest_version": "0.2.0",
            "release_date": "2026-04-20",
            "download_url": "https://example.com/pce.zip",
            "changelog_url": "https://example.com/CHANGELOG.md",
            "notes": "bugfix release",
        })
        assert out["latest_version"] == "0.2.0"
        assert out["release_date"] == "2026-04-20"
        assert out["download_url"] == "https://example.com/pce.zip"
        assert out["notes"] == "bugfix release"

    def test_github_releases_shape_strips_v_prefix(self):
        from pce_core.updates import _normalise_manifest
        out = _normalise_manifest({
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/.../releases/tag/v0.2.0",
            "body": "## What's new",
            "published_at": "2026-04-20T12:00:00Z",
            "assets": [{
                "browser_download_url": "https://github.com/.../pce-0.2.0.zip",
            }],
        })
        assert out["latest_version"] == "0.2.0"
        assert out["release_date"] == "2026-04-20T12:00:00Z"
        assert "0.2.0.zip" in out["download_url"]
        assert out["notes"].startswith("## What's new")

    def test_unknown_shape_raises(self):
        from pce_core.updates import _normalise_manifest
        with pytest.raises(ValueError, match="unknown manifest shape"):
            _normalise_manifest({"not": "a manifest"})


# ---------------------------------------------------------------------------
# check_for_updates
# ---------------------------------------------------------------------------

class TestCheckForUpdates:

    def test_disabled_when_updates_env_flag(self, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", True, raising=False)
        result = updates.check_for_updates()
        assert result.disabled is True
        assert result.update_available is False
        assert result.error is None

    def test_disabled_when_manifest_url_empty(self, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)
        monkeypatch.setattr(updates, "DEFAULT_MANIFEST_URL", "", raising=False)
        result = updates.check_for_updates(manifest_url="")
        assert result.disabled is True
        assert result.error == "no_manifest_url"

    def test_reports_update_available(self, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)

        def fake_fetch(url, *, timeout):
            return {
                "latest_version": "9.9.9",
                "release_date": "2099-01-01",
                "download_url": "https://example.com/pce-9.9.9.zip",
                "notes": "the future",
            }, 1712345678.0

        monkeypatch.setattr(updates, "_fetch_manifest", fake_fetch, raising=True)
        result = updates.check_for_updates(manifest_url="https://example.com/m.json")
        assert result.update_available is True
        assert result.latest_version == "9.9.9"
        assert result.download_url.endswith("pce-9.9.9.zip")
        assert result.error is None

    def test_reports_up_to_date(self, monkeypatch):
        import pce_core.updates as updates
        from pce_core import __version__

        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)
        monkeypatch.setattr(
            updates, "_fetch_manifest",
            lambda *_a, **_kw: ({"latest_version": __version__}, 1.0),
            raising=True,
        )
        result = updates.check_for_updates(manifest_url="https://example.com/m.json")
        assert result.update_available is False
        assert result.latest_version == __version__

    def test_network_failure_is_surfaced_not_raised(self, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)

        def _boom(*_a, **_kw):
            raise ConnectionError("dns failure")

        monkeypatch.setattr(updates, "_fetch_manifest", _boom, raising=True)
        result = updates.check_for_updates(manifest_url="https://example.com/m.json")
        assert result.update_available is False
        assert result.error is not None
        assert "fetch_failed" in result.error

    def test_bad_manifest_is_surfaced_not_raised(self, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)
        monkeypatch.setattr(
            updates, "_fetch_manifest",
            lambda *_a, **_kw: ({"garbage": True}, 1.0),
            raising=True,
        )
        result = updates.check_for_updates(manifest_url="https://example.com/m.json")
        assert result.error is not None
        assert "parse_failed" in result.error

    def test_cache_hits_do_not_trigger_second_fetch(self, monkeypatch):
        import time
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)

        # Fresh fetched_at so the TTL doesn't immediately evict the entry.
        now = time.time()
        calls = {"n": 0}
        def fake_fetch(url, *, timeout):
            calls["n"] += 1
            return {"latest_version": "9.9.9"}, now

        monkeypatch.setattr(updates, "_fetch_manifest", fake_fetch, raising=True)
        r1 = updates.check_for_updates(manifest_url="https://example.com/a.json")
        r2 = updates.check_for_updates(manifest_url="https://example.com/a.json")
        assert calls["n"] == 1
        assert r2.cached is True
        assert r1.update_available == r2.update_available

    def test_force_refresh_bypasses_cache(self, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)

        calls = {"n": 0}
        def fake_fetch(url, *, timeout):
            calls["n"] += 1
            return {"latest_version": "9.9.9"}, float(calls["n"])

        monkeypatch.setattr(updates, "_fetch_manifest", fake_fetch, raising=True)
        updates.check_for_updates(manifest_url="https://example.com/a.json")
        updates.check_for_updates(manifest_url="https://example.com/a.json", force_refresh=True)
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# /api/v1/updates/check HTTP endpoint
# ---------------------------------------------------------------------------

class TestUpdatesHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_endpoint_returns_update_envelope(self, isolated_data_dir, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", False, raising=False)
        monkeypatch.setattr(
            updates, "_fetch_manifest",
            lambda *_a, **_kw: ({"latest_version": "9.9.9"}, 1.0),
            raising=True,
        )

        with self._client() as client:
            resp = client.get("/api/v1/updates/check?manifest_url=https://example.com/m.json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["update_available"] is True
        assert body["latest_version"] == "9.9.9"
        assert body["error"] is None

    def test_endpoint_disabled_path_still_200(self, isolated_data_dir, monkeypatch):
        import pce_core.updates as updates
        monkeypatch.setattr(updates, "UPDATES_DISABLED", True, raising=False)
        with self._client() as client:
            resp = client.get("/api/v1/updates/check")
        assert resp.status_code == 200
        body = resp.json()
        assert body["disabled"] is True
        assert body["update_available"] is False
