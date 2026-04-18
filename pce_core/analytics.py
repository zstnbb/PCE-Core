# SPDX-License-Identifier: Apache-2.0
"""DuckDB-powered analytics layer over the SQLite source of truth (P4.2).

Why DuckDB?

- **Zero data duplication.** DuckDB's ``sqlite_scanner`` extension reads
  the live PCE SQLite file in read-only mode, so every analytics query
  sees the same bytes the ingest server just wrote. No ETL, no cron, no
  consistency window.
- **Fast columnar analytics.** GROUP BY / window functions / quantiles
  over ``raw_captures``, ``sessions`` and ``messages`` in sub-100 ms for
  the single-user data sizes PCE targets.
- **Parquet export out of the box.** ``COPY (SELECT …) TO 'x.parquet'``
  gives us a one-liner export that Langfuse / Polars / pandas can load
  directly.

Why not replace SQLite entirely?

- DuckDB isn't a row-store — it's optimised for analytics, not for
  high-frequency transactional writes.
- SQLite's FTS5 + triggers + WAL are perfect for the ingest hot path.
- Keeping SQLite as the source of truth means P0-P3's invariants (FTS
  coverage, retention, migrations, backups) stay intact.

The two engines play well together: SQLite owns the writer side,
DuckDB owns the reader analytics side, both see the same file.

Dependency posture
==================

``duckdb`` is an **optional** dependency. If it isn't installed every
function in this module short-circuits to a structured
``{"ok": False, "error": "duckdb_not_installed", ...}`` dict and the
matching HTTP endpoint returns ``503``. Same pattern as the Phoenix
integration — see ``pce_core.phoenix_integration``.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .config import DB_PATH
from .logging_config import log_event

logger = logging.getLogger("pce.analytics")


# ---------------------------------------------------------------------------
# Feature probe
# ---------------------------------------------------------------------------

try:
    import duckdb as _duckdb  # noqa: F401
    DUCKDB_AVAILABLE = True
    DUCKDB_VERSION: Optional[str] = getattr(_duckdb, "__version__", "unknown")
except Exception:
    DUCKDB_AVAILABLE = False
    DUCKDB_VERSION = None


# Cap every analytical query so a pathological filter on a hot DB can't
# lock up the server. Users who legitimately need more can pass
# ``limit`` / ``days`` explicitly within these bounds.
_MAX_LIMIT = 1_000
_MAX_DAYS = 365
_PARQUET_ROW_CAP = 1_000_000


# ---------------------------------------------------------------------------
# Public API (all "never-raise": errors surface in the return envelope)
# ---------------------------------------------------------------------------

def status() -> dict:
    """Report whether the analytics backend is ready."""
    return {
        "available": DUCKDB_AVAILABLE,
        "duckdb_version": DUCKDB_VERSION,
        "db_path": str(DB_PATH),
        "db_exists": Path(DB_PATH).exists(),
        "hint": (
            None if DUCKDB_AVAILABLE
            else "pip install duckdb (optional analytics backend, ~20 MB)"
        ),
    }


def timeseries(
    *,
    by: str = "day",
    days: int = 30,
    provider: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Captures / sessions / messages counted per time bucket.

    ``by`` ∈ {"hour", "day", "week"}. ``days`` caps the lookback window.
    """
    if not DUCKDB_AVAILABLE:
        return _unavailable()
    bucket = _validate_bucket(by)
    days = max(1, min(_MAX_DAYS, int(days)))
    cutoff = time.time() - days * 86_400

    # NB: we cast ``to_timestamp(x)`` to a naive TIMESTAMP before
    # ``date_trunc`` because DuckDB's TIMESTAMPTZ path pulls in ``pytz``,
    # which we don't want as a hard dep. The epoch math is identical;
    # we just interpret the bucket in UTC ourselves.
    sql = f"""
        WITH buckets AS (
            SELECT
                date_trunc('{bucket}', to_timestamp(created_at)::TIMESTAMP) AS bucket,
                COUNT(*) AS captures
            FROM pce.raw_captures
            WHERE created_at >= ?
              AND (? IS NULL OR provider = ?)
            GROUP BY bucket
        ),
        msgs AS (
            SELECT
                date_trunc('{bucket}', to_timestamp(m.ts)::TIMESTAMP) AS bucket,
                COUNT(*) AS messages,
                COUNT(DISTINCT m.session_id) AS sessions
            FROM pce.messages m
            JOIN pce.sessions s ON m.session_id = s.id
            WHERE m.ts >= ?
              AND (? IS NULL OR s.provider = ?)
            GROUP BY bucket
        )
        SELECT
            COALESCE(b.bucket, m.bucket) AS bucket,
            COALESCE(b.captures, 0)       AS captures,
            COALESCE(m.messages, 0)       AS messages,
            COALESCE(m.sessions, 0)       AS sessions
        FROM buckets b
        FULL OUTER JOIN msgs m ON b.bucket = m.bucket
        ORDER BY bucket
    """
    rows = _run_query(
        sql, (cutoff, provider, provider, cutoff, provider, provider),
        db_path=db_path,
    )
    if rows is None:
        return _query_failed()
    series = [
        {
            "bucket": _ts_to_iso(r[0]),
            "captures": int(r[1]),
            "messages": int(r[2]),
            "sessions": int(r[3]),
        }
        for r in rows
    ]
    return {
        "ok": True,
        "by": bucket,
        "days": days,
        "provider": provider,
        "points": series,
    }


def top_models(
    *,
    limit: int = 10,
    days: int = 30,
    db_path: Optional[Path] = None,
) -> dict:
    """Most-frequent ``model_name`` values on recent captures."""
    if not DUCKDB_AVAILABLE:
        return _unavailable()
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    days = max(1, min(_MAX_DAYS, int(days)))
    cutoff = time.time() - days * 86_400

    sql = """
        SELECT
            COALESCE(model_name, '(unknown)') AS model,
            COALESCE(provider,  '(unknown)')  AS provider,
            COUNT(*) AS captures
        FROM pce.raw_captures
        WHERE created_at >= ?
          AND direction IN ('response', 'conversation', 'network_intercept')
        GROUP BY model, provider
        ORDER BY captures DESC
        LIMIT ?
    """
    rows = _run_query(sql, (cutoff, limit), db_path=db_path)
    if rows is None:
        return _query_failed()
    return {
        "ok": True,
        "days": days,
        "limit": limit,
        "rows": [
            {"model": r[0], "provider": r[1], "captures": int(r[2])}
            for r in rows
        ],
    }


def top_hosts(
    *,
    limit: int = 10,
    days: int = 30,
    db_path: Optional[Path] = None,
) -> dict:
    """Most-active ``host`` values on recent captures."""
    if not DUCKDB_AVAILABLE:
        return _unavailable()
    limit = max(1, min(_MAX_LIMIT, int(limit)))
    days = max(1, min(_MAX_DAYS, int(days)))
    cutoff = time.time() - days * 86_400

    sql = """
        SELECT
            COALESCE(host, '(unknown)') AS host,
            COUNT(*)                    AS captures,
            COUNT(DISTINCT pair_id)     AS pairs,
            MAX(created_at)             AS last_seen
        FROM pce.raw_captures
        WHERE created_at >= ?
        GROUP BY host
        ORDER BY captures DESC
        LIMIT ?
    """
    rows = _run_query(sql, (cutoff, limit), db_path=db_path)
    if rows is None:
        return _query_failed()
    return {
        "ok": True,
        "days": days,
        "limit": limit,
        "rows": [
            {
                "host": r[0], "captures": int(r[1]),
                "pairs": int(r[2]), "last_seen": float(r[3]) if r[3] else None,
            }
            for r in rows
        ],
    }


def token_usage(
    *,
    by: str = "day",
    days: int = 30,
    db_path: Optional[Path] = None,
) -> dict:
    """Rollup of OpenInference token columns per time bucket."""
    if not DUCKDB_AVAILABLE:
        return _unavailable()
    bucket = _validate_bucket(by)
    days = max(1, min(_MAX_DAYS, int(days)))
    cutoff = time.time() - days * 86_400

    sql = f"""
        SELECT
            date_trunc('{bucket}', to_timestamp(m.ts)::TIMESTAMP) AS bucket,
            COALESCE(SUM(m.oi_input_tokens),  0) AS input_tokens,
            COALESCE(SUM(m.oi_output_tokens), 0) AS output_tokens,
            COUNT(*) FILTER (
                WHERE m.oi_input_tokens IS NOT NULL
                   OR m.oi_output_tokens IS NOT NULL
            ) AS counted_messages,
            COUNT(*) AS messages_total
        FROM pce.messages m
        WHERE m.ts >= ?
        GROUP BY bucket
        ORDER BY bucket
    """
    rows = _run_query(sql, (cutoff,), db_path=db_path)
    if rows is None:
        return _query_failed()
    return {
        "ok": True,
        "by": bucket,
        "days": days,
        "points": [
            {
                "bucket": _ts_to_iso(r[0]),
                "input_tokens": int(r[1]),
                "output_tokens": int(r[2]),
                "total_tokens": int(r[1]) + int(r[2]),
                "counted_messages": int(r[3]),
                "messages_total": int(r[4]),
            }
            for r in rows
        ],
    }


def export_parquet(
    *,
    table: str,
    days: Optional[int] = None,
    db_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> dict:
    """Dump a whitelisted table into a Parquet file.

    Safe tables: ``raw_captures``, ``sessions``, ``messages``,
    ``message_embeddings``. Embeddings are exported as packed bytes for
    round-tripping to numpy with ``np.frombuffer``.

    Returns ``{"ok": True, "path": ..., "rows": N, "size_bytes": ...}``
    — callers are expected to stream the file back (see the HTTP
    endpoint in ``server.py``).
    """
    if not DUCKDB_AVAILABLE:
        return _unavailable()
    table = (table or "").strip().lower()
    if table not in {"raw_captures", "sessions", "messages", "message_embeddings"}:
        return {"ok": False, "error": "unsupported_table", "table": table}

    where = ""
    params: list[Any] = []
    if days is not None:
        days = max(1, min(_MAX_DAYS, int(days)))
        cutoff = time.time() - days * 86_400
        ts_col = "created_at" if table != "messages" else "ts"
        where = f"WHERE {ts_col} >= ?"
        params = [cutoff]

    # We always cap the dump. If a legitimate workload needs more the
    # user should call ``duckdb`` directly against the DB file.
    select_sql = f"SELECT * FROM pce.{table} {where} LIMIT {_PARQUET_ROW_CAP}"

    out_path = Path(output_path) if output_path else Path(
        tempfile.gettempdir(),
        f"pce-export-{table}-{int(time.time())}.parquet",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    copy_sql = (
        f"COPY ({select_sql}) TO '{_escape_single_quotes(str(out_path))}' "
        f"(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    try:
        _connect_and_run(copy_sql, params, db_path=db_path, fetch=False)
    except Exception as exc:
        logger.exception("parquet export failed for table=%s", table)
        return {"ok": False, "error": f"export_failed: {type(exc).__name__}: {exc}"}

    size = out_path.stat().st_size if out_path.exists() else 0
    # Row count — cheap SELECT on the parquet we just wrote.
    try:
        count_sql = f"SELECT COUNT(*) FROM read_parquet('{_escape_single_quotes(str(out_path))}')"
        rows = _connect_and_run(count_sql, [], db_path=db_path, fetch=True)
        row_count = int(rows[0][0]) if rows else 0
    except Exception:
        row_count = 0

    log_event(
        logger, "analytics.export_parquet",
        table=table, rows=row_count, path=str(out_path), size_bytes=size,
    )
    return {
        "ok": True,
        "table": table,
        "path": str(out_path),
        "rows": row_count,
        "size_bytes": size,
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _unavailable() -> dict:
    return {
        "ok": False,
        "error": "duckdb_not_installed",
        "hint": "pip install duckdb",
    }


def _query_failed() -> dict:
    return {"ok": False, "error": "query_failed"}


def _validate_bucket(by: str) -> str:
    by = (by or "").strip().lower()
    if by not in {"hour", "day", "week", "month"}:
        return "day"
    return by


def _ts_to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    # DuckDB returns a ``datetime.datetime``.
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _escape_single_quotes(s: str) -> str:
    return s.replace("'", "''")


def _run_query(
    sql: str,
    params: Any = (),
    *,
    db_path: Optional[Path] = None,
) -> Optional[list[tuple]]:
    """Execute ``sql`` returning a list of row tuples. ``None`` on failure."""
    try:
        return _connect_and_run(sql, params, db_path=db_path, fetch=True)
    except Exception:
        logger.exception("duckdb query failed")
        return None


def _connect_and_run(
    sql: str,
    params: Any,
    *,
    db_path: Optional[Path] = None,
    fetch: bool,
) -> Optional[list[tuple]]:
    """Open a fresh DuckDB connection, attach SQLite read-only, run, close.

    DuckDB connections are cheap; using a fresh one per request avoids
    any risk of schema-cache staleness when SQLite's structure changes
    underneath us (e.g. after a migration).
    """
    import duckdb  # noqa: WPS433 — lazy import so the module stays importable
    path = Path(db_path or DB_PATH)
    if not path.exists():
        logger.warning("duckdb: source SQLite db not found at %s", path)
        # Still return a shaped response so callers see an empty result
        # instead of a traceback.
        return []

    conn = duckdb.connect(":memory:")
    try:
        conn.execute("INSTALL sqlite")
        conn.execute("LOAD sqlite")
        # ``READ_ONLY`` keeps us from stepping on the live writer — the
        # extension also refuses to take the WAL write lock.
        conn.execute(
            f"ATTACH '{_escape_single_quotes(str(path))}' AS pce "
            f"(TYPE SQLITE, READ_ONLY)"
        )
        cur = conn.execute(sql, list(params) if params else [])
        if fetch:
            return cur.fetchall()
        return None
    finally:
        conn.close()


__all__ = [
    "DUCKDB_AVAILABLE",
    "DUCKDB_VERSION",
    "export_parquet",
    "status",
    "timeseries",
    "token_usage",
    "top_hosts",
    "top_models",
]
