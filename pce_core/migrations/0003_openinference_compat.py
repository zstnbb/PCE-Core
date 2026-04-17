"""Migration 0003 – OpenInference / OpenTelemetry GenAI compatibility.

Adds additive columns to ``messages`` and ``sessions`` so PCE can emit
OpenInference-shaped attribute dicts and OTLP spans without a destructive
rename. See `docs/engineering/adr/ADR-004-align-schema-with-openinference.md`
and `docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`.

Rules of engagement (ADR-004 §Guardrails):

- Never drop or rename an existing column. Old readers keep working.
- New columns are nullable and populated lazily (existing rows keep a
  ``NULL`` oi_attributes_json; export code regenerates on demand).
- ``oi_attributes_json`` stores the serialised OpenInference attribute
  dict as produced by ``pce_core.normalizer.openinference_mapper``.
- ``oi_schema_version`` tracks which OI mapper version produced the row
  so future mapper evolutions can invalidate stale caches.

Columns added:

``messages``
    oi_role_raw         TEXT      – original provider-specific role
                                    ("model", "human", "ai" …) before
                                    normalisation to user/assistant/...
    oi_input_tokens     INTEGER   – prompt tokens (assistant msg only)
    oi_output_tokens    INTEGER   – completion tokens
    oi_attributes_json  TEXT      – serialised OpenInference attributes
    oi_schema_version   INTEGER   – OI mapper version, default 1

``sessions``
    oi_attributes_json  TEXT      – session-level OI attributes cache
    oi_schema_version   INTEGER   – default 1

Indexes added:

    idx_messages_ts_role  on (ts, role)   – retention / export pagination
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0003")


_MSG_COLUMNS: list[tuple[str, str]] = [
    ("oi_role_raw", "TEXT"),
    ("oi_input_tokens", "INTEGER"),
    ("oi_output_tokens", "INTEGER"),
    ("oi_attributes_json", "TEXT"),
    ("oi_schema_version", "INTEGER NOT NULL DEFAULT 1"),
]

_SESS_COLUMNS: list[tuple[str, str]] = [
    ("oi_attributes_json", "TEXT"),
    ("oi_schema_version", "INTEGER NOT NULL DEFAULT 1"),
]


def upgrade(conn: sqlite3.Connection) -> None:
    _add_columns(conn, "messages", _MSG_COLUMNS)
    _add_columns(conn, "sessions", _SESS_COLUMNS)

    # Retention / export pagination helper. Already covered by idx_messages_ts
    # for pure ts range scans, but the compound index makes role-filtered
    # deletes (e.g. "drop all user messages older than N days") trivially
    # index-scan-able without a table scan.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_ts_role ON messages(ts, role)"
    )


def downgrade(conn: sqlite3.Connection) -> None:
    # Additive migrations are reversible in principle — SQLite ≥ 3.35
    # supports ``ALTER TABLE ... DROP COLUMN``. We refuse anyway because
    # removing oi_attributes_json would throw away potentially hours of
    # OTLP-compatible work. Let the operator roll back via a backup.
    raise RuntimeError(
        "Refusing to downgrade 0003 – drop user-visible OI data. "
        "Restore from a backup taken before the upgrade instead."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_columns(
    conn: sqlite3.Connection,
    table: str,
    cols: list[tuple[str, str]],
) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in cols:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        logger.debug("added %s.%s (%s)", table, name, decl)
