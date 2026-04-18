# SPDX-License-Identifier: Apache-2.0
"""Migration 0007 — TLS failure log for pinning detection (P5.A-6).

The proxy addon records client-side TLS handshake failures here. When a
proxied application rejects the MITM-injected certificate, that's the
signature of certificate pinning — the aggregation endpoint
``/api/v1/health/pinning`` reads this table to flag hosts where the
failure rate crosses the "suspected pinning" threshold (UCS §3.2).

Design notes:

- Additive, non-destructive — no existing table touched.
- ``error_message`` is truncated at write-time (see
  ``pce_core.db.record_tls_failure``) to avoid unbounded blobs, but the
  column itself is ``TEXT`` so older rows aren't re-validated on read.
- Two indexes support the only query we run today (aggregate by host
  within a time window). A combined ``(host, created_at)`` index covers
  both the ``WHERE host = ?`` and ``WHERE created_at >= ?`` paths; a
  standalone ``created_at`` index accelerates retention sweeps.
- Downgrade is reversible because nothing else references this table.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0007")


_DDL = """
CREATE TABLE IF NOT EXISTS tls_failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      REAL NOT NULL,
    host            TEXT NOT NULL,
    error_category  TEXT NOT NULL,
    error_message   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tls_failures_host_time
    ON tls_failures(host, created_at);

CREATE INDEX IF NOT EXISTS idx_tls_failures_time
    ON tls_failures(created_at);
"""


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    logger.info("migration 0007: tls_failures table ready")


def downgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP INDEX IF EXISTS idx_tls_failures_host_time;
        DROP INDEX IF EXISTS idx_tls_failures_time;
        DROP TABLE  IF EXISTS tls_failures;
        """
    )
