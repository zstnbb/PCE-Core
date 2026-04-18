# SPDX-License-Identifier: Apache-2.0
"""Tests for the P4.1 semantic + hybrid search module.

We drive :mod:`pce_core.semantic_search` with a **stub embedding
backend** so the tests are fast, deterministic, and require no model
download. The stub's `embed_batch` hashes its input to produce a cheap
but content-sensitive vector, so cosine ranking behaves meaningfully.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    # ``pce_core.db`` imports DB_PATH/DATA_DIR *by value* at module load
    # time, so we have to rebind both the source (``config``) **and**
    # the copy in ``db`` for the HTTP tests to hit our seeded database.
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


@dataclass
class _StubBackend:
    """Deterministic 16-dim embedder for tests.

    Produces a unit vector whose direction depends on the input tokens
    so two sentences sharing vocabulary get high cosine similarity.
    """
    name: str = "stub"
    model_name: str = "stub-16"
    dim: int = 16
    available: bool = True
    reason: Optional[str] = None

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def _encode(self, text: str) -> list[float]:
        # Bag-of-words over character trigrams → 16-dim float vector.
        vec = [0.0] * self.dim
        tokens = text.lower().split()
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


@pytest.fixture
def stub_backend(monkeypatch) -> _StubBackend:
    """Force every ``get_backend()`` call to return our deterministic stub.

    Patching the function directly is cleaner than priming the internal
    cache because ``get_backend`` otherwise re-resolves the spec (env /
    prefs) and throws the cache away when they disagree.
    """
    from pce_core import embeddings
    embeddings.reset()
    stub = _StubBackend()
    monkeypatch.setattr(embeddings, "get_backend", lambda spec=None: stub)
    yield stub
    embeddings.reset()


@pytest.fixture
def seeded_db(tmp_db: Path) -> Path:
    """DB with three sessions, two messages each."""
    from pce_core.db import insert_message, insert_session

    s1 = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider="openai",
        title_hint="Python hello world",
        db_path=tmp_db,
    )
    insert_message(
        session_id=s1, ts=time.time(), role="user",
        content_text="write a hello world program in python",
        db_path=tmp_db,
    )
    insert_message(
        session_id=s1, ts=time.time(), role="assistant",
        content_text="here is a simple python hello world script",
        db_path=tmp_db,
    )

    s2 = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider="anthropic",
        title_hint="rust ownership",
        db_path=tmp_db,
    )
    insert_message(
        session_id=s2, ts=time.time(), role="user",
        content_text="explain ownership in rust programming language",
        db_path=tmp_db,
    )
    insert_message(
        session_id=s2, ts=time.time(), role="assistant",
        content_text="rust uses ownership and borrowing to manage memory",
        db_path=tmp_db,
    )

    s3 = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider="openai",
        title_hint="math derivative",
        db_path=tmp_db,
    )
    insert_message(
        session_id=s3, ts=time.time(), role="user",
        content_text="what is the derivative of x squared",
        db_path=tmp_db,
    )
    insert_message(
        session_id=s3, ts=time.time(), role="assistant",
        content_text="the derivative of x squared is two x",
        db_path=tmp_db,
    )

    return tmp_db


# ---------------------------------------------------------------------------
# Packing / unpacking
# ---------------------------------------------------------------------------

class TestPacking:

    def test_pack_unpack_roundtrip(self):
        from pce_core.semantic_search import _pack_vector, _unpack_vector
        vec = [0.1, -0.2, 0.3, 0.4]
        blob = _pack_vector(vec)
        assert len(blob) == 4 * 4
        out = _unpack_vector(blob, 4)
        for a, b in zip(vec, out):
            assert abs(a - b) < 1e-6

    def test_unpack_corrupt_blob_returns_empty(self):
        from pce_core.semantic_search import _unpack_vector
        assert _unpack_vector(b"\x00\x01\x02", 4) == []
        assert _unpack_vector(b"", 4) == []


# ---------------------------------------------------------------------------
# embed_message + backfill_embeddings
# ---------------------------------------------------------------------------

class TestEmbedWriteSide:

    def test_embed_message_writes_row(self, seeded_db, stub_backend):
        from pce_core.db import get_connection
        from pce_core.semantic_search import embed_message

        msg_id = get_connection(seeded_db).execute(
            "SELECT id FROM messages LIMIT 1"
        ).fetchone()[0]

        assert embed_message(msg_id, "hello", db_path=seeded_db) is True
        conn = get_connection(seeded_db)
        row = conn.execute(
            "SELECT backend, model_name, dim FROM message_embeddings "
            "WHERE message_id = ?",
            (msg_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "stub"
        assert row[1] == "stub-16"
        assert row[2] == 16

    def test_embed_message_empty_text_is_noop(self, seeded_db, stub_backend):
        from pce_core.semantic_search import embed_message
        assert embed_message("some-id", "   ", db_path=seeded_db) is False

    def test_backfill_covers_all_messages(self, seeded_db, stub_backend):
        from pce_core.db import get_connection
        from pce_core.semantic_search import backfill_embeddings

        report = backfill_embeddings(limit=100, db_path=seeded_db)
        assert report["ok"] is True
        assert report["backfilled"] == 6  # 3 sessions × 2 messages
        assert report["remaining"] == 0

        count = get_connection(seeded_db).execute(
            "SELECT COUNT(*) FROM message_embeddings"
        ).fetchone()[0]
        assert count == 6

    def test_backfill_is_idempotent(self, seeded_db, stub_backend):
        from pce_core.semantic_search import backfill_embeddings
        r1 = backfill_embeddings(limit=100, db_path=seeded_db)
        r2 = backfill_embeddings(limit=100, db_path=seeded_db)
        assert r1["backfilled"] == 6
        assert r2["backfilled"] == 0
        assert r2["remaining"] == 0

    def test_backfill_reports_unavailable_backend(self, seeded_db, monkeypatch):
        from pce_core import embeddings
        from pce_core.semantic_search import backfill_embeddings

        embeddings.reset()
        # Force the disabled backend.
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        report = backfill_embeddings(limit=10, db_path=seeded_db)
        assert report["ok"] is False
        assert report["backfilled"] == 0


# ---------------------------------------------------------------------------
# Search modes
# ---------------------------------------------------------------------------

class TestSearchModes:

    def test_mode_fts_always_works(self, seeded_db, monkeypatch):
        # No stub backend — semantic arm is disabled.
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        from pce_core import embeddings
        from pce_core.semantic_search import search
        embeddings.reset()

        results = search("hello", mode="fts", db_path=seeded_db)
        assert len(results) >= 1
        assert any("hello" in r["content_text"] for r in results)

    def test_semantic_falls_back_to_fts_when_backend_missing(
        self, seeded_db, monkeypatch,
    ):
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        from pce_core import embeddings
        from pce_core.semantic_search import search
        embeddings.reset()

        results = search("hello", mode="semantic", db_path=seeded_db)
        # Degrades to FTS results rather than raising.
        assert isinstance(results, list)
        assert any("hello" in r["content_text"] for r in results)

    def test_semantic_returns_knn_results(self, seeded_db, stub_backend):
        from pce_core.semantic_search import backfill_embeddings, semantic_search

        backfill_embeddings(limit=100, db_path=seeded_db)
        results = semantic_search(
            "python hello program", limit=3, db_path=seeded_db,
        )
        assert len(results) >= 1
        assert all(r["match_mode"] == "semantic" for r in results)
        # The top hit should reference Python since the query shares tokens
        # with the Python hello-world session.
        assert any("python" in r["content_text"].lower() for r in results[:2])

    def test_semantic_filters_by_provider(self, seeded_db, stub_backend):
        from pce_core.semantic_search import backfill_embeddings, semantic_search
        backfill_embeddings(limit=100, db_path=seeded_db)

        results = semantic_search(
            "hello", provider="anthropic", limit=10, db_path=seeded_db,
        )
        assert all(r["provider"] == "anthropic" for r in results)

    def test_hybrid_merges_fts_and_semantic(self, seeded_db, stub_backend):
        from pce_core.semantic_search import backfill_embeddings, hybrid_search

        backfill_embeddings(limit=100, db_path=seeded_db)
        results = hybrid_search("python hello", limit=5, db_path=seeded_db)
        assert len(results) >= 1
        for r in results:
            # RRF attaches a merged score.
            assert "rrf_score" in r
            assert r["match_mode"] == "hybrid"

    def test_empty_query_returns_empty(self, seeded_db, stub_backend):
        from pce_core.semantic_search import search
        assert search("", mode="semantic", db_path=seeded_db) == []
        assert search("   ", mode="hybrid", db_path=seeded_db) == []

    def test_invalid_mode_falls_back_to_fts(self, seeded_db, stub_backend):
        from pce_core.semantic_search import search
        results = search("hello", mode="nonsense", db_path=seeded_db)
        # Should behave like mode=fts.
        assert all(r.get("match_mode") != "semantic" for r in results)


# ---------------------------------------------------------------------------
# embeddings_status
# ---------------------------------------------------------------------------

class TestEmbeddingsStatus:

    def test_shape_when_empty(self, seeded_db, monkeypatch):
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        from pce_core import embeddings
        from pce_core.semantic_search import embeddings_status
        embeddings.reset()

        status = embeddings_status(db_path=seeded_db)
        assert status["backend"]["available"] is False
        assert status["messages_total"] == 6
        assert status["messages_covered"] == 0
        assert status["coverage_pct"] == 0.0
        assert status["by_model"] == []

    def test_reports_coverage_after_backfill(self, seeded_db, stub_backend):
        from pce_core.semantic_search import (
            backfill_embeddings, embeddings_status,
        )
        backfill_embeddings(limit=100, db_path=seeded_db)
        status = embeddings_status(db_path=seeded_db)
        assert status["messages_covered"] == 6
        assert status["coverage_pct"] == 100.0
        assert len(status["by_model"]) == 1


# ---------------------------------------------------------------------------
# /api/v1 HTTP surface
# ---------------------------------------------------------------------------

class TestSearchHttp:

    def _client(self):
        from fastapi.testclient import TestClient
        from pce_core.server import app
        return TestClient(app)

    def test_search_mode_fts_backcompat(self, seeded_db, monkeypatch):
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        from pce_core import embeddings
        embeddings.reset()

        with self._client() as client:
            resp = client.get("/api/v1/search?q=hello")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_search_mode_semantic_endpoint(self, seeded_db, stub_backend):
        from pce_core.semantic_search import backfill_embeddings
        backfill_embeddings(limit=100, db_path=seeded_db)

        with self._client() as client:
            resp = client.get("/api/v1/search?q=python+hello&mode=semantic")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        # Semantic results carry the semantic match_mode.
        if body:
            assert all(r.get("match_mode") == "semantic" for r in body)

    def test_search_mode_hybrid_endpoint(self, seeded_db, stub_backend):
        from pce_core.semantic_search import backfill_embeddings
        backfill_embeddings(limit=100, db_path=seeded_db)

        with self._client() as client:
            resp = client.get("/api/v1/search?q=python&mode=hybrid")
        assert resp.status_code == 200
        body = resp.json()
        for r in body:
            assert r.get("match_mode") == "hybrid"

    def test_search_rejects_invalid_mode(self, seeded_db):
        with self._client() as client:
            resp = client.get("/api/v1/search?q=x&mode=bogus")
        # Pattern validation catches it at FastAPI level.
        assert resp.status_code == 422

    def test_status_endpoint(self, seeded_db, monkeypatch):
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        from pce_core import embeddings
        embeddings.reset()

        with self._client() as client:
            resp = client.get("/api/v1/embeddings/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "backend" in body
        assert "messages_total" in body

    def test_backfill_endpoint(self, seeded_db, stub_backend):
        with self._client() as client:
            resp = client.post("/api/v1/embeddings/backfill?limit=100")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["backfilled"] >= 1
