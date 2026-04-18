# SPDX-License-Identifier: Apache-2.0
"""Semantic / hybrid search over the normalized messages layer (P4.1).

Three search modes are exposed:

``fts``
    FTS5 / LIKE text search (existing behaviour from ``db.search_messages``).
    This is the default and the fallback when embeddings are disabled.

``semantic``
    Embed the query with the configured :mod:`pce_core.embeddings` backend
    and rank stored ``message_embeddings`` rows by cosine similarity.
    Returns ``[]`` (not an error) when the backend is unavailable so
    callers can gracefully degrade.

``hybrid``
    Run FTS and semantic in parallel and merge the two rank lists via
    Reciprocal Rank Fusion (``RRF(d) = Σ 1 / (k + rank_i(d))``) with the
    standard ``k=60``. Works even when one arm returns nothing.

Storage format (see migration 0004)::

    message_embeddings(
        message_id  TEXT PRIMARY KEY,
        backend     TEXT,        -- backend name at embed time
        model_name  TEXT,        -- model identifier
        dim         INTEGER,
        vector      BLOB,        -- little-endian float32 array
        created_at  REAL,
    )

Brute-force cosine is pure Python; it's comfortably under 50 ms for the
single-user DB sizes PCE targets. We can swap in ``sqlite-vec`` for KNN
acceleration later without changing the stored format.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import struct
import time
from pathlib import Path
from typing import Iterable, Optional

from . import embeddings as _embeddings
from .db import get_connection, search_messages as _fts_search_messages

logger = logging.getLogger("pce.semantic_search")

# Reciprocal rank fusion constant. 60 is the canonical value from Cormack
# et al. 2009 — small enough that top-ranked items dominate, large enough
# that tail differences still count.
_RRF_K = 60

# Guardrail: refuse to materialise more than this many rows in memory at
# once during brute-force KNN. Above this threshold we still return
# results but log a warning — the right fix is sqlite-vec.
_BRUTE_FORCE_WARN_AT = 50_000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search(
    query: str,
    *,
    mode: str = "fts",
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Unified search entry point used by ``/api/v1/search``.

    ``mode`` is ``"fts"``, ``"semantic"`` or ``"hybrid"`` — anything else
    falls back to ``"fts"``. When semantic is requested but the backend
    is unavailable we transparently fall back to FTS so the UI is never
    empty just because the user didn't configure embeddings.
    """
    q = (query or "").strip()
    if not q:
        return []

    mode = (mode or "fts").strip().lower()
    if mode not in {"fts", "semantic", "hybrid"}:
        mode = "fts"

    if mode == "fts":
        return _fts_search_messages(q, provider=provider, limit=limit, db_path=db_path)

    backend = _embeddings.get_backend()
    if not backend.available:
        # Semantic / hybrid degrades silently to FTS.
        logger.info(
            "semantic search requested but backend unavailable "
            "(%s: %s) — falling back to FTS",
            backend.name, backend.reason,
        )
        return _fts_search_messages(q, provider=provider, limit=limit, db_path=db_path)

    if mode == "semantic":
        return semantic_search(q, provider=provider, limit=limit, db_path=db_path)
    # hybrid
    return hybrid_search(q, provider=provider, limit=limit, db_path=db_path)


def semantic_search(
    query: str,
    *,
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Vector KNN over ``message_embeddings``.

    Returns the same dict shape as ``db.search_messages`` so the
    dashboard can render either uniformly. Each row gets an extra
    ``score`` (cosine similarity, 0–1) and ``match_mode='semantic'``.
    """
    backend = _embeddings.get_backend()
    if not backend.available:
        return []
    query_vec = _embeddings.embed_texts([query])
    if not query_vec or not query_vec[0]:
        return []
    qv = _normalise(query_vec[0])

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Only compare against embeddings produced by the currently-
        # configured backend+model; different dims can't be cosine'd.
        rows = conn.execute(
            """
            SELECT me.message_id, me.dim, me.vector, me.backend, me.model_name,
                   m.id, m.session_id, m.role, m.content_text, m.model_name as msg_model,
                   m.ts, m.token_estimate,
                   s.provider, s.title_hint, s.started_at as session_started,
                   s.tool_family
            FROM message_embeddings me
            JOIN messages m ON m.id = me.message_id
            JOIN sessions s ON m.session_id = s.id
            WHERE me.backend = ? AND me.model_name = ?
              AND (? IS NULL OR s.provider = ?)
            """,
            (backend.name, backend.model_name, provider, provider),
        ).fetchall()

        if len(rows) > _BRUTE_FORCE_WARN_AT:
            logger.warning(
                "brute-force KNN over %d embeddings is slow — "
                "consider installing sqlite-vec",
                len(rows),
            )

        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            vec = _unpack_vector(row["vector"], int(row["dim"]))
            if len(vec) != len(qv):
                continue
            score = _cosine_similarity_unit(qv, _normalise(vec))
            scored.append((score, row))

        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:limit]
        return [_row_to_dict(r, score=s, mode="semantic") for s, r in top]
    finally:
        conn.close()


def hybrid_search(
    query: str,
    *,
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Merge FTS and semantic rankings via RRF.

    We pull ``limit * 3`` from each arm to give RRF room to promote a
    result that's rank-7 in both arms over a rank-1 that only appears
    once. Duplicates across arms are de-duped by ``message_id``.
    """
    expand_k = max(limit * 3, limit + 10)

    fts_results = _fts_search_messages(
        query, provider=provider, limit=expand_k, db_path=db_path,
    )
    sem_results = semantic_search(
        query, provider=provider, limit=expand_k, db_path=db_path,
    )

    scores: dict[str, float] = {}
    blob: dict[str, dict] = {}

    for rank, item in enumerate(fts_results):
        mid = item.get("id")
        if not mid:
            continue
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        blob.setdefault(mid, dict(item, match_mode="hybrid"))

    for rank, item in enumerate(sem_results):
        mid = item.get("id")
        if not mid:
            continue
        scores[mid] = scores.get(mid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        if mid in blob:
            # Semantic arm has an extra ``score`` — keep the richest row.
            blob[mid]["semantic_score"] = item.get("score")
        else:
            blob[mid] = dict(item, match_mode="hybrid")

    if not scores:
        return []

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    out: list[dict] = []
    for mid, rrf in ordered:
        item = blob[mid]
        item["rrf_score"] = round(rrf, 6)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Embedding write-side
# ---------------------------------------------------------------------------

def embed_message(
    message_id: str,
    text: str,
    *,
    db_path: Optional[Path] = None,
) -> bool:
    """Compute and store the embedding for a single message. Idempotent.

    Returns ``True`` if we wrote a vector, ``False`` if the backend was
    unavailable or the text was empty. Never raises.
    """
    text = (text or "").strip()
    if not text:
        return False
    vecs = _embeddings.embed_texts([text])
    if not vecs or not vecs[0]:
        return False
    return _store_vectors(
        [message_id], vecs, db_path=db_path,
    ) == 1


def backfill_embeddings(
    *,
    limit: int = 500,
    db_path: Optional[Path] = None,
) -> dict:
    """Embed up to ``limit`` messages that don't yet have a vector.

    Intended to be run opportunistically (e.g. from a background thread
    or a dashboard button). Returns a structured report so callers can
    surface progress.
    """
    backend = _embeddings.get_backend()
    if not backend.available:
        return {
            "ok": False,
            "reason": backend.reason or "backend_unavailable",
            "backend": backend.name,
            "backfilled": 0,
        }

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content_text
            FROM messages m
            LEFT JOIN message_embeddings me
                   ON me.message_id = m.id
                  AND me.backend = ?
                  AND me.model_name = ?
            WHERE me.message_id IS NULL
              AND m.content_text IS NOT NULL
              AND length(m.content_text) > 0
            ORDER BY m.ts DESC
            LIMIT ?
            """,
            (backend.name, backend.model_name, int(limit)),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "ok": True,
            "backend": backend.name,
            "model": backend.model_name,
            "backfilled": 0,
            "remaining": 0,
        }

    ids = [r["id"] for r in rows]
    texts = [r["content_text"] or "" for r in rows]

    started = time.time()
    vectors = _embeddings.embed_texts(texts)
    if vectors is None:
        return {
            "ok": False,
            "reason": f"{backend.name}_embed_failed",
            "backfilled": 0,
        }

    written = _store_vectors(ids, vectors, db_path=db_path)

    remaining = _count_pending(backend.name, backend.model_name, db_path=db_path)
    return {
        "ok": True,
        "backend": backend.name,
        "model": backend.model_name,
        "backfilled": written,
        "duration_s": round(time.time() - started, 3),
        "remaining": remaining,
    }


def embeddings_status(db_path: Optional[Path] = None) -> dict:
    """Summarise embeddings coverage for the dashboard / health view."""
    backend = _embeddings.get_backend()
    conn = get_connection(db_path)
    try:
        total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        try:
            by_model = conn.execute(
                "SELECT backend, model_name, COUNT(*) AS c "
                "FROM message_embeddings GROUP BY backend, model_name"
            ).fetchall()
        except sqlite3.OperationalError:
            # Table doesn't exist yet (e.g. very old install that somehow
            # skipped migration 0004). Degrade gracefully.
            by_model = []
        covered = 0
        if backend.available and backend.name != "disabled":
            row = conn.execute(
                "SELECT COUNT(*) FROM message_embeddings "
                "WHERE backend = ? AND model_name = ?",
                (backend.name, backend.model_name),
            ).fetchone()
            covered = int(row[0]) if row else 0
    finally:
        conn.close()

    coverage_pct = round(covered / total_msgs * 100, 2) if total_msgs else 0.0
    return {
        "backend": {
            "name": backend.name,
            "model": backend.model_name,
            "dim": backend.dim,
            "available": bool(backend.available),
            "reason": backend.reason,
        },
        "messages_total": int(total_msgs),
        "messages_covered": int(covered),
        "coverage_pct": coverage_pct,
        "by_model": [
            {"backend": r[0], "model": r[1], "count": int(r[2])}
            for r in by_model
        ],
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _store_vectors(
    message_ids: list[str],
    vectors: list[list[float]],
    *,
    db_path: Optional[Path] = None,
) -> int:
    """Persist ``(id, vector)`` pairs. Returns the number actually written."""
    if not message_ids:
        return 0
    backend = _embeddings.get_backend()
    if not backend.available:
        return 0

    rows = []
    now = time.time()
    for mid, vec in zip(message_ids, vectors):
        if not vec:
            continue
        rows.append((
            mid,
            backend.name,
            backend.model_name,
            len(vec),
            _pack_vector(vec),
            now,
        ))

    if not rows:
        return 0

    conn = get_connection(db_path)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO message_embeddings
                (message_id, backend, model_name, dim, vector, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        logger.warning("store_vectors failed: %s", exc)
        return 0
    finally:
        conn.close()
    return len(rows)


def _count_pending(
    backend_name: str,
    model_name: str,
    db_path: Optional[Path] = None,
) -> int:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM messages m
            LEFT JOIN message_embeddings me
                   ON me.message_id = m.id
                  AND me.backend = ?
                  AND me.model_name = ?
            WHERE me.message_id IS NULL
              AND m.content_text IS NOT NULL
              AND length(m.content_text) > 0
            """,
            (backend_name, model_name),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _pack_vector(vec: Iterable[float]) -> bytes:
    data = list(vec)
    return struct.pack(f"<{len(data)}f", *data)


def _unpack_vector(blob: bytes, dim: int) -> list[float]:
    if not blob:
        return []
    expected = dim * 4  # float32 = 4 bytes
    if len(blob) != expected:
        # Corrupt / migrated vector — skip silently; the caller filters these.
        return []
    return list(struct.unpack(f"<{dim}f", blob))


def _normalise(vec: list[float]) -> list[float]:
    """L2-normalise the vector in place-like semantics. Returns a new list."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    inv = 1.0 / norm
    return [x * inv for x in vec]


def _cosine_similarity_unit(a: list[float], b: list[float]) -> float:
    """Cosine similarity assuming both inputs are already L2-normalised."""
    return sum(x * y for x, y in zip(a, b))


def _row_to_dict(row: sqlite3.Row, *, score: float, mode: str) -> dict:
    """Convert the semantic-search JOIN row into the dashboard's search shape."""
    text = row["content_text"] or ""
    # Lightweight snippet: first 160 chars with an ellipsis. Semantic
    # matches aren't substring-based so we don't do highlighting here.
    snippet = text[:160] + ("…" if len(text) > 160 else "")
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content_text": text,
        "model_name": row["msg_model"] if "msg_model" in row.keys() else None,
        "ts": row["ts"],
        "token_estimate": row["token_estimate"],
        "provider": row["provider"],
        "title_hint": row["title_hint"],
        "session_started": row["session_started"],
        "tool_family": row["tool_family"],
        "snippet": snippet,
        "score": round(float(score), 6),
        "match_mode": mode,
    }


__all__ = [
    "backfill_embeddings",
    "embed_message",
    "embeddings_status",
    "hybrid_search",
    "search",
    "semantic_search",
]
