# SPDX-License-Identifier: Apache-2.0
"""Migration 0010 – Add ``messages.interaction_kind`` + register the
``desktop-electron-default`` capture source (P5.B.2 Phase 3).

Two additive changes:

1. **``messages.interaction_kind TEXT NULL``** — a free-form tag that
   distinguishes the *kind* of message the row represents. Use cases:

   - ``"chat"``           — ordinary user/assistant exchange
   - ``"tool_call"``      — model-emitted tool invocation
   - ``"tool_result"``    — tool's response shipped back to the model
   - ``"thinking"``       — reasoning trace / scratchpad
   - ``"system"``         — system prompt or system-injected content

   Earlier P5.A code distinguished these via the ``role`` column alone
   (``"user"`` / ``"assistant"``), but P5.B.2 ingests Anthropic Claude
   Desktop traffic through L3d CDP capture, where a single API response
   commonly bundles multiple kinds of message blocks (assistant text +
   tool_use + tool_result + thinking) under the same ``role``. The new
   column lets the dashboard and exporter distinguish them without
   parsing ``content_json`` on every read.

   This is **additive and never required**. Rows existing before the
   migration ran keep ``interaction_kind = NULL`` and continue to work
   exactly as before.

2. **``desktop-electron-default`` source row** — the L3d CDP launcher
   for Claude Desktop / Cursor / Windsurf (P5.B.2/P5.B.3) writes
   captures with this source_id so they're distinguishable from the
   generic ``cdp-embedded`` source which P4 used for PCE-launched
   embedded Chromium sessions.

ADR cross-references:

- ADR-016 §3.6 explicitly renumbered ``interaction_kind`` from the
  originally-planned 0009 to 0010 because P5.B.1 (the MCP middleware
  proxy source registration) had already taken 0009.
- ADR-016 §3.2 documents the L3d CDP launcher architecture this
  migration's source row supports.
- ADR-015 §4.2 + migration 0009 cover the MCP proxy source row that
  this migration mirrors in shape.

Why a single migration for both changes? Both ship in the same
P5.B.2/Phase 3 commit and an in-place ``schema_migrations`` row keeps
audit history compact. Either change is safe in isolation: the column
add is additive; the source row uses ``INSERT OR IGNORE``.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0010")


_DESKTOP_ELECTRON_SOURCE_ROW = (
    "desktop-electron-default",
    "desktop_electron",
    "pce-app-launcher",
    "complete",
    "default Electron desktop app capture source via L3d CDP launcher (UCS L3d, ADR-016)",
)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def upgrade(conn: sqlite3.Connection) -> None:
    # 1. Add messages.interaction_kind if missing.
    if not _column_exists(conn, "messages", "interaction_kind"):
        conn.execute(
            "ALTER TABLE messages ADD COLUMN interaction_kind TEXT NULL"
        )
        logger.info(
            "migration 0010: messages.interaction_kind column added"
        )
    else:
        logger.info(
            "migration 0010: messages.interaction_kind already present (no-op)"
        )

    # 2. Register the desktop-electron capture source.
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _DESKTOP_ELECTRON_SOURCE_ROW,
    )
    logger.info(
        "migration 0010: desktop-electron-default source registered"
    )


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal:
    # - Don't drop the column (SQLite supports DROP COLUMN since 3.35,
    #   but production DBs may be on older SQLite; better to leave the
    #   column NULL on existing rows and rely on the next upgrade to
    #   no-op).
    # - Only delete the source row if no captures reference it.
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_DESKTOP_ELECTRON_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0010 downgrade: desktop-electron-default has "
            "captures, leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_DESKTOP_ELECTRON_SOURCE_ROW[0],),
    )
