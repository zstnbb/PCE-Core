# SPDX-License-Identifier: Apache-2.0
"""Migration 0009 – Register the MCP middleware proxy capture source (P5.B.1).

The ``raw_captures.source_id`` column is a foreign key into ``sources``.
The P5.B.1 MCP proxy (``pce_mcp_proxy``, capture posture B / UCS layer
L3f) writes rows with ``source_id = 'mcp-proxy-default'``, so that id
must exist in ``sources`` before the first insert on every database —
including ones that were created before P5.B.1 shipped.

This migration is **additive and idempotent** (``INSERT OR IGNORE``),
mirroring migration 0005 which did the same thing for the CDP source
(``cdp-embedded``).

Why a dedicated migration instead of editing ``0001_baseline``?

- Project policy: "Never change a migration's side-effects after
  release." Users tracking schema evolution between PCE versions would
  otherwise see the same migration number produce different rows.
- Keeping the change in ``0009`` preserves the audit trail — a
  ``SELECT * FROM schema_migrations`` will show exactly when an
  operator's DB became MCP-proxy-aware.
- ``0001_baseline._DEFAULT_SOURCES`` is also updated so fresh installs
  get the row at baseline time, but existing installs only acquire it
  through this migration.

UCS context:

- L3f = MCP middleware proxy. Sits between the MCP host (Claude
  Desktop / Cursor / Windsurf / Claude Code / Codex CLI / Gemini CLI /
  Cascade-Windsurf) and the upstream MCP server, transparently
  forwarding stdio JSON-RPC 2.0 frames while side-channelling each
  frame into ``raw_captures``.
- This complements the existing L3f sibling ``pce_mcp/`` (capture
  posture A — PCE *as* an MCP server with explicit ``pce_capture``
  tools), with which it shares the OSS / Apache-2.0 classification
  per ADR-013.

See:
- ``Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`` §4.1 +
  §5.2 for the architecture and source_type rationale.
- ADR-015 (planned, P5.B.1) for the formal UCS amendment introducing
  L3f as a first-class layer.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0009")


_MCP_PROXY_SOURCE_ROW = (
    "mcp-proxy-default",
    "mcp_proxy",
    "pce-mcp-proxy",
    "complete",
    "default MCP middleware proxy capture source (UCS L3f, posture B)",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _MCP_PROXY_SOURCE_ROW,
    )
    logger.info("migration 0009: mcp-proxy-default source registered")


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal: only delete the row if no captures reference
    # it. Same pattern as migration 0005 — we never destructively cascade
    # away production data because of a downgrade.
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_MCP_PROXY_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0009 downgrade: mcp-proxy-default source has "
            "captures, leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_MCP_PROXY_SOURCE_ROW[0],),
    )
