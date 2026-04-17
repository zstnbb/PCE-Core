"""PCE Core – Idempotent import of previously-exported data.

Accepts either format emitted by :mod:`pce_core.export`:

- **OTLP JSONL** – one OpenInference span per line
  (``iter_oi_spans`` / ``stream_jsonl``).
- **PCE JSON envelope** – the single-document shape produced by
  ``stream_json_envelope``.

Key contract:

- **Idempotent**: re-importing the same payload produces zero new rows.
- **Additive**: never deletes existing data.
- **Best-effort**: malformed records are skipped with a warning and
  surface in the returned summary's ``errors`` bucket.

Idempotency key per message: ``session_id + role + message_hash(content)``.
Reusing the existing ``message_hash`` helper keeps this in lock-step with
the reconciler's dedup logic.

Sessions are looked up by their original ``id`` first (from the envelope
or span's ``session.id`` attribute). If none exists locally, a new
session is created with that same id so trace continuity survives the
round-trip.

Imports are NOT auto-normalised – the imported rows are the authoritative
normalised form. Pipeline stages (redact, dedup, reconcile) are skipped.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional

from .db import (
    get_connection,
    query_messages,
    update_session_oi_attributes,
)
from .logging_config import log_event
from .normalizer.message_processor import message_hash

logger = logging.getLogger("pce.import")

IMPORT_SOURCE_DEFAULT = "imported-default"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def import_jsonl(
    stream: Iterable[bytes | str],
    *,
    source_id: str = IMPORT_SOURCE_DEFAULT,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Import OTLP-JSONL spans or raw envelope JSON objects from ``stream``.

    ``stream`` must yield one JSON document per iteration. Bytes and str
    are both accepted.
    """
    summary = _new_summary()
    for line_no, raw in enumerate(stream, start=1):
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                summary["errors"].append({"line": line_no, "error": "invalid utf-8"})
                continue
        raw = raw.strip() if isinstance(raw, str) else raw
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            summary["errors"].append({"line": line_no, "error": f"json: {exc}"})
            continue

        if isinstance(rec, Mapping) and "attributes" in rec and "trace_id_hex" in rec:
            _import_oi_span(rec, source_id=source_id, db_path=db_path, summary=summary)
        elif isinstance(rec, Mapping) and "session" in rec and "messages" in rec:
            _import_envelope(rec, source_id=source_id, db_path=db_path, summary=summary)
        else:
            summary["errors"].append({
                "line": line_no,
                "error": f"unknown record shape: keys={list(rec)[:5] if isinstance(rec, dict) else type(rec).__name__}",
            })
            continue

    log_event(
        logger, "import.completed",
        sessions_created=summary["sessions_created"],
        sessions_matched=summary["sessions_matched"],
        messages_created=summary["messages_created"],
        messages_skipped=summary["messages_skipped_duplicate"],
        errors=len(summary["errors"]),
    )
    return summary


def import_document(
    payload: Mapping[str, Any],
    *,
    source_id: str = IMPORT_SOURCE_DEFAULT,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Import a single ``stream_json_envelope`` document.

    The payload's ``sessions`` array is iterated and each envelope is
    imported with the same guarantees as :func:`import_jsonl`.
    """
    summary = _new_summary()
    if not isinstance(payload, Mapping):
        summary["errors"].append({"error": "payload is not a JSON object"})
        return summary
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        summary["errors"].append({"error": "missing or malformed 'sessions' array"})
        return summary
    for env in sessions:
        if isinstance(env, Mapping):
            _import_envelope(env, source_id=source_id, db_path=db_path, summary=summary)
        else:
            summary["errors"].append({"error": "envelope is not a JSON object"})
    log_event(
        logger, "import.completed",
        sessions_created=summary["sessions_created"],
        sessions_matched=summary["sessions_matched"],
        messages_created=summary["messages_created"],
        messages_skipped=summary["messages_skipped_duplicate"],
        errors=len(summary["errors"]),
    )
    return summary


# ---------------------------------------------------------------------------
# Envelope path
# ---------------------------------------------------------------------------

def _import_envelope(
    envelope: Mapping[str, Any],
    *,
    source_id: str,
    db_path: Optional[Path],
    summary: dict[str, Any],
) -> None:
    sess = envelope.get("session") or {}
    messages = envelope.get("messages") or []
    if not isinstance(sess, Mapping) or not isinstance(messages, list):
        summary["errors"].append({"error": "malformed envelope"})
        return

    session_id = str(sess.get("id") or "")
    if not session_id:
        summary["errors"].append({"error": "envelope session missing id"})
        return

    created = _ensure_session(
        session_id=session_id,
        source=_merge_source(sess, source_id),
        db_path=db_path,
    )
    if created == "error":
        summary["errors"].append({"session_id": session_id[:8], "error": "session upsert failed"})
        return
    summary["sessions_created" if created == "created" else "sessions_matched"] += 1

    # OI attribute cache (session-level)
    oi_json = envelope.get("openinference_attributes")
    if isinstance(oi_json, Mapping):
        try:
            update_session_oi_attributes(
                session_id,
                json.dumps(oi_json, ensure_ascii=False, sort_keys=True),
                db_path=db_path,
            )
        except Exception:
            pass

    # Existing message dedup index
    existing_hashes = _existing_hash_set(session_id, db_path=db_path)

    for raw_msg in messages:
        if not isinstance(raw_msg, Mapping):
            summary["errors"].append({"error": "message not a JSON object"})
            continue
        _insert_imported_message(
            session_id=session_id,
            message=raw_msg,
            existing_hashes=existing_hashes,
            db_path=db_path,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# OI span path
# ---------------------------------------------------------------------------

def _import_oi_span(
    span: Mapping[str, Any],
    *,
    source_id: str,
    db_path: Optional[Path],
    summary: dict[str, Any],
) -> None:
    attrs = span.get("attributes") or {}
    if not isinstance(attrs, Mapping):
        summary["errors"].append({"error": "span attributes not a JSON object"})
        return
    session_id = str(attrs.get("session.id") or "")
    if not session_id:
        summary["errors"].append({"error": "span missing session.id attribute"})
        return

    provider = attrs.get("llm.provider")
    model = attrs.get("llm.model_name")

    created = _ensure_session(
        session_id=session_id,
        source={
            "id": session_id,
            "provider": provider,
            "model_names": model,
            "source_id": source_id,
            "started_at": _span_start_ts(span),
            "tool_family": "imported",
            "created_via": "import",
        },
        db_path=db_path,
    )
    if created == "error":
        summary["errors"].append({"session_id": session_id[:8], "error": "session upsert failed"})
        return
    summary["sessions_created" if created == "created" else "sessions_matched"] += 1

    # Rebuild messages from indexed OI attributes
    imported_msgs = _messages_from_oi_attrs(attrs)
    if not imported_msgs:
        summary["errors"].append({"error": "span has no input/output messages"})
        return

    pair_id = _pair_id_from_attrs(attrs) or _span_pair_id(span)
    existing_hashes = _existing_hash_set(session_id, db_path=db_path)
    ts_default = _span_start_ts(span) or time.time()
    model_name = str(model) if model else None

    prompt_tokens = attrs.get("llm.token_count.prompt")
    completion_tokens = attrs.get("llm.token_count.completion")

    for m in imported_msgs:
        m.setdefault("capture_pair_id", pair_id)
        m.setdefault("model_name", model_name if m.get("role") == "assistant" else None)
        m.setdefault("ts", ts_default)
        if m.get("role") == "assistant":
            m.setdefault("oi_output_tokens", completion_tokens)
        else:
            m.setdefault("oi_input_tokens", prompt_tokens)
        _insert_imported_message(
            session_id=session_id,
            message=m,
            existing_hashes=existing_hashes,
            db_path=db_path,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _ensure_session(
    *,
    session_id: str,
    source: Mapping[str, Any],
    db_path: Optional[Path],
) -> str:
    """Insert (with explicit id) or detect existing session.

    Returns 'created' | 'matched' | 'error'.
    """
    try:
        conn = get_connection(db_path)
        try:
            existing = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,),
            ).fetchone()
            if existing:
                return "matched"

            # Make sure the referenced source_id exists (FK). Use the
            # configured import source fallback.
            src_id = str(source.get("source_id") or IMPORT_SOURCE_DEFAULT)
            conn.execute(
                "INSERT OR IGNORE INTO sources (id, source_type, tool_name, install_mode, notes) "
                "VALUES (?, 'import', 'pce-import', 'light', 'auto-created for data import')",
                (src_id,),
            )

            started = float(source.get("started_at") or time.time())
            conn.execute(
                """
                INSERT INTO sessions
                    (id, source_id, started_at, ended_at, provider, tool_family,
                     session_key, message_count, title_hint, created_via,
                     oi_attributes_json, oi_schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, 1)
                """,
                (
                    session_id,
                    src_id,
                    started,
                    source.get("ended_at"),
                    source.get("provider"),
                    source.get("tool_family"),
                    source.get("session_key"),
                    source.get("title_hint"),
                    source.get("created_via") or "import",
                ),
            )
            conn.commit()
            return "created"
        finally:
            conn.close()
    except sqlite3.IntegrityError as exc:
        logger.warning("session upsert integrity error: %s", exc)
        return "error"
    except Exception:
        logger.exception("session upsert failed")
        return "error"


def _existing_hash_set(
    session_id: str,
    *,
    db_path: Optional[Path],
) -> set[str]:
    try:
        msgs = query_messages(session_id, db_path=db_path)
    except Exception:
        return set()
    return {
        message_hash(m.get("role") or "", m.get("content_text")) for m in msgs
    }


def _insert_imported_message(
    *,
    session_id: str,
    message: Mapping[str, Any],
    existing_hashes: set[str],
    db_path: Optional[Path],
    summary: dict[str, Any],
) -> None:
    role = str(message.get("role") or "user")
    content_text = message.get("content_text")
    h = message_hash(role, content_text)
    if h in existing_hashes:
        summary["messages_skipped_duplicate"] += 1
        return
    existing_hashes.add(h)

    ts = float(message.get("ts") or time.time())
    try:
        conn = get_connection(db_path)
        try:
            import uuid
            new_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, capture_pair_id, ts, role,
                     content_text, content_json, model_name, token_estimate,
                     oi_role_raw, oi_input_tokens, oi_output_tokens,
                     oi_attributes_json, oi_schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 1))
                """,
                (
                    new_id,
                    session_id,
                    message.get("capture_pair_id"),
                    ts,
                    role,
                    content_text,
                    _as_json_str(message.get("content_json")),
                    message.get("model_name"),
                    message.get("token_estimate"),
                    message.get("oi_role_raw") or role,
                    _as_int(message.get("oi_input_tokens")),
                    _as_int(message.get("oi_output_tokens")),
                    _as_json_str(message.get("oi_attributes_json")),
                    message.get("oi_schema_version"),
                ),
            )
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1, "
                "ended_at = COALESCE(MAX(ended_at, ?), ?) WHERE id = ?",
                (ts, ts, session_id),
            )
            conn.commit()
            summary["messages_created"] += 1
        finally:
            conn.close()
    except Exception as exc:
        summary["errors"].append({
            "session_id": session_id[:8],
            "error": f"message insert: {type(exc).__name__}: {exc}",
        })


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _messages_from_oi_attrs(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct message list from OI indexed attributes.

    Reads ``llm.input_messages.{i}.message.*`` and ``llm.output_messages.{i}.*``
    Returns in [input..., output...] order (both sorted by index).
    """
    inputs = _collect_indexed(attrs, "llm.input_messages")
    outputs = _collect_indexed(attrs, "llm.output_messages")
    return [*inputs, *outputs]


def _collect_indexed(attrs: Mapping[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Group attrs like ``<prefix>.{i}.message.role`` into ordered list of dicts."""
    buckets: dict[int, dict[str, Any]] = {}
    for key, val in attrs.items():
        if not isinstance(key, str) or not key.startswith(prefix + "."):
            continue
        remainder = key[len(prefix) + 1:]
        try:
            i_str, sub = remainder.split(".", 1)
            idx = int(i_str)
        except (ValueError, IndexError):
            continue
        bucket = buckets.setdefault(idx, {})
        if sub == "message.role":
            bucket["role"] = val
        elif sub == "message.content":
            bucket["content_text"] = val
        elif sub.startswith("message.contents."):
            # Re-assemble attachments as a generic list
            bucket.setdefault("_parts", []).append({"key": sub, "value": val})
    out: list[dict[str, Any]] = []
    for idx in sorted(buckets):
        b = buckets[idx]
        parts = b.pop("_parts", None)
        if parts:
            b["content_json"] = json.dumps(
                {"attachments": _group_parts(parts)},
                ensure_ascii=False,
            )
        out.append(b)
    return out


def _group_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-aggregate indexed message_content.* pairs back into attachments."""
    by_index: dict[int, dict[str, Any]] = {}
    for p in parts:
        key = p["key"]  # e.g. "message.contents.0.message_content.type"
        try:
            _, _, idx_str, *rest = key.split(".")
            idx = int(idx_str)
        except ValueError:
            continue
        sub = ".".join(rest)
        by_index.setdefault(idx, {})[sub] = p["value"]
    aggregated: list[dict[str, Any]] = []
    for idx in sorted(by_index):
        raw = by_index[idx]
        kind = raw.get("message_content.type", "unknown")
        if kind == "image":
            aggregated.append({
                "type": "image_url",
                "url": raw.get("message_content.image.image.url", ""),
            })
        elif kind == "text":
            aggregated.append({"type": "text", "text": raw.get("message_content.text", "")})
        else:
            aggregated.append({
                "type": kind,
                "raw": raw.get("message_content.metadata", ""),
            })
    return aggregated


def _span_start_ts(span: Mapping[str, Any]) -> float:
    start_ns = span.get("start_time_ns")
    if isinstance(start_ns, (int, float)) and start_ns > 0:
        return float(start_ns) / 1_000_000_000.0
    return 0.0


def _span_pair_id(span: Mapping[str, Any]) -> Optional[str]:
    """Fallback: use the hex span_id as a synthetic pair_id."""
    sid = span.get("span_id_hex")
    if isinstance(sid, str) and sid:
        return f"imported:{sid}"
    return None


def _pair_id_from_attrs(attrs: Mapping[str, Any]) -> Optional[str]:
    meta_str = attrs.get("metadata")
    if not isinstance(meta_str, str):
        return None
    try:
        meta = json.loads(meta_str)
    except (json.JSONDecodeError, TypeError):
        return None
    v = meta.get("pce.pair_id")
    return str(v) if v else None


def _as_json_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return None


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _new_summary() -> dict[str, Any]:
    return {
        "sessions_created": 0,
        "sessions_matched": 0,
        "messages_created": 0,
        "messages_skipped_duplicate": 0,
        "errors": [],
    }


def _merge_source(sess: Mapping[str, Any], fallback: str) -> dict[str, Any]:
    """Build a source-dict for ``_ensure_session`` from an envelope session."""
    return {
        "id": sess.get("id"),
        "source_id": sess.get("source_id") or fallback,
        "provider": sess.get("provider"),
        "tool_family": sess.get("tool_family"),
        "session_key": sess.get("session_key"),
        "title_hint": sess.get("title_hint"),
        "created_via": sess.get("created_via") or "import",
        "started_at": sess.get("started_at"),
        "ended_at": sess.get("ended_at"),
        "model_names": sess.get("model_names"),
    }


__all__ = ["import_jsonl", "import_document"]
