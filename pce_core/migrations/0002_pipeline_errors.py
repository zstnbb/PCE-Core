"""Migration 0002 – pipeline_errors table.

P0 introduces a lightweight error log used by the health API to compute
``normalizer_errors_last_24h`` / ``reconciler_errors_last_24h`` and
similar rollups. Separating this from ``raw_captures`` keeps the fact
layer immutable and keeps error churn from inflating user-facing stats.

Schema design notes:
- Bounded growth: callers must trim rows older than 7 days periodically
  (see ``pce_core.db._prune_pipeline_errors``). The table is capped at
  ~10k rows in practice.
- ``stage`` is a free-form text column instead of an enum so new
  pipeline steps can be recorded without a schema migration. Typical
  values: ``ingest``, ``normalize``, ``reconcile``, ``session_resolve``,
  ``persist``, ``fts_index``, ``otel_emit``.
- ``level`` is free-form text matching Python logging levels
  (``ERROR``, ``WARNING``). Kept as text for forward compatibility.
- ``details_json`` optionally holds structured context (pair_id, provider,
  exception class, etc.). Must not contain sensitive payload content.
"""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipeline_errors (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            stage         TEXT NOT NULL,
            level         TEXT NOT NULL DEFAULT 'ERROR',
            source_id     TEXT,
            pair_id       TEXT,
            message       TEXT NOT NULL,
            details_json  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pipeline_errors_ts
            ON pipeline_errors(ts);
        CREATE INDEX IF NOT EXISTS idx_pipeline_errors_stage
            ON pipeline_errors(stage);
        CREATE INDEX IF NOT EXISTS idx_pipeline_errors_stage_ts
            ON pipeline_errors(stage, ts);
        """
    )


def downgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_pipeline_errors_stage_ts;
        DROP INDEX IF EXISTS idx_pipeline_errors_stage;
        DROP INDEX IF EXISTS idx_pipeline_errors_ts;
        DROP TABLE IF EXISTS pipeline_errors;
        """
    )
