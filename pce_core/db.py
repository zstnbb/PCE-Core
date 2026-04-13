"""PCE Core – SQLite storage engine.

Implements:
- Tier 0: raw_captures (immutable fact layer)
- Tier 1: sources, sessions, messages (normalized view)

All writes are designed to be fail-safe: exceptions are logged but never
propagated to the caller so that upstream requests are not blocked.
"""

import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from .config import DB_PATH, DATA_DIR

logger = logging.getLogger("pce.db")

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
    """Create tables and seed default sources if needed."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        # Migrate: rebuild raw_captures if CHECK constraint is outdated
        # (pre-v0.1.1 databases lacked 'conversation' in the direction CHECK)
        try:
            _needs_rebuild = False
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_captures'"
            ).fetchone()
            if table_sql and table_sql[0] and ("'network_intercept'" not in table_sql[0] or "'clipboard'" not in table_sql[0]):
                _needs_rebuild = True
            if _needs_rebuild:
                conn.execute("ALTER TABLE raw_captures RENAME TO _raw_captures_old")
                conn.executescript(SCHEMA_SQL)
                conn.execute("""
                    INSERT INTO raw_captures
                        (id, created_at, source_id, direction, pair_id, host, path,
                         method, provider, model_name, status_code, latency_ms,
                         headers_redacted_json, body_text_or_json, body_format,
                         error, session_hint)
                    SELECT id, created_at, source_id, direction, pair_id, host, path,
                           method, provider, model_name, status_code, latency_ms,
                           headers_redacted_json, body_text_or_json, body_format,
                           error, session_hint
                    FROM _raw_captures_old
                """)
                conn.execute("DROP TABLE _raw_captures_old")
                logger.info("Migrated raw_captures: rebuilt table with updated CHECK constraint")
        except Exception:
            logger.exception("Migration check failed (non-fatal)")
        # Migrate: add meta_json column if missing
        cols = {row[1] for row in conn.execute("PRAGMA table_info(raw_captures)").fetchall()}
        if "meta_json" not in cols:
            conn.execute("ALTER TABLE raw_captures ADD COLUMN meta_json TEXT")
            logger.info("Migrated raw_captures: added meta_json column")
        # Migrate: add session metadata columns if missing
        sess_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        for col, coltype in [
            ("language", "TEXT"),
            ("topic_tags", "TEXT"),
            ("total_tokens", "INTEGER"),
            ("model_names", "TEXT"),
        ]:
            if col not in sess_cols:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {coltype}")
                logger.info("Migrated sessions: added %s column", col)
        # Migrate: add favorited column to sessions if missing
        if "favorited" not in sess_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN favorited INTEGER NOT NULL DEFAULT 0")
            logger.info("Migrated sessions: added favorited column")
        # Migrate: rebuild FTS index if empty but messages exist
        try:
            fts_count = conn.execute(
                "SELECT COUNT(*) FROM messages_fts"
            ).fetchone()[0]
            msg_count = conn.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            if fts_count == 0 and msg_count > 0:
                conn.execute(
                    "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
                )
                logger.info("Migrated: rebuilt FTS index for %d existing messages", msg_count)
        except Exception:
            logger.debug("FTS migration check skipped (non-fatal)")
        for src_id, src_type, tool, mode, notes in _DEFAULT_SOURCES:
            conn.execute(
                "INSERT OR IGNORE INTO sources (id, source_type, tool_name, install_mode, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (src_id, src_type, tool, mode, notes),
            )
        conn.commit()
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
    source_id: str = SOURCE_PROXY,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a raw capture row. Returns the capture id, or None on failure."""
    capture_id = new_id()
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO raw_captures
                    (id, created_at, source_id, direction, pair_id, host, path,
                     method, provider, model_name, status_code, latency_ms,
                     headers_redacted_json, body_text_or_json, body_format,
                     error, session_hint, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    db_path: Optional[Path] = None,
) -> bool:
    """Update an existing message with richer content (e.g. newly-extracted attachments).

    Only updates fields that are provided (not None). Returns True on success.
    """
    sets = []
    params: list = []
    if content_text is not None:
        sets.append("content_text = ?")
        params.append(content_text)
    if content_json is not None:
        sets.append("content_json = ?")
        params.append(content_json)
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
