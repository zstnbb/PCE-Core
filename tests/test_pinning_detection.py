# SPDX-License-Identifier: Apache-2.0
"""P5.A-6 — TLS pinning detection tests.

Covers migration 0007 + the record_tls_failure / query_pinning_stats
helpers + the GET /api/v1/health/pinning endpoint. Also exercises the
``_categorize_tls_error`` / ``_extract_tls_host`` pure helpers in the
proxy addon so the categorisation stays stable across OpenSSL versions.
"""
from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path

import pytest

from pce_core.db import (
    SOURCE_PROXY,
    insert_capture,
    query_pinning_stats,
    record_tls_failure,
    prune_tls_failures,
)
from pce_core.migrations import apply_migrations, discover_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reloaded_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PCE_RETENTION_DAYS", raising=False)

    import pce_core.config as _cfg
    import pce_core.db as _db

    importlib.reload(_cfg)
    importlib.reload(_db)
    _db.init_db()
    return _db


@pytest.fixture
def reloaded_server(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import pce_core.config as _cfg
    import pce_core.db as _db
    import pce_core.otel_exporter as _otel
    import pce_core.server as _server

    importlib.reload(_cfg)
    importlib.reload(_db)
    _otel._initialised = False
    _otel._enabled = False
    importlib.reload(_server)
    # Ensure migration 0007 has run before tests record failures against
    # the freshly-reloaded DB (server lifespan also does this, but our
    # test bodies insert rows *before* entering ``with client:``).
    _db.init_db()
    return _db, _server


# ---------------------------------------------------------------------------
# Migration 0007
# ---------------------------------------------------------------------------

class TestMigration0007:
    def test_migration_0007_in_discovery(self):
        versions = [m.version for m in discover_migrations()]
        assert 7 in versions

    def test_tls_failures_table_created(self, tmp_path):
        db = tmp_path / "m.db"
        conn = sqlite3.connect(db)
        try:
            apply_migrations(conn)
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(tls_failures)"
            ).fetchall()}
            assert cols == {
                "id", "created_at", "host",
                "error_category", "error_message",
            }
        finally:
            conn.close()

    def test_tls_failures_indexes_present(self, tmp_path):
        db = tmp_path / "m.db"
        conn = sqlite3.connect(db)
        try:
            apply_migrations(conn)
            idx = {r[1] for r in conn.execute(
                "PRAGMA index_list(tls_failures)"
            ).fetchall()}
            assert "idx_tls_failures_host_time" in idx
            assert "idx_tls_failures_time" in idx
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# record_tls_failure
# ---------------------------------------------------------------------------

class TestRecordTLSFailure:
    def test_inserts_row(self, reloaded_db):
        record_tls_failure("claude.ai", "cert_rejected", "OpenSSL: bad cert")
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            row = conn.execute(
                "SELECT host, error_category, error_message FROM tls_failures"
            ).fetchone()
            assert row == ("claude.ai", "cert_rejected", "OpenSSL: bad cert")
        finally:
            conn.close()

    def test_empty_host_is_ignored(self, reloaded_db):
        record_tls_failure("", "cert_rejected", "noop")
        record_tls_failure(None, "cert_rejected", "noop")  # type: ignore[arg-type]
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM tls_failures"
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()

    def test_message_is_capped(self, reloaded_db):
        long = "x" * 10_000
        record_tls_failure("claude.ai", "cert_rejected", long)
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            msg = conn.execute(
                "SELECT error_message FROM tls_failures"
            ).fetchone()[0]
            assert len(msg) == 500  # _TLS_FAILURE_MESSAGE_CAP
        finally:
            conn.close()

    def test_empty_category_stored_as_unknown(self, reloaded_db):
        record_tls_failure("claude.ai", "", "")
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            cat = conn.execute(
                "SELECT error_category FROM tls_failures"
            ).fetchone()[0]
            assert cat == "unknown"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# query_pinning_stats
# ---------------------------------------------------------------------------

class TestQueryPinningStats:
    def test_empty_report_when_no_failures(self, reloaded_db):
        report = query_pinning_stats()
        assert report["suspected_pinning_count"] == 0
        assert report["hosts"] == []

    def test_host_flagged_when_above_both_thresholds(self, reloaded_db):
        # 10 failures, 0 successes → rate 1.0, >0.2, >=5
        for _ in range(10):
            record_tls_failure("pinned.example", "cert_rejected", "x")
        report = query_pinning_stats()
        assert report["suspected_pinning_count"] == 1
        entry = report["hosts"][0]
        assert entry["host"] == "pinned.example"
        assert entry["failures"] == 10
        assert entry["successes"] == 0
        assert entry["failure_rate"] == 1.0
        assert entry["suspected_pinning"] is True

    def test_host_NOT_flagged_when_below_min_failures(self, reloaded_db):
        # Only 3 failures: 100% rate but below min_failures=5
        for _ in range(3):
            record_tls_failure("occasional.example", "cert_rejected", "x")
        report = query_pinning_stats()
        entry = report["hosts"][0]
        assert entry["failures"] == 3
        assert entry["suspected_pinning"] is False
        assert report["suspected_pinning_count"] == 0

    def test_host_NOT_flagged_when_failure_rate_too_low(self, reloaded_db):
        # 5 failures but 100 successes → rate ~5% (< 20%)
        for _ in range(5):
            record_tls_failure("mostly-ok.example", "handshake_timeout", "x")
        for _ in range(100):
            insert_capture(
                direction="response", pair_id=f"p-{_}",
                host="mostly-ok.example", path="/",
                method="GET", provider="openai",
                source_id=SOURCE_PROXY,
            )
        report = query_pinning_stats()
        entry = next(h for h in report["hosts"] if h["host"] == "mostly-ok.example")
        assert entry["failures"] == 5
        assert entry["successes"] == 100
        assert entry["failure_rate"] < 0.20
        assert entry["suspected_pinning"] is False

    def test_custom_thresholds_honored(self, reloaded_db):
        # 3 failures — default min_failures=5 would skip, but custom=2 flags it
        for _ in range(3):
            record_tls_failure("tiny.example", "cert_rejected", "x")
        default = query_pinning_stats()
        assert default["hosts"][0]["suspected_pinning"] is False

        tuned = query_pinning_stats(
            min_failures=2, failure_rate_threshold=0.1,
        )
        assert tuned["hosts"][0]["suspected_pinning"] is True

    def test_window_excludes_old_failures(self, reloaded_db):
        # Insert an "old" failure manually (backdated by 48h)
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            past = time.time() - 48 * 3600
            for _ in range(10):
                conn.execute(
                    "INSERT INTO tls_failures "
                    "(created_at, host, error_category, error_message) "
                    "VALUES (?, ?, ?, ?)",
                    (past, "stale.example", "cert_rejected", ""),
                )
            conn.commit()
        finally:
            conn.close()

        # Default window = 24h — should NOT include the 48h-old failures
        report = query_pinning_stats(window_hours=24.0)
        assert report["hosts"] == []

        # Larger window DOES include them
        report = query_pinning_stats(window_hours=72.0)
        assert any(h["host"] == "stale.example" for h in report["hosts"])

    def test_only_proxy_source_captures_count_as_successes(self, reloaded_db):
        # Browser-extension captures shouldn't help a host's "success" count —
        # the TLS handshake the proxy cares about happens on source_id=proxy.
        for _ in range(5):
            record_tls_failure("mixed.example", "cert_rejected", "")
        # 50 browser-ext captures for the same host
        from pce_core.db import SOURCE_BROWSER_EXT
        for i in range(50):
            insert_capture(
                direction="response", pair_id=f"mix-{i}",
                host="mixed.example", path="/",
                method="GET", provider="openai",
                source_id=SOURCE_BROWSER_EXT,
            )
        report = query_pinning_stats()
        entry = next(h for h in report["hosts"] if h["host"] == "mixed.example")
        # successes from browser-ext shouldn't mask pinning
        assert entry["successes"] == 0
        assert entry["suspected_pinning"] is True


# ---------------------------------------------------------------------------
# prune_tls_failures
# ---------------------------------------------------------------------------

class TestPruneTLSFailures:
    def test_drops_old_rows(self, reloaded_db):
        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            stale = time.time() - 10 * 86400  # 10 days old
            for _ in range(3):
                conn.execute(
                    "INSERT INTO tls_failures "
                    "(created_at, host, error_category, error_message) "
                    "VALUES (?, ?, ?, ?)",
                    (stale, "stale.example", "x", ""),
                )
            fresh = time.time() - 3600  # 1 hour old
            conn.execute(
                "INSERT INTO tls_failures "
                "(created_at, host, error_category, error_message) "
                "VALUES (?, ?, ?, ?)",
                (fresh, "fresh.example", "x", ""),
            )
            conn.commit()
        finally:
            conn.close()

        deleted = prune_tls_failures()
        assert deleted == 3

        conn = sqlite3.connect(reloaded_db.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT host FROM tls_failures"
            ).fetchall()
            assert [r[0] for r in rows] == ["fresh.example"]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

class TestPinningEndpoint:
    def test_empty_endpoint_response(self, reloaded_server):
        _db, _server = reloaded_server
        from fastapi.testclient import TestClient
        client = TestClient(_server.app)
        with client:
            r = client.get("/api/v1/health/pinning")
            assert r.status_code == 200
            body = r.json()
            assert body["suspected_pinning_count"] == 0
            assert body["hosts"] == []
            assert body["window_hours"] == 24.0
            assert body["min_failures_threshold"] == 5
            assert body["failure_rate_threshold"] == 0.2

    def test_endpoint_reflects_recorded_failures(self, reloaded_server):
        _db, _server = reloaded_server
        from fastapi.testclient import TestClient
        for _ in range(6):
            _db.record_tls_failure("pinned.app", "cert_rejected", "x")
        client = TestClient(_server.app)
        with client:
            r = client.get("/api/v1/health/pinning")
            body = r.json()
            assert body["suspected_pinning_count"] == 1
            entry = body["hosts"][0]
            assert entry["host"] == "pinned.app"
            assert entry["failures"] == 6
            assert entry["suspected_pinning"] is True
            assert entry["last_error_category"] == "cert_rejected"

    def test_endpoint_respects_query_params(self, reloaded_server):
        _db, _server = reloaded_server
        from fastapi.testclient import TestClient
        for _ in range(3):
            _db.record_tls_failure("small.app", "cert_rejected", "x")

        client = TestClient(_server.app)
        with client:
            # Default thresholds: 3 failures is below min_failures=5
            r = client.get("/api/v1/health/pinning")
            assert r.json()["suspected_pinning_count"] == 0

            r = client.get(
                "/api/v1/health/pinning?min_failures=2&failure_rate_threshold=0.1"
            )
            body = r.json()
            assert body["suspected_pinning_count"] == 1
            assert body["min_failures_threshold"] == 2
            assert body["failure_rate_threshold"] == 0.1


# ---------------------------------------------------------------------------
# Proxy addon helpers (pure functions — no mitmproxy runtime required)
# ---------------------------------------------------------------------------

class TestTLSErrorCategorisation:
    @pytest.mark.parametrize("raw,expected", [
        ("", "unknown"),
        ("Unknown CA", "cert_unknown_ca"),
        ("SSL alert: certificate_unknown", "cert_unknown_ca"),
        ("Certificate was rejected by client", "cert_verify_failed"),
        ("certificate invalid", "cert_verify_failed"),
        ("bad_certificate", "cert_rejected"),
        ("pinning failure detected", "pinning_explicit"),
        ("pinned cert mismatch", "pinning_explicit"),
        ("TLS handshake_failure alert", "handshake_failure"),
        ("handshake timed out", "handshake_timeout"),
        ("unsupported protocol version", "protocol_version"),
        ("SSL internal error", "tls_other"),
        ("completely nothing useful", "unknown"),
    ])
    def test_categories_are_stable(self, raw, expected):
        from pce_proxy.addon import _categorize_tls_error
        assert _categorize_tls_error(raw) == expected


class TestTLSHostExtraction:
    def test_prefers_sni_string(self):
        from pce_proxy.addon import _extract_tls_host

        class FakeConn:
            sni = "claude.ai"
            error = None

        class FakeData:
            conn = FakeConn()
            context = None

        assert _extract_tls_host(FakeData()) == "claude.ai"

    def test_decodes_bytes_sni(self):
        from pce_proxy.addon import _extract_tls_host

        class FakeConn:
            sni = b"chatgpt.com"

        class FakeData:
            conn = FakeConn()
            context = None

        assert _extract_tls_host(FakeData()) == "chatgpt.com"

    def test_falls_back_to_peername(self):
        from pce_proxy.addon import _extract_tls_host

        class FakeConn:
            sni = None

        class FakeClient:
            peername = ("10.0.0.2", 443)

        class FakeContext:
            client = FakeClient()

        class FakeData:
            conn = FakeConn()
            context = FakeContext()

        assert _extract_tls_host(FakeData()) == "10.0.0.2"

    def test_returns_empty_when_nothing_available(self):
        from pce_proxy.addon import _extract_tls_host

        class FakeData:
            conn = None
            context = None

        assert _extract_tls_host(FakeData()) == ""
