# SPDX-License-Identifier: Apache-2.0
"""Migration 0011 – Register the L3g Local Persistence Watcher source (ADR-018).

The ``raw_captures.source_id`` column is a foreign key into ``sources``.
The ADR-018 Phase 3 `pce_persistence_watcher` package (UCS layer L3g)
writes rows with ``source_id = 'l3g-local-persistence-default'``, so
that id must exist in ``sources`` before the first insert on every
database — including ones created before ADR-018 shipped.

This migration is **additive and idempotent** (``INSERT OR IGNORE``),
mirroring the pattern of migration 0005 (CDP source) and 0009 (MCP
proxy source).

Why a dedicated migration instead of editing ``0001_baseline``?

- Project policy: "Never change a migration's side-effects after
  release." Users tracking schema evolution between PCE versions would
  otherwise see the same migration number produce different rows.
- Keeping the change in ``0011`` preserves the audit trail — a
  ``SELECT * FROM schema_migrations`` will show exactly when an
  operator's DB became L3g-aware.
- ``0001_baseline._DEFAULT_SOURCES`` is also updated (same commit) so
  fresh installs get the row at baseline time; existing installs
  acquire it through this migration.

UCS + ADR-018 context:

- **L3g** = Local Persistence Watcher. Parses what the target
  application itself persists to user-readable filesystem locations:
  Chromium Local Storage LevelDB, IndexedDB LevelDB, application-
  specific JSON / sqlite files. On Claude Desktop (MSIX) this covers
  ``%LOCALAPPDATA%\\Packages\\Claude_pzs8sxrjxfjjc\\LocalCache\\Roaming\\Claude\\``:

  * ``Local Storage\\leveldb\\*.ldb`` — session config, user prefs
  * ``IndexedDB\\https_claude.ai_0.indexeddb.leveldb\\*.ldb`` — main
    chat history cache (claude.ai origin)
  * ``local-agent-mode-sessions\\<session-uuid>\\manifest.json`` —
    Cowork (agent-mode) session metadata, timestamps, plugin refs
  * ``local-agent-mode-sessions\\skills-plugin\\<uuid>\\<uuid>\\manifest.json``
    — installed Skills catalogue (schedule / setup-cowork / consolidate-
    memory / skill-creator / xlsx / pptx / pdf / docx)

- **Quality tier**: T1 for structured JSON manifests + decoded
  LevelDB entries; T2 when the on-disk format forces UI-text
  extraction (rare — most Chromium-origin records are structured).

- **Posture**: read-only, poll-based (ReadDirectoryChangesW on
  Windows is an optimisation, not required). LevelDB reads use a
  safe-copy pattern to avoid conflicting with the running app's
  LOCK file.

- **Coverage** (per ADR-018 §3.6 scenario analysis): covers ~60-80%
  of user-visible Chat + ~100% of Cowork session metadata + ~80%
  of installed-Skills / user-config state. The latency floor is
  sub-second when the watcher is poll-driven; eventual consistency
  for the app's in-memory buffer flush to disk (typically <1s for
  LevelDB MemTable roll-over).

See:
- ``Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md``
  §3.4 for the L3g sub-layer definition and §3.5 for its role in the
  three-axis MSIX implementation model.
- ``pce_persistence_watcher/`` — the OSS package that writes rows
  against this source.
- ``tests/e2e_l3g/`` — migration + discovery + observer tests.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0011")


_L3G_LOCAL_PERSISTENCE_SOURCE_ROW = (
    "l3g-local-persistence-default",
    "local_persistence",
    "pce-persistence-watcher",
    "light",
    "default local persistence watcher capture source (UCS L3g, ADR-018)",
)


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, source_type, tool_name, install_mode, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        _L3G_LOCAL_PERSISTENCE_SOURCE_ROW,
    )
    logger.info("migration 0011: l3g-local-persistence-default source registered")


def downgrade(conn: sqlite3.Connection) -> None:
    # Best-effort reversal: only delete the row if no captures reference
    # it. Same pattern as migrations 0005 / 0009 / 0010 — we never
    # destructively cascade away production data for a downgrade.
    has_captures = conn.execute(
        "SELECT 1 FROM raw_captures WHERE source_id = ? LIMIT 1",
        (_L3G_LOCAL_PERSISTENCE_SOURCE_ROW[0],),
    ).fetchone()
    if has_captures:
        logger.warning(
            "migration 0011 downgrade: l3g-local-persistence-default "
            "source has captures, leaving row in place"
        )
        return
    conn.execute(
        "DELETE FROM sources WHERE id = ?",
        (_L3G_LOCAL_PERSISTENCE_SOURCE_ROW[0],),
    )
