"""PCE Core – Session management for the normalization pipeline.

Handles session lookup, creation, metadata upgrades, and title management.
Extracted from pipeline.py for single-responsibility decomposition.
"""

import logging
from typing import Optional
from pathlib import Path

from ..db import get_connection, insert_session

logger = logging.getLogger("pce.normalizer.session_manager")


def find_existing_session(
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


def create_session(
    *,
    source_id: str,
    started_at: float,
    provider: str,
    tool_family: str,
    session_key: Optional[str],
    title_hint: Optional[str],
    created_via: str,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Create a new session and return its ID."""
    return insert_session(
        source_id=source_id,
        started_at=started_at,
        provider=provider,
        tool_family=tool_family,
        session_key=session_key,
        title_hint=title_hint,
        created_via=created_via,
        db_path=db_path,
    )


def find_or_create_session(
    *,
    session_key: Optional[str],
    source_id: str,
    started_at: float,
    provider: str,
    tool_family: str,
    title_hint: Optional[str],
    created_via: str,
    db_path: Optional[Path] = None,
) -> tuple[Optional[str], bool]:
    """Find an existing session or create a new one.

    Returns (session_id, is_existing).
    """
    session_id = find_existing_session(session_key, db_path=db_path)
    is_existing = session_id is not None

    if not is_existing:
        session_id = create_session(
            source_id=source_id,
            started_at=started_at,
            provider=provider,
            tool_family=tool_family,
            session_key=session_key,
            title_hint=title_hint,
            created_via=created_via,
            db_path=db_path,
        )

    return session_id, is_existing


def update_session_title(
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


def upgrade_session_metadata(
    session_id: str,
    *,
    provider: Optional[str] = None,
    tool_family: Optional[str] = None,
    title: Optional[str] = None,
    created_via: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    """Upgrade weak session metadata when a better capture later arrives."""
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                UPDATE sessions
                SET
                    provider = CASE
                        WHEN (provider IS NULL OR provider = '' OR provider = 'unknown')
                             AND ? IS NOT NULL AND ? != '' AND ? != 'unknown'
                        THEN ?
                        ELSE provider
                    END,
                    tool_family = CASE
                        WHEN (tool_family IS NULL OR tool_family = '' OR tool_family LIKE 'unknown%')
                             AND ? IS NOT NULL AND ? != ''
                        THEN ?
                        ELSE tool_family
                    END,
                    title_hint = CASE
                        WHEN (title_hint IS NULL OR title_hint = '' OR title_hint LIKE '{%')
                             AND ? IS NOT NULL AND ? != ''
                        THEN ?
                        ELSE title_hint
                    END,
                    created_via = CASE
                        WHEN (created_via IS NULL OR created_via = '' OR created_via = 'browser_extension_network')
                             AND ? IS NOT NULL AND ? != ''
                        THEN ?
                        ELSE created_via
                    END
                WHERE id = ?
                """,
                (
                    provider, provider, provider, provider,
                    tool_family, tool_family, tool_family,
                    title, title, title[:200] if title else None,
                    created_via, created_via, created_via,
                    session_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to upgrade session metadata")
