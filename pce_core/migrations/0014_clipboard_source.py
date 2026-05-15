# SPDX-License-Identifier: Apache-2.0
"""Migration 0014 - Register the L4a clipboard monitor source (P5.D.1 W4).

The ``raw_captures.source_id`` column is a foreign key into ``sources``.
The clipboard monitor (``pce_core/clipboard_monitor.py``) was historically
inserting captures under ``SOURCE_BROWSER_EXT`` because no dedicated
source existed; that misclassified clipboard-origin AI text as
"browser extension capture", inflating ext counts and making clipboard
its own redundancy leg invisible.

P5.D.1 Wave 4 promotes clipboard to its own leg (UCS L4a) so it can
count toward the ">= 3 V-GREEN per scenario" redundancy invariant for
P0 web sites (F1 Gemini / GAS / Grok) and Windsurf (F5 P4).

The migration is **additive and idempotent** (``INSERT OR IGNORE``),
mirroring the pattern of migrations 0005 / 0009 / 0010 / 0011 / 0012.

UCS context:

- **L4a** = manual clipboard mirror. User copies assistant text from
  any surface (web, desktop, IDE, terminal). The monitor watches the
  Windows / macOS clipboard, scores each new value for AI-text
  signature, and emits a capture when ``ai_signal_score >= 0.6``.

- **Quality tier**: T2_ui_text (free-form prose, no schema). The
  capture body is the raw clipboard string; meta_json carries
  ``ai_signal_score``, optional ``subsystem`` (windsurf / generic
  web / desktop), and detection features.

- **Posture**: out-of-band, after-the-fact. The clipboard monitor
  does not intercept anything live; it only sees what the user
  actively copies. This makes it the most user-mediated leg in the
  stack and a robust fallback when L1/L3a/L3g all fail (e.g. a paid
  tier with opaque wire format and no DOM access).

- **Independence basis** (per REDUNDANCY-AUDIT-MATRIX section 1.2):
  L4a is fully independent of L1 (no proxy involvement), L3a (no
  browser extension), L3g (no on-disk file watcher), L3f (no MCP
  protocol). The only shared basis is "user is interacting with the
  AI" -- which is also the basis for every other leg.

References:

- ``Docs/stability/REDUNDANCY-AUDIT-MATRIX.md`` section 1.2
  independence rules
- ``Docs/stability/redundancy-sprint/04-wave4-third-leg.md`` task
  W4-T3 / W4-T5 (Gemini / GAS / Grok / Windsurf via clipboard)
- ``pce_core/clipboard_monitor.py`` -- the writer
- ``tests/test_clipboard_monitor.py`` -- AI-signal detection tests
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0014")


_CLIPBOARD_SOURCE_ROW = (
    "clipboard-monitor-default",
    "clipboard_monitor",
    "pce-clipboard-monitor",
    "light",
    "default clipboard AI-signal capture source (UCS L4a, P5.D.1 W4)",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _CLIPBOARD_SOURCE_ROW,
    )
    logger.info("migration 0014: clipboard-monitor-default source registered")


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal: only delete the row if no captures reference
    # it. Same pattern as migrations 0005 / 0009 / 0010 / 0011 / 0012.
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_CLIPBOARD_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0014 downgrade: clipboard-monitor-default "
            "source has captures, leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_CLIPBOARD_SOURCE_ROW[0],),
    )
