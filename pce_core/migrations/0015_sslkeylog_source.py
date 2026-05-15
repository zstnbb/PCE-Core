# SPDX-License-Identifier: Apache-2.0
"""Migration 0015 - Register the A2 SSLKEYLOGFILE source (P5.D.1 Wave 2).

P5.D.1 Wave 2 (rewritten 2026-05-15 for Architecture B / tshark wrap)
introduces a new capture source: `sslkeylog-default`. This is the
V-GREEN-clean compliance replacement for L1 MITM (`proxy-default`)
on Chromium-based AI surfaces.

The migration is additive and idempotent (`INSERT OR IGNORE`),
following the pattern established by migrations 0005 / 0009 / 0010
/ 0011 / 0012 / 0014.

References:
- `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 1.0 (V-GREEN-C
  amendment introducing this leg as the clean replacement)
- `Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` section 1.2.1 (L1+A2=2-leg
  independence proof)
- `Docs/stability/redundancy-sprint/02-wave2-sslkeylogfile.md` (Wave 2
  plan, Arch B tshark wrap)
- `pce_sslkeylog/__init__.py` (the package that writes captures here)
- `Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`
  section 5.2 Phase 5 (original ADR; superseded by Wave 2 Arch B for
  the independence requirement)
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0015")


_SSLKEYLOG_SOURCE_ROW = (
    "sslkeylog-default",
    "sslkeylog",
    "tshark-wrap",
    "complete",
    "A2 SSLKEYLOGFILE network capture via tshark + Chromium-written keylog "
    "(UCS L1-alt, V-GREEN-clean replacement for L1 MITM, P5.D.1 Wave 2)",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _SSLKEYLOG_SOURCE_ROW,
    )
    logger.info("migration 0015: sslkeylog-default source registered")


def downgrade(conn: sqlite3.Connection) -> None:
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_SSLKEYLOG_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0015 downgrade: sslkeylog-default source has "
            "captures, leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_SSLKEYLOG_SOURCE_ROW[0],),
    )
