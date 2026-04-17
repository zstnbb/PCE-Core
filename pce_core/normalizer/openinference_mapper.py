"""PCE Core – OpenInference / OpenTelemetry GenAI attribute mapper.

Converts PCE's own (session, message, raw_capture) records into
OpenInference-shaped attribute dicts that can be:

- serialised into ``messages.oi_attributes_json`` / ``sessions.oi_attributes_json``
  for fast export without re-parsing every row
- attached to an OpenTelemetry span (via `pce_core.otel_exporter`) for
  any OTel-compatible backend (Phoenix, Langfuse, Jaeger, …)
- streamed as JSONL by ``pce_core.export`` so users can hand their data
  to external analysis tools

Spec references (the definitive truth, not copies):

- OpenInference semantic conventions (Arize):
  https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md
- OpenTelemetry GenAI semconv:
  https://opentelemetry.io/docs/specs/semconv/gen-ai/

Design decisions (ADR-004):

- We populate OpenInference keys first; we fall back to OTel GenAI keys
  when OI has no equivalent.
- We never invent our own keys here. If OI has no field for something
  PCE-specific (e.g. ``tool_family``, reconciliation confidence), we
  surface it under the ``metadata`` attribute (which OI declares as a
  free-form JSON blob).
- This module is **read-only on PCE data**. Mapper output is derived, not
  authoritative — the SQLite row is the source of truth.

Mapper versioning:

``OI_MAPPER_VERSION`` is written as ``oi_schema_version`` so future
evolutions can detect stale caches and re-materialise.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger("pce.normalizer.openinference_mapper")

OI_MAPPER_VERSION = 1

# --------------------------------------------------------------------------- 
# OpenInference attribute keys (centralised so call-sites don't stringify)
# ---------------------------------------------------------------------------

# Span-level
OI_SPAN_KIND = "openinference.span.kind"
OI_LLM_PROVIDER = "llm.provider"
OI_LLM_SYSTEM = "llm.system"
OI_LLM_MODEL_NAME = "llm.model_name"
OI_LLM_INVOCATION_PARAMETERS = "llm.invocation_parameters"
OI_LLM_PROMPT_TEMPLATE = "llm.prompt_template.template"
OI_LLM_TOKEN_PROMPT = "llm.token_count.prompt"
OI_LLM_TOKEN_COMPLETION = "llm.token_count.completion"
OI_LLM_TOKEN_TOTAL = "llm.token_count.total"
OI_INPUT_VALUE = "input.value"
OI_INPUT_MIME_TYPE = "input.mime_type"
OI_OUTPUT_VALUE = "output.value"
OI_OUTPUT_MIME_TYPE = "output.mime_type"
OI_SESSION_ID = "session.id"
OI_USER_ID = "user.id"
OI_METADATA = "metadata"

# Message-level (indexed)
OI_MSG_ROLE = "message.role"
OI_MSG_CONTENT = "message.content"
OI_MSG_NAME = "message.name"
OI_MSG_TOOL_CALL_ID = "message.tool_call_id"
OI_MSG_CONTENTS_TYPE = "message.contents.{j}.message_content.type"
OI_MSG_CONTENTS_TEXT = "message.contents.{j}.message_content.text"
OI_MSG_CONTENTS_IMAGE_URL = "message.contents.{j}.message_content.image.image.url"
OI_MSG_TOOL_CALLS = "message.tool_calls.{j}.tool_call"
OI_MSG_TOOL_CALL_ID_KEY = "tool_call.id"
OI_MSG_TOOL_CALL_NAME = "tool_call.function.name"
OI_MSG_TOOL_CALL_ARGUMENTS = "tool_call.function.arguments"

# Prefixes
OI_INPUT_MESSAGES_PREFIX = "llm.input_messages"
OI_OUTPUT_MESSAGES_PREFIX = "llm.output_messages"

# Allowed span kinds (from OI spec)
SPAN_KIND_LLM = "LLM"
SPAN_KIND_CHAT = "CHAT"
SPAN_KIND_AGENT = "AGENT"
SPAN_KIND_TOOL = "TOOL"

# Role normalisation: OI expects "user" | "assistant" | "system" | "tool" | "function"
_ROLE_ALIASES = {
    "user": "user",
    "human": "user",
    "customer": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",      # Gemini's role name
    "bot": "assistant",
    "system": "system",
    "developer": "system",     # OpenAI's o1-series
    "tool": "tool",
    "function": "tool",
    "observation": "tool",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def message_to_oi_attributes(
    message: Mapping[str, Any],
    session: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the OI attribute dict for a single message.

    This is the payload that gets cached in ``messages.oi_attributes_json``.
    It is intentionally message-scoped (not span-scoped) so the same dict
    can be reused for individual-message export, audit trails, and
    lookups without recomputing.
    """
    role_raw = str(message.get("role") or "")
    role = _normalise_role(role_raw)

    out: dict[str, Any] = {
        OI_MSG_ROLE: role,
        OI_SPAN_KIND: SPAN_KIND_LLM,
        OI_SESSION_ID: str(session.get("id") or ""),
    }
    if session.get("provider"):
        out[OI_LLM_PROVIDER] = str(session["provider"])
        out[OI_LLM_SYSTEM] = str(session["provider"])
    model = message.get("model_name") or session.get("model_names") or None
    if model:
        out[OI_LLM_MODEL_NAME] = _first_model(model)

    content_text = message.get("content_text")
    if content_text:
        out[OI_MSG_CONTENT] = content_text

    # Rich content → OI indexed ``contents`` array
    contents_parts = _message_rich_parts(message)
    for j, part in enumerate(contents_parts):
        for k, v in part.items():
            out[f"message.contents.{j}.{k}"] = v

    # Token counts (assistant messages carry completion tokens; user carry prompt)
    if message.get("oi_input_tokens") is not None:
        out[OI_LLM_TOKEN_PROMPT] = int(message["oi_input_tokens"])
    if message.get("oi_output_tokens") is not None:
        out[OI_LLM_TOKEN_COMPLETION] = int(message["oi_output_tokens"])

    # Metadata – all PCE-specific bits live here so we don't pollute OI keys
    meta = {
        "pce.tool_family": session.get("tool_family"),
        "pce.source_id": session.get("source_id"),
        "pce.capture_pair_id": message.get("capture_pair_id"),
        "pce.token_estimate": message.get("token_estimate"),
        "pce.role_raw": role_raw,
        "pce.mapper_version": OI_MAPPER_VERSION,
    }
    meta = {k: v for k, v in meta.items() if v not in (None, "")}
    if meta:
        out[OI_METADATA] = json.dumps(meta, ensure_ascii=False, sort_keys=True)

    return out


def session_to_oi_attributes(session: Mapping[str, Any]) -> dict[str, Any]:
    """Build OI attributes representing a session envelope.

    Cached in ``sessions.oi_attributes_json``. Used as the base attribute
    set for every span derived from this session (each capture pair will
    merge message-level keys onto this).
    """
    out: dict[str, Any] = {
        OI_SPAN_KIND: SPAN_KIND_CHAT,
        OI_SESSION_ID: str(session.get("id") or ""),
    }
    if session.get("provider"):
        out[OI_LLM_PROVIDER] = str(session["provider"])
        out[OI_LLM_SYSTEM] = str(session["provider"])
    if session.get("model_names"):
        out[OI_LLM_MODEL_NAME] = _first_model(session["model_names"])

    meta = {
        "pce.tool_family": session.get("tool_family"),
        "pce.created_via": session.get("created_via"),
        "pce.source_id": session.get("source_id"),
        "pce.session_key": session.get("session_key"),
        "pce.title_hint": session.get("title_hint"),
        "pce.language": session.get("language"),
        "pce.topic_tags": _split_tags(session.get("topic_tags")),
        "pce.total_tokens": session.get("total_tokens"),
        "pce.message_count": session.get("message_count"),
        "pce.favorited": bool(session.get("favorited")) if session.get("favorited") is not None else None,
        "pce.mapper_version": OI_MAPPER_VERSION,
    }
    meta = {k: v for k, v in meta.items() if v not in (None, "", [])}
    if meta:
        out[OI_METADATA] = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    return out


def pair_to_oi_span(
    pair_id: str,
    session: Mapping[str, Any],
    messages: list[Mapping[str, Any]],
    raw_request: Optional[Mapping[str, Any]] = None,
    raw_response: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a full OI span record for a capture pair.

    Returns a dict with keys:

        name:           str    – human-readable span name
        kind:           str    – OI span kind (LLM / CHAT / …)
        trace_id_hex:   str    – 32-char hex derived from session_id
        span_id_hex:    str    – 16-char hex derived from pair_id
        parent_span_id: str | None
        start_time_ns:  int
        end_time_ns:    int
        attributes:     dict   – OI attribute flat dict
        status:         "OK" | "ERROR"
    """
    if not messages:
        raise ValueError("pair_to_oi_span requires at least one message")

    # Sort messages by timestamp so input_messages / output_messages
    # come out in authoring order regardless of DB row order.
    sorted_msgs = sorted(messages, key=lambda m: m.get("ts") or 0.0)

    attrs: dict[str, Any] = dict(session_to_oi_attributes(session))
    attrs[OI_SPAN_KIND] = SPAN_KIND_LLM  # pair-level spans are LLM, not CHAT

    # Preserve session-level metadata but promote pair-specific fields
    pair_meta_extra = {
        "pce.pair_id": pair_id,
        "pce.normalizer": session.get("normalizer"),
    }
    if raw_request is not None:
        pair_meta_extra["pce.http.method"] = raw_request.get("method")
        pair_meta_extra["pce.http.host"] = raw_request.get("host")
        pair_meta_extra["pce.http.path"] = raw_request.get("path")
    if raw_response is not None:
        pair_meta_extra["pce.http.status_code"] = raw_response.get("status_code")
        pair_meta_extra["pce.http.latency_ms"] = raw_response.get("latency_ms")

    # Merge pair-specific metadata into whatever session put in `metadata`.
    meta_current: dict[str, Any] = {}
    if OI_METADATA in attrs:
        try:
            meta_current = json.loads(attrs[OI_METADATA])
        except (json.JSONDecodeError, TypeError):
            meta_current = {}
    for k, v in pair_meta_extra.items():
        if v not in (None, ""):
            meta_current[k] = v
    if meta_current:
        attrs[OI_METADATA] = json.dumps(meta_current, ensure_ascii=False, sort_keys=True)

    # Input / output message decomposition --------------------------------
    input_msgs = [m for m in sorted_msgs if _normalise_role(m.get("role")) in ("user", "system", "tool")]
    output_msgs = [m for m in sorted_msgs if _normalise_role(m.get("role")) == "assistant"]

    for i, m in enumerate(input_msgs):
        for key, value in _indexed_message_attrs(OI_INPUT_MESSAGES_PREFIX, i, m).items():
            attrs[key] = value
    for i, m in enumerate(output_msgs):
        for key, value in _indexed_message_attrs(OI_OUTPUT_MESSAGES_PREFIX, i, m).items():
            attrs[key] = value

    # Convenient full-value fallbacks for backends that don't expand lists
    if input_msgs:
        attrs[OI_INPUT_VALUE] = _messages_as_json(input_msgs)
        attrs[OI_INPUT_MIME_TYPE] = "application/json"
    if output_msgs:
        attrs[OI_OUTPUT_VALUE] = _messages_as_json(output_msgs)
        attrs[OI_OUTPUT_MIME_TYPE] = "application/json"

    # Token counts (sum across messages, source = per-message oi_*_tokens)
    prompt_tokens = sum(int(m.get("oi_input_tokens") or 0) for m in sorted_msgs) or None
    completion_tokens = sum(int(m.get("oi_output_tokens") or 0) for m in sorted_msgs) or None
    if prompt_tokens:
        attrs[OI_LLM_TOKEN_PROMPT] = prompt_tokens
    if completion_tokens:
        attrs[OI_LLM_TOKEN_COMPLETION] = completion_tokens
    if prompt_tokens or completion_tokens:
        attrs[OI_LLM_TOKEN_TOTAL] = (prompt_tokens or 0) + (completion_tokens or 0)

    # Span model is the most specific we have: pair's assistant msg > session
    model = None
    for m in output_msgs:
        if m.get("model_name"):
            model = m["model_name"]
            break
    if not model:
        model = attrs.get(OI_LLM_MODEL_NAME)
    if model:
        attrs[OI_LLM_MODEL_NAME] = _first_model(model)

    # Timing
    start_ts = sorted_msgs[0].get("ts") or (raw_request or {}).get("created_at") or 0.0
    end_ts = sorted_msgs[-1].get("ts") or (raw_response or {}).get("created_at") or start_ts
    if end_ts < start_ts:
        end_ts = start_ts

    provider = str(session.get("provider") or "?")
    model_name = attrs.get(OI_LLM_MODEL_NAME) or provider
    name = f"{provider}.{model_name}"

    status = "OK"
    if raw_response is not None:
        status_code = raw_response.get("status_code")
        if isinstance(status_code, int) and (status_code >= 400 or status_code == 0):
            status = "ERROR"
    if not output_msgs:
        status = "ERROR"

    return {
        "name": name,
        "kind": SPAN_KIND_LLM,
        "trace_id_hex": _hex_id(str(session.get("id") or ""), 32),
        "span_id_hex": _hex_id(pair_id, 16),
        "parent_span_id": None,
        "start_time_ns": int(start_ts * 1_000_000_000),
        "end_time_ns": int(end_ts * 1_000_000_000),
        "attributes": attrs,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Message-level helpers
# ---------------------------------------------------------------------------

def _indexed_message_attrs(
    prefix: str,
    index: int,
    message: Mapping[str, Any],
) -> dict[str, Any]:
    """Emit ``{prefix}.{i}.message.*`` attributes for one message."""
    out: dict[str, Any] = {}
    role = _normalise_role(message.get("role"))
    out[f"{prefix}.{index}.message.role"] = role
    content = message.get("content_text")
    if content:
        out[f"{prefix}.{index}.message.content"] = content

    for j, part in enumerate(_message_rich_parts(message)):
        for subkey, subval in part.items():
            out[f"{prefix}.{index}.message.contents.{j}.{subkey}"] = subval

    return out


def _message_rich_parts(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert PCE's content_json attachments into OI ``message_content.*`` parts.

    PCE stores attachments as ``{"attachments": [{"type": "image_url", ...}]}``
    or a list directly. OI expects indexed ``message_content`` parts with
    a ``type`` discriminator.
    """
    content_json = message.get("content_json")
    if not content_json:
        return []
    try:
        parsed = json.loads(content_json) if isinstance(content_json, str) else content_json
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, dict):
        attachments = parsed.get("attachments") or parsed.get("parts") or []
    elif isinstance(parsed, list):
        attachments = parsed
    else:
        return []

    parts: list[dict[str, Any]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        atype = att.get("type") or att.get("content_type") or "unknown"
        if atype in ("image_url", "image"):
            url = att.get("url") or att.get("image_url") or att.get("asset_pointer")
            if url:
                parts.append({
                    "message_content.type": "image",
                    "message_content.image.image.url": str(url),
                })
        elif atype == "text":
            t = att.get("text", "")
            if t:
                parts.append({"message_content.type": "text", "message_content.text": t})
        elif atype in ("tool_call",):
            # Represent as text with a marker; OI has tool_calls on message level
            # which we also emit in _tool_calls_for_message below.
            pass
        elif atype in ("audio",):
            parts.append({
                "message_content.type": "audio",
                "message_content.audio.audio.transcript": str(att.get("transcript") or ""),
            })
        else:
            # Unknown attachment type → preserve as generic metadata
            parts.append({
                "message_content.type": atype,
                "message_content.metadata": json.dumps(att, ensure_ascii=False),
            })
    return parts


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _normalise_role(raw: Any) -> str:
    if raw is None:
        return "user"
    key = str(raw).strip().lower()
    return _ROLE_ALIASES.get(key, key or "user")


def _first_model(model: Any) -> str:
    """Pick a single model string out of a potentially multi-value field.

    sessions.model_names is a comma-separated list of models that appeared
    in the conversation. For OI we pick the first one as canonical.
    """
    if model is None:
        return ""
    text = str(model).strip()
    if "," in text:
        return text.split(",", 1)[0].strip()
    return text


def _split_tags(tags: Any) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, list):
        return [str(t) for t in tags if t]
    if isinstance(tags, str):
        return [t.strip() for t in tags.split(",") if t.strip()]
    return []


def _messages_as_json(messages: Iterable[Mapping[str, Any]]) -> str:
    payload = [
        {
            "role": _normalise_role(m.get("role")),
            "content": m.get("content_text") or "",
        }
        for m in messages
    ]
    return json.dumps(payload, ensure_ascii=False)


def _hex_id(seed: str, width: int) -> str:
    """Deterministic hex id of ``width`` chars derived from ``seed``.

    Using a stable hash means the same pair_id / session_id always maps to
    the same OTLP trace/span id, which is essential for re-exporting idempotent
    data into a trace backend.
    """
    import hashlib
    if not seed:
        seed = "pce-empty"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:width]


__all__ = [
    "OI_MAPPER_VERSION",
    "message_to_oi_attributes",
    "session_to_oi_attributes",
    "pair_to_oi_span",
]
