# SPDX-License-Identifier: Apache-2.0
"""PCE Data Export

Converts sessions and messages into portable formats (Markdown, JSON, JSONL).
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .db import get_connection, query_messages, query_sessions


# ---------------------------------------------------------------------------
# Single-session export
# ---------------------------------------------------------------------------

def export_session_markdown(session_id: str, *, db_path: Optional[Path] = None) -> str:
    """Export a single session as a Markdown document."""
    conn = get_connection(db_path)
    conn.row_factory = __import__("sqlite3").Row
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return ""

    sess = dict(row)
    messages = query_messages(session_id, db_path=db_path)
    if not messages:
        return ""

    title = sess["title_hint"] or "Untitled Session"
    provider = sess["provider"] or "unknown"
    started = _format_date(sess["started_at"])
    msg_count = len(messages)

    # Collect model names from messages
    models = sorted(set(m["model_name"] for m in messages if m.get("model_name")))
    model_str = ", ".join(models) if models else "unknown"

    lines = [
        f"# {title}",
        f"> Provider: {provider} | Model: {model_str} | Date: {started} | Messages: {msg_count}",
        "",
    ]

    if sess.get("language") or sess.get("topic_tags"):
        meta_parts = []
        if sess.get("language"):
            meta_parts.append(f"Language: {sess['language']}")
        if sess.get("topic_tags"):
            try:
                tags = json.loads(sess["topic_tags"])
                meta_parts.append(f"Topics: {', '.join(tags)}")
            except (json.JSONDecodeError, TypeError):
                pass
        if sess.get("total_tokens"):
            meta_parts.append(f"Tokens: {sess['total_tokens']}")
        if meta_parts:
            lines.append(f"> {' | '.join(meta_parts)}")
            lines.append("")

    for m in messages:
        role_label = m["role"].capitalize()
        lines.append(f"## {role_label}")
        lines.append("")
        lines.append(m.get("content_text") or "*(empty)*")
        lines.append("")

    return "\n".join(lines)


def export_session_json(session_id: str, *, db_path: Optional[Path] = None) -> dict:
    """Export a single session as a JSON-serializable dict."""
    conn = get_connection(db_path)
    conn.row_factory = __import__("sqlite3").Row
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return {}

    sess = dict(row)
    messages = query_messages(session_id, db_path=db_path)

    return {
        "session_id": sess["id"],
        "provider": sess["provider"],
        "title": sess["title_hint"],
        "started_at": sess["started_at"],
        "ended_at": sess["ended_at"],
        "message_count": sess["message_count"],
        "language": sess.get("language"),
        "topic_tags": _safe_json_loads(sess.get("topic_tags")),
        "total_tokens": sess.get("total_tokens"),
        "model_names": _safe_json_loads(sess.get("model_names")),
        "messages": [
            {
                "role": m["role"],
                "content": m.get("content_text"),
                "model": m.get("model_name"),
                "ts": m["ts"],
                "token_estimate": m.get("token_estimate"),
            }
            for m in messages
        ],
    }


# ---------------------------------------------------------------------------
# Batch export (JSONL streaming)
# ---------------------------------------------------------------------------

def export_sessions_jsonl(
    *,
    provider: Optional[str] = None,
    since: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> Iterator[str]:
    """Yield JSONL lines for all matching sessions and their messages."""
    sessions = query_sessions(
        last=10000, provider=provider, since=since, db_path=db_path,
    )

    for sess in sessions:
        messages = query_messages(sess["id"], db_path=db_path)
        for m in messages:
            line = json.dumps({
                "session_id": sess["id"],
                "provider": sess.get("provider"),
                "title": sess.get("title_hint"),
                "role": m["role"],
                "content": m.get("content_text"),
                "model": m.get("model_name"),
                "ts": m["ts"],
                "token_estimate": m.get("token_estimate"),
                "language": sess.get("language"),
            }, ensure_ascii=False)
            yield line + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_date(ts: Optional[float]) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _safe_json_loads(val):
    if not val:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None
