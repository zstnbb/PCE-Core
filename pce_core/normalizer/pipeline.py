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

from ..db import query_by_pair, query_sessions
from ..otel_exporter import pipeline_span
from ..rich_content import build_content_json
from .base import normalize_pair, NormalizedResult
from .message_processor import (
    estimate_tokens as _estimate_tokens,
    message_hash as _message_hash,
    normalize_message_text_for_dedup as _normalize_message_text_for_dedup,
    merge_content_json as _merge_content_json,
    choose_better_content_text as _choose_better_content_text,
    load_attachments as _load_attachments,
    extract_attachment_file_id as _extract_attachment_file_id,
    attachment_key as _attachment_key,
    merge_attachment as _merge_attachment,
    persist_result,
    ATTACHMENT_MARKER_RE as _ATTACHMENT_MARKER_RE,
    FILE_UPLOAD_RE as _FILE_UPLOAD_RE,
)

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
    with pipeline_span("pce.pipeline.try_normalize_pair", pair_id=pair_id, source_id=source_id):
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

        with pipeline_span("pce.normalize", pair_id=pair_id, provider=request_row.get("provider", "")):
            result = normalize_pair(request_row, response_row)
        if result is None:
            return None

        with pipeline_span(
            "pce.persist.message",
            pair_id=pair_id,
            provider=result.provider,
            normalizer=getattr(result, "normalizer_name", None),
            confidence=getattr(result, "confidence", 0.0),
        ):
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
    with pipeline_span(
        "pce.pipeline.normalize_conversation",
        direction=direction, source_id=source_id,
        provider=capture_row.get("provider", ""),
    ):
        with pipeline_span("pce.normalize", direction=direction):
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

        with pipeline_span(
            "pce.persist.message",
            provider=result.provider,
            normalizer=getattr(result, "normalizer_name", None),
        ):
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
        cj = build_content_json(attachments, plain_text=text)
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
            cj = build_content_json(attachments, plain_text=text)
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

    # Confidence: generic fallback starts lower than dedicated normalizers
    confidence = 0.30
    roles = {m.role for m in messages}
    if "user" in roles and "assistant" in roles:
        confidence += 0.20
    if model:
        confidence += 0.05
    if isinstance(resp_data, dict) and resp_data.get("choices"):
        confidence += 0.10
    if len(messages) >= 4:
        confidence += 0.05

    logger.info(
        "generic fallback normalized: provider=%s host=%s model=%s msgs=%d confidence=%.2f",
        provider, host, model, len(messages), confidence,
    )

    return NormalizedResult(
        provider=provider or "unknown",
        tool_family=f"{provider}-network" if provider else "network",
        model_name=model,
        session_key=session_key,
        title_hint=title_hint,
        messages=messages,
        confidence=confidence,
        normalizer_name="_try_generic_normalize",
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
            notion_conf = 0.60 if len(messages) >= 2 else 0.40
            return NormalizedResult(
                provider="notion",
                tool_family="notion-ai",
                model_name=model or "notion-ai",
                session_key=session_key,
                title_hint=user_text[:100] if user_text else None,
                messages=messages,
                confidence=notion_conf,
                normalizer_name="_try_embedded_ai_normalize[notion]",
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
            gemini_conf = 0.55
            roles = {m.role for m in messages}
            if "user" in roles and "assistant" in roles:
                gemini_conf += 0.15
            return NormalizedResult(
                provider=provider or "google",
                tool_family="gemini-embedded",
                model_name=model,
                session_key=None,
                title_hint=messages[0].content_text[:100] if messages else None,
                messages=messages,
                confidence=gemini_conf,
                normalizer_name="_try_embedded_ai_normalize[gemini]",
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
                confidence=0.35,
                normalizer_name="_try_embedded_ai_normalize",
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

    Delegates to message_processor.persist_result which handles session
    find/create, message dedup, quality-aware enrichment via reconciler,
    and auto-tagging.
    """
    return persist_result(
        result,
        pair_id=pair_id,
        source_id=source_id,
        created_via=created_via,
        created_at=created_at,
        db_path=db_path,
    )
