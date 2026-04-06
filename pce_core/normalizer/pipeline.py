"""PCE Core – Normalization pipeline.

Watches for completed request/response pairs and auto-normalizes them
into sessions + messages (Tier 1).
"""

import logging
import time
from typing import Optional
from pathlib import Path

from ..db import (
    insert_message,
    insert_session,
    query_by_pair,
    query_sessions,
    get_connection,
)
from .base import normalize_pair, NormalizedResult

logger = logging.getLogger("pce.normalizer.pipeline")


def try_normalize_pair(
    pair_id: str,
    source_id: str,
    created_via: str = "auto",
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Attempt to normalize a capture pair into session + messages.

    Called after a response capture is ingested. Looks up the request
    in the DB, runs the normalizer, and writes Tier 1 records.

    Returns session_id on success, None if normalization is not possible
    or the pair is incomplete.
    """
    rows = query_by_pair(pair_id, db_path=db_path)
    if len(rows) < 2:
        return None  # pair not yet complete

    request_row = None
    response_row = None
    for r in rows:
        if r["direction"] == "request":
            request_row = r
        elif r["direction"] == "response":
            response_row = r

    if request_row is None or response_row is None:
        return None

    result = normalize_pair(request_row, response_row)
    if result is None:
        return None

    return _persist_result(
        result,
        pair_id=pair_id,
        source_id=source_id,
        created_via=created_via,
        created_at=request_row.get("created_at", time.time()),
        db_path=db_path,
    )


def normalize_conversation(
    capture_row: dict,
    source_id: str,
    created_via: str = "auto",
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Normalize a 'conversation' direction capture (e.g. from browser extension).

    For conversation captures, both user and assistant content are in one body.
    We attempt to parse it with the appropriate normalizer using the body as
    both request and response.
    """
    result = normalize_pair(capture_row, capture_row)
    if result is None:
        return None

    return _persist_result(
        result,
        pair_id=capture_row.get("pair_id", ""),
        source_id=source_id,
        created_via=created_via,
        created_at=capture_row.get("created_at", time.time()),
        db_path=db_path,
    )


def _persist_result(
    result: NormalizedResult,
    *,
    pair_id: str,
    source_id: str,
    created_via: str,
    created_at: float,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Write a NormalizedResult to sessions + messages tables."""

    # Find or create session
    session_id = _find_existing_session(result.session_key, db_path=db_path)

    if session_id is None:
        session_id = insert_session(
            source_id=source_id,
            started_at=created_at,
            provider=result.provider,
            tool_family=result.tool_family,
            session_key=result.session_key,
            title_hint=result.title_hint,
            created_via=created_via,
            db_path=db_path,
        )

    if session_id is None:
        logger.error("Failed to create session for pair %s", pair_id)
        return None

    # Insert messages
    msg_count = 0
    for msg in result.messages:
        msg_id = insert_message(
            session_id=session_id,
            ts=msg.ts or created_at,
            role=msg.role,
            content_text=msg.content_text,
            content_json=msg.content_json,
            model_name=msg.model_name or result.model_name,
            capture_pair_id=pair_id,
            token_estimate=msg.token_estimate,
            db_path=db_path,
        )
        if msg_id:
            msg_count += 1

    logger.info(
        "Normalized pair %s → session %s (%d messages, provider=%s)",
        pair_id[:8], session_id[:8], msg_count, result.provider,
    )
    return session_id


def _find_existing_session(
    session_key: Optional[str],
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Find an existing session by session_key (for multi-turn grouping)."""
    if not session_key:
        return None
    try:
        conn = get_connection(db_path)
        conn.row_factory = __import__("sqlite3").Row
        try:
            row = conn.execute(
                "SELECT id FROM sessions WHERE session_key = ? ORDER BY started_at DESC LIMIT 1",
                (session_key,),
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()
    except Exception:
        return None
