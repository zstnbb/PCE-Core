# SPDX-License-Identifier: Apache-2.0
"""Migration 0005 – Register the CDP embedded-browser capture source (P4.4).

The ``raw_captures.source_id`` column is a foreign key into ``sources``.
The P4.4 CDP driver writes rows with ``source_id = 'cdp-embedded'``, so
that id must exist in ``sources`` before the first insert on every
database — including ones that were created before P4.4 shipped.

This migration is **additive and idempotent**: it uses ``INSERT OR
IGNORE`` so re-running it on a freshly-baselined install (where the
caller also chose to pre-seed the source) is a no-op rather than a
conflict.

Why a dedicated migration instead of editing ``0001_baseline``?

- Project policy: "Never delete a migration after release" extends to
  "Never silently change a migration's side-effects after release."
  Users tracking schema evolution between PCE versions would otherwise
  see the same migration number produce different rows.
- Keeping the change in ``0005`` preserves the audit trail — a
  ``SELECT * FROM schema_migrations`` will show exactly when an
  operator's DB became CDP-aware.

The CDP feature itself is optional (requires ``playwright``). This
migration always runs regardless; registering the source is free and
lets users flip the feature on later without a schema touch.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0005")


_CDP_SOURCE_ROW = (
    "cdp-embedded",
    "cdp_embedded",
    "playwright-chromium",
    "complete",
    "embedded Chromium capture via Chrome DevTools Protocol",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _CDP_SOURCE_ROW,
    )
    logger.info("migration 0005: cdp-embedded source registered")


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal: only delete the row if no captures reference
    # it, otherwise the FK cascade would either take production data
    # with it or the DELETE would fail. Safer to leave the orphan row in
    # place and let the operator decide.
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_CDP_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0005 downgrade: cdp-embedded source has captures, "
            "leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_CDP_SOURCE_ROW[0],),
    )
