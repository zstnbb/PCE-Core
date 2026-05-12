# SPDX-License-Identifier: Apache-2.0
"""PCE Core – SSE (Server-Sent Events) parsing utilities.

Streaming AI responses (ChatGPT, DeepSeek, Claude, etc.) are returned as SSE:

    data: {"choices":[{"delta":{"role":"assistant"}}]}
    data: {"choices":[{"delta":{"content":"Hello"}}]}
    data: {"choices":[{"delta":{"content":" world"}}]}
    data: [DONE]

This module detects and assembles SSE text into a single JSON response that
the normalizers can parse like a non-streaming response.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("pce.normalizer.sse")


def is_sse_text(text: str) -> bool:
    """Return True if *text* looks like Server-Sent Events data."""
    if not text or len(text) < 6:
        return False
    stripped = text.lstrip()
    # SSE streams start with "data:" or have it early on
    return stripped[:5] == "data:" or "\ndata:" in text[:500]


def assemble_sse_response(sse_text: str) -> Optional[dict]:
    """Parse SSE chunks and assemble into a single OpenAI-compatible response.

    Input:  raw SSE text (multiple ``data: {...}`` lines)
    Output: ``{"choices": [{"message": {"role": "assistant", "content": "..."}}],
              "model": "...", "usage": {...}}``

    Returns *None* if the SSE cannot be meaningfully assembled.
    """
    if not sse_text:
        return None

    model: Optional[str] = None
    role = "assistant"
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: Optional[dict] = None
    chunk_count = 0

    for line in sse_text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue

        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            continue

        try:
            chunk = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(chunk, dict):
            continue

        chunk_count += 1

        # Extract model (take first non-empty)
        if not model and chunk.get("model"):
            model = chunk["model"]

        # Extract usage (take last, as final chunk often has cumulative usage)
        if chunk.get("usage") and isinstance(chunk["usage"], dict):
            usage = chunk["usage"]

        # Process choices / delta
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            continue

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue

            if delta.get("role"):
                role = delta["role"]

            # Standard content
            if delta.get("content"):
                content_parts.append(delta["content"])

            # Reasoning / thinking content (DeepSeek, OpenAI o1, etc.)
            reasoning = delta.get("reasoning_content") or delta.get("thinking")
            if reasoning:
                reasoning_parts.append(reasoning)

    if not content_parts and not reasoning_parts:
        # Some APIs put the full response in a single non-delta chunk
        # Try to extract from non-streaming format within SSE
        for line in sse_text.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                chunk = json.loads(data_str)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(chunk, dict):
                continue
            # Check for non-delta message format
            for choice in chunk.get("choices", []):
                msg = choice.get("message", {})
                if isinstance(msg, dict) and msg.get("content"):
                    content_parts.append(msg["content"])
                    if msg.get("role"):
                        role = msg["role"]
                    break
            if content_parts:
                break

    if not content_parts and not reasoning_parts:
        return None

    content = "".join(content_parts)

    result: dict = {
        "choices": [{"message": {"role": role, "content": content}}],
    }

    # Include reasoning as a separate field if present
    if reasoning_parts:
        reasoning_text = "".join(reasoning_parts)
        result["choices"][0]["message"]["reasoning_content"] = reasoning_text

    if model:
        result["model"] = model
    if usage:
        result["usage"] = usage

    logger.debug(
        "Assembled SSE: %d chunks → %d chars content, model=%s",
        chunk_count, len(content), model,
    )
    return result


def assemble_anthropic_sse(sse_text: str) -> Optional[dict]:
    """Parse Anthropic-style SSE and assemble into a single response dict.

    Anthropic streaming format with the four delta types we care about:
        event: message_start
        data: {"type":"message_start","message":{"model":"claude-...","role":"assistant",...}}

        event: content_block_start
        data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

        event: content_block_start
        data: {"type":"content_block_start","index":1,"content_block":{"type":"thinking","thinking":""}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":1,"delta":{"type":"thinking_delta","thinking":"Let me ..."}}

        event: content_block_start
        data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu_x","name":"create_file","input":{}}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"file_path\":..."}}

        event: content_block_stop
        data: {"type":"content_block_stop","index":2}

        event: message_delta
        data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{...}}

    Returns assembled dict matching Anthropic non-streaming response:
        {"content": [<block>, ...], "model": "...", "role": "assistant", "usage": {...}}

    Handles ``text_delta``, ``thinking_delta``, ``signature_delta`` and
    ``input_json_delta`` so artifact bodies streamed via Claude's
    ``create_file`` / ``str_replace_editor`` tool_use end up reachable
    through the structured ``content`` array (resolves ``fu_recon_join``
    items 1 + 4).
    """
    if not sse_text:
        return None

    model: Optional[str] = None
    role = "assistant"
    usage: Optional[dict] = None
    blocks: dict[int, dict] = {}
    block_order: list[int] = []

    def _ensure_block(idx: int, default_type: str = "text") -> dict:
        if idx not in blocks:
            blocks[idx] = {"type": default_type}
            block_order.append(idx)
        return blocks[idx]

    for line in sse_text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue

        data_str = line[5:].strip()
        if not data_str or data_str == "[DONE]":
            continue

        try:
            chunk = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(chunk, dict):
            continue

        chunk_type = chunk.get("type", "")

        # message_start: extract model and role
        if chunk_type == "message_start":
            msg = chunk.get("message", {})
            if isinstance(msg, dict):
                if msg.get("model"):
                    model = msg["model"]
                if msg.get("role"):
                    role = msg["role"]

        elif chunk_type == "content_block_start":
            idx = chunk.get("index", 0)
            block_seed = chunk.get("content_block", {})
            if not isinstance(block_seed, dict):
                continue
            block = _ensure_block(idx, block_seed.get("type") or "text")
            for k, v in block_seed.items():
                # Preserve incoming metadata (id, name, type, etc.)
                # but don't clobber any deltas already accumulated.
                if k not in block or block[k] in (None, ""):
                    block[k] = v
            btype = block.get("type", "")
            if btype == "text":
                block.setdefault("text", "")
            elif btype == "thinking":
                block.setdefault("thinking", "")
            elif btype == "tool_use":
                block.setdefault("__partial_json", "")

        elif chunk_type == "content_block_delta":
            idx = chunk.get("index", 0)
            delta = chunk.get("delta", {})
            if not isinstance(delta, dict):
                continue
            block = _ensure_block(idx)
            dtype = delta.get("type", "")
            if dtype == "text_delta":
                block["type"] = block.get("type") or "text"
                block["text"] = (block.get("text") or "") + (delta.get("text", "") or "")
            elif dtype == "thinking_delta":
                block["type"] = "thinking"
                block["thinking"] = (block.get("thinking") or "") + (delta.get("thinking", "") or "")
            elif dtype == "signature_delta":
                # Anthropic ships a signature alongside thinking for verification.
                block["signature"] = (block.get("signature") or "") + (delta.get("signature", "") or "")
            elif dtype == "input_json_delta":
                block["type"] = "tool_use"
                block["__partial_json"] = (block.get("__partial_json") or "") + (delta.get("partial_json", "") or "")

        elif chunk_type == "content_block_stop":
            idx = chunk.get("index", 0)
            block = blocks.get(idx)
            if isinstance(block, dict) and "__partial_json" in block:
                pj = block.pop("__partial_json", "") or ""
                if pj:
                    try:
                        parsed = json.loads(pj)
                        if isinstance(parsed, dict):
                            block["input"] = parsed
                        else:
                            block["input"] = {"_value": parsed}
                    except (json.JSONDecodeError, TypeError):
                        # Stream may end before the JSON is well-formed
                        # (rare, e.g. truncated capture). Preserve raw.
                        block["input"] = {"_partial_json": pj}
                else:
                    block.setdefault("input", {})

        elif chunk_type == "message_delta":
            if chunk.get("usage") and isinstance(chunk["usage"], dict):
                usage = chunk["usage"]

    # Finalise any tool_use blocks that never received an explicit stop
    # (truncated SSE / partial capture).
    for block in blocks.values():
        if isinstance(block, dict) and "__partial_json" in block:
            pj = block.pop("__partial_json", "") or ""
            if pj:
                try:
                    parsed = json.loads(pj)
                    block["input"] = parsed if isinstance(parsed, dict) else {"_value": parsed}
                except (json.JSONDecodeError, TypeError):
                    block["input"] = {"_partial_json": pj}
            else:
                block.setdefault("input", {})

    content_blocks: list[dict] = []
    for idx in block_order:
        block = blocks.get(idx)
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        # Drop empty placeholder blocks
        if btype == "text" and not (block.get("text") or "").strip():
            continue
        if btype == "thinking" and not (block.get("thinking") or "").strip():
            continue
        content_blocks.append(block)

    if not content_blocks:
        return None

    result: dict = {
        "content": content_blocks,
        "role": role,
    }
    if model:
        result["model"] = model
    if usage:
        result["usage"] = usage

    text_chars = sum(len(b.get("text", "")) for b in content_blocks if b.get("type") == "text")
    logger.debug(
        "Assembled Anthropic SSE: %d blocks (%d text chars), model=%s",
        len(content_blocks), text_chars, model,
    )
    return result


def assemble_any_sse(sse_text: str) -> Optional[dict]:
    """Try both OpenAI and Anthropic SSE formats, return whichever works."""
    if not sse_text:
        return None

    # Detect format: Anthropic uses "type": "content_block_delta"
    if "content_block_delta" in sse_text or "message_start" in sse_text:
        result = assemble_anthropic_sse(sse_text)
        if result:
            return result

    # Default: try OpenAI format
    return assemble_sse_response(sse_text)


# ---------------------------------------------------------------------------
# ChatGPT Web / Desktop ``/backend-api/f/conversation`` SSE assembler
# ---------------------------------------------------------------------------
#
# Per ``Docs/research/2026-05-12-chatgpt-desktop-f-endpoint-recon-findings.md``,
# ChatGPT Web (and Desktop, which embeds the same web app) ships its
# assistant stream as **JSON-patch SSE deltas** rather than the OpenAI-API
# ``choices[].delta.content`` shape. Wire format::
#
#     event: delta
#     data: {"p":"", "o":"add", "v":{"message":{...skeleton...}, "conversation_id":"..."}}
#
#     event: delta
#     data: {"p":"/message/content/text", "o":"append", "v":"<first-tok>"}
#
#     event: delta
#     data: {"v":"<next-tok>"}        # bare v — continues last (p,o)
#
#     ... (N more deltas) ...
#     data: {"type":"message_stream_complete", ...}
#     data: [DONE]
#
# The standard ``assemble_sse_response`` above does NOT handle this — it
# expects ``choices[].delta.content`` and finds no chunks here, returning
# None. The 2026-05-10 P2 empirical run mistook this as "no assistant
# content captured" and built a now-rebutted "split-channel WSS"
# hypothesis around it.
#
# This assembler closes the P2 D02 blocker: it walks SSE events, applies
# JSON-patch ops against an in-memory message tree, and returns an
# OpenAI-compatible dict so the existing ``OpenAIChatNormalizer``
# pipeline flows unchanged.

def assemble_chatgpt_web_f_sse(sse_text: str) -> Optional[dict]:
    """Assemble ChatGPT Web ``/backend-api/f/conversation`` JSON-patch SSE.

    Returns an OpenAI-compatible response dict::

        {"choices": [{"message": {"role": "assistant", "content": "..."}}],
         "model": "<resolved_model_slug or model_slug>"}

    so callers can flow through the existing
    ``OpenAIChatNormalizer.normalize()`` path that already handles
    ``choices[0].message.content`` extraction. Returns None when:

    - the body has no SSE structure
    - no ``event: delta`` frames present (e.g. a 567-byte handoff envelope
      from the legacy / pre-rollout branch — those should be handled
      elsewhere or ignored)
    - no assembled message has non-empty text content

    Multi-message streams (e.g. tool call then reply) emit one ``choices``
    entry per message in stream order. Each ``{"p":"", "o":"add"}`` delta
    starts a fresh message; the previous one is committed.

    Reference: ``Docs/research/2026-05-12-chatgpt-desktop-f-endpoint-recon-findings.md``
    §2 "Wire format" + §4 "Stage 4 work to implement".
    """
    if not sse_text:
        return None

    events = _parse_sse_events(sse_text)
    if not events:
        return None

    messages: list[dict] = []  # list of completed message tree dicts
    state: Optional[dict] = None  # current message tree
    last_path: Optional[str] = None
    last_op: Optional[str] = None
    saw_delta = False

    for event_type, data_str in events:
        if not data_str or data_str == "[DONE]":
            continue
        # Skip non-JSON scalar payloads like the leading `data: "v1"` after
        # `event: delta_encoding`.
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        if event_type == "delta":
            saw_delta = True
            # Determine path + op
            if "p" in data or "o" in data:
                p = data.get("p", "")
                o = data.get("o", "add")
                last_path = p
                last_op = o
            else:
                # bare {"v": "..."} — must reuse last (p, o)
                if last_path is None:
                    # Stream started with bare v — treat as append to root text
                    continue
                p = last_path
                o = last_op or "append"
            v = data.get("v")

            # Root-add: starts a fresh message tree, commit any previous one
            if p == "" and o == "add":
                if state is not None and isinstance(state, dict) and state.get("message"):
                    messages.append(state)
                state = dict(v) if isinstance(v, dict) else {}
                continue

            # Sub-path op against current state
            if state is None:
                # Defensive: deltas before any root-add — synthesize empty
                state = {}
            _apply_patch_op(state, p, o, v)
        else:
            # Non-delta data line. We only care about message_stream_complete
            # for committing the current state. Other types we ignore:
            #   resume_conversation_token, input_message, message_marker,
            #   server_ste_metadata, beacon_ui_response, ...
            t = data.get("type", "")
            if t == "message_stream_complete":
                if state is not None and isinstance(state, dict) and state.get("message"):
                    messages.append(state)
                    state = None
                    last_path = None
                    last_op = None

    # Flush any remaining in-progress state at end of stream
    if state is not None and isinstance(state, dict) and state.get("message"):
        messages.append(state)

    if not saw_delta or not messages:
        return None

    # Extract content from each message tree → OpenAI-compatible choices
    choices: list[dict] = []
    model_slug: Optional[str] = None
    conversation_id: Optional[str] = None

    for m in messages:
        if not conversation_id:
            cid = m.get("conversation_id")
            if isinstance(cid, str) and cid:
                conversation_id = cid

        msg = m.get("message")
        if not isinstance(msg, dict):
            continue

        author = msg.get("author")
        role = "assistant"
        if isinstance(author, dict) and isinstance(author.get("role"), str):
            role = author["role"]

        text = _extract_message_text(msg.get("content"))
        if not text:
            continue

        # Pull model slug from metadata if we don't have one yet.
        meta = msg.get("metadata")
        if isinstance(meta, dict) and not model_slug:
            slug = meta.get("resolved_model_slug") or meta.get("model_slug")
            if isinstance(slug, str) and slug:
                model_slug = slug

        choices.append({"message": {"role": role, "content": text}})

    if not choices:
        return None

    result: dict = {"choices": choices}
    if model_slug:
        result["model"] = model_slug
    if conversation_id:
        # Surface conversation_id so OpenAIChatNormalizer can fall back to
        # it for session_key extraction when the request body's
        # ``conversation_id`` field is missing (rare, but seen in some
        # legacy / a-b-test request shapes).
        result["conversation_id"] = conversation_id
    return result


def _parse_sse_events(body: str) -> list[tuple[Optional[str], str]]:
    """Parse an SSE body into ``[(event_type, data_str), ...]`` pairs.

    Each event ends at a blank line per the SSE spec. ``event_type`` is
    the value of an ``event:`` line within the event, or None if absent.
    ``data_str`` is the concatenation of all ``data:`` lines in that
    event (joined with ``\\n`` per spec, though ChatGPT's stream uses
    one data line per event).
    """
    out: list[tuple[Optional[str], str]] = []
    current_event: Optional[str] = None
    current_data: list[str] = []
    for line in body.splitlines():
        if line == "":
            if current_data:
                out.append((current_event, "\n".join(current_data)))
            current_event = None
            current_data = []
            continue
        if line.startswith(":"):
            # SSE comment line
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            # Strip the leading "data:" + optional single space (per spec)
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            current_data.append(payload)
    # Flush trailing event without blank line
    if current_data:
        out.append((current_event, "\n".join(current_data)))
    return out


def _apply_patch_op(state: dict, p: str, o: str, v) -> None:
    """Apply one JSON-patch-style op to ``state`` in place.

    Supported ops: ``add``, ``replace``, ``append``, ``remove``,
    ``patch`` (bulk: v is a list of sub-ops). ``append`` on a string
    field concatenates, on a list appends an element. JSON-patch
    escapes ``~1`` → ``/`` and ``~0`` → ``~`` are honoured.
    """
    if not isinstance(state, dict):
        return

    if o == "patch" and isinstance(v, list):
        for sub in v:
            if isinstance(sub, dict):
                _apply_patch_op(
                    state,
                    sub.get("p", "") or "",
                    sub.get("o", "add") or "add",
                    sub.get("v"),
                )
        return

    parts = [seg for seg in (p.split("/") if p else []) if seg]

    if not parts:
        # Root op (caller normally short-circuits these for `add`, but
        # honour `replace` here for completeness).
        if o in ("add", "replace") and isinstance(v, dict):
            state.clear()
            state.update(v)
        return

    cur: object = state
    for seg in parts[:-1]:
        seg = seg.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            try:
                idx = int(seg)
            except ValueError:
                return
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return
        elif isinstance(cur, dict):
            existing = cur.get(seg)
            if not isinstance(existing, (dict, list)):
                cur[seg] = {}
            cur = cur[seg]
        else:
            return

    leaf = parts[-1].replace("~1", "/").replace("~0", "~")

    if isinstance(cur, list):
        try:
            idx = int(leaf)
        except ValueError:
            return
        if o == "append":
            cur.append(v)
        elif o in ("add", "replace"):
            if idx == len(cur):
                cur.append(v)
            elif 0 <= idx < len(cur):
                cur[idx] = v
        elif o == "remove":
            if 0 <= idx < len(cur):
                cur.pop(idx)
        return

    if isinstance(cur, dict):
        if o in ("add", "replace"):
            cur[leaf] = v
        elif o == "append":
            existing = cur.get(leaf, "")
            if isinstance(existing, str) and isinstance(v, str):
                cur[leaf] = existing + v
            else:
                # Fallback for non-string append: replace
                cur[leaf] = v
        elif o == "remove":
            cur.pop(leaf, None)


def _extract_message_text(content) -> str:
    """Extract human-readable text from a ChatGPT Web ``message.content`` dict.

    Two main shapes observed:

    - ``{"content_type": "text"|"code", "text": "..."}`` — plain
      string under ``text``. ``code`` content_type is used when the
      assistant emits a tool call payload (e.g. ``search_query`` JSON);
      we still return the text faithfully — downstream consumers
      decide whether to re-parse.
    - ``{"content_type": "multimodal_text", "parts": [<str|dict>, ...]}``
      — concatenate string parts; for dict parts with a ``text`` field,
      pull that out. Image / audio parts are skipped (they get
      attachments downstream via ``_extract_rich_content``).
    """
    if not isinstance(content, dict):
        return ""
    text_val = content.get("text")
    if isinstance(text_val, str) and text_val:
        return text_val

    parts = content.get("parts")
    if isinstance(parts, list):
        out: list[str] = []
        for part in parts:
            if isinstance(part, str) and part:
                out.append(part)
            elif isinstance(part, dict):
                pt = part.get("text")
                if isinstance(pt, str) and pt:
                    out.append(pt)
        return "".join(out)
    return ""
