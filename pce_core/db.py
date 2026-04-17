"""PCE Core – SQLite storage engine.

Implements:
- Tier 0: raw_captures (immutable fact layer)
- Tier 1: sources, sessions, messages (normalized view)
- Pipeline errors (internal observability, added in P0)

All writes are designed to be fail-safe: exceptions are logged but never
propagated to the caller so that upstream requests are not blocked.

Schema evolution is managed by ``pce_core.migrations`` — see that module
and its README for how to add new migrations.
"""

import json as _json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .config import DB_PATH, DATA_DIR
from .migrations import (
    EXPECTED_SCHEMA_VERSION,
    apply_migrations,
    get_current_version,
    get_migration_history,
)

logger = logging.getLogger("pce.db")

# Current capture payload format version — bump when the capture payload
# format changes. This is a *per-row* tag on raw_captures and is distinct
# from the database schema version tracked by ``pce_core.migrations``.
# v1: Original format (proxy-era)
# v2: Added meta_json, session_hint, schema_version, network_intercept direction
CAPTURE_SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Tier 0 + shared ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL DEFAULT 'proxy',
    tool_name       TEXT,
    install_mode    TEXT NOT NULL DEFAULT 'complete',
    active          INTEGER NOT NULL DEFAULT 1,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS raw_captures (
    id                      TEXT PRIMARY KEY,
    created_at              REAL NOT NULL,
    source_id               TEXT NOT NULL,
    direction               TEXT NOT NULL CHECK (direction IN ('request', 'response', 'conversation', 'network_intercept', 'clipboard')),
    pair_id                 TEXT NOT NULL,
    host                    TEXT,
    path                    TEXT,
    method                  TEXT,
    provider                TEXT,
    model_name              TEXT,
    status_code             INTEGER,
    latency_ms              REAL,
    headers_redacted_json   TEXT,
    body_text_or_json       TEXT,
    body_format             TEXT,
    error                   TEXT,
    session_hint            TEXT,
    meta_json               TEXT,
    schema_version          INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_captures_created   ON raw_captures(created_at);
CREATE INDEX IF NOT EXISTS idx_captures_pair       ON raw_captures(pair_id);
CREATE INDEX IF NOT EXISTS idx_captures_host       ON raw_captures(host);
CREATE INDEX IF NOT EXISTS idx_captures_provider   ON raw_captures(provider);
CREATE INDEX IF NOT EXISTS idx_captures_source     ON raw_captures(source_id);

-- Tier 1: Normalized Session Layer -----------------------------------------

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    provider        TEXT,
    tool_family     TEXT,
    session_key     TEXT,
    message_count   INTEGER DEFAULT 0,
    title_hint      TEXT,
    created_via     TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_started  ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_provider ON sessions(provider);
CREATE INDEX IF NOT EXISTS idx_sessions_key      ON sessions(session_key);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    capture_pair_id TEXT,
    ts              REAL NOT NULL,
    role            TEXT NOT NULL,
    content_text    TEXT,
    content_json    TEXT,
    model_name      TEXT,
    token_estimate  INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts      ON messages(ts);

-- FTS5 full-text search on messages -------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content_text,
    content='messages',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content_text)
    VALUES (new.rowid, new.content_text);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content_text)
    VALUES ('delete', old.rowid, old.content_text);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content_text)
    VALUES ('delete', old.rowid, old.content_text);
    INSERT INTO messages_fts(rowid, content_text)
    VALUES (new.rowid, new.content_text);
END;

-- Snippets (user-collected text selections) -----------------------------------

CREATE TABLE IF NOT EXISTS snippets (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    source_url      TEXT,
    source_domain   TEXT,
    provider        TEXT,
    category        TEXT NOT NULL DEFAULT 'general',
    content_text    TEXT NOT NULL,
    note            TEXT,
    favorited       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snippets_created   ON snippets(created_at);
CREATE INDEX IF NOT EXISTS idx_snippets_category  ON snippets(category);
CREATE INDEX IF NOT EXISTS idx_snippets_domain    ON snippets(source_domain);

CREATE VIRTUAL TABLE IF NOT EXISTS snippets_fts USING fts5(
    content_text,
    note,
    content='snippets',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS snippets_fts_ai AFTER INSERT ON snippets BEGIN
    INSERT INTO snippets_fts(rowid, content_text, note)
    VALUES (new.rowid, new.content_text, new.note);
END;

CREATE TRIGGER IF NOT EXISTS snippets_fts_ad AFTER DELETE ON snippets BEGIN
    INSERT INTO snippets_fts(snippets_fts, rowid, content_text, note)
    VALUES ('delete', old.rowid, old.content_text, old.note);
END;

CREATE TRIGGER IF NOT EXISTS snippets_fts_au AFTER UPDATE ON snippets BEGIN
    INSERT INTO snippets_fts(snippets_fts, rowid, content_text, note)
    VALUES ('delete', old.rowid, old.content_text, old.note);
    INSERT INTO snippets_fts(rowid, content_text, note)
    VALUES (new.rowid, new.content_text, new.note);
END;

-- Custom domains (dynamic allowlist) -----------------------------------------

CREATE TABLE IF NOT EXISTS custom_domains (
    domain          TEXT PRIMARY KEY,
    added_at        REAL NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    confidence      TEXT,
    reason          TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);
"""

# ---------------------------------------------------------------------------
# Well-known source IDs
# ---------------------------------------------------------------------------

SOURCE_PROXY = "proxy-default"
SOURCE_BROWSER_EXT = "browser-extension-default"
SOURCE_MCP = "mcp-default"

_DEFAULT_SOURCES = [
    (SOURCE_PROXY, "proxy", "mitmproxy", "complete", "default proxy source"),
    (SOURCE_BROWSER_EXT, "browser_extension", "chrome", "light", "default browser extension source"),
    (SOURCE_MCP, "mcp", "pce-mcp-server", "light", "default MCP server source"),
]


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _ensure_dir(data_dir: Optional[Path] = None) -> None:
    (data_dir or DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a new SQLite connection (caller is responsible for closing)."""
    path = db_path or DB_PATH
    _ensure_dir(path.parent)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialise the database, applying any pending schema migrations.

    Responsibilities delegated to ``pce_core.migrations``:
    - Create every baseline table / index / trigger (0001_baseline)
    - Handle legacy ad-hoc migrations for existing installs
    - Create the ``pipeline_errors`` table (0002_pipeline_errors)
    - Track current schema version in ``schema_meta``

    Raises:
        RuntimeError: if the database is at a schema version newer than
            this build expects (downgrade protection). The caller — typically
            the FastAPI lifespan — should treat this as fatal.
    """
    conn = get_connection(db_path)
    try:
        applied = apply_migrations(conn)
        logger.info(
            "database ready",
            extra={"event": "db.ready", "pce_fields": {
                "schema_version": applied,
                "expected_schema_version": EXPECTED_SCHEMA_VERSION,
                "capture_payload_version": CAPTURE_SCHEMA_VERSION,
                "db_path": str((db_path or DB_PATH)),
            }},
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def new_pair_id() -> str:
    return uuid.uuid4().hex[:16]


def new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Tier 0: raw_captures
# ---------------------------------------------------------------------------

def insert_capture(
    *,
    direction: str,
    pair_id: str,
    host: str,
    path: str,
    method: str,
    provider: str,
    model_name: Optional[str] = None,
    status_code: Optional[int] = None,
    latency_ms: Optional[float] = None,
    headers_redacted_json: str = "{}",
    body_text_or_json: str = "",
    body_format: str = "json",
    error: Optional[str] = None,
    session_hint: Optional[str] = None,
    meta_json: Optional[str] = None,
    schema_version: Optional[int] = None,
    source_id: str = SOURCE_PROXY,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a raw capture row. Returns the capture id, or None on failure."""
    capture_id = new_id()
    sv = schema_version if schema_version is not None else CAPTURE_SCHEMA_VERSION
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO raw_captures
                    (id, created_at, source_id, direction, pair_id, host, path,
                     method, provider, model_name, status_code, latency_ms,
                     headers_redacted_json, body_text_or_json, body_format,
                     error, session_hint, meta_json, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    time.time(),
                    source_id,
                    direction,
                    pair_id,
                    host,
                    path,
                    method,
                    provider,
                    model_name,
                    status_code,
                    latency_ms,
                    headers_redacted_json,
                    body_text_or_json,
                    body_format,
                    error,
                    session_hint,
                    meta_json,
                    sv,
                ),
            )
            conn.commit()
            return capture_id
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to insert capture – swallowing error to keep proxy alive")
        return None


def query_recent(n: int = 20, db_path: Optional[Path] = None) -> list[dict]:
    """Return the most recent *n* captures as dicts."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM raw_captures ORDER BY created_at DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_by_pair(pair_id: str, db_path: Optional[Path] = None) -> list[dict]:
    """Return all captures for a given pair_id."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM raw_captures WHERE pair_id = ? ORDER BY created_at", (pair_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_captures(
    *,
    last: int = 20,
    provider: Optional[str] = None,
    source_type: Optional[str] = None,
    host: Optional[str] = None,
    direction: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Filtered query on raw_captures."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params: list = []
        if provider:
            clauses.append("rc.provider = ?")
            params.append(provider)
        if source_type:
            clauses.append("s.source_type = ?")
            params.append(source_type)
        if host:
            clauses.append("rc.host = ?")
            params.append(host)
        if direction:
            clauses.append("rc.direction = ?")
            params.append(direction)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        params.append(last)
        sql = f"""
            SELECT rc.* FROM raw_captures rc
            JOIN sources s ON rc.source_id = s.id
            {where}
            ORDER BY rc.created_at DESC LIMIT ?
        """
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stats(db_path: Optional[Path] = None) -> dict:
    """Return summary statistics."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM raw_captures").fetchone()["c"]
        if total == 0:
            return {"total_captures": 0, "by_provider": {}, "by_source": {}, "by_direction": {}}

        by_provider = {
            r["provider"]: r["c"]
            for r in conn.execute(
                "SELECT provider, COUNT(*) as c FROM raw_captures GROUP BY provider ORDER BY c DESC"
            ).fetchall()
        }
        by_source = {
            r["source_type"]: r["c"]
            for r in conn.execute(
                "SELECT s.source_type, COUNT(*) as c FROM raw_captures rc "
                "JOIN sources s ON rc.source_id = s.id "
                "GROUP BY s.source_type ORDER BY c DESC"
            ).fetchall()
        }
        by_direction = {
            r["direction"]: r["c"]
            for r in conn.execute(
                "SELECT direction, COUNT(*) as c FROM raw_captures GROUP BY direction"
            ).fetchall()
        }
        earliest = conn.execute("SELECT MIN(created_at) as t FROM raw_captures").fetchone()["t"]
        latest = conn.execute("SELECT MAX(created_at) as t FROM raw_captures").fetchone()["t"]

        # Storage info
        import time as _time
        sessions_count = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
        messages_count = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()["c"]
        db_file = db_path or DB_PATH
        db_size_mb = round(db_file.stat().st_size / 1024 / 1024, 2) if db_file.exists() else 0
        oldest_days = round((_time.time() - earliest) / 86400, 1) if earliest else 0

        return {
            "total_captures": total,
            "by_provider": by_provider,
            "by_source": by_source,
            "by_direction": by_direction,
            "earliest": earliest,
            "latest": latest,
            "storage": {
                "db_size_mb": db_size_mb,
                "raw_captures_count": total,
                "sessions_count": sessions_count,
                "messages_count": messages_count,
                "oldest_capture_days": oldest_days,
            },
        }
    finally:
        conn.close()


def get_source_activity(db_path: Optional[Path] = None) -> dict:
    """Return per-source_id capture count and last activity timestamp."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT rc.source_id,
                   s.source_type,
                   COUNT(*) as capture_count,
                   MAX(rc.created_at) as last_seen
            FROM raw_captures rc
            JOIN sources s ON rc.source_id = s.id
            GROUP BY rc.source_id
            """
        ).fetchall()
        return {r["source_id"]: dict(r) for r in rows}
    finally:
        conn.close()


def get_capture_health(db_path: Optional[Path] = None) -> dict:
    """Return per-channel capture health with time-windowed counts.

    Returns a dict with per-source and per-direction breakdowns over
    multiple time windows (5 min, 1 hour, 24 hours, all time), plus
    recent provider activity and normalization success rate.
    """
    now = time.time()
    windows = {
        "5m": now - 300,
        "1h": now - 3600,
        "24h": now - 86400,
    }
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # ── Per source_id, time-windowed counts ────────────────────
        source_health = {}
        rows = conn.execute(
            """
            SELECT rc.source_id,
                   s.source_type,
                   COUNT(*) as total,
                   MAX(rc.created_at) as last_seen,
                   SUM(CASE WHEN rc.created_at >= ? THEN 1 ELSE 0 END) as count_5m,
                   SUM(CASE WHEN rc.created_at >= ? THEN 1 ELSE 0 END) as count_1h,
                   SUM(CASE WHEN rc.created_at >= ? THEN 1 ELSE 0 END) as count_24h
            FROM raw_captures rc
            JOIN sources s ON rc.source_id = s.id
            GROUP BY rc.source_id
            """,
            (windows["5m"], windows["1h"], windows["24h"]),
        ).fetchall()
        for r in rows:
            source_health[r["source_id"]] = {
                "source_type": r["source_type"],
                "total": r["total"],
                "last_seen": r["last_seen"],
                "count_5m": r["count_5m"],
                "count_1h": r["count_1h"],
                "count_24h": r["count_24h"],
            }

        # ── Per direction, time-windowed counts ────────────────────
        direction_rows = conn.execute(
            """
            SELECT direction,
                   COUNT(*) as total,
                   MAX(created_at) as last_seen,
                   SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as count_5m,
                   SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as count_1h,
                   SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as count_24h
            FROM raw_captures
            GROUP BY direction
            """,
            (windows["5m"], windows["1h"], windows["24h"]),
        ).fetchall()
        direction_health = {}
        for r in direction_rows:
            direction_health[r["direction"]] = {
                "total": r["total"],
                "last_seen": r["last_seen"],
                "count_5m": r["count_5m"],
                "count_1h": r["count_1h"],
                "count_24h": r["count_24h"],
            }

        # ── Recent provider activity ───────────────────────────────
        provider_rows = conn.execute(
            """
            SELECT provider,
                   COUNT(*) as count_1h,
                   MAX(created_at) as last_seen
            FROM raw_captures
            WHERE created_at >= ? AND provider IS NOT NULL AND provider != ''
            GROUP BY provider
            ORDER BY count_1h DESC
            """,
            (windows["1h"],),
        ).fetchall()
        recent_providers = [
            {"provider": r["provider"], "count_1h": r["count_1h"], "last_seen": r["last_seen"]}
            for r in provider_rows
        ]

        # ── Normalization success rate (sessions vs raw pairs) ─────
        pair_count_row = conn.execute(
            """
            SELECT COUNT(DISTINCT pair_id) as pair_count
            FROM raw_captures
            WHERE direction = 'response' AND created_at >= ?
            """,
            (windows["24h"],),
        ).fetchone()
        session_count_row = conn.execute(
            """
            SELECT COUNT(*) as session_count
            FROM sessions
            WHERE started_at >= ?
            """,
            (windows["24h"],),
        ).fetchone()
        pairs_24h = pair_count_row["pair_count"] if pair_count_row else 0
        sessions_24h = session_count_row["session_count"] if session_count_row else 0

        return {
            "timestamp": now,
            "sources": source_health,
            "directions": direction_health,
            "recent_providers": recent_providers,
            "normalization": {
                "pairs_24h": pairs_24h,
                "sessions_24h": sessions_24h,
                "success_rate": round(sessions_24h / pairs_24h, 2) if pairs_24h > 0 else None,
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pipeline errors (P0)
# ---------------------------------------------------------------------------

# Cap on total rows kept in pipeline_errors. When exceeded, the oldest
# rows are pruned. This keeps the health API cheap and the DB from
# ballooning when the pipeline hits a hot failure.
_PIPELINE_ERROR_MAX_ROWS = 10_000
_PIPELINE_ERROR_RETAIN_SECONDS = 7 * 86400  # 7 days


def record_pipeline_error(
    stage: str,
    message: str,
    *,
    level: str = "ERROR",
    source_id: Optional[str] = None,
    pair_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Record an internal pipeline error for health-API roll-ups.

    Fail-safe: any DB error is swallowed so instrumentation never takes
    down the pipeline itself.

    Args:
        stage: Stage identifier. Canonical values are ``ingest``,
            ``normalize``, ``reconcile``, ``session_resolve``, ``persist``,
            ``fts_index``, ``otel_emit``. New values are accepted as-is.
        message: Short human-readable summary (<= 500 chars recommended).
        level: Python logging level name, default ``ERROR``.
        source_id: Source that triggered the error, if known.
        pair_id: Capture pair the error applies to, if known.
        details: Optional structured context. Must not contain sensitive
            payload content \u2014 keep to identifiers / counts / class names.
    """
    try:
        details_json = _json.dumps(details, ensure_ascii=False) if details else None
    except Exception:
        details_json = None

    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO pipeline_errors
                    (ts, stage, level, source_id, pair_id, message, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (time.time(), stage, level, source_id, pair_id,
                 message[:2000] if message else "",
                 details_json),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Last-resort: never propagate. Use stderr logger so we at least
        # see it in the console even if DB is completely broken.
        logger.exception("Failed to record pipeline error (stage=%s)", stage)


def get_pipeline_error_counts(
    *,
    window_seconds: float = 86400,
    db_path: Optional[Path] = None,
) -> dict[str, dict[str, int]]:
    """Return pipeline error counts grouped by stage and level.

    Returns a dict of ``{stage: {level: count}}`` covering the last
    ``window_seconds`` seconds. Stages with zero errors are omitted.
    """
    since = time.time() - window_seconds
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT stage, level, COUNT(*) AS c
            FROM pipeline_errors
            WHERE ts >= ?
            GROUP BY stage, level
            """,
            (since,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table may not exist yet on a DB that somehow skipped migration 0002.
        return {}
    finally:
        conn.close()

    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["stage"], {})[r["level"]] = r["c"]
    return out


def prune_pipeline_errors(db_path: Optional[Path] = None) -> int:
    """Trim old pipeline_errors rows. Returns the number of rows deleted.

    Keeps at most ``_PIPELINE_ERROR_MAX_ROWS`` rows and drops anything
    older than ``_PIPELINE_ERROR_RETAIN_SECONDS``.
    """
    cutoff = time.time() - _PIPELINE_ERROR_RETAIN_SECONDS
    deleted = 0
    try:
        conn = get_connection(db_path)
        try:
            cur = conn.execute(
                "DELETE FROM pipeline_errors WHERE ts < ?", (cutoff,),
            )
            deleted += cur.rowcount or 0
            # Enforce the row cap by keeping only the newest N.
            cur = conn.execute(
                """
                DELETE FROM pipeline_errors
                WHERE id IN (
                    SELECT id FROM pipeline_errors
                    ORDER BY ts DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (_PIPELINE_ERROR_MAX_ROWS,),
            )
            deleted += cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("prune_pipeline_errors failed")
    return deleted


# ---------------------------------------------------------------------------
# Detailed health (P0)
# ---------------------------------------------------------------------------

def get_detailed_health(db_path: Optional[Path] = None) -> dict:
    """Return the full health snapshot consumed by ``GET /api/v1/health``.

    Structure matches TASK-002 \u00a75.1:

    - ``schema_version`` / ``expected_schema_version`` / ``capture_payload_version``
    - ``sources``: per-source counters, failures, drop rate, latency p50/p95
    - ``pipeline``: error counts per stage, FTS index lag
    - ``storage``: DB size bytes, table row counts

    Never raises \u2014 degraded fields become ``None`` instead.
    """
    now = time.time()
    path = db_path or DB_PATH

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        schema_version = get_current_version(conn)

        # ── Per-source counters & latency percentiles ────────────────
        since_1h = now - 3600
        since_24h = now - 86400

        src_rows = conn.execute(
            """
            SELECT rc.source_id,
                   s.source_type,
                   s.tool_name,
                   MAX(rc.created_at)                                          AS last_capture_at,
                   SUM(CASE WHEN rc.created_at >= ? THEN 1 ELSE 0 END)         AS captures_1h,
                   SUM(CASE WHEN rc.created_at >= ? THEN 1 ELSE 0 END)         AS captures_24h,
                   COUNT(*)                                                    AS captures_total
            FROM raw_captures rc
            JOIN sources s ON rc.source_id = s.id
            GROUP BY rc.source_id
            """,
            (since_1h, since_24h),
        ).fetchall()

        # Per-source ingest failures recorded into pipeline_errors
        fail_rows = conn.execute(
            """
            SELECT source_id, COUNT(*) AS c
            FROM pipeline_errors
            WHERE ts >= ? AND stage = 'ingest'
            GROUP BY source_id
            """,
            (since_24h,),
        ).fetchall() if _has_table(conn, "pipeline_errors") else []
        failures_by_source = {r["source_id"]: r["c"] for r in fail_rows if r["source_id"]}

        # Per-source response latency percentiles (last 24h)
        lat_rows = conn.execute(
            """
            SELECT source_id, latency_ms
            FROM raw_captures
            WHERE direction = 'response'
              AND created_at >= ?
              AND latency_ms IS NOT NULL
            """,
            (since_24h,),
        ).fetchall()
        lat_by_source: dict[str, list[float]] = {}
        for r in lat_rows:
            lat_by_source.setdefault(r["source_id"], []).append(r["latency_ms"])

        sources_out: dict[str, dict] = {}
        for r in src_rows:
            source_id = r["source_id"]
            captures_24h = int(r["captures_24h"] or 0)
            failures_24h = int(failures_by_source.get(source_id, 0))
            denom = captures_24h + failures_24h
            drop_rate = round(failures_24h / denom, 4) if denom > 0 else 0.0
            p50, p95 = _percentiles(lat_by_source.get(source_id, []))

            sources_out[source_id] = {
                "source_type": r["source_type"],
                "tool_name": r["tool_name"],
                "last_capture_at": r["last_capture_at"],
                "last_capture_ago_s": round(now - r["last_capture_at"], 2) if r["last_capture_at"] else None,
                "captures_last_1h": int(r["captures_1h"] or 0),
                "captures_last_24h": captures_24h,
                "captures_total": int(r["captures_total"] or 0),
                "failures_last_24h": failures_24h,
                "drop_rate": drop_rate,
                "latency_p50_ms": p50,
                "latency_p95_ms": p95,
            }

        # Ensure well-known sources are always present, even if they have
        # no rows yet, so the dashboard shows them as "never" rather than
        # silently omitting them.
        for sid in (SOURCE_PROXY, SOURCE_BROWSER_EXT, SOURCE_MCP):
            sources_out.setdefault(sid, {
                "source_type": sid.split("-")[0] if "-" in sid else sid,
                "tool_name": None,
                "last_capture_at": None,
                "last_capture_ago_s": None,
                "captures_last_1h": 0,
                "captures_last_24h": 0,
                "captures_total": 0,
                "failures_last_24h": int(failures_by_source.get(sid, 0)),
                "drop_rate": 0.0,
                "latency_p50_ms": None,
                "latency_p95_ms": None,
            })

        # ── Pipeline stage errors ────────────────────────────────────
        pipeline_errors_24h: dict[str, dict[str, int]] = {}
        if _has_table(conn, "pipeline_errors"):
            rows = conn.execute(
                """
                SELECT stage, level, COUNT(*) AS c
                FROM pipeline_errors
                WHERE ts >= ?
                GROUP BY stage, level
                """,
                (since_24h,),
            ).fetchall()
            for r in rows:
                pipeline_errors_24h.setdefault(r["stage"], {})[r["level"]] = r["c"]

        def _stage_errors(stage: str) -> int:
            levels = pipeline_errors_24h.get(stage, {})
            return sum(levels.values())

        # FTS index lag = messages.rowid max - messages_fts.rowid max.
        # When triggers are working and no rebuild is pending, the two
        # should match; any positive number indicates lag.
        try:
            msg_max = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0]
            fts_max = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages_fts").fetchone()[0]
            fts_lag_rows = int(msg_max) - int(fts_max)
        except sqlite3.OperationalError:
            fts_lag_rows = None

        pipeline = {
            "errors_last_24h_by_stage": pipeline_errors_24h,
            "normalizer_errors_last_24h": _stage_errors("normalize"),
            "reconciler_errors_last_24h": _stage_errors("reconcile"),
            "persist_errors_last_24h": _stage_errors("persist"),
            "ingest_errors_last_24h": _stage_errors("ingest"),
            "fts_index_lag_rows": fts_lag_rows,
        }

        # ── Storage stats ────────────────────────────────────────────
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
        sess_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        snip_count = conn.execute("SELECT COUNT(*) FROM snippets").fetchone()[0]
        earliest = conn.execute("SELECT MIN(created_at) FROM raw_captures").fetchone()[0]
        try:
            db_size = path.stat().st_size if path.exists() else 0
        except OSError:
            db_size = 0

        storage = {
            "db_path": str(path),
            "db_size_bytes": int(db_size),
            "db_size_mb": round(db_size / 1024 / 1024, 2),
            "raw_captures_rows": int(raw_count),
            "sessions_rows": int(sess_count),
            "messages_rows": int(msg_count),
            "snippets_rows": int(snip_count),
            "oldest_capture_age_days": (
                round((now - earliest) / 86400, 2) if earliest else None
            ),
        }

        return {
            "status": "ok",
            "timestamp": now,
            "schema_version": schema_version,
            "expected_schema_version": EXPECTED_SCHEMA_VERSION,
            "capture_payload_version": CAPTURE_SCHEMA_VERSION,
            "sources": sources_out,
            "pipeline": pipeline,
            "storage": storage,
        }
    finally:
        conn.close()


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone()
    return row is not None


def _percentiles(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Return (p50, p95) in milliseconds, rounded. ``None`` if empty."""
    if not values:
        return None, None
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _pick(pct: float) -> float:
        # Nearest-rank method; adequate for dashboard purposes.
        idx = max(0, min(n - 1, int(round(pct / 100 * (n - 1)))))
        return sorted_vals[idx]

    return round(_pick(50), 2), round(_pick(95), 2)


# ---------------------------------------------------------------------------
# Tier 1: sessions / messages
# ---------------------------------------------------------------------------

def insert_session(
    *,
    source_id: str,
    started_at: float,
    provider: Optional[str] = None,
    tool_family: Optional[str] = None,
    session_key: Optional[str] = None,
    title_hint: Optional[str] = None,
    created_via: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a session. Returns session id or None on failure."""
    session_id = new_id()
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO sessions
                    (id, source_id, started_at, provider, tool_family,
                     session_key, message_count, title_hint, created_via)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (session_id, source_id, started_at, provider, tool_family,
                 session_key, title_hint, created_via),
            )
            conn.commit()
            return session_id
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to insert session")
        return None


def insert_message(
    *,
    session_id: str,
    ts: float,
    role: str,
    content_text: Optional[str] = None,
    content_json: Optional[str] = None,
    model_name: Optional[str] = None,
    capture_pair_id: Optional[str] = None,
    token_estimate: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a message. Returns message id or None on failure."""
    msg_id = new_id()
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, capture_pair_id, ts, role,
                     content_text, content_json, model_name, token_estimate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (msg_id, session_id, capture_pair_id, ts, role,
                 content_text, content_json, model_name, token_estimate),
            )
            # Update session message count and ended_at
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1, ended_at = ? WHERE id = ?",
                (ts, session_id),
            )
            conn.commit()
            return msg_id
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to insert message")
        return None


def update_message_enrichment(
    msg_id: str,
    *,
    content_text: Optional[str] = None,
    content_json: Optional[str] = None,
    model_name: Optional[str] = None,
    token_estimate: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update an existing message with richer content (e.g. newly-extracted attachments).

    Only updates fields that are provided (not None). Returns True on success.
    Used by the reconciler to enrich messages when a higher-quality capture
    arrives from a different channel (e.g. network adds model_name/tokens
    to a DOM-extracted message).
    """
    sets = []
    params: list = []
    if content_text is not None:
        sets.append("content_text = ?")
        params.append(content_text)
    if content_json is not None:
        sets.append("content_json = ?")
        params.append(content_json)
    if model_name is not None:
        sets.append("model_name = ?")
        params.append(model_name)
    if token_estimate is not None:
        sets.append("token_estimate = ?")
        params.append(token_estimate)
    if not sets:
        return False
    params.append(msg_id)
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to update message enrichment for %s", msg_id)
        return False


def query_sessions(
    *,
    last: int = 20,
    provider: Optional[str] = None,
    language: Optional[str] = None,
    topic: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
    min_messages: Optional[int] = None,
    title_search: Optional[str] = None,
    favorited_only: bool = False,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Return recent sessions with optional filters."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses: list[str] = []
        params: list = []
        if favorited_only:
            clauses.append("favorited = 1")
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if language:
            clauses.append("language = ?")
            params.append(language)
        if topic:
            clauses.append("topic_tags LIKE ?")
            params.append(f"%{topic}%")
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("started_at <= ?")
            params.append(until)
        if min_messages is not None:
            clauses.append("message_count >= ?")
            params.append(min_messages)
        if title_search:
            clauses.append("title_hint LIKE ?")
            params.append(f"%{title_search}%")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM sessions{where} ORDER BY started_at DESC LIMIT ?"
        params.append(last)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_messages(session_id: str, db_path: Optional[Path] = None) -> list[dict]:
    """Return all messages in a session, ordered by timestamp."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY ts", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _has_cjk(text: str) -> bool:
    """Return True if text contains CJK characters."""
    return any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff'
               or '\uac00' <= c <= '\ud7af' for c in text)


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH safety.

    FTS5 has special syntax characters (quotes, parentheses, AND/OR/NOT, *, ^, NEAR)
    that can cause parse errors if passed raw. We wrap each token in double quotes
    to treat them as literal phrases.
    """
    import re
    # Remove characters that are FTS5 operators
    cleaned = re.sub(r'["\(\)\*\^\{\}]', ' ', query)
    # Split into tokens and quote each one
    tokens = cleaned.split()
    if not tokens:
        return ""
    # Wrap each token in double quotes for literal matching
    return " ".join(f'"{t}"' for t in tokens if t.strip())


def search_messages(
    query: str,
    *,
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Full-text search on messages.

    Uses FTS5 for Latin text, falls back to LIKE for CJK queries since
    the unicode61 tokenizer doesn't split CJK characters into words.
    Returns messages with parent session info and highlighted snippets.
    """
    if _has_cjk(query):
        return _search_messages_like(query, provider=provider, limit=limit, db_path=db_path)
    return _search_messages_fts(query, provider=provider, limit=limit, db_path=db_path)


def _search_messages_like(
    query: str,
    *,
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """LIKE-based search fallback for CJK queries."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT m.id, m.session_id, m.role, m.content_text, m.model_name,
                   m.ts, m.token_estimate,
                   s.provider, s.title_hint, s.started_at as session_started,
                   s.tool_family
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content_text LIKE ?
        """
        params: list = [f"%{query}%"]
        if provider:
            sql += " AND s.provider = ?"
            params.append(provider)
        sql += " ORDER BY m.ts DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()

        # Build snippets manually with <mark> highlighting
        results = []
        for r in rows:
            d = dict(r)
            text = d.get("content_text") or ""
            idx = text.lower().find(query.lower())
            if idx >= 0:
                start = max(0, idx - 40)
                end = min(len(text), idx + len(query) + 40)
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(text) else ""
                snippet = (prefix + text[start:idx]
                           + "<mark>" + text[idx:idx + len(query)] + "</mark>"
                           + text[idx + len(query):end] + suffix)
            else:
                snippet = text[:80] + ("..." if len(text) > 80 else "")
            d["snippet"] = snippet
            results.append(d)
        return results
    except Exception:
        logger.exception("LIKE search failed for query=%r", query)
        return []
    finally:
        conn.close()


def _search_messages_fts(
    query: str,
    *,
    provider: Optional[str] = None,
    limit: int = 20,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """FTS5-based search for Latin text."""
    safe_query = _sanitize_fts_query(query)
    if not safe_query:
        return []

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT m.id, m.session_id, m.role, m.content_text, m.model_name,
                   m.ts, m.token_estimate,
                   s.provider, s.title_hint, s.started_at as session_started,
                   s.tool_family,
                   snippet(messages_fts, 0, '<mark>', '</mark>', '...', 32) as snippet
            FROM messages_fts
            JOIN messages m ON m.rowid = messages_fts.rowid
            JOIN sessions s ON m.session_id = s.id
            WHERE messages_fts MATCH ?
        """
        params: list = [safe_query]
        if provider:
            sql += " AND s.provider = ?"
            params.append(provider)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("FTS search failed for query=%r", query)
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Custom domains (dynamic allowlist)
# ---------------------------------------------------------------------------

# In-memory cache refreshed lazily
_custom_domains_cache: set[str] = set()
_custom_domains_loaded: bool = False


def _load_custom_domains(db_path: Optional[Path] = None) -> set[str]:
    """Load active custom domains from DB into cache."""
    global _custom_domains_cache, _custom_domains_loaded
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT domain FROM custom_domains WHERE active = 1"
        ).fetchall()
        _custom_domains_cache = {r[0] for r in rows}
        _custom_domains_loaded = True
        return _custom_domains_cache
    except Exception:
        logger.exception("Failed to load custom domains")
        return set()
    finally:
        conn.close()


def get_custom_domains(db_path: Optional[Path] = None) -> set[str]:
    """Return set of active custom domain strings (cached)."""
    global _custom_domains_loaded
    if not _custom_domains_loaded:
        return _load_custom_domains(db_path)
    return _custom_domains_cache


def refresh_custom_domains(db_path: Optional[Path] = None) -> set[str]:
    """Force-refresh the custom domains cache from DB."""
    global _custom_domains_loaded
    _custom_domains_loaded = False
    return _load_custom_domains(db_path)


def add_custom_domain(
    domain: str,
    *,
    source: str = "user",
    confidence: Optional[str] = None,
    reason: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Add a domain to the custom allowlist. Returns True on success."""
    global _custom_domains_cache
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO custom_domains
                (domain, added_at, source, confidence, reason, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (domain, time.time(), source, confidence, reason),
        )
        conn.commit()
        _custom_domains_cache.add(domain)
        logger.info("Added custom domain: %s (source=%s)", domain, source)
        return True
    except Exception:
        logger.exception("Failed to add custom domain %s", domain)
        return False
    finally:
        conn.close()


def remove_custom_domain(domain: str, db_path: Optional[Path] = None) -> bool:
    """Deactivate a custom domain. Returns True on success."""
    global _custom_domains_cache
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE custom_domains SET active = 0 WHERE domain = ?", (domain,)
        )
        conn.commit()
        _custom_domains_cache.discard(domain)
        logger.info("Removed custom domain: %s", domain)
        return True
    except Exception:
        logger.exception("Failed to remove custom domain %s", domain)
        return False
    finally:
        conn.close()


def set_session_favorite(
    session_id: str,
    favorited: bool = True,
    db_path: Optional[Path] = None,
) -> bool:
    """Set or clear the favorite flag on a session. Returns True on success."""
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                "UPDATE sessions SET favorited = ? WHERE id = ?",
                (1 if favorited else 0, session_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to set favorite on session %s", session_id)
        return False


def count_favorited_sessions(db_path: Optional[Path] = None) -> int:
    """Return the number of favorited sessions."""
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE favorited = 1"
        ).fetchone()[0]
    finally:
        conn.close()


def reset_all_data(db_path: Optional[Path] = None) -> dict:
    """Delete all non-favorited captures, sessions, and messages.

    Favorited sessions and their messages and linked raw_captures are
    preserved.  Returns counts of deleted and protected rows.

    WARNING: This is a destructive operation intended for dev/test use only.
    Sources and custom_domains are always preserved.
    """
    conn = get_connection(db_path)
    try:
        # Count protected (favorited) items
        fav_sess = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE favorited = 1"
        ).fetchone()[0]
        fav_msgs = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id IN "
            "(SELECT id FROM sessions WHERE favorited = 1)"
        ).fetchone()[0]

        # Collect pair_ids linked to favorited messages so their raw_captures survive
        fav_pair_rows = conn.execute(
            "SELECT DISTINCT capture_pair_id FROM messages "
            "WHERE session_id IN (SELECT id FROM sessions WHERE favorited = 1) "
            "AND capture_pair_id IS NOT NULL"
        ).fetchall()
        fav_pair_ids = {r[0] for r in fav_pair_rows}

        # Count totals before delete
        msg_total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        sess_total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        cap_total = conn.execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]

        # Delete non-favorited messages
        conn.execute(
            "DELETE FROM messages WHERE session_id NOT IN "
            "(SELECT id FROM sessions WHERE favorited = 1)"
        )
        # Delete non-favorited sessions
        conn.execute("DELETE FROM sessions WHERE favorited != 1")
        # Delete raw_captures not linked to favorited messages
        if fav_pair_ids:
            placeholders = ",".join("?" for _ in fav_pair_ids)
            conn.execute(
                f"DELETE FROM raw_captures WHERE pair_id NOT IN ({placeholders})",
                list(fav_pair_ids),
            )
        else:
            conn.execute("DELETE FROM raw_captures")
        conn.commit()

        deleted_sess = sess_total - fav_sess
        deleted_msgs = msg_total - fav_msgs
        cap_remaining = conn.execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
        deleted_caps = cap_total - cap_remaining

        logger.warning(
            "RESET: deleted %d captures, %d sessions, %d messages (protected %d fav sessions)",
            deleted_caps, deleted_sess, deleted_msgs, fav_sess,
        )
        return {
            "captures_deleted": deleted_caps,
            "sessions_deleted": deleted_sess,
            "messages_deleted": deleted_msgs,
            "favorites_protected": fav_sess,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Snippets CRUD
# ---------------------------------------------------------------------------

def insert_snippet(
    *,
    content_text: str,
    source_url: Optional[str] = None,
    source_domain: Optional[str] = None,
    provider: Optional[str] = None,
    category: str = "general",
    note: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a text snippet. Returns snippet id or None on failure."""
    snippet_id = new_id()
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO snippets
                    (id, created_at, source_url, source_domain, provider,
                     category, content_text, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snippet_id, time.time(), source_url, source_domain,
                 provider, category, content_text, note),
            )
            conn.commit()
            return snippet_id
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to insert snippet")
        return None


def query_snippets(
    *,
    last: int = 50,
    category: Optional[str] = None,
    domain: Optional[str] = None,
    favorited_only: bool = False,
    q: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Query snippets with optional filters."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = []
        params: list = []

        if category:
            clauses.append("s.category = ?")
            params.append(category)
        if domain:
            clauses.append("s.source_domain = ?")
            params.append(domain)
        if favorited_only:
            clauses.append("s.favorited = 1")
        if q:
            clauses.append("s.rowid IN (SELECT rowid FROM snippets_fts WHERE snippets_fts MATCH ?)")
            params.append(q)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(last)

        rows = conn.execute(
            f"SELECT s.* FROM snippets s {where} ORDER BY s.created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_snippet(snippet_id: str, db_path: Optional[Path] = None) -> Optional[dict]:
    """Return a single snippet by id."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM snippets WHERE id = ?", (snippet_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_snippet(
    snippet_id: str,
    *,
    category: Optional[str] = None,
    note: Optional[str] = None,
    favorited: Optional[bool] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update snippet fields. Returns True on success."""
    sets = []
    params: list = []
    if category is not None:
        sets.append("category = ?")
        params.append(category)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if favorited is not None:
        sets.append("favorited = ?")
        params.append(1 if favorited else 0)
    if not sets:
        return True
    params.append(snippet_id)
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                f"UPDATE snippets SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to update snippet %s", snippet_id)
        return False


def delete_snippet(snippet_id: str, db_path: Optional[Path] = None) -> bool:
    """Delete a snippet by id. Returns True on success."""
    try:
        conn = get_connection(db_path)
        try:
            conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to delete snippet %s", snippet_id)
        return False


def get_snippet_categories(db_path: Optional[Path] = None) -> list[dict]:
    """Return all distinct categories with counts."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT category, COUNT(*) as count FROM snippets GROUP BY category ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_custom_domains(
    include_inactive: bool = False,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Return all custom domains as dicts."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if include_inactive:
            rows = conn.execute("SELECT * FROM custom_domains ORDER BY added_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM custom_domains WHERE active = 1 ORDER BY added_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
