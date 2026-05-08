# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Best-effort branch backfill (ADR-2026-04-26 §5.5).

Re-derives ``branch_id`` for messages already stored in the ``messages``
table whose ``content_json.threading`` exposes a
``provider_parent_uuid``. Two messages of the same role that share a
parent_uuid but carry different ``content_text`` are an unambiguous
fork (Regenerate / Edit-user-message) — we mint a fresh branch_id for
the SECOND one and point its ``branch_parent_id`` at the first.

Best-effort by design:

- Pre-G4 captures don't have ``provider_parent_uuid`` in their
  ``content_json``. Those sessions stay linear after the backfill
  runs — the ADR explicitly accepts this trade-off (§5.5).
- We never DOWNGRADE a row already on a non-default branch (branch_id
  != '0'). If the live reconciler / DOM channel already minted a
  branch, the backfill leaves it alone.
- Idempotent: running the backfill twice on the same DB has the same
  effect as running it once; the second pass finds zero unmoved rows.

Intended use: one-shot ops invocation after upgrading to a build that
has G4. Exposed via plain Python (``backfill_all_sessions``) so it
can be wired into a CLI, an ``alembic``-style hook, or just called
from a REPL during migration.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from .db import get_connection, new_id
from .rich_content import load_threading_from_content_json

logger = logging.getLogger("pce.branch_backfill")


def backfill_session_branches(
    session_id: str,
    *,
    db_path: Optional[Path] = None,
) -> int:
    """Re-derive branch_id for one session. Returns rows updated.

    Walks the session's messages in ``(ts, id)`` order — same order
    the live reconciler sees on insert — and remembers the FIRST
    message per ``(provider_parent_uuid, role)`` pair. Any later
    message in that pair with different text is a fork; we update its
    ``branch_id`` (only if still on default ``'0'``) and stamp
    ``branch_parent_id`` at the first sibling.
    """
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, role, content_text, content_json, branch_id, "
            "       branch_parent_id "
            "FROM messages WHERE session_id = ? "
            "ORDER BY ts ASC, id ASC",
            (session_id,),
        ).fetchall()

        # First-seen sibling per (parent_uuid, role). The live path
        # uses (parent_uuid, role) too — see
        # ``message_processor._detect_branch_fork`` — so the backfill
        # produces semantically identical output.
        seen: dict[tuple[str, str], dict] = {}
        # Active branch_id per (parent_uuid, role) so a THIRD
        # regenerate on the same parent gets its own branch (not
        # collapsed onto the alt of regenerate #2).
        active_alt: dict[tuple[str, str], str] = {}
        updated = 0

        for row in rows:
            threading = load_threading_from_content_json(row["content_json"])
            parent_uuid = threading.get("provider_parent_uuid")
            if not isinstance(parent_uuid, str) or not parent_uuid:
                continue
            key = (parent_uuid, row["role"])
            first = seen.get(key)
            if first is None:
                seen[key] = dict(row)
                continue
            # Idempotent — same parent + same role + same text →
            # already-handled replay, leave the row alone.
            if (row["content_text"] or "") == (first["content_text"] or ""):
                continue
            # Don't downgrade rows already on a non-default branch
            # (DOM channel beat us to it, or a previous backfill ran).
            if row["branch_id"] and row["branch_id"] != "0":
                continue

            new_bid = new_id()
            active_alt[key] = new_bid
            conn.execute(
                "UPDATE messages SET branch_id = ?, "
                "       branch_parent_id = COALESCE(branch_parent_id, ?) "
                "WHERE id = ?",
                (new_bid, first["id"], row["id"]),
            )
            updated += 1

        conn.commit()
        return updated
    finally:
        conn.close()


def backfill_all_sessions(
    *,
    db_path: Optional[Path] = None,
) -> dict:
    """Run :func:`backfill_session_branches` over every session.

    Returns a summary suitable for logging / CLI output:

    - ``sessions_scanned`` — every session row visited
    - ``sessions_updated`` — sessions where at least one row moved
    - ``rows_updated`` — total messages re-stamped onto an alt branch
    """
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sids = [
            r["id"] for r in conn.execute("SELECT id FROM sessions").fetchall()
        ]
    finally:
        conn.close()

    sessions_scanned = 0
    sessions_updated = 0
    rows_updated = 0
    for sid in sids:
        sessions_scanned += 1
        n = backfill_session_branches(sid, db_path=db_path)
        if n > 0:
            sessions_updated += 1
            rows_updated += n

    summary = {
        "sessions_scanned": sessions_scanned,
        "sessions_updated": sessions_updated,
        "rows_updated": rows_updated,
    }
    logger.info(
        "branches.backfill_complete",
        extra={"event_data": summary},
    )
    return summary
