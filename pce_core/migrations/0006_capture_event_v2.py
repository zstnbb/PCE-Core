# SPDX-License-Identifier: Apache-2.0
"""Migration 0006 — CaptureEvent v2 native columns on ``raw_captures`` (P5.A-4).

UCS §5.5 ratifies CaptureEvent v2 as the unified data contract. Until this
migration runs, the v2 ingest endpoint (``/api/v1/captures/v2``) fits v2
payloads into the legacy v1 row shape and preserves the v2 envelope inside
``meta_json`` (see the TODO(T-1c) shims in ``pce_core/server.py``).

This migration promotes those shim fields to first-class columns on
``raw_captures`` so that:

- The Normalizer and Query API can filter / sort by ``source``,
  ``app_name``, ``fingerprint`` directly (no JSON parsing per-row).
- Fingerprint-based deduplication can short-circuit at ingest time with a
  single indexed lookup.
- Pro layers (ADR-010) that submit via local HTTP get a durable schema
  guarantee for their ``layer_meta`` escape hatch.

The migration is **additive and idempotent**:

- Each ``ALTER TABLE ... ADD COLUMN`` is guarded by a ``PRAGMA table_info``
  pre-check so re-running on an already-migrated database is a no-op.
  (SQLite does not support ``ADD COLUMN IF NOT EXISTS`` until very recent
  builds; the PRAGMA check works everywhere Python 3.10+ ships.)
- Indexes use ``CREATE INDEX IF NOT EXISTS``.
- No rows are rewritten. Existing rows get ``NULL`` for the new columns,
  which is legal — the v2 envelope for those rows lives in ``meta_json``
  and T-1d will back-fill lazily on read if needed.

Downgrade is a no-op with a warning: SQLite has supported ``DROP COLUMN``
only since 3.35 and rolling back a released schema change is deliberately
not supported per project policy. If an operator truly needs to downgrade,
they should restore a pre-migration backup.

See ``docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`` §5.5 for the
full column rationale and the 10 Forms × 5 Layers routing matrix that
these columns enable on the query side.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0006")


# Ordered list of (column_name, ddl_spec). The order matches UCS §5.5 so
# ``PRAGMA table_info(raw_captures)`` reads left-to-right the same as the
# spec does. Do not reorder after release — downstream tools rely on the
# file-order audit trail.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("source", "TEXT"),
    ("agent_name", "TEXT"),
    ("agent_version", "TEXT"),
    ("capture_time_ns", "INTEGER"),
    ("quality_tier", "TEXT DEFAULT 'T1_structured'"),
    ("fingerprint", "TEXT"),
    ("deduped_by", "TEXT"),
    ("form_id", "TEXT"),
    ("app_name", "TEXT"),
    ("layer_meta_json", "TEXT"),
]

_NEW_INDEXES: list[tuple[str, str]] = [
    ("idx_rc_fingerprint", "raw_captures(fingerprint)"),
    ("idx_rc_source", "raw_captures(source)"),
    ("idx_rc_app", "raw_captures(app_name)"),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names currently on ``table``."""
    # Using ``PRAGMA table_info`` instead of information_schema because
    # SQLite doesn't expose information_schema. Columns are in index 1.
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def upgrade(conn: sqlite3.Connection) -> None:
    # raw_captures is created by migration 0001 — if it somehow does not
    # exist we're running on a broken database; surface that loudly.
    existing = _existing_columns(conn, "raw_captures")
    if not existing:
        raise RuntimeError(
            "migration 0006: raw_captures table is missing — migrations "
            "out of order? 0001_baseline should have created it."
        )

    added: list[str] = []
    for col_name, spec in _NEW_COLUMNS:
        if col_name in existing:
            continue
        conn.execute(f"ALTER TABLE raw_captures ADD COLUMN {col_name} {spec}")
        added.append(col_name)

    for idx_name, target in _NEW_INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {target}")

    logger.info(
        "migration 0006: raw_captures gained v2 columns",
        extra={"event": "migrations.0006.upgraded", "pce_fields": {
            "columns_added": added,
            "columns_total_expected": [c for c, _ in _NEW_COLUMNS],
            "indexes_ensured": [n for n, _ in _NEW_INDEXES],
        }},
    )


def downgrade(conn: sqlite3.Connection) -> None:
    # Intentionally a no-op. Dropping columns on a released schema is
    # destructive and SQLite's DROP COLUMN support has version-dependent
    # caveats (FK-in-trigger, generated columns). If an operator really
    # needs to revert, the supported path is to restore from backup.
    logger.warning(
        "migration 0006 downgrade is a no-op; restore from backup if "
        "you must revert the CaptureEvent v2 native columns."
    )
