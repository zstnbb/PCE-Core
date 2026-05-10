# SPDX-License-Identifier: Apache-2.0
"""Migration 0012 – Register the L3h CLI Wrapper source (ADR-018 Phase 4).

The ``raw_captures.source_id`` column is a foreign key into ``sources``.
The ADR-018 Phase 4 ``pce_cli_wrapper`` package (UCS layer L3h) writes
rows with ``source_id = 'l3h-cli-wrapper-default'``, so that id must
exist in ``sources`` before the first insert on every database —
including ones created before ADR-018 shipped.

This migration is **additive and idempotent** (``INSERT OR IGNORE``),
mirroring the pattern of migrations 0005 / 0009 / 0010 / 0011.

UCS + ADR-018 context:

- **L3h** = CLI wrap. PATH-priority stdio interceptor for user-installed
  CLI AI agents. v0 canonical case is the npm-installed ``claude``
  shim from ``@anthropic-ai/claude-code``:

      C:\\Users\\<u>\\AppData\\Roaming\\npm\\claude.cmd
      C:\\Users\\<u>\\AppData\\Roaming\\npm\\claude.ps1

  both of which boil down to:

      node <prefix>\\node_modules\\@anthropic-ai\\claude-code\\cli.js  $args

  The wrapper inserts itself in front of these shims, mirrors stdin /
  stdout / stderr to PCE, and forwards every byte through to the real
  child process unchanged. Exit code is preserved.

- **Quality tier**: T1_structured for JSON-RPC / structured stream
  events; T2_ui_text fallback for free-form prose chunks. The wrapper
  always emits the raw byte stream so Tier-1 normalisation can run
  off-line.

- **Posture**: live in-band stdio mirror. Captures are emitted in real
  time via ``insert_capture`` (not buffered until exit), so a
  long-running interactive session is observable at runtime.

- **Coverage** (per ADR-018 §3.6 scenario analysis): closes the Code
  region for users that drive Claude Code from their terminal — the
  one region M plane (.mcpb / mcp_proxy) and L3g (persistence watcher)
  do not naturally cover.

- **Slot letter** (L3h vs ADR's original L3e suggestion): see the
  comment block above ``SOURCE_L3H_CLI_WRAPPER`` in ``pce_core/db.py``.

See:

- ``Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md``
  §3.4 / §3.5 / §6 for the L3h sub-layer definition and Phase 4
  implementation roadmap.
- ``pce_cli_wrapper/`` — the OSS package that writes rows against this
  source.
- ``tests/e2e_cli/`` — discovery + relay + capture tests.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0012")


_L3H_CLI_WRAPPER_SOURCE_ROW = (
    "l3h-cli-wrapper-default",
    "cli_wrapper",
    "pce-cli-wrapper",
    "complete",
    "default CLI wrapper capture source (UCS L3h, ADR-018)",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _L3H_CLI_WRAPPER_SOURCE_ROW,
    )
    logger.info("migration 0012: l3h-cli-wrapper-default source registered")


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal: only delete the row if no captures reference
    # it. Same pattern as migrations 0005 / 0009 / 0010 / 0011 — we
    # never destructively cascade away production data for a downgrade.
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_L3H_CLI_WRAPPER_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0012 downgrade: l3h-cli-wrapper-default "
            "source has captures, leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_L3H_CLI_WRAPPER_SOURCE_ROW[0],),
    )
