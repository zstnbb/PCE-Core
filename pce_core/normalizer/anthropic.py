# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Anthropic Messages API normalizer.

Handles:
- POST /v1/messages  (request: messages array, response: content blocks)

Request body example:
{
  "model": "claude-sonnet-4-20250514",
  "max_tokens": 1024,
  "system": "You are helpful.",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}

Response body example:
{
  "content": [
    {"type": "text", "text": "Hi there!"}
  ],
  "model": "claude-sonnet-4-20250514",
  "role": "assistant",
  "usage": {"input_tokens": 10, "output_tokens": 25}
}
"""

import json
import logging
import re
from typing import Optional

from pce_core.rich_content import build_content_json

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult
from .conversation import _threading_contract_for_message

logger = logging.getLogger("pce.normalizer.anthropic")

_MESSAGES_PATHS = {
    "/v1/messages",
    "/messages",
}

# claude.ai web/desktop completion path:
#   /api/organizations/<ORG_UUID>/chat_conversations/<CONV_UUID>/completion
# The CONV_UUID is the stable session identifier across multi-turn — Claude
# Desktop POSTs each turn to the SAME path, so extracting it gives us the
# session_key fallback when the request body itself doesn't carry one.
# Empirically verified 2026-05-10 on Claude Desktop v1.6608.2.0 MSIX
# (5-turn conversation, all turns hit /chat_conversations/<same UUID>/completion).
_CHAT_CONV_PATH_RE = re.compile(
    r"/chat_conversations/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})/completion",
    re.IGNORECASE,
)


class AnthropicMessagesNormalizer(BaseNormalizer):
    """Normalizes Anthropic Messages API responses."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        if path in _MESSAGES_PATHS:
            return True
        if "anthropic" in host and "messages" in path:
            return True
        # Browser extension captures from web UI (conversation direction)
        if host == "claude.ai" or provider == "anthropic":
            return True
        return False

    def normalize(
        self,
        request_body: str,
        response_body: str,
        *,
        provider: str,
        host: str,
        path: str,
        model_name: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Optional[NormalizedResult]:
        messages: list[NormalizedMessage] = []

        # --- Parse request ---
        req_data = _safe_json(request_body)
        if req_data is None:
            return None

        model = model_name or req_data.get("model")

        # System message (Anthropic puts it at top level, not in messages array)
        system = req_data.get("system")
        if system:
            if isinstance(system, str):
                messages.append(NormalizedMessage(
                    role="system", content_text=system, ts=created_at,
                ))
            elif isinstance(system, list):
                # Array of content blocks
                text = _blocks_to_text(system)
                if text:
                    messages.append(NormalizedMessage(
                        role="system", content_text=text, ts=created_at,
                    ))

        # ── claude.ai web vs /v1/messages API discrimination ────────────
        # claude.ai web's completion endpoint sends ``{"prompt": "...",
        # "parent_message_uuid": "...", ...}`` whereas the public API
        # sends ``{"messages": [...]}``. The two shapes are mutually
        # exclusive in practice. We branch here so the web shape's
        # ``parent_message_uuid`` flows into branch-fork detection
        # downstream (ADR-2026-04-26 §5.2).
        web_prompt = req_data.get("prompt")
        web_parent_uuid = req_data.get("parent_message_uuid")
        is_web_shape = isinstance(web_prompt, str) and (
            web_parent_uuid is not None or "messages" not in req_data
        )

        if is_web_shape:
            # User turn: prompt + parent_uuid → user message whose
            # provider_parent_uuid is the previous turn's uuid. We don't
            # have a uuid for the user message itself in the request
            # (claude.ai mints it server-side and only surfaces via the
            # SSE response), so leave provider_message_uuid as None for
            # users — assistant's uuid is what matters for branch
            # detection (the next completion will reference IT as parent).
            user_attachments: list = []
            user_text = web_prompt
            cj = build_content_json(
                user_attachments,
                plain_text=user_text,
                threading=_threading_payload(
                    provider_parent_uuid=web_parent_uuid,
                ),
            )
            messages.append(NormalizedMessage(
                role="user",
                content_text=user_text,
                content_json=cj,
                ts=created_at,
                provider_parent_uuid=web_parent_uuid if isinstance(web_parent_uuid, str) else None,
            ))
        else:
            # API shape: ``messages`` array, each entry has its own role
            # + content blocks. No parent_message_uuid here (the API is
            # stateless), so branch detection never fires on these.
            req_messages = req_data.get("messages", [])
            if not isinstance(req_messages, list):
                return None

            for msg in req_messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content")
                text, attachments = _extract_rich_blocks(content)
                if text is not None:
                    threading = _dom_threading_contract(
                        req_data,
                        msg=msg,
                        role=role,
                        content=text,
                        local_index=len(messages),
                    )
                    cj = build_content_json(
                        attachments,
                        plain_text=text,
                        threading=threading,
                    )
                    messages.append(NormalizedMessage(
                        role=role,
                        content_text=text,
                        content_json=cj,
                        model_name=model if role == "assistant" else None,
                        ts=created_at,
                    ))

        # --- Parse response ---
        resp_data = _safe_json(response_body)

        # SSE fallback: if response isn't valid JSON, try assembling SSE chunks
        if resp_data is None and response_body:
            from .sse import is_sse_text, assemble_any_sse
            if is_sse_text(response_body):
                resp_data = assemble_any_sse(response_body)

        if resp_data and isinstance(resp_data, dict):
            resp_model = resp_data.get("model", model)
            resp_role = resp_data.get("role", "assistant")
            usage = resp_data.get("usage", {})
            output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
            # claude.ai web stamps a uuid on the assistant message
            # (surfaces as ``uuid`` on the SSE message_start event, which
            # ``assemble_any_sse`` lifts into the assembled dict). Public
            # API responses also carry an ``id`` field. Either gives us
            # the assistant's provider_message_uuid.
            resp_uuid = resp_data.get("uuid") or resp_data.get("id")
            # In web shape the assistant's parent IS the user message
            # whose uuid the SSE stream returns; if not surfaced, fall
            # back to the request's parent_message_uuid (good enough for
            # branch detection — sibling assistants share the same
            # request parent).
            assistant_parent_uuid = (
                resp_data.get("parent_message_uuid")
                or (web_parent_uuid if is_web_shape else None)
            )

            content_blocks = resp_data.get("content", [])
            text, attachments = _extract_rich_blocks(content_blocks)
            if text:
                cj = build_content_json(
                    attachments,
                    plain_text=text,
                    threading=_threading_payload(
                        provider_message_uuid=resp_uuid if isinstance(resp_uuid, str) else None,
                        provider_parent_uuid=assistant_parent_uuid if isinstance(assistant_parent_uuid, str) else None,
                    ),
                )
                messages.append(NormalizedMessage(
                    role=resp_role,
                    content_text=text,
                    content_json=cj,
                    model_name=resp_model,
                    token_estimate=output_tokens,
                    ts=created_at,
                    provider_message_uuid=resp_uuid if isinstance(resp_uuid, str) else None,
                    provider_parent_uuid=assistant_parent_uuid if isinstance(assistant_parent_uuid, str) else None,
                ))

        # Extract provider-specific session metadata (Claude Writing Style,
        # files manifest, etc.) BEFORE we early-out on empty messages so the
        # layer_meta is available to the persist layer even when the messages
        # array is sparse.
        layer_meta = _extract_request_layer_meta(req_data)

        # Attach Claude file/attachment manifest to the LAST user message
        # so that the file_uuid round-trips into per-message attachments,
        # closing the upload ↔ completion join gap (fu_recon_join item 2/3).
        files_manifest = (layer_meta or {}).get("files") if layer_meta else None
        if files_manifest:
            _attach_files_to_last_user_message(messages, files_manifest)

        if not messages:
            return None

        # Session key
        # 1. Prefer ``conversation_id`` / ``session_id`` from the request body
        #    (public /v1/messages API may carry this in custom wrappers).
        # 2. Fall back to the conversation UUID embedded in the path for
        #    claude.ai web/desktop captures
        #    (``/api/organizations/<ORG>/chat_conversations/<UUID>/completion``).
        #    This is the canonical multi-turn key — the desktop client
        #    POSTs every turn to the SAME path. Without this fallback,
        #    each turn lands in its own session row (D03 multi-turn
        #    failure observed empirically 2026-05-10).
        session_key = req_data.get("conversation_id") or req_data.get("session_id")
        if not session_key:
            m = _CHAT_CONV_PATH_RE.search(path or "")
            if m:
                session_key = m.group("uuid")

        # Title hint
        title_hint = None
        for m in messages:
            if m.role == "user" and m.content_text:
                title_hint = m.content_text[:100]
                break

        # Confidence scoring
        confidence = _compute_confidence(
            req_data=req_data,
            resp_data=resp_data,
            messages=messages,
            host=host,
            path=path,
        )

        return NormalizedResult(
            provider=provider or "anthropic",
            tool_family="api-direct",
            model_name=model,
            session_key=session_key,
            title_hint=title_hint,
            messages=messages,
            confidence=confidence,
            layer_meta=layer_meta or None,
        )


def _compute_confidence(
    req_data: Optional[dict],
    resp_data: Optional[dict],
    messages: list,
    host: str,
    path: str,
) -> float:
    """Score how confidently this normalizer parsed the data.

    Signals:
    - Request has ``messages`` array with content blocks     → +0.25
    - Response has ``content`` blocks array                  → +0.20
    - ``model`` field starts with 'claude'                   → +0.15
    - ``usage`` with input/output tokens                     → +0.10
    - Both user and assistant messages extracted              → +0.15
    - Host is anthropic/claude                               → +0.10
    - Path matches /v1/messages                              → +0.05
    """
    score = 0.0

    if isinstance(req_data, dict):
        req_msgs = req_data.get("messages")
        if isinstance(req_msgs, list) and len(req_msgs) > 0:
            score += 0.25

        model = req_data.get("model", "")
        if isinstance(model, str) and "claude" in model.lower():
            score += 0.15
        elif model:
            score += 0.05

    if isinstance(resp_data, dict):
        content = resp_data.get("content")
        if isinstance(content, list) and len(content) > 0:
            score += 0.20
        usage = resp_data.get("usage")
        if isinstance(usage, dict) and (usage.get("input_tokens") or usage.get("output_tokens")):
            score += 0.10

    roles = {m.role for m in messages} if messages else set()
    if "user" in roles and "assistant" in roles:
        score += 0.15
    elif messages:
        score += 0.05

    if "anthropic" in host or host == "claude.ai":
        score += 0.10
    if path in _MESSAGES_PATHS:
        score += 0.05

    return min(score, 1.0)


def _safe_json(text: str) -> Optional[dict]:
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _dom_threading_contract(
    data: dict,
    *,
    msg: dict,
    role: str,
    content: str,
    local_index: int,
) -> Optional[dict]:
    """Promote claude.ai DOM action metadata into render contracts."""
    if not isinstance(data, dict):
        return None
    is_dom_capture = (
        isinstance(data.get("_capture_meta"), dict)
        or data.get("total_messages") is not None
        or data.get("url") is not None
    )
    if not is_dom_capture:
        return None
    return _threading_contract_for_message(
        data=data,
        msg=msg,
        role=role,
        content=content,
        local_index=local_index,
    )


def _extract_rich_blocks(content) -> tuple[Optional[str], list[dict]]:
    """Extract text and rich-content attachments from Anthropic content.

    Returns ``(text, attachments)`` where *attachments* captures non-text
    content: tool_use, tool_result, images, thinking blocks, etc.
    """
    if content is None:
        return None, []
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []

    text_parts: list[str] = []
    attachments: list[dict] = []

    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue

        btype = block.get("type", "")

        if btype == "text":
            text_parts.append(block.get("text", ""))
            # Citations in annotations
            for cite in block.get("citations", []):
                if isinstance(cite, dict):
                    attachments.append({
                        "type": "citation",
                        "url": cite.get("url", ""),
                        "title": cite.get("title", ""),
                        "text": cite.get("cited_text", cite.get("text", "")),
                    })

        elif btype == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            attachments.append({
                "type": "tool_call",
                "id": block.get("id", ""),
                "name": name,
                "arguments": _truncate(json.dumps(inp, ensure_ascii=False) if inp else "", 4000),
            })
            text_parts.append(f"[Tool call: {name}]")

            # Synthesize a richer attachment when this tool_use is one of
            # Claude's artifact-creating tools (create_file / artifacts /
            # str_replace_editor). The artifact body lands in tool_input
            # after SSE assembly merges all input_json_delta events.
            # Closes fu_recon_join item 1 (artifact body extraction).
            if isinstance(inp, dict):
                artifact_att = _maybe_synthesize_artifact_attachment(name, inp)
                if artifact_att:
                    attachments.append(artifact_att)

        elif btype == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                inner_text, inner_att = _extract_rich_blocks(result_content)
                result_str = inner_text or ""
                attachments.extend(inner_att)
            elif isinstance(result_content, str):
                result_str = result_content
            else:
                result_str = json.dumps(result_content, ensure_ascii=False) if result_content else ""
            attachments.append({
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id", ""),
                "content": _truncate(result_str, 8000),
                "is_error": block.get("is_error", False),
            })
            text_parts.append(f"[Tool result]")

        elif btype == "image":
            source = block.get("source", {})
            if isinstance(source, dict):
                stype = source.get("type", "")
                if stype == "url":
                    attachments.append({
                        "type": "image_url",
                        "url": source.get("url", ""),
                        "media_type": source.get("media_type", ""),
                    })
                else:
                    # base64 — store media type only, not the data
                    attachments.append({
                        "type": "image_url",
                        "media_type": source.get("media_type", ""),
                        "source_type": stype,
                    })
            text_parts.append("[Image]")

        elif btype == "thinking":
            thinking_text = block.get("thinking", "")
            if thinking_text:
                text_parts.append(f"<thinking>\n{thinking_text}\n</thinking>")

        elif btype == "document":
            # PDF / document block
            source = block.get("source", {})
            attachments.append({
                "type": "file",
                "source_type": source.get("type", "") if isinstance(source, dict) else "",
                "media_type": source.get("media_type", "") if isinstance(source, dict) else "",
                "title": block.get("title", ""),
            })
            text_parts.append(f"[Document: {block.get('title', '?')}]")

        else:
            # Unknown block type — preserve metadata
            attachments.append({"type": btype, "raw": _truncate(json.dumps(block, ensure_ascii=False), 2000)})
            text_parts.append(f"[{btype}]")

    text = "\n".join(text_parts) if text_parts else None
    return text, attachments


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + "...[truncated]"


def _threading_payload(
    *,
    provider_message_uuid: Optional[str] = None,
    provider_parent_uuid: Optional[str] = None,
) -> Optional[dict]:
    """Tiny shim that returns ``None`` when both uuids are missing.

    ``build_content_json`` skips empty threading dicts, but feeding it
    ``{"provider_parent_uuid": None}`` would still create a key. Filtering
    upfront keeps the stored ``content_json`` lean for sessions that don't
    surface uuids (the official API).
    """
    payload: dict[str, str] = {}
    if isinstance(provider_message_uuid, str) and provider_message_uuid:
        payload["provider_message_uuid"] = provider_message_uuid
    if isinstance(provider_parent_uuid, str) and provider_parent_uuid:
        payload["provider_parent_uuid"] = provider_parent_uuid
    return payload or None


# ---------------------------------------------------------------------------
# Layer metadata extraction (fu_recon_join items 2, 3, 5)
# ---------------------------------------------------------------------------

_FILE_FIELDS = (
    "file_uuid", "file_name", "file_kind", "size_bytes",
    "thumbnail_url", "preview_url",
    "image_width", "image_height", "primary_color",
    "created_at",
)

_STYLE_FIELDS = ("type", "key", "name")


def _extract_request_layer_meta(req_data: Optional[dict]) -> dict:
    """Pull Claude-specific session metadata out of the request envelope.

    Currently extracted:
    - ``personalized_styles`` (Writing Style: type/key/name/prompt) → N11 / C8
    - active style key when the request also carries ``style_key`` /
      ``active_style`` so the dashboard can highlight the selected one
    - ``files`` (newer schema) and ``attachments`` (legacy schema) → a
      unified file manifest keyed by ``file_uuid`` so reconcilers can
      join against an upload-file capture without per-schema branching.
    """
    if not isinstance(req_data, dict):
        return {}

    meta: dict = {}

    # ----- personalized_styles -----------------------------------------
    raw_styles = req_data.get("personalized_styles")
    if isinstance(raw_styles, list) and raw_styles:
        styles_normalized: list[dict] = []
        for s in raw_styles:
            if not isinstance(s, dict):
                continue
            entry: dict = {}
            for k in _STYLE_FIELDS:
                v = s.get(k)
                if isinstance(v, str) and v:
                    entry[k] = v
            prompt = s.get("prompt")
            if isinstance(prompt, str) and prompt:
                entry["prompt"] = _truncate(prompt, 8000)
            if entry:
                styles_normalized.append(entry)
        if styles_normalized:
            meta["personalized_styles"] = styles_normalized
            active_key = (
                req_data.get("style_key")
                or req_data.get("active_style")
                or req_data.get("selected_style")
            )
            active_style: Optional[dict] = None
            if isinstance(active_key, str) and active_key:
                active_style = next(
                    (s for s in styles_normalized if s.get("key") == active_key),
                    None,
                )
            if active_style is None and len(styles_normalized) == 1:
                active_style = styles_normalized[0]
            if active_style:
                meta["style"] = active_style

    # ----- files / attachments unified manifest ------------------------
    files_manifest: list[dict] = []
    seen_uuids: set[str] = set()

    def _add_file_entry(item):
        if isinstance(item, str) and item:
            if item in seen_uuids:
                return
            seen_uuids.add(item)
            files_manifest.append({"file_uuid": item})
            return
        if not isinstance(item, dict):
            return
        entry: dict = {}
        # Canonicalise across schema variants
        uuid = (
            item.get("file_uuid")
            or item.get("id")
            or item.get("uuid")
            or item.get("file_id")
        )
        if isinstance(uuid, str) and uuid:
            entry["file_uuid"] = uuid
        for k in _FILE_FIELDS:
            if k == "file_uuid":
                continue
            v = item.get(k)
            if v not in (None, "", [], {}):
                entry[k] = v
        # Anthropic legacy variant: {"file_uuid": "...", ...} or
        # plain bare uuid string handled above; nothing else needed.
        if not entry:
            return
        key = entry.get("file_uuid") or json.dumps(entry, sort_keys=True)
        if key in seen_uuids:
            return
        seen_uuids.add(key)
        files_manifest.append(entry)

    for k in ("files", "attachments"):
        items = req_data.get(k)
        if not isinstance(items, list):
            continue
        for item in items:
            _add_file_entry(item)

    if files_manifest:
        meta["files"] = files_manifest

    return meta


def _attach_files_to_last_user_message(
    messages: list[NormalizedMessage],
    files_manifest: list[dict],
) -> None:
    """Append a ``file`` attachment per manifest entry to the latest user msg.

    Mutates ``messages`` in place. The Claude completion endpoint sends only
    the latest turn's prompt; the files referenced by ``files: [<uuid>]``
    belong to that turn. We surface them as per-message attachments so the
    session-layer message has the file context, and copy the same manifest
    into ``layer_meta.files`` for forensic / cross-capture join work.
    """
    if not messages or not files_manifest:
        return
    target: Optional[NormalizedMessage] = None
    for m in reversed(messages):
        if m.role == "user":
            target = m
            break
    if target is None:
        return

    new_atts: list[dict] = []
    for entry in files_manifest:
        if not isinstance(entry, dict):
            continue
        att: dict = {"type": "file"}
        uuid = entry.get("file_uuid")
        if uuid:
            att["file_uuid"] = uuid
            att["file_id"] = uuid  # surface under the canonical attachment key too
        if entry.get("file_name"):
            att["name"] = entry["file_name"]
        for k in (
            "file_kind", "size_bytes", "thumbnail_url", "preview_url",
            "image_width", "image_height", "primary_color",
        ):
            if entry.get(k) not in (None, "", [], {}):
                att[k] = entry[k]
        new_atts.append(att)

    if not new_atts:
        return

    from pce_core.rich_content import build_content_json, load_attachments_from_content_json

    existing = load_attachments_from_content_json(target.content_json) or []
    seen_uuids = {
        a.get("file_uuid") for a in existing if isinstance(a, dict) and a.get("file_uuid")
    }
    merged = list(existing)
    for a in new_atts:
        u = a.get("file_uuid")
        if u and u in seen_uuids:
            continue
        if u:
            seen_uuids.add(u)
        merged.append(a)
    target.content_json = build_content_json(merged, plain_text=target.content_text)


# ---------------------------------------------------------------------------
# Artifact synthesis (fu_recon_join item 1)
# ---------------------------------------------------------------------------

_ARTIFACT_TOOL_NAMES = {
    "artifacts",
    "create_file",
    "save_file",
    "write_file",
    "str_replace_editor",
    "str_replace_based_edit_tool",
}

_LANG_BY_EXT = {
    "py": "python", "js": "javascript", "ts": "typescript",
    "jsx": "jsx", "tsx": "tsx", "html": "html", "htm": "html",
    "css": "css", "md": "markdown", "json": "json", "yml": "yaml",
    "yaml": "yaml", "xml": "xml", "svg": "svg", "sh": "bash",
    "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
    "rb": "ruby", "php": "php", "sql": "sql", "toml": "toml",
    "ini": "ini", "mermaid": "mermaid", "mmd": "mermaid",
}

_CANVAS_LANGS = {"html", "jsx", "tsx", "svg", "markdown", "mermaid", "react"}


def _detect_artifact_lang(path: str, declared_type: str) -> str:
    if declared_type:
        dl = declared_type.lower().strip()
        if dl in _LANG_BY_EXT.values() or dl in _CANVAS_LANGS:
            return dl
        if dl == "application/vnd.ant.react":
            return "jsx"
        if dl == "text/html":
            return "html"
        if dl == "image/svg+xml":
            return "svg"
        if dl == "text/markdown":
            return "markdown"
        if dl == "application/vnd.ant.mermaid":
            return "mermaid"
    if not path:
        return ""
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    return _LANG_BY_EXT.get(ext, "")


def _maybe_synthesize_artifact_attachment(
    tool_name: str,
    tool_input: dict,
) -> Optional[dict]:
    """Convert a Claude artifact tool_use into a code_block/canvas attachment.

    Recognises the common Claude tool names that ship a file body in their
    input JSON. For long / rich-rendered artifacts (HTML, JSX, SVG, markdown,
    mermaid) emit a ``canvas`` attachment; for plain code emit a
    ``code_block`` attachment. The raw tool_use stays attached as
    ``tool_call`` so forensic fidelity is preserved.
    """
    name_l = (tool_name or "").lower().strip()
    if name_l not in _ARTIFACT_TOOL_NAMES:
        return None

    path = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("title")
        or tool_input.get("id")
        or ""
    )
    body = (
        tool_input.get("content")
        or tool_input.get("file_text")
        or tool_input.get("new_str")
        or tool_input.get("text")
        or ""
    )
    if not isinstance(body, str) or not body.strip():
        return None

    declared_type = tool_input.get("type") or tool_input.get("language") or ""
    lang = _detect_artifact_lang(str(path) if path else "", str(declared_type))
    body_truncated = _truncate(body, 50000)

    if lang in _CANVAS_LANGS or len(body) > 4000:
        return {
            "type": "canvas",
            "title": str(path) if path else "",
            "content_type": lang or "text",
            "content": body_truncated,
        }

    return {
        "type": "code_block",
        "language": lang or "text",
        "code": body_truncated,
        "title": str(path) if path else "",
    }
