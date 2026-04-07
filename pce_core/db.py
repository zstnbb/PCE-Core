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
