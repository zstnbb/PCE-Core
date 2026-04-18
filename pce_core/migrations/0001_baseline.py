# SPDX-License-Identifier: Apache-2.0
"""Migration 0001 – Baseline.

Represents the database layout that existed in PCE just before the
industrialization-P0 work introduced this migration framework.

Responsibilities:

1. Create every table / index / trigger that ``pce_core.db.SCHEMA_SQL``
   declares (idempotent via ``IF NOT EXISTS``).
2. Apply every historic ad-hoc ``ALTER TABLE`` fix that used to live
   inline in ``pce_core.db.init_db``. Existing installs may have been
   upgraded out-of-band, so each step is guarded.

After this migration runs, the database is guaranteed to match the
pre-P0 schema shape regardless of how old the install is.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0001")


# Kept in sync with pce_core.db.SCHEMA_SQL. Duplicated here so the
# migration is self-contained and does not import from db.py (which
# would create a circular import on startup).
_BASELINE_SQL = """
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
CREATE INDEX IF NOT EXISTS idx_captures_pair      ON raw_captures(pair_id);
CREATE INDEX IF NOT EXISTS idx_captures_host      ON raw_captures(host);
CREATE INDEX IF NOT EXISTS idx_captures_provider  ON raw_captures(provider);
CREATE INDEX IF NOT EXISTS idx_captures_source    ON raw_captures(source_id);

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

CREATE TABLE IF NOT EXISTS custom_domains (
    domain          TEXT PRIMARY KEY,
    added_at        REAL NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    confidence      TEXT,
    reason          TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);
"""


# Default sources seeded on every fresh install.
_DEFAULT_SOURCES: list[tuple[str, str, str, str, str]] = [
    ("proxy-default", "proxy", "mitmproxy", "complete", "default proxy source"),
    ("browser-extension-default", "browser_extension", "chrome", "light", "default browser extension source"),
    ("mcp-default", "mcp", "pce-mcp-server", "light", "default MCP server source"),
]


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(_BASELINE_SQL)

    # ── Legacy fix: older raw_captures had a narrower direction CHECK ─
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_captures'"
    ).fetchone()
    if row and row[0] and (
        "'network_intercept'" not in row[0] or "'clipboard'" not in row[0]
    ):
        logger.info("rebuilding raw_captures with updated CHECK constraint")
        conn.execute("ALTER TABLE raw_captures RENAME TO _raw_captures_old")
        conn.executescript(_BASELINE_SQL)
        conn.execute(
            """
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
            """
        )
        conn.execute("DROP TABLE _raw_captures_old")

    # ── Legacy fix: add columns that old installs may still be missing ─
    cap_cols = _columns(conn, "raw_captures")
    if "schema_version" not in cap_cols:
        conn.execute(
            "ALTER TABLE raw_captures ADD COLUMN schema_version "
            "INTEGER NOT NULL DEFAULT 1"
        )
    if "meta_json" not in cap_cols:
        conn.execute("ALTER TABLE raw_captures ADD COLUMN meta_json TEXT")

    sess_cols = _columns(conn, "sessions")
    for col, coltype in [
        ("language", "TEXT"),
        ("topic_tags", "TEXT"),
        ("total_tokens", "INTEGER"),
        ("model_names", "TEXT"),
    ]:
        if col not in sess_cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {coltype}")
    if "favorited" not in sess_cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN favorited INTEGER NOT NULL DEFAULT 0"
        )

    # ── Legacy fix: rebuild FTS index if it got out of sync ──────────
    try:
        fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if fts_count == 0 and msg_count > 0:
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            logger.info("rebuilt FTS index for %d legacy messages", msg_count)
    except sqlite3.OperationalError:
        # FTS5 virtual tables can sometimes not exist on very old installs
        # or on SQLite builds without FTS5. Do nothing; the schema creation
        # above will have logged an error already.
        pass

    # ── Seed default sources ─────────────────────────────────────────
    import time
    now = time.time()  # noqa: F841 – unused but kept for future timestamped seeds
    for src_id, src_type, tool, mode, notes in _DEFAULT_SOURCES:
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_type, tool_name, install_mode, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (src_id, src_type, tool, mode, notes),
        )


def downgrade(conn: sqlite3.Connection) -> None:
    # Baseline is irreversible — downgrading would drop user data. We
    # explicitly refuse so no one accidentally runs it from a REPL.
    raise RuntimeError("Refusing to downgrade baseline – this would drop user data.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
