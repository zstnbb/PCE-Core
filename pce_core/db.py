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
    direction               TEXT NOT NULL CHECK (direction IN ('request', 'response', 'conversation')),
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

        return {
            "total_captures": total,
            "by_provider": by_provider,
            "by_source": by_source,
            "by_direction": by_direction,
            "earliest": earliest,
            "latest": latest,
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


def query_sessions(
    *,
    last: int = 20,
    provider: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[dict]:
    """Return recent sessions."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if provider:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE provider = ? ORDER BY started_at DESC LIMIT ?",
                (provider, last),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (last,)
            ).fetchall()
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
