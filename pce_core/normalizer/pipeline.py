"""PCE Core – Normalization pipeline.

Watches for completed request/response pairs and auto-normalizes them
into sessions + messages (Tier 1).
"""

import json
import logging
import re
import time
from typing import Optional
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..db import (
    insert_message,
    insert_session,
    query_by_pair,
    query_sessions,
    get_connection,
)
from .base import normalize_pair, NormalizedResult

logger = logging.getLogger("pce.normalizer.pipeline")

_ATTACHMENT_MARKER_RE = re.compile(
    r"^\[(?:image|file(?::[^\]]+)?|audio|document(?::[^\]]+)?|tool(?:\s+call)?(?::[^\]]+)?|citation|code(?:\s+output)?)\]$",
    re.IGNORECASE,
)
_FILE_UPLOAD_RE = re.compile(
    r'^.+\.(?:pdf|docx?|txt|md|py|js|ts|csv|json|xml|html?|xlsx?|pptx?|zip|rar|'
    r'png|jpe?g|gif|svg|mp[34]|wav|webp|heic|c|cpp|h|rb|go|rs|java|kt|swift|sh|'
    r'bat|ps1|r|sql|ya?ml|toml|ini|cfg|log|ipynb)\s*$',
    re.IGNORECASE,
)


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
        # For conversation captures (DOM extraction), use ConversationNormalizer
        # directly.  Going through the registry would pick OpenAIChatNormalizer
        # first for provider=openai/host=chatgpt.com, which doesn't read the
        # DOM-extracted 'attachments' field on each message.
        from .conversation import ConversationNormalizer
        _conv_norm = ConversationNormalizer()
        body = capture_row.get("body_text_or_json", "")
        result = _conv_norm.normalize(
            body, body,
            provider=capture_row.get("provider", ""),
            host=capture_row.get("host", ""),
            path=capture_row.get("path", ""),
            model_name=capture_row.get("model_name"),
            created_at=capture_row.get("created_at"),
        )

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
        text, attachments = _extract_generic_rich(msg, content)
        if not text or len(text.strip()) < 1:
            continue
        cj = json.dumps({"attachments": attachments}, ensure_ascii=False) if attachments else None
        messages.append(NormalizedMessage(
            role=role,
            content_text=text,
            content_json=cj,
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
            role = msg.get("role", "assistant")
            text, attachments = _extract_generic_rich(msg, msg.get("content", ""))
            if not text:
                continue
            cj = json.dumps({"attachments": attachments}, ensure_ascii=False) if attachments else None
            messages.append(NormalizedMessage(
                role=role,
                content_text=text,
                content_json=cj,
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


def _extract_generic_rich(msg: dict, content) -> tuple[str, list[dict]]:
    """Extract text + attachments from a message with OpenAI-like content.

    Handles both simple string and array-of-parts ``content``, plus
    ``tool_calls``, ``function_call``, role=tool results, and audio.
    """
    text_parts: list[str] = []
    attachments: list[dict] = []

    def _consume_internal_content(obj: dict) -> bool:
        ctype = obj.get("content_type", "")
        parts = obj.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, str):
                    if part:
                        text_parts.append(part)
                elif isinstance(part, dict):
                    part_type = part.get("content_type", "")
                    if part_type == "image_asset_pointer" or "asset_pointer" in part:
                        asset_pointer = part.get("asset_pointer", part.get("url", ""))
                        file_id = ""
                        if isinstance(asset_pointer, str) and asset_pointer.startswith("sediment://file_"):
                            file_id = asset_pointer.split("sediment://", 1)[-1]
                        attachments.append({
                            "type": "image_url",
                            "url": asset_pointer,
                            "file_id": file_id,
                            "size": part.get("size_bytes", 0),
                        })
                        text_parts.append("[Image]")
                    elif isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                    else:
                        attachments.append({
                            "type": part_type or "unknown_part",
                            "raw": _truncate(json.dumps(part, ensure_ascii=False), 2000),
                        })
                        text_parts.append(f"[{part_type or 'unknown'}]")
            return True
        if ctype == "text":
            text_val = obj.get("text")
            if isinstance(text_val, str) and text_val:
                text_parts.append(text_val)
                return True
        return False

    if isinstance(content, str):
        stripped = content.strip()
        parsed_internal = None
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed_internal = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                try:
                    import ast
                    parsed_internal = ast.literal_eval(stripped)
                except Exception:
                    parsed_internal = None
        if isinstance(parsed_internal, dict) and (
            "parts" in parsed_internal or "content_type" in parsed_internal
        ):
            if not _consume_internal_content(parsed_internal):
                text_parts.append(content)
        else:
            text_parts.append(content)
    elif isinstance(content, dict):
        if not _consume_internal_content(content):
            text_parts.append(str(content))
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                ptype = part.get("type", "")
                if ptype == "text":
                    text_parts.append(part.get("text", ""))
                elif ptype == "image_url":
                    img = part.get("image_url", {})
                    url = img.get("url", "") if isinstance(img, dict) else str(img)
                    stored = url if not url.startswith("data:") else url[:200] + "...[truncated]"
                    attachments.append({"type": "image_url", "url": stored})
                    text_parts.append("[Image]")
                elif ptype == "input_audio":
                    attachments.append({"type": "audio", "format": part.get("input_audio", {}).get("format", "")})
                    text_parts.append("[Audio input]")
                else:
                    attachments.append({"type": ptype, "raw": _truncate(json.dumps(part, ensure_ascii=False), 2000)})
                    text_parts.append(f"[{ptype}]")
    elif content is not None:
        text_parts.append(str(content))

    # tool_calls
    for tc in msg.get("tool_calls", []):
        if not isinstance(tc, dict):
            continue
        func = tc.get("function", {})
        name = func.get("name", "?") if isinstance(func, dict) else "?"
        attachments.append({
            "type": "tool_call", "id": tc.get("id", ""), "name": name,
            "arguments": _truncate(func.get("arguments", "") if isinstance(func, dict) else "", 4000),
        })
        text_parts.append(f"[Tool call: {name}]")

    # legacy function_call
    fc = msg.get("function_call")
    if isinstance(fc, dict):
        attachments.append({"type": "tool_call", "name": fc.get("name", ""), "arguments": _truncate(fc.get("arguments", ""), 4000)})
        text_parts.append(f"[Tool call: {fc.get('name', '?')}]")

    # role=tool result
    if msg.get("role") == "tool":
        attachments.append({
            "type": "tool_result", "tool_call_id": msg.get("tool_call_id", ""),
            "content": _truncate(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False) if content else "", 8000),
        })

    # audio output
    audio = msg.get("audio")
    if isinstance(audio, dict):
        transcript = audio.get("transcript", "")
        attachments.append({"type": "audio", "transcript": _truncate(transcript, 4000)})
        if transcript:
            text_parts.append(f"[Audio: {transcript[:100]}]")

    return "\n".join(text_parts), attachments


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + "...[truncated]"


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

    # Build dedup map from existing messages (only for existing sessions)
    # Maps hash → (msg_id, has_content_json) so we can enrich existing messages
    existing_hash_map: dict[str, dict] = {}
    if is_existing:
        existing_hash_map = _get_existing_message_hash_map(session_id, db_path=db_path)

    # Insert new messages or enrich existing ones
    msg_count = 0
    enriched_count = 0
    for msg in result.messages:
        msg_hash = _message_hash(msg.role, msg.content_text)

        if msg_hash in existing_hash_map:
            # Message already exists — but check if we can enrich it with
            # newly-extracted content_json (attachments) from this capture
            existing = existing_hash_map[msg_hash]
            merged_json = _merge_content_json(
                existing.get("content_json"),
                msg.content_json,
            )
            better_text = _choose_better_content_text(
                existing.get("content_text"),
                msg.content_text,
            )
            if merged_json != existing.get("content_json") or better_text != existing.get("content_text"):
                from ..db import update_message_enrichment
                if update_message_enrichment(
                    existing["id"],
                    content_text=better_text if better_text != existing.get("content_text") else None,
                    content_json=merged_json if merged_json != existing.get("content_json") else None,
                    db_path=db_path,
                ):
                    enriched_count += 1
                    existing["content_text"] = better_text
                    existing["content_json"] = merged_json
            continue

        existing_hash_map[msg_hash] = {
            "id": "",
            "content_text": msg.content_text,
            "content_json": msg.content_json,
        }  # prevent within-batch dupes

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
            existing_hash_map[msg_hash]["id"] = msg_id

    # Upgrade metadata on existing sessions when a better capture arrives later.
    if is_existing:
        _upgrade_session_metadata(
            session_id,
            provider=result.provider,
            tool_family=result.tool_family,
            title=result.title_hint,
            created_via=created_via,
            db_path=db_path,
        )

    logger.info(
        "Normalized %s -> session %s (%d new, %d enriched, provider=%s, existing=%s)",
        pair_id[:8], session_id[:8], msg_count, enriched_count, result.provider, is_existing,
    )
    return session_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _message_hash(role: str, content_text: Optional[str]) -> str:
    """Produce a dedup key for a message: role + first 200 chars of normalized content.

    Normalizes raw platform JSON (e.g. ChatGPT's {'content_type':'text','parts':[...]})
    to plain text so the same message from DOM and network intercept deduplicates.

    Also normalizes file upload patterns (e.g. 'report.pdf\\nPDF\\nAnalyze this')
    to just the message text, so re-processed captures match originals.
    """
    text = content_text or ""
    # Quick check: if content looks like raw JSON/Python-repr with 'parts',
    # extract visible text markers so DOM + network copies hash the same way.
    stripped = text.strip()
    if stripped.startswith("{") and "parts" in stripped[:50]:
        try:
            import ast
            parsed = ast.literal_eval(stripped)
            if isinstance(parsed, dict) and isinstance(parsed.get("parts"), list):
                parts_out = []
                for p in parsed["parts"]:
                    if isinstance(p, str):
                        if p:
                            parts_out.append(p)
                    elif isinstance(p, dict):
                        if p.get("content_type") == "image_asset_pointer" or "asset_pointer" in p:
                            parts_out.append("[Image]")
                        elif isinstance(p.get("text"), str):
                            parts_out.append(p["text"])
                text = " ".join(parts_out).strip()
        except Exception:
            pass

    # Normalize file upload patterns: "filename.ext\nLabel\nActual message" → "Actual message"
    text = _normalize_message_text_for_dedup(text)

    return f"{role}:{text[:200]}"


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


def _get_existing_message_hash_map(
    session_id: str,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    """Return a dict mapping dedup hash → (msg_id, has_content_json) for a session.

    This allows the persist logic to detect existing messages that can be
    enriched with newly-extracted content_json (e.g. attachments from a
    page refresh after the extraction code was improved).
    """
    hash_map: dict[str, dict] = {}
    try:
        conn = get_connection(db_path)
        conn.row_factory = __import__("sqlite3").Row
        try:
            rows = conn.execute(
                "SELECT id, role, content_text, content_json FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            for r in rows:
                h = _message_hash(r["role"], r["content_text"])
                hash_map[h] = {
                    "id": r["id"],
                    "content_text": r["content_text"],
                    "content_json": r["content_json"],
                }
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to fetch existing message hashes")
    return hash_map


def _normalize_message_text_for_dedup(text: str) -> str:
    """Collapse attachment-only prefixes so DOM and network copies hash alike."""
    if not text:
        return ""

    lines = [line.strip() for line in text.strip().split("\n")]
    while len(lines) > 1 and lines[0] and _ATTACHMENT_MARKER_RE.match(lines[0]):
        lines.pop(0)
        while lines and not lines[0]:
            lines.pop(0)

    if len(lines) >= 2 and _FILE_UPLOAD_RE.match(lines[0]):
        rest_start = 1
        if len(lines) > 1 and len(lines[1]) <= 30:
            rest_start = 2
        lines = lines[rest_start:]

    return "\n".join(lines).strip()


def _load_attachments(content_json: Optional[str]) -> list[dict]:
    if not content_json:
        return []
    try:
        data = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return []
    attachments = data.get("attachments")
    return attachments if isinstance(attachments, list) else []


def _extract_attachment_file_id(att: dict) -> str:
    file_id = att.get("file_id", "") if isinstance(att, dict) else ""
    if file_id:
        return str(file_id)

    url = att.get("url", "") if isinstance(att, dict) else ""
    if not isinstance(url, str) or not url:
        return ""
    if url.startswith("sediment://file_"):
        return url.split("sediment://", 1)[-1]

    try:
        parsed = urlparse(url)
        file_ids = parse_qs(parsed.query).get("id", [])
    except Exception:
        return ""
    if file_ids and file_ids[0].startswith("file_"):
        return file_ids[0]
    return ""


def _attachment_key(att: dict) -> str:
    if not isinstance(att, dict):
        return json.dumps(att, ensure_ascii=False, sort_keys=True)

    att_type = att.get("type", "")
    file_id = _extract_attachment_file_id(att)
    if file_id:
        return f"{att_type}:file_id:{file_id}"

    if att_type == "file" and att.get("name"):
        return f"file:name:{att['name']}"
    if att_type == "citation" and att.get("url"):
        return f"citation:url:{att['url']}"
    if att_type == "image_url" and att.get("url"):
        return f"image_url:url:{att['url']}"
    if att_type == "code_block" and att.get("code"):
        return f"code_block:{att.get('language', '')}:{att['code']}"
    if att_type == "tool_call" and (att.get("id") or att.get("name")):
        return f"tool_call:{att.get('id', '')}:{att.get('name', '')}"

    return json.dumps(att, ensure_ascii=False, sort_keys=True)


def _merge_attachment(existing: dict, incoming: dict) -> dict:
    merged = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value in (None, "", [], {}):
            continue
        if key == "url":
            current = merged.get("url", "")
            if isinstance(current, str) and current.startswith("sediment://") and isinstance(value, str) and value.startswith("http"):
                merged[key] = value
                continue
            if not current:
                merged[key] = value
                continue
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    return merged


def _merge_content_json(existing_json: Optional[str], incoming_json: Optional[str]) -> Optional[str]:
    if not existing_json:
        return incoming_json
    if not incoming_json:
        return existing_json

    merged: dict[str, dict] = {}
    order: list[str] = []
    for source_json in (existing_json, incoming_json):
        for att in _load_attachments(source_json):
            if not isinstance(att, dict):
                continue
            key = _attachment_key(att)
            if key not in merged:
                merged[key] = dict(att)
                order.append(key)
            else:
                merged[key] = _merge_attachment(merged[key], att)

    if not order:
        return existing_json or incoming_json
    return json.dumps(
        {"attachments": [merged[key] for key in order]},
        ensure_ascii=False,
    )


def _choose_better_content_text(existing_text: Optional[str], incoming_text: Optional[str]) -> Optional[str]:
    if not existing_text:
        return incoming_text
    if not incoming_text:
        return existing_text

    existing_norm = _normalize_message_text_for_dedup(existing_text)
    incoming_norm = _normalize_message_text_for_dedup(incoming_text)
    if existing_norm != incoming_norm:
        return incoming_text if len(incoming_text.strip()) > len(existing_text.strip()) else existing_text

    if incoming_text.strip() == incoming_norm and existing_text.strip() != existing_norm:
        return incoming_norm
    if existing_text.strip() == existing_norm:
        return existing_text
    return incoming_norm or existing_text


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


def _upgrade_session_metadata(
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
