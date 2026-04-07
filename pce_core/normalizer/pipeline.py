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
    """Normalize a 'conversation' or 'network_intercept' direction capture.

    For conversation captures (DOM extraction), both user and assistant content
    are in one body. We attempt to parse it with the appropriate normalizer
    using the body as both request and response.

    For network_intercept captures, the body contains a wrapper JSON with
    'request_body' and 'response_body' fields that need to be unwrapped
    into synthetic request/response rows for the normalizer.

    The session_hint from the capture row (e.g. ChatGPT conversation ID from
    the URL) is used as the session_key so that multiple incremental captures
    from the same conversation are grouped into one session.
    """
    direction = capture_row.get("direction", "")

    if direction == "network_intercept":
        result = _normalize_network_intercept(capture_row)
    else:
        result = normalize_pair(capture_row, capture_row)

    if result is None:
        return None

    # Use session_hint (conversation ID from URL) as session_key fallback
    session_hint = capture_row.get("session_hint")
    if session_hint and not result.session_key:
        result.session_key = session_hint

    return _persist_result(
        result,
        pair_id=capture_row.get("pair_id", ""),
        source_id=source_id,
        created_via=created_via,
        created_at=capture_row.get("created_at", time.time()),
        db_path=db_path,
    )


def _normalize_network_intercept(capture_row: dict) -> Optional[NormalizedResult]:
    """Unwrap a network_intercept capture and normalize the inner request/response.

    The body_text_or_json contains a wrapper:
    {
        "request_body": "<original request JSON string>",
        "response_body": "<original response JSON/text string>",
        "url": "https://...",
        "is_streaming": true/false,
        ...
    }
    """
    import json

    body_str = capture_row.get("body_text_or_json", "")
    if not body_str:
        return None

    try:
        wrapper = json.loads(body_str)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(wrapper, dict):
        return None

    req_body = wrapper.get("request_body") or ""
    resp_body = wrapper.get("response_body") or ""

    if not req_body and not resp_body:
        return None

    # Build synthetic rows with the unwrapped bodies
    request_row = dict(capture_row)
    request_row["body_text_or_json"] = req_body
    request_row["direction"] = "request"

    response_row = dict(capture_row)
    response_row["body_text_or_json"] = resp_body
    response_row["direction"] = "response"

    # Try to extract host/path from the intercepted URL for better normalizer matching
    intercepted_url = wrapper.get("url", "")
    if intercepted_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(intercepted_url)
            if parsed.hostname:
                request_row["host"] = parsed.hostname
                response_row["host"] = parsed.hostname
            if parsed.path:
                request_row["path"] = parsed.path
                response_row["path"] = parsed.path
        except Exception:
            pass

    return normalize_pair(request_row, response_row)


def _persist_result(
    result: NormalizedResult,
    *,
    pair_id: str,
    source_id: str,
    created_via: str,
    created_at: float,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Write a NormalizedResult to sessions + messages tables.

    For existing sessions (matched by session_key), new messages are
    deduplicated against what is already stored so incremental captures
    from the same conversation don't produce duplicates.
    """

    # Find or create session
    session_id = _find_existing_session(result.session_key, db_path=db_path)
    is_existing = session_id is not None

    if not is_existing:
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

    # Build dedup set from existing messages (only for existing sessions)
    existing_hashes = set()
    if is_existing:
        existing_hashes = _get_existing_message_hashes(session_id, db_path=db_path)

    # Insert only genuinely new messages
    msg_count = 0
    for msg in result.messages:
        msg_hash = _message_hash(msg.role, msg.content_text)
        if msg_hash in existing_hashes:
            continue
        existing_hashes.add(msg_hash)  # prevent within-batch dupes

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

    # Update title if the session didn't have one yet
    if is_existing and result.title_hint:
        _update_session_title(session_id, result.title_hint, db_path=db_path)

    logger.info(
        "Normalized %s -> session %s (%d new messages, provider=%s, existing=%s)",
        pair_id[:8], session_id[:8], msg_count, result.provider, is_existing,
    )
    return session_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _message_hash(role: str, content_text: Optional[str]) -> str:
    """Produce a dedup key for a message: role + first 200 chars of content."""
    return f"{role}:{(content_text or '')[:200]}"


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


def _get_existing_message_hashes(
    session_id: str,
    db_path: Optional[Path] = None,
) -> set:
    """Return a set of dedup hashes for all messages already in a session."""
    hashes = set()
    try:
        conn = get_connection(db_path)
        conn.row_factory = __import__("sqlite3").Row
        try:
            rows = conn.execute(
                "SELECT role, content_text FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            for r in rows:
                hashes.add(_message_hash(r["role"], r["content_text"]))
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to fetch existing message hashes")
    return hashes


def _update_session_title(
    session_id: str,
    title: str,
    db_path: Optional[Path] = None,
) -> None:
    """Set title_hint on a session if it was previously empty."""
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                "UPDATE sessions SET title_hint = ? WHERE id = ? AND (title_hint IS NULL OR title_hint = '')",
                (title[:200], session_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
