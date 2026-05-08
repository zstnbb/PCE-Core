# SPDX-License-Identifier: Apache-2.0
"""Migration 0008 — Branch semantics columns on ``messages`` (ADR-2026-04-26).

``ADR-2026-04-26 regenerate-edit-branch-semantics`` ratifies that the
legacy flat ``messages`` row layout cannot faithfully represent the
forest of alternate assistant replies (Regenerate) and alternate user
prompts (Edit user message) that every Tier-1 site now exposes through
its ``‹ 1/N ›`` switcher UI.

Until this migration runs, branch information lives only inside the
``content_json.threading`` JSON blob minted by
``pce_core/normalizer/conversation.py:_threading_contract_for_message``.
That is fine for payload fidelity but means the dashboard can only
project a flat ``ORDER BY ts`` view — there is no SQL-level way to ask
"give me the default branch only" or "how many alternates does turn 3
have" without parsing JSON on every row.

This migration promotes three native columns so that:

- The query API (``GET /sessions/{id}/messages?branches=collapse``,
  G2 in the T08/T09 stability workstream) can filter by branch in one
  indexed scan instead of a JSON walk.
- The dashboard's ``1/N ▾`` switcher (G3) can compute
  ``distinct branch_id WHERE session_id = ? AND turn_index = ?`` cheaply.
- Forensic queries for audit ("show me every regenerate on this
  session") stay a single SQL statement.

The migration is **additive and idempotent**, matching the policy
established in migrations 0003/0006:

- Each ``ALTER TABLE ... ADD COLUMN`` is guarded by a
  ``PRAGMA table_info`` pre-check — SQLite did not ship
  ``ADD COLUMN IF NOT EXISTS`` until 3.35 and some packaged Python
  builds still ship older amalgamations.
- The backfill that populates ``turn_index`` for legacy rows uses a
  window function (``ROW_NUMBER() OVER (PARTITION BY session_id ORDER
  BY ts, id)``). Window functions have shipped in SQLite since 3.25
  (2018-09-15); Python 3.10 bundles SQLite 3.35+ so this is safe on
  every platform the project targets.
- Indexes use ``CREATE INDEX IF NOT EXISTS``.
- No existing row's ``content_json.threading`` is rewritten — this
  migration promotes a *projection*, the JSON remains the source of
  truth for render-only metadata such as ``branch_group_id``.

Column semantics (authoritative — ADR-2026-04-26 §5.1):

- ``branch_id TEXT NOT NULL DEFAULT '0'``
    The default "main" branch is ``'0'``. All rows inserted before this
    migration are in the main branch by construction — they predate
    any Regenerate/Edit detection. New inserts populated by the
    normalizer use the ``branch_id`` minted in
    ``_threading_contract_for_message`` (e.g.
    ``<conversation_id>:branch:<turn_index>:<text_sig>``). The column
    is ``TEXT`` rather than a foreign key because branch identity is a
    logical grouping, not a first-class entity — ADR §4 evaluated and
    rejected a separate ``branches`` table as premature.

- ``branch_parent_id TEXT`` (nullable)
    Best-effort pointer to the message (user turn or asst turn) where
    this branch forked from. For the default branch (``branch_id='0'``)
    this is always ``NULL``. For alternate branches it may be a real
    ``messages.id`` (when the reconciler resolves it) or a synthetic
    pointer like ``<conversation_id>:turn:<N>`` minted by the
    normalizer (ADR §5.1 allows this degradation — see "weak
    reference" note).

- ``turn_index INTEGER`` (nullable)
    Monotonic position within ``(session_id, branch_id)``. The index
    below makes ``ORDER BY turn_index`` the canonical ordering for
    rendering a single branch. Nullable for forward-compat with older
    rows whose ``ts`` ordering is ambiguous (see backfill below).

Downgrade is a no-op with a warning, matching project policy for
released schema changes — see migration 0006 rationale for the same
reasoning (SQLite ``DROP COLUMN`` has version-dependent caveats and
dropping columns on a released schema is destructive).

See ``Docs/docs/decisions/2026-04-26-regenerate-edit-branch-semantics.md``
for the full decision record including the rejected alternatives
(separate ``branches`` table, JSON-only, etc.) and the downstream work
this unblocks (G2 API param, G3 dashboard switcher).
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("pce.migrations.0008")


# Ordered list of (column_name, ddl_spec). Left-to-right matches the
# order ADR-2026-04-26 §5.1 lists them — keep this order stable after
# release so ``PRAGMA table_info(messages)`` reads identically to the
# ADR spec for audit purposes.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("branch_id", "TEXT NOT NULL DEFAULT '0'"),
    ("branch_parent_id", "TEXT"),
    ("turn_index", "INTEGER"),
]


# Single composite index is enough for the three access patterns G2/G3
# care about:
#   1. "all messages in a session, collapsed to default branch":
#      WHERE session_id=? AND branch_id='0' ORDER BY turn_index
#   2. "all messages in a specific branch":
#      WHERE session_id=? AND branch_id=? ORDER BY turn_index
#   3. "distinct branches at turn N" (for the switcher counter):
#      WHERE session_id=? AND turn_index=? GROUP BY branch_id
# The ordering (session_id, branch_id, turn_index) covers 1+2 directly
# and still lets 3 do an index-range scan.
_NEW_INDEXES: list[tuple[str, str]] = [
    (
        "idx_messages_session_branch",
        "messages(session_id, branch_id, turn_index)",
    ),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names currently on ``table``."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _backfill_turn_index(conn: sqlite3.Connection) -> int:
    """Populate ``turn_index`` for legacy rows using row-number by ts.

    Only touches rows where ``turn_index IS NULL`` so that re-running
    this migration (or re-invoking the backfill after a partial
    upgrade) is safe. Returns the number of rows updated.

    We order by ``(ts, id)`` rather than ``ts`` alone because multiple
    messages in the same capture pair frequently share a timestamp —
    ``id`` is a UUID so it's not semantically meaningful, but it is
    stable and deterministic, which is what ``turn_index`` needs.
    """
    # The window function form produces the assignment in a single
    # indexed scan; the alternative (correlated subquery) is O(N^2) on
    # legacy databases with thousands of messages.
    rows = conn.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY session_id
                    ORDER BY ts, id
                ) - 1 AS ti
            FROM messages
            WHERE turn_index IS NULL
        )
        UPDATE messages
           SET turn_index = (SELECT ti FROM ordered WHERE ordered.id = messages.id)
         WHERE turn_index IS NULL
        """
    )
    # ``rowcount`` on UPDATE ... WHERE is accurate in SQLite post-3.7.
    return rows.rowcount if rows.rowcount is not None else 0


def upgrade(conn: sqlite3.Connection) -> None:
    # ``messages`` is created by migration 0001 — if it's missing we're
    # running on a broken database; surface that loudly, don't pretend.
    existing = _existing_columns(conn, "messages")
    if not existing:
        raise RuntimeError(
            "migration 0008: messages table is missing — migrations out "
            "of order? 0001_baseline should have created it."
        )

    added: list[str] = []
    for col_name, spec in _NEW_COLUMNS:
        if col_name in existing:
            continue
        conn.execute(f"ALTER TABLE messages ADD COLUMN {col_name} {spec}")
        added.append(col_name)

    # Backfill turn_index for rows that predate this migration. New
    # rows written by insert_message() after the migration land with a
    # non-null turn_index directly; this pass is a one-time catch-up.
    backfilled = _backfill_turn_index(conn)

    for idx_name, target in _NEW_INDEXES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {target}")

    logger.info(
        "migration 0008: messages gained branch columns",
        extra={"event": "migrations.0008.upgraded", "pce_fields": {
            "columns_added": added,
            "columns_total_expected": [c for c, _ in _NEW_COLUMNS],
            "indexes_ensured": [n for n, _ in _NEW_INDEXES],
            "rows_backfilled": backfilled,
        }},
    )


def downgrade(conn: sqlite3.Connection) -> None:
    # Intentionally a no-op. See migration 0006 for the full
    # rationale — dropping columns on a released schema is destructive
    # and SQLite's DROP COLUMN has version-dependent caveats. If an
    # operator truly needs to revert, the supported path is restoring
    # a pre-migration backup.
    logger.warning(
        "migration 0008 downgrade is a no-op; restore from backup if "
        "you must revert the branch-semantics columns."
    )
