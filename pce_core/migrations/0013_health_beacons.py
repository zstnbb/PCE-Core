# SPDX-License-Identifier: Apache-2.0
"""Migration 0013 – Health beacons table (P5.C.1 Meta-Pipeline).

Adds the ``health_beacons`` table — the third-pillar persistence layer
for the lane × target × case health-as-data contract defined in
``Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md`` §2.3 and adopted via
``Docs/docs/engineering/adr/ADR-019-maintenance-as-first-class-concern.md``.

The table is intentionally **separate** from ``raw_captures``:

- ``raw_captures`` = business data (user AI conversation content),
  governed by PRIVACY.md
- ``health_beacons`` = system state (lane / target / case status),
  no PII allowed, server-side reject on PII detection (see
  ``pce_core/health.py::validate_meta``)

This separation gives two distinct retention policies, two distinct
permission models, and ensures the health matrix surface (Dashboard
"Lane Health" view + ``GET /api/v1/health/matrix``) never leaks
business content.

Schema is **additive and idempotent** (CREATE TABLE IF NOT EXISTS).
Mirrors the pattern of migrations 0007 / 0008 (also CREATE TABLE).

See also:
- ``pce_core/health.py`` — the read/write API for this table
- ``pce_core/server.py`` — the 4 ``/api/v1/health/beacon*`` +
  ``/api/v1/health/matrix`` + ``/api/v1/health/timeseries`` endpoints
- ``Docs/docs/engineering/META-PIPELINE-FRAMEWORK.md`` §2.3
- ``tests/test_health_beacon.py``
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0013")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS health_beacons (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    lane               TEXT NOT NULL,
    layer              TEXT NOT NULL,
    target             TEXT NOT NULL,
    case_id            TEXT,
    status             TEXT NOT NULL CHECK(status IN
                           ('pass','fail','skip','degraded','infra_error')),
    ts                 REAL NOT NULL,
    elapsed_ms         INTEGER,
    meta_json          TEXT,
    selector_hits_json TEXT,
    created_at         REAL NOT NULL DEFAULT (strftime('%s','now'))
);
"""

# Index design rationale:
# - (lane, target, ts DESC) — the canonical "current state per target"
#   query path (Lane Health matrix view + auto-issue trigger check).
# - (status, ts DESC) — "show me recent failures across all targets",
#   used by nightly probe summary + dashboard red-banner.
# - (target, case_id, ts DESC) WHERE case_id IS NOT NULL — per-case
#   timeline drill-down view (HEALTH-MATRIX §6.3). Partial index keeps
#   heartbeat (case_id IS NULL) beacons out of this index — they only
#   need the lane/target index.
_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_health_beacons_lane_target_ts "
    "ON health_beacons(lane, target, ts DESC);",
    "CREATE INDEX IF NOT EXISTS idx_health_beacons_status_ts "
    "ON health_beacons(status, ts DESC);",
    "CREATE INDEX IF NOT EXISTS idx_health_beacons_case "
    "ON health_beacons(target, case_id, ts DESC) "
    "WHERE case_id IS NOT NULL;",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    for stmt in _INDEX_SQL:
        conn.execute(stmt)
    logger.info(
        "migration 0013: health_beacons table created (idempotent)",
        extra={"event": "migrations.0013.upgrade"},
    )


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal. We keep the row data — the next upgrade
    # would re-create the table empty, which would silently destroy
    # health history. The least surprising downgrade is therefore a
    # no-op (matches the policy in ``__init__.py`` "downgrade is
    # best-effort").
    logger.info(
        "migration 0013 downgrade: no-op (preserving health_beacons data)",
        extra={"event": "migrations.0013.downgrade"},
    )
