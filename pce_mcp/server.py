# SPDX-License-Identifier: Apache-2.0
"""PCE MCP Server – Exposes PCE capture and query tools via MCP.

AI assistants (Claude Desktop, Cursor, Windsurf, etc.) can use these
tools to record their own interactions into the local PCE database
and query previously captured data.

Usage:
  python -m pce_mcp                  # stdio transport (default)
  python -m pce_mcp --sse            # SSE transport on port 9801
"""

import json
import logging
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from pce_core.config import DB_PATH
from pce_core.db import (
    init_db,
    insert_capture,
    new_pair_id,
    get_stats,
    query_captures,
    query_by_pair,
    query_sessions,
    query_messages,
    insert_session,
    insert_message,
    record_pipeline_error,
    SOURCE_MCP,
)
from pce_core.logging_config import configure_logging, log_event
from pce_core.normalizer.pipeline import normalize_conversation, try_normalize_pair

# Configure logging once per process; safe to call repeatedly.
configure_logging()
logger = logging.getLogger("pce.mcp")

# ---------------------------------------------------------------------------
# MCP Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP("PCE - Personal Cognitive Engine")


# ---------------------------------------------------------------------------
# Tool: pce_capture
# ---------------------------------------------------------------------------

@mcp.tool()
def pce_capture(
    provider: str,
    direction: str = "conversation",
    host: str = "",
    path: str = "",
    method: str = "POST",
    model_name: str | None = None,
    request_body: str | None = None,
    response_body: str | None = None,
    conversation_json: str | None = None,
    status_code: int | None = None,
    latency_ms: float | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Record an AI interaction into the local PCE database.

    Use this to capture your own conversations or API calls.

    Args:
        provider: AI provider name (e.g. 'openai', 'anthropic', 'google')
        direction: 'request', 'response', or 'conversation' (default: conversation)
        host: API host (e.g. 'api.openai.com')
        path: API path (e.g. '/v1/chat/completions')
        method: HTTP method (default: POST)
        model_name: Model used (e.g. 'gpt-4', 'claude-sonnet-4-20250514')
        request_body: JSON string of the request body (for request/response pairs)
        response_body: JSON string of the response body
        conversation_json: JSON string of a full conversation (for 'conversation' direction)
        status_code: HTTP status code (for responses)
        latency_ms: Response latency in milliseconds
        meta: Additional metadata dict
    """
    _ensure_db()

    pair_id = new_pair_id()
    meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
    results = []

    log_event(
        logger, "mcp.tool_called",
        tool="pce_capture",
        pair_id=pair_id[:8],
        provider=provider, direction=direction, host=host,
        has_conversation=bool(conversation_json),
        has_request=bool(request_body),
        has_response=bool(response_body),
    )

    if direction == "conversation" and conversation_json:
        # Single conversation capture
        cid = insert_capture(
            direction="conversation",
            pair_id=pair_id,
            host=host,
            path=path,
            method=method,
            provider=provider,
            model_name=model_name,
            headers_redacted_json="{}",
            body_text_or_json=conversation_json,
            body_format="json",
            meta_json=meta_json,
            source_id=SOURCE_MCP,
        )
        if cid:
            results.append(f"Captured conversation: {cid[:8]}")
            # Auto-normalize conversation into sessions + messages
            try:
                rows = query_by_pair(pair_id)
                if rows:
                    sid = normalize_conversation(rows[0], source_id=SOURCE_MCP, created_via="mcp")
                    if sid:
                        results.append(f"Normalized → session: {sid[:8]}")
            except Exception as e:
                results.append(f"Conversation normalization skipped: {e}")
                record_pipeline_error(
                    "normalize",
                    f"mcp normalize_conversation: {type(e).__name__}: {e}",
                    source_id=SOURCE_MCP, pair_id=pair_id,
                    details={"provider": provider, "host": host},
                )
        else:
            record_pipeline_error(
                "ingest", "mcp conversation insert returned None",
                source_id=SOURCE_MCP, pair_id=pair_id,
                details={"provider": provider, "host": host},
            )
            return "Error: Failed to insert capture"

    elif request_body or response_body:
        # Request/response pair
        if request_body:
            cid = insert_capture(
                direction="request",
                pair_id=pair_id,
                host=host,
                path=path,
                method=method,
                provider=provider,
                model_name=model_name,
                headers_redacted_json="{}",
                body_text_or_json=request_body,
                body_format="json",
                meta_json=meta_json,
                source_id=SOURCE_MCP,
            )
            if cid:
                results.append(f"Captured request: {cid[:8]}")
            else:
                record_pipeline_error(
                    "ingest", "mcp request insert returned None",
                    source_id=SOURCE_MCP, pair_id=pair_id,
                    details={"provider": provider, "host": host},
                )

        if response_body:
            cid = insert_capture(
                direction="response",
                pair_id=pair_id,
                host=host,
                path=path,
                method=method,
                provider=provider,
                model_name=model_name,
                status_code=status_code,
                latency_ms=latency_ms,
                headers_redacted_json="{}",
                body_text_or_json=response_body,
                body_format="json",
                meta_json=meta_json,
                source_id=SOURCE_MCP,
            )
            if cid:
                results.append(f"Captured response: {cid[:8]}")
            else:
                record_pipeline_error(
                    "ingest", "mcp response insert returned None",
                    source_id=SOURCE_MCP, pair_id=pair_id,
                    details={"provider": provider, "host": host},
                )

            # Auto-normalize if we have both request and response
            if request_body:
                try:
                    sid = try_normalize_pair(pair_id, source_id=SOURCE_MCP, created_via="mcp")
                    if sid:
                        results.append(f"Normalized → session: {sid[:8]}")
                except Exception as e:
                    results.append(f"Normalization skipped: {e}")
                    record_pipeline_error(
                        "normalize",
                        f"mcp try_normalize_pair: {type(e).__name__}: {e}",
                        source_id=SOURCE_MCP, pair_id=pair_id,
                        details={"provider": provider, "host": host},
                    )
    else:
        return "Error: Provide either conversation_json or request_body/response_body"

    log_event(
        logger, "mcp.tool_completed",
        tool="pce_capture",
        pair_id=pair_id[:8],
        outcome="; ".join(results) if results else "no-op",
    )
    return f"OK. pair_id={pair_id}. " + "; ".join(results)


# ---------------------------------------------------------------------------
# Tool: pce_query
# ---------------------------------------------------------------------------

@mcp.tool()
def pce_query(
    last: int = 10,
    provider: str | None = None,
    host: str | None = None,
    direction: str | None = None,
) -> str:
    """Query recent AI interaction captures from the local PCE database.

    Args:
        last: Number of recent captures to return (default: 10, max: 100)
        provider: Filter by provider (e.g. 'openai', 'anthropic')
        host: Filter by host (e.g. 'api.openai.com')
        direction: Filter by direction ('request', 'response', 'conversation')
    """
    _ensure_db()
    last = min(last, 100)

    rows = query_captures(
        last=last,
        provider=provider,
        host=host,
        direction=direction,
    )

    if not rows:
        return "No captures found matching the criteria."

    lines = [f"Found {len(rows)} capture(s):\n"]
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"]))
        line = (
            f"- [{ts}] {r['direction']} {r['provider']} {r['host']}{r['path']} "
            f"model={r.get('model_name', '?')} pair={r['pair_id'][:8]}"
        )
        if r.get("status_code"):
            line += f" status={r['status_code']}"
        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: pce_stats
# ---------------------------------------------------------------------------

@mcp.tool()
def pce_stats() -> str:
    """Get summary statistics from the local PCE database.

    Returns total captures, breakdown by provider, by direction, and
    by source type.
    """
    _ensure_db()
    stats = get_stats()

    lines = [
        f"Total captures: {stats['total_captures']}",
        f"\nBy provider:",
    ]
    for p, c in sorted(stats.get("by_provider", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {p}: {c}")

    lines.append(f"\nBy direction:")
    for d, c in sorted(stats.get("by_direction", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {d}: {c}")

    lines.append(f"\nBy source:")
    for s, c in sorted(stats.get("by_source", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {s}: {c}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: pce_sessions
# ---------------------------------------------------------------------------

@mcp.tool()
def pce_sessions(
    last: int = 10,
    provider: str | None = None,
) -> str:
    """List normalized conversation sessions from the PCE database.

    Sessions are created when request/response pairs are auto-normalized.

    Args:
        last: Number of recent sessions (default: 10, max: 50)
        provider: Filter by provider
    """
    _ensure_db()
    last = min(last, 50)

    sessions = query_sessions(last=last, provider=provider)
    if not sessions:
        return "No sessions found."

    lines = [f"Found {len(sessions)} session(s):\n"]
    for s in sessions:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started_at"]))
        title = s.get("title_hint", "untitled") or "untitled"
        lines.append(
            f"- [{ts}] {s['provider']} | {title[:60]} | "
            f"{s['message_count']} msgs | id={s['id'][:8]}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: pce_session_messages
# ---------------------------------------------------------------------------

@mcp.tool()
def pce_session_messages(session_id: str) -> str:
    """Get all messages from a specific conversation session.

    Args:
        session_id: The session ID (full or prefix, at least 8 chars)
    """
    _ensure_db()

    msgs = query_messages(session_id)
    if not msgs:
        return f"No messages found for session {session_id}."

    lines = [f"Session {session_id[:8]} — {len(msgs)} message(s):\n"]
    for m in msgs:
        ts = time.strftime("%H:%M:%S", time.localtime(m["ts"]))
        content = (m.get("content_text") or "")[:200]
        model_tag = f" [{m['model_name']}]" if m.get("model_name") else ""
        tokens_tag = f" ({m['token_estimate']}tok)" if m.get("token_estimate") else ""
        lines.append(f"[{ts}] {m['role']}{model_tag}{tokens_tag}: {content}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: pce_capture_pair
# ---------------------------------------------------------------------------

@mcp.tool()
def pce_capture_pair(pair_id: str) -> str:
    """Get the full request/response pair for a given pair_id.

    Args:
        pair_id: The pair ID linking a request and response capture
    """
    _ensure_db()

    rows = query_by_pair(pair_id)
    if not rows:
        return f"No captures found for pair_id={pair_id}."

    lines = [f"Pair {pair_id[:8]} — {len(rows)} capture(s):\n"]
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"]))
        body_preview = (r.get("body_text_or_json") or "")[:300]
        lines.append(
            f"--- {r['direction'].upper()} [{ts}] ---\n"
            f"Provider: {r['provider']} | Host: {r['host']}{r['path']}\n"
            f"Model: {r.get('model_name', '?')} | Status: {r.get('status_code', '-')}\n"
            f"Body preview: {body_preview}\n"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_db_initialized = False

def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True
        logger.info("PCE MCP: database initialized at %s", DB_PATH)
