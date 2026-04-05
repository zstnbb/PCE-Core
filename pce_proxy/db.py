"""PCE Proxy – SQLite storage engine.

Implements Tier 0 (raw_captures) and a minimal sources table.
All writes are designed to be fail-safe: exceptions are logged but never
propagated to the caller so that upstream requests are not blocked.
"""

import json
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
    direction               TEXT NOT NULL CHECK (direction IN ('request', 'response')),
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
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_captures_created   ON raw_captures(created_at);
CREATE INDEX IF NOT EXISTS idx_captures_pair       ON raw_captures(pair_id);
CREATE INDEX IF NOT EXISTS idx_captures_host       ON raw_captures(host);
CREATE INDEX IF NOT EXISTS idx_captures_provider   ON raw_captures(provider);
"""

DEFAULT_SOURCE_ID = "proxy-default"


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a new SQLite connection (caller is responsible for closing)."""
    path = db_path or DB_PATH
    _ensure_dir()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Create tables and seed default source if needed."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_type, tool_name, install_mode, notes) "
            "VALUES (?, 'proxy', 'mitmproxy', 'complete', 'default proxy source')",
            (DEFAULT_SOURCE_ID,),
        )
        conn.commit()
    finally:
        conn.close()


def new_pair_id() -> str:
    return uuid.uuid4().hex[:16]


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
    headers_redacted_json: str,
    body_text_or_json: str,
    body_format: str = "json",
    error: Optional[str] = None,
    session_hint: Optional[str] = None,
    source_id: str = DEFAULT_SOURCE_ID,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Insert a raw capture row. Returns the capture id, or None on failure."""
    capture_id = uuid.uuid4().hex
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO raw_captures
                    (id, created_at, source_id, direction, pair_id, host, path,
                     method, provider, model_name, status_code, latency_ms,
                     headers_redacted_json, body_text_or_json, body_format,
                     error, session_hint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
