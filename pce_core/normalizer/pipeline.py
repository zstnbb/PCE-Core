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

    Handles SSE streaming responses by pre-assembling them into JSON before
    passing to the normalizer.  Falls back to generic OpenAI-compatible
    parsing if no registered normalizer matches.
    """
    import json
    from .sse import is_sse_text, assemble_any_sse

    body_str = capture_row.get("body_text_or_json", "")
    if not body_str:
        logger.debug("network_intercept: empty body")
        return None

    try:
        wrapper = json.loads(body_str)
    except (json.JSONDecodeError, TypeError):
        logger.debug("network_intercept: body is not JSON")
        return None

    if not isinstance(wrapper, dict):
        return None

    req_body = wrapper.get("request_body") or ""
    resp_body = wrapper.get("response_body") or ""

    if not req_body and not resp_body:
        logger.debug("network_intercept: both request_body and response_body empty")
        return None

    # ── SSE pre-processing ──────────────────────────────────────────────
    # Streaming responses arrive as raw SSE text.  Assemble into a single
    # JSON object so downstream normalizers can parse it normally.
    if is_sse_text(resp_body):
        assembled = assemble_any_sse(resp_body)
        if assembled:
            resp_body = json.dumps(assembled, ensure_ascii=False)
            logger.debug("network_intercept: assembled SSE → JSON (%d chars)", len(resp_body))
        else:
            logger.debug("network_intercept: SSE detected but assembly failed")

    # ── Build synthetic rows ────────────────────────────────────────────
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

    # ── Try registered normalizers first ────────────────────────────────
    result = normalize_pair(request_row, response_row)
    if result is not None:
        # Sanity check: for network intercepts we expect both user AND assistant.
        # If only user messages were found (e.g. ConversationNormalizer parsed
        # the request body's messages array but missed the response body's
        # choices), fall through to the generic fallback which handles both.
        has_assistant = any(m.role == "assistant" for m in result.messages)
        if has_assistant:
            return result
        logger.debug(
            "network_intercept: normalizer returned %d msgs but no assistant — trying fallback",
            len(result.messages),
        )

    # ── Fallback: generic OpenAI-compatible parsing ─────────────────────
    # If no normalizer matched (unknown provider/host/path), attempt to
    # parse the request/response using the common {messages} + {choices}
    # format.  This ensures network intercepts from NEW platforms still
    # produce sessions without any code changes.
    provider = capture_row.get("provider", "unknown")
    logger.debug(
        "network_intercept: no normalizer matched for %s %s %s — trying generic fallback",
        provider, request_row.get("host"), request_row.get("path"),
    )
    result = _try_generic_normalize(request_row, response_row, provider)
    if result is not None:
        return result

    # ── Fallback 2: embedded AI format (Notion AI, etc.) ─────────────
    return _try_embedded_ai_normalize(request_row, response_row, provider)


def _try_generic_normalize(
    request_row: dict,
    response_row: dict,
    provider: str,
) -> Optional[NormalizedResult]:
    """Last-resort normalizer for any request with a ``messages`` array.

    Handles the OpenAI-compatible format that most AI APIs use:
    - Request:  ``{"model": "...", "messages": [{"role": ..., "content": ...}]}``
    - Response: ``{"choices": [{"message": {"role": ..., "content": ...}}]}``
    """
    import json
    from .base import NormalizedMessage, NormalizedResult

    # ── Parse request ───────────────────────────────────────────────────
    req_str = request_row.get("body_text_or_json", "")
    try:
        req_data = json.loads(req_str) if req_str else None
    except (json.JSONDecodeError, TypeError):
        req_data = None

    if not isinstance(req_data, dict):
        return None

    req_messages = req_data.get("messages")
    if not isinstance(req_messages, list) or len(req_messages) == 0:
        return None

    model = req_data.get("model")
    messages: list[NormalizedMessage] = []
    created_at = request_row.get("created_at")

    for msg in req_messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content")
        if content is None:
            continue
        if isinstance(content, list):
            # Array-of-parts format
            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = "\n".join(parts)
        if not isinstance(content, str) or len(content.strip()) < 1:
            continue
        messages.append(NormalizedMessage(
            role=role,
            content_text=content,
            model_name=model if role == "assistant" else None,
            ts=created_at,
        ))

    # ── Parse response ──────────────────────────────────────────────────
    resp_str = response_row.get("body_text_or_json", "")
    try:
        resp_data = json.loads(resp_str) if resp_str else None
    except (json.JSONDecodeError, TypeError):
        resp_data = None

    if isinstance(resp_data, dict):
        resp_model = resp_data.get("model", model)
        usage = resp_data.get("usage", {})
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None

        for choice in resp_data.get("choices", []):
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message") or choice.get("delta", {})
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if not content:
                continue
            role = msg.get("role", "assistant")
            messages.append(NormalizedMessage(
                role=role,
                content_text=content,
                model_name=resp_model,
                token_estimate=completion_tokens,
                ts=created_at,
            ))
        if resp_model:
            model = resp_model

    if not messages:
        return None

    # ── Build result ────────────────────────────────────────────────────
    session_key = req_data.get("conversation_id") or req_data.get("session_id")
    title_hint = None
    for m in messages:
        if m.role == "user" and m.content_text:
            title_hint = m.content_text[:100]
            break

    host = request_row.get("host", "")

    logger.info(
        "generic fallback normalized: provider=%s host=%s model=%s msgs=%d",
        provider, host, model, len(messages),
    )

    return NormalizedResult(
        provider=provider or "unknown",
        tool_family=f"{provider}-network" if provider else "network",
        model_name=model,
        session_key=session_key,
        title_hint=title_hint,
        messages=messages,
    )


def _try_embedded_ai_normalize(
    request_row: dict,
    response_row: dict,
    provider: str,
) -> Optional[NormalizedResult]:
    """Normalize embedded AI formats (Notion AI, Google Docs Gemini, etc.).

    These services don't use the standard OpenAI {messages}+{choices} format.
    Instead they have provider-specific request/response structures:

    - Notion AI:  req={context, prompt, aiSessionId} → resp={completion} or streamed text
    - Google:     req={contents:[{parts:[{text}]}]} → resp={candidates:[{content:{parts}}]}
    - Gmail:      req={candidatesCount, ...} → resp={suggestions:[...]}
    """
    import json
    from .base import NormalizedMessage, NormalizedResult

    req_str = request_row.get("body_text_or_json", "")
    resp_str = response_row.get("body_text_or_json", "")

    try:
        req_data = json.loads(req_str) if req_str else None
    except (json.JSONDecodeError, TypeError):
        req_data = None

    try:
        resp_data = json.loads(resp_str) if resp_str else None
    except (json.JSONDecodeError, TypeError):
        resp_data = None

    if not isinstance(req_data, dict):
        return None

    host = request_row.get("host", "")
    created_at = request_row.get("created_at")
    messages: list[NormalizedMessage] = []
    model = req_data.get("model")

    # ── Notion AI format ───────────────────────────────────────────────
    if req_data.get("aiSessionId") or req_data.get("prompt") and "notion" in host:
        user_text = req_data.get("prompt", "")
        context = req_data.get("context", "")
        if context and user_text:
            user_text = f"[Context: {context[:200]}]\n\n{user_text}"
        elif context:
            user_text = context

        if user_text:
            messages.append(NormalizedMessage(
                role="user", content_text=user_text, ts=created_at,
            ))

        # Response: {completion: "..."} or raw streamed text
        assistant_text = ""
        if isinstance(resp_data, dict):
            assistant_text = resp_data.get("completion", "") or resp_data.get("text", "")
        elif isinstance(resp_str, str) and len(resp_str) > 5:
            assistant_text = resp_str  # raw streamed text

        if assistant_text:
            messages.append(NormalizedMessage(
                role="assistant",
                content_text=assistant_text,
                model_name=model or "notion-ai",
                ts=created_at,
            ))

        if messages:
            session_key = req_data.get("aiSessionId")
            return NormalizedResult(
                provider="notion",
                tool_family="notion-ai",
                model_name=model or "notion-ai",
                session_key=session_key,
                title_hint=user_text[:100] if user_text else None,
                messages=messages,
            )

    # ── Google Gemini format {contents:[{parts:[{text}]}]} ─────────────
    contents = req_data.get("contents")
    if isinstance(contents, list) and len(contents) > 0:
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            parts = item.get("parts", [])
            text_parts = []
            for p in parts:
                if isinstance(p, dict) and "text" in p:
                    text_parts.append(p["text"])
            if text_parts:
                messages.append(NormalizedMessage(
                    role="user" if role == "user" else "assistant",
                    content_text="\n".join(text_parts),
                    ts=created_at,
                ))

        # Response: {candidates:[{content:{parts:[{text}]}}]}
        if isinstance(resp_data, dict):
            resp_model = resp_data.get("modelVersion", model)
            for candidate in resp_data.get("candidates", []):
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                text_parts = []
                for p in parts:
                    if isinstance(p, dict) and "text" in p:
                        text_parts.append(p["text"])
                if text_parts:
                    messages.append(NormalizedMessage(
                        role="assistant",
                        content_text="\n".join(text_parts),
                        model_name=resp_model,
                        ts=created_at,
                    ))
            if resp_model:
                model = resp_model

        if messages:
            return NormalizedResult(
                provider=provider or "google",
                tool_family="gemini-embedded",
                model_name=model,
                session_key=None,
                title_hint=messages[0].content_text[:100] if messages else None,
                messages=messages,
            )

    # ── Generic {prompt} + {completion/text/output} ────────────────────
    prompt = req_data.get("prompt")
    if isinstance(prompt, str) and len(prompt) > 3:
        messages.append(NormalizedMessage(
            role="user", content_text=prompt, ts=created_at,
        ))

        assistant_text = ""
        if isinstance(resp_data, dict):
            assistant_text = (
                resp_data.get("completion", "")
                or resp_data.get("text", "")
                or resp_data.get("output", "")
                or resp_data.get("generated_text", "")
            )
        if assistant_text:
            messages.append(NormalizedMessage(
                role="assistant",
                content_text=assistant_text,
                model_name=model,
                ts=created_at,
            ))

        if len(messages) >= 2:
            logger.info(
                "embedded AI fallback normalized: provider=%s host=%s model=%s msgs=%d",
                provider, host, model, len(messages),
            )
            return NormalizedResult(
                provider=provider or "unknown",
                tool_family=f"{provider}-embedded" if provider else "embedded",
                model_name=model,
                session_key=None,
                title_hint=prompt[:100],
                messages=messages,
            )

    return None


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
