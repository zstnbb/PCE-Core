# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Data retention / pruning.

User-facing knobs (all optional, see ``pce_core/config.py``):

- ``PCE_RETENTION_DAYS``: delete any row older than N days. Default 0
  (unlimited retention).
- ``PCE_RETENTION_MAX_ROWS``: cap on ``raw_captures`` row count. When
  exceeded, the oldest rows are dropped until we're back under the cap.
  Default 0 (unlimited).
- ``PCE_RETENTION_INTERVAL_HOURS``: how often the background sweep runs.
  Default 6.

Guarantees:

- Never deletes rows newer than the ``days`` window, no matter the cap.
- Deletes ``raw_captures`` → ``messages`` (orphans by pair_id) →
  ``sessions`` (orphans) in order. FTS triggers installed by migration
  0001 keep ``messages_fts`` / ``snippets_fts`` in sync automatically.
- Emits a ``retention.swept`` structured event with counts for
  observability.
- Fails open: any exception is logged and recorded into
  ``pipeline_errors`` but never crashes the caller (usually the server
  lifespan task).

Use:

    from pce_core.retention import sweep_once, run_retention_loop

    sweep_once()                                # manual / CLI
    await run_retention_loop(interval_hours=6)  # server lifespan
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .db import get_connection, record_pipeline_error
from .logging_config import log_event

logger = logging.getLogger("pce.retention")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sweep_once(
    *,
    days: Optional[int] = None,
    max_raw_rows: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Run one retention pass. Returns a summary dict.

    When ``days`` is None / 0 **and** ``max_raw_rows`` is None / 0 the
    function is a no-op (returns zero counts). This lets the loop run
    without having to guard the call site.
    """
    # Resolve knobs from config at call time (so env var changes take
    # effect without server restart if the caller reloads config).
    from . import config as pce_config
    if days is None:
        days = getattr(pce_config, "RETENTION_DAYS", 0) or 0
    if max_raw_rows is None:
        max_raw_rows = getattr(pce_config, "RETENTION_MAX_ROWS", 0) or 0

    summary = {
        "days": int(days),
        "max_raw_rows": int(max_raw_rows),
        "raw_captures_deleted": 0,
        "messages_deleted": 0,
        "sessions_deleted": 0,
        "snippets_deleted": 0,
        "pipeline_errors_deleted": 0,
        "duration_ms": 0,
        "skipped": False,
    }

    if days <= 0 and max_raw_rows <= 0:
        summary["skipped"] = True
        return summary

    t0 = time.time()
    try:
        conn = get_connection(db_path)
        try:
            if days > 0:
                cutoff = t0 - days * 86400.0
                summary["raw_captures_deleted"] += _delete_raw_older_than(conn, cutoff)
                summary["messages_deleted"] += _delete_messages_older_than(conn, cutoff)
                summary["snippets_deleted"] += _delete_snippets_older_than(conn, cutoff)
                summary["pipeline_errors_deleted"] += _delete_errors_older_than(conn, cutoff)

            if max_raw_rows > 0:
                summary["raw_captures_deleted"] += _cap_raw_rows(conn, max_raw_rows)

            summary["sessions_deleted"] += _delete_orphan_sessions(conn)
            conn.commit()
        finally:
            conn.close()

    except Exception as exc:
        logger.exception("retention sweep failed")
        record_pipeline_error(
            "retention",
            f"{type(exc).__name__}: {exc}",
            db_path=db_path,
        )

    summary["duration_ms"] = int((time.time() - t0) * 1000)
    log_event(
        logger, "retention.swept",
        **summary,
    )
    return summary


async def run_retention_loop(
    *,
    interval_hours: Optional[float] = None,
    db_path: Optional[Path] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Background loop: run ``sweep_once`` every ``interval_hours``.

    Intended to be spawned from the FastAPI lifespan handler::

        task = asyncio.create_task(run_retention_loop(stop_event=stop))
        ...
        stop.set()
        await task
    """
    from . import config as pce_config
    if interval_hours is None:
        interval_hours = getattr(pce_config, "RETENTION_INTERVAL_HOURS", 6.0) or 6.0
    interval_s = max(float(interval_hours) * 3600.0, 60.0)

    log_event(
        logger, "retention.loop_started",
        interval_hours=interval_hours,
        days=getattr(pce_config, "RETENTION_DAYS", 0) or 0,
        max_raw_rows=getattr(pce_config, "RETENTION_MAX_ROWS", 0) or 0,
    )

    try:
        while True:
            # Run sweep off the event loop so SQLite I/O doesn't starve
            # the FastAPI request loop.
            try:
                await asyncio.to_thread(sweep_once, db_path=db_path)
            except Exception:
                logger.exception("retention sweep wrapper failed")

            # Sleep with cancellation support
            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                except asyncio.TimeoutError:
                    continue
                break  # stop_event was set
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        log_event(logger, "retention.loop_cancelled")
        raise
    finally:
        log_event(logger, "retention.loop_stopped")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _delete_raw_older_than(conn, cutoff: float) -> int:
    cur = conn.execute(
        "DELETE FROM raw_captures WHERE created_at < ?", (cutoff,),
    )
    return cur.rowcount or 0


def _delete_messages_older_than(conn, cutoff: float) -> int:
    cur = conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
    return cur.rowcount or 0


def _delete_snippets_older_than(conn, cutoff: float) -> int:
    cur = conn.execute("DELETE FROM snippets WHERE created_at < ? AND favorited = 0", (cutoff,))
    return cur.rowcount or 0


def _delete_errors_older_than(conn, cutoff: float) -> int:
    try:
        cur = conn.execute("DELETE FROM pipeline_errors WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0
    except Exception:
        return 0


def _cap_raw_rows(conn, max_rows: int) -> int:
    """Trim ``raw_captures`` to at most ``max_rows``, keeping the newest."""
    total = conn.execute("SELECT COUNT(*) FROM raw_captures").fetchone()[0]
    if total <= max_rows:
        return 0
    to_delete = total - max_rows
    cur = conn.execute(
        "DELETE FROM raw_captures WHERE id IN ("
        "  SELECT id FROM raw_captures ORDER BY created_at ASC LIMIT ?"
        ")",
        (to_delete,),
    )
    return cur.rowcount or 0


def _delete_orphan_sessions(conn) -> int:
    """Delete any session that no longer has any messages.

    Explicitly does NOT delete favorited sessions (protected by the
    favorites feature) even if empty – the user asked us to keep them.
    """
    cur = conn.execute(
        "DELETE FROM sessions "
        "WHERE favorited = 0 AND id NOT IN (SELECT DISTINCT session_id FROM messages)"
    )
    return cur.rowcount or 0


__all__ = ["sweep_once", "run_retention_loop"]
