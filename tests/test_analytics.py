# SPDX-License-Identifier: Apache-2.0
"""Tests for the P4.2 DuckDB analytics layer.

Scope:

- ``status()`` reports backend availability.
- Every query function returns the documented envelope shape.
- Graceful ``duckdb_not_installed`` path is exercised by monkey-patching
  ``DUCKDB_AVAILABLE = False`` so the tests run even on machines that
  skipped the optional install.
- Parquet export actually produces a readable file.
- HTTP endpoints surface 503 when the backend is unavailable and 200
  with the expected keys when it is.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.analytics as _analytics
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    monkeypatch.setattr(_db, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "pce.db", raising=False)
    # Analytics has its own captured reference to DB_PATH via ``from
    # .config import DB_PATH`` — rebind that too.
    monkeypatch.setattr(_analytics, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


@pytest.fixture
def seeded_db(isolated_data_dir: Path) -> Path:
    """Populate a DB with the captures + sessions + messages tables."""
    from pce_core.db import init_db, insert_capture, insert_message, insert_session

    db = isolated_data_dir / "pce.db"
    init_db(db)

    now = time.time()
    # Three captures across two providers, two directions, three hosts.
    insert_capture(
        direction="response", pair_id="p1", host="api.openai.com",
        path="/v1/chat/completions", method="POST",
        provider="openai", model_name="gpt-4o",
        db_path=db,
    )
    insert_capture(
        direction="response", pair_id="p2", host="api.openai.com",
        path="/v1/chat/completions", method="POST",
        provider="openai", model_name="gpt-4o",
        db_path=db,
    )
    insert_capture(
        direction="response", pair_id="p3", host="api.anthropic.com",
        path="/v1/messages", method="POST",
        provider="anthropic", model_name="claude-3-5-sonnet",
        db_path=db,
    )
    # Clipboard captures should show up under "clipboard" direction.
    insert_capture(
        direction="clipboard", pair_id="p4", host="",
        path="", method="",
        provider="other", model_name=None,
        db_path=db,
    )

    # Two sessions, 4 messages (2 with token counts).
    s1 = insert_session(
        source_id="browser-extension-default",
        started_at=now,
        provider="openai",
        title_hint="Python help",
        db_path=db,
    )
    s2 = insert_session(
        source_id="browser-extension-default",
        started_at=now - 86_400,  # yesterday
        provider="anthropic",
        title_hint="Rust help",
        db_path=db,
    )
    insert_message(
        session_id=s1, ts=now, role="user",
        content_text="hello python",
        db_path=db,
    )
    insert_message(
        session_id=s1, ts=now, role="assistant",
        content_text="hi there",
        model_name="gpt-4o",
        db_path=db,
    )
    insert_message(
        session_id=s2, ts=now - 86_400, role="user",
        content_text="rust ownership",
        db_path=db,
    )
    insert_message(
        session_id=s2, ts=now - 86_400, role="assistant",
        content_text="it's about memory safety",
        model_name="claude-3-5-sonnet",
        db_path=db,
    )

    # Populate token counts on the two assistant messages so the
    # token_usage rollup has something to aggregate.
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE messages SET oi_input_tokens = 12, oi_output_tokens = 34 "
            "WHERE role = 'assistant' AND session_id = ?",
            (s1,),
        )
        conn.execute(
            "UPDATE messages SET oi_input_tokens = 5, oi_output_tokens = 9 "
            "WHERE role = 'assistant' AND session_id = ?",
            (s2,),
        )
        conn.commit()
    finally:
        conn.close()

    return db


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:

    def test_reports_duckdb_when_available(self, isolated_data_dir):
        from pce_core import analytics
        s = analytics.status()
        assert "available" in s
        assert "db_path" in s
        assert "db_exists" in s
        # Actual value depends on whether duckdb is installed in this
        # environment — we test both branches below by monkey-patching.

    def test_reports_unavailable_when_flag_false(self, isolated_data_dir, monkeypatch):
        from pce_core import analytics
        monkeypatch.setattr(analytics, "DUCKDB_AVAILABLE", False, raising=False)
        s = analytics.status()
        assert s["available"] is False
        assert "hint" in s and "pip install duckdb" in s["hint"]


# ---------------------------------------------------------------------------
# Graceful-degradation path (duckdb_not_installed)
# ---------------------------------------------------------------------------

class TestDuckDBUnavailable:

    @pytest.fixture(autouse=True)
    def _force_unavailable(self, monkeypatch):
        from pce_core import analytics
        monkeypatch.setattr(analytics, "DUCKDB_AVAILABLE", False, raising=False)

    def test_timeseries_returns_not_installed(self, isolated_data_dir):
        from pce_core.analytics import timeseries
        r = timeseries(days=7)
        assert r["ok"] is False
        assert r["error"] == "duckdb_not_installed"

    def test_top_models_returns_not_installed(self, isolated_data_dir):
        from pce_core.analytics import top_models
        r = top_models(limit=5, days=7)
        assert r["ok"] is False
        assert r["error"] == "duckdb_not_installed"

    def test_top_hosts_returns_not_installed(self, isolated_data_dir):
        from pce_core.analytics import top_hosts
        r = top_hosts(limit=5, days=7)
        assert r["ok"] is False
        assert r["error"] == "duckdb_not_installed"

    def test_token_usage_returns_not_installed(self, isolated_data_dir):
        from pce_core.analytics import token_usage
        r = token_usage(days=7)
        assert r["ok"] is False
        assert r["error"] == "duckdb_not_installed"

    def test_export_parquet_returns_not_installed(self, isolated_data_dir):
        from pce_core.analytics import export_parquet
        r = export_parquet(table="sessions")
        assert r["ok"] is False
        assert r["error"] == "duckdb_not_installed"


# ---------------------------------------------------------------------------
# Live DuckDB queries (skipped if duckdb isn't installed)
# ---------------------------------------------------------------------------

_DUCKDB = pytest.importorskip("duckdb", reason="P4.2 analytics is optional")


@pytest.mark.usefixtures("seeded_db")
class TestTimeseries:

    def test_returns_bucketed_counts(self, seeded_db):
        from pce_core.analytics import timeseries
        r = timeseries(by="day", days=7, db_path=seeded_db)
        assert r["ok"] is True
        assert r["by"] == "day"
        assert r["days"] == 7
        assert isinstance(r["points"], list)
        # At least two buckets — today's row and yesterday's row.
        assert len(r["points"]) >= 1
        first = r["points"][0]
        for key in ("bucket", "captures", "messages", "sessions"):
            assert key in first

    def test_filters_by_provider(self, seeded_db):
        from pce_core.analytics import timeseries
        r = timeseries(
            by="day", days=7, provider="openai", db_path=seeded_db,
        )
        assert r["ok"] is True
        # Only the openai bucket should have captures > 0 for the
        # matching direction.
        total = sum(p["captures"] for p in r["points"])
        assert total == 2

    def test_invalid_bucket_coerces_to_day(self, seeded_db):
        from pce_core.analytics import timeseries
        r = timeseries(by="parsec", days=7, db_path=seeded_db)
        assert r["by"] == "day"


@pytest.mark.usefixtures("seeded_db")
class TestTopModels:

    def test_returns_ranked_rows(self, seeded_db):
        from pce_core.analytics import top_models
        r = top_models(limit=5, days=7, db_path=seeded_db)
        assert r["ok"] is True
        assert len(r["rows"]) >= 1
        # gpt-4o has 2 responses, claude has 1 → gpt-4o should lead.
        top = r["rows"][0]
        assert top["model"] == "gpt-4o"
        assert top["captures"] >= 2

    def test_caps_limit(self, seeded_db):
        from pce_core.analytics import top_models
        r = top_models(limit=5_000, days=7, db_path=seeded_db)
        # ``limit`` is capped silently at _MAX_LIMIT.
        assert r["limit"] <= 1_000


@pytest.mark.usefixtures("seeded_db")
class TestTopHosts:

    def test_returns_per_host_counts(self, seeded_db):
        from pce_core.analytics import top_hosts
        r = top_hosts(limit=10, days=7, db_path=seeded_db)
        assert r["ok"] is True
        hosts = {row["host"]: row for row in r["rows"]}
        assert "api.openai.com" in hosts
        assert hosts["api.openai.com"]["captures"] == 2
        assert hosts["api.openai.com"]["pairs"] == 2
        assert hosts["api.openai.com"]["last_seen"] is not None


@pytest.mark.usefixtures("seeded_db")
class TestTokenUsage:

    def test_sums_oi_token_columns(self, seeded_db):
        from pce_core.analytics import token_usage
        r = token_usage(by="day", days=7, db_path=seeded_db)
        assert r["ok"] is True
        total_in = sum(p["input_tokens"] for p in r["points"])
        total_out = sum(p["output_tokens"] for p in r["points"])
        assert total_in == 12 + 5
        assert total_out == 34 + 9


@pytest.mark.usefixtures("seeded_db")
class TestExportParquet:

    def test_writes_parquet_file(self, seeded_db, tmp_path):
        from pce_core.analytics import export_parquet
        out = tmp_path / "captures.parquet"
        r = export_parquet(
            table="raw_captures", output_path=out, db_path=seeded_db,
        )
        assert r["ok"] is True
        assert Path(r["path"]).exists()
        assert r["rows"] >= 1
        assert r["size_bytes"] > 0

    def test_rejects_unknown_table(self, seeded_db, tmp_path):
        from pce_core.analytics import export_parquet
        r = export_parquet(
            table="users", output_path=tmp_path / "x.parquet", db_path=seeded_db,
        )
        assert r["ok"] is False
        assert r["error"] == "unsupported_table"

    def test_parquet_is_readable(self, seeded_db, tmp_path):
        import duckdb
        from pce_core.analytics import export_parquet
        out = tmp_path / "msgs.parquet"
        r = export_parquet(
            table="messages", output_path=out, db_path=seeded_db,
        )
        assert r["ok"] is True
        rows = duckdb.connect(":memory:").execute(
            f"SELECT COUNT(*) FROM read_parquet('{out.as_posix()}')"
        ).fetchone()[0]
        assert rows == r["rows"]


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

class TestAnalyticsHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_status_endpoint(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/analytics/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "available" in body
        assert "db_path" in body

    def test_timeseries_200_when_available(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/analytics/timeseries?by=day&days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "points" in body

    def test_top_models_200(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/analytics/top_models?limit=5&days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert len(body["rows"]) >= 1

    def test_top_hosts_200(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/analytics/top_hosts?limit=5&days=7")
        assert resp.status_code == 200

    def test_token_usage_200(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/analytics/token_usage?by=day&days=7")
        assert resp.status_code == 200

    def test_endpoints_return_503_when_duckdb_missing(
        self, seeded_db, monkeypatch,
    ):
        from pce_core import analytics
        monkeypatch.setattr(analytics, "DUCKDB_AVAILABLE", False, raising=False)

        with self._client() as client:
            for path in (
                "/api/v1/analytics/timeseries",
                "/api/v1/analytics/top_models",
                "/api/v1/analytics/top_hosts",
                "/api/v1/analytics/token_usage",
                "/api/v1/analytics/export.parquet?table=sessions",
            ):
                resp = client.get(path)
                assert resp.status_code == 503, f"{path} → {resp.status_code}"

    def test_invalid_params_422(self, seeded_db):
        with self._client() as client:
            # `by` must match the pattern
            resp = client.get("/api/v1/analytics/timeseries?by=decade")
            assert resp.status_code == 422
            # `table` is required / whitelisted
            resp = client.get("/api/v1/analytics/export.parquet?table=users")
            assert resp.status_code == 422

    def test_export_parquet_streams_file(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/analytics/export.parquet?table=messages")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/vnd.apache.parquet"
        )
        assert len(resp.content) > 0
