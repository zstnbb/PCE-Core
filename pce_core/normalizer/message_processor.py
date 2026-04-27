# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Message processing for the normalization pipeline.

Handles message hashing, deduplication, enrichment, content merging,
attachment management, and persistence to the database.
Extracted from pipeline.py for single-responsibility decomposition.
"""

import json
import logging
import re
from typing import Optional
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..db import (
    insert_message,
    get_connection,
    query_messages,
    query_sessions,
    record_pipeline_error,
    update_message_enrichment,
    update_session_oi_attributes,
)
from ..logging_config import log_event
from ..rich_content import build_content_json, load_attachments_from_content_json
from .base import NormalizedMessage, NormalizedResult
from .openinference_mapper import (
    message_to_oi_attributes,
    pair_to_oi_span,
    session_to_oi_attributes,
)

logger = logging.getLogger("pce.normalizer.message_processor")

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

ATTACHMENT_MARKER_RE = re.compile(
    r"^\[(?:image|file(?::[^\]]+)?|audio|document(?::[^\]]+)?|tool(?:\s+call)?(?::[^\]]+)?|citation|code(?:\s+output)?)\]$",
    re.IGNORECASE,
)
FILE_UPLOAD_RE = re.compile(
    r'^.+\.(?:pdf|docx?|txt|md|py|js|ts|csv|json|xml|html?|xlsx?|pptx?|zip|rar|'
    r'png|jpe?g|gif|svg|mp[34]|wav|webp|heic|c|cpp|h|rb|go|rs|java|kt|swift|sh|'
    r'bat|ps1|r|sql|ya?ml|toml|ini|cfg|log|ipynb)\s*$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: Optional[str]) -> Optional[int]:
    """Estimate token count from text using a simple heuristic.

    - English/Latin: ~1.3 tokens per whitespace-separated word
    - CJK characters: ~1.5 tokens per character
    Returns None if text is empty.
    """
    if not text:
        return None
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
              or '\u3040' <= c <= '\u30ff'
              or '\uac00' <= c <= '\ud7af')
    words = len(text.split())
    # CJK chars are roughly 1.5 tokens each; Latin words ~1.3 tokens
    estimate = int(cjk * 1.5 + (words - cjk) * 1.3)
    return max(estimate, 1) if text.strip() else None


# ---------------------------------------------------------------------------
# Message hashing & deduplication
# ---------------------------------------------------------------------------

def message_hash(role: str, content_text: Optional[str]) -> str:
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
    text = normalize_message_text_for_dedup(text)

    return f"{role}:{text[:200]}"


def normalize_message_text_for_dedup(text: str) -> str:
    """Collapse attachment-only prefixes so DOM and network copies hash alike."""
    if not text:
        return ""

    lines = [line.strip() for line in text.strip().split("\n")]
    while len(lines) > 1 and lines[0] and ATTACHMENT_MARKER_RE.match(lines[0]):
        lines.pop(0)
        while lines and not lines[0]:
            lines.pop(0)

    if len(lines) >= 2 and FILE_UPLOAD_RE.match(lines[0]):
        rest_start = 1
        if len(lines) > 1 and len(lines[1]) <= 30:
            rest_start = 2
        lines = lines[rest_start:]

    return "\n".join(lines).strip()


def get_existing_message_hash_buckets(
    session_id: str,
    db_path: Optional[Path] = None,
) -> dict[str, list[dict]]:
    """Return ``{dedup_hash → [msg_info, ...]}`` for a session, ts-ordered.

    Multiple messages can share the same dedup hash legitimately when the
    user repeats themselves across turns ("go on", "yes", "tell me more")
    or when the same prompt is sent on different branches. The bucket list
    preserves chronological order so the persist loop can match incoming
    messages positionally (incoming[N] ↔ existing[N]) instead of
    collapsing them onto the first hit. Closes **N10** (multi-turn /
    branch user msg dropped).
    """
    buckets: dict[str, list[dict]] = {}
    try:
        conn = get_connection(db_path)
        conn.row_factory = __import__("sqlite3").Row
        try:
            rows = conn.execute(
                "SELECT id, role, content_text, content_json, model_name, token_estimate, ts "
                "FROM messages WHERE session_id = ? ORDER BY ts ASC, id ASC",
                (session_id,),
            ).fetchall()
            for r in rows:
                h = message_hash(r["role"], r["content_text"])
                buckets.setdefault(h, []).append({
                    "id": r["id"],
                    "role": r["role"],
                    "content_text": r["content_text"],
                    "content_json": r["content_json"],
                    "model_name": r["model_name"],
                    "token_estimate": r["token_estimate"],
                })
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to fetch existing message hash buckets")
    return buckets


# Backwards-compat shim. Prior callers got a flat ``dict[hash → msg_info]``
# which silently dropped duplicates. Returning ``buckets[h][0]`` preserves
# that shape but consumers that need full-fidelity dedup must migrate to
# :func:`get_existing_message_hash_buckets` directly.
def get_existing_message_hash_map(
    session_id: str,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    buckets = get_existing_message_hash_buckets(session_id, db_path=db_path)
    return {h: lst[0] for h, lst in buckets.items() if lst}


# ---------------------------------------------------------------------------
# Attachment / content_json merging
# ---------------------------------------------------------------------------

def load_attachments(content_json: Optional[str]) -> list[dict]:
    return load_attachments_from_content_json(content_json)


def extract_attachment_file_id(att: dict) -> str:
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


def attachment_key(att: dict) -> str:
    if not isinstance(att, dict):
        return json.dumps(att, ensure_ascii=False, sort_keys=True)

    att_type = att.get("type", "")
    file_id = extract_attachment_file_id(att)
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


def merge_attachment(existing: dict, incoming: dict) -> dict:
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


def merge_content_json(existing_json: Optional[str], incoming_json: Optional[str]) -> Optional[str]:
    if not existing_json:
        return incoming_json
    if not incoming_json:
        return existing_json

    merged: dict[str, dict] = {}
    order: list[str] = []
    for source_json in (existing_json, incoming_json):
        for att in load_attachments(source_json):
            if not isinstance(att, dict):
                continue
            key = attachment_key(att)
            if key not in merged:
                merged[key] = dict(att)
                order.append(key)
            else:
                merged[key] = merge_attachment(merged[key], att)

    if not order:
        return existing_json or incoming_json
    return build_content_json([merged[key] for key in order])


def choose_better_content_text(existing_text: Optional[str], incoming_text: Optional[str]) -> Optional[str]:
    if not existing_text:
        return incoming_text
    if not incoming_text:
        return existing_text

    existing_norm = normalize_message_text_for_dedup(existing_text)
    incoming_norm = normalize_message_text_for_dedup(incoming_text)
    if existing_norm != incoming_norm:
        return incoming_text if len(incoming_text.strip()) > len(existing_text.strip()) else existing_text

    if incoming_text.strip() == incoming_norm and existing_text.strip() != existing_norm:
        return incoming_norm
    if existing_text.strip() == existing_norm:
        return existing_text
    return incoming_norm or existing_text


# ---------------------------------------------------------------------------
# Persist result to DB
# ---------------------------------------------------------------------------

def persist_result(
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
    from .session_manager import find_or_create_session, upgrade_session_metadata
    from .reconciler import score_message, merge_messages as reconciler_merge

    # Find or create session
    session_id, is_existing = find_or_create_session(
        session_key=result.session_key,
        source_id=source_id,
        started_at=created_at,
        provider=result.provider,
        tool_family=result.tool_family,
        title_hint=result.title_hint,
        created_via=created_via,
        db_path=db_path,
    )

    if session_id is None:
        record_pipeline_error(
            "session_resolve",
            "find_or_create_session returned None",
            source_id=source_id, pair_id=pair_id,
            details={
                "provider": result.provider,
                "tool_family": result.tool_family,
                "created_via": created_via,
            },
        )
        log_event(
            logger, "session.resolve_failed",
            level=logging.ERROR,
            pair_id=pair_id[:8], provider=result.provider,
        )
        return None

    # Build dedup buckets from existing messages (only for existing sessions).
    # Each bucket holds chronologically-ordered duplicates of the same
    # ``message_hash`` so we can position-match instead of collapsing them.
    existing_buckets: dict[str, list[dict]] = {}
    if is_existing:
        existing_buckets = get_existing_message_hash_buckets(session_id, db_path=db_path)

    # Cursor per dedup hash — incoming[N] for hash H matches existing
    # bucket[H][N]. Once we run past the bucket length, subsequent
    # identical-hash incoming messages insert as fresh rows. This is the
    # core N10 fix: legitimate repeats survive instead of getting eaten
    # by an over-eager dedup.
    incoming_cursor: dict[str, int] = {}

    msg_count = 0
    enriched_count = 0
    for msg in result.messages:
        msg_h = message_hash(msg.role, msg.content_text)
        cursor = incoming_cursor.get(msg_h, 0)
        bucket = existing_buckets.get(msg_h, [])

        if cursor < len(bucket):
            # Position-matched: enrich the corresponding existing message.
            existing = bucket[cursor]
            incoming_cursor[msg_h] = cursor + 1

            existing_msg = NormalizedMessage(
                role=existing.get("role", msg.role),
                content_text=existing.get("content_text"),
                content_json=existing.get("content_json"),
                model_name=existing.get("model_name"),
                token_estimate=existing.get("token_estimate"),
            )
            eq = score_message(existing_msg)
            iq = score_message(msg)

            # Merge if incoming is higher quality OR has new attachments
            if iq.score > eq.score or (iq.has_attachments and not eq.has_attachments):
                merged = reconciler_merge(
                    msg if iq.score >= eq.score else existing_msg,
                    existing_msg if iq.score >= eq.score else msg,
                )
                new_text = merged.content_text
                new_json = merged.content_json
                new_model = merged.model_name
                new_tokens = merged.token_estimate
            else:
                # Existing is better — still merge content_json for attachments
                new_json = merge_content_json(
                    existing.get("content_json"),
                    msg.content_json,
                )
                new_text = choose_better_content_text(
                    existing.get("content_text"),
                    msg.content_text,
                )
                new_model = existing.get("model_name") or msg.model_name
                new_tokens = existing.get("token_estimate") or msg.token_estimate

            updates_needed = (
                new_json != existing.get("content_json")
                or new_text != existing.get("content_text")
                or (new_model and new_model != existing.get("model_name"))
                or (new_tokens and new_tokens != existing.get("token_estimate"))
            )
            if updates_needed and existing.get("id"):
                if update_message_enrichment(
                    existing["id"],
                    content_text=new_text if new_text != existing.get("content_text") else None,
                    content_json=new_json if new_json != existing.get("content_json") else None,
                    model_name=new_model if new_model and new_model != existing.get("model_name") else None,
                    token_estimate=new_tokens if new_tokens and new_tokens != existing.get("token_estimate") else None,
                    db_path=db_path,
                ):
                    enriched_count += 1
                    existing["content_text"] = new_text
                    existing["content_json"] = new_json
                    if new_model:
                        existing["model_name"] = new_model
                    if new_tokens:
                        existing["token_estimate"] = new_tokens
            continue

        # No matching existing message at this position — INSERT as new.
        # Advance the cursor so a *later* incoming msg with the same hash
        # within this same batch maps to a different position.
        incoming_cursor[msg_h] = cursor + 1

        token_est = msg.token_estimate or estimate_tokens(msg.content_text)
        oi_fields = _compute_message_oi_fields(
            msg=msg,
            session_id=session_id,
            result=result,
            source_id=source_id,
            pair_id=pair_id,
            token_est=token_est,
        )
        msg_id = insert_message(
            session_id=session_id,
            ts=msg.ts or created_at,
            role=msg.role,
            content_text=msg.content_text,
            content_json=msg.content_json,
            model_name=msg.model_name or result.model_name,
            capture_pair_id=pair_id,
            token_estimate=token_est,
            oi_role_raw=oi_fields["oi_role_raw"],
            oi_input_tokens=oi_fields["oi_input_tokens"],
            oi_output_tokens=oi_fields["oi_output_tokens"],
            oi_attributes_json=oi_fields["oi_attributes_json"],
            db_path=db_path,
        )
        if msg_id:
            msg_count += 1
            # Add a synthetic bucket entry so we don't try to enrich this
            # row from a later incoming msg that hashes to the same key.
            existing_buckets.setdefault(msg_h, []).append({
                "id": msg_id,
                "role": msg.role,
                "content_text": msg.content_text,
                "content_json": msg.content_json,
                "model_name": msg.model_name or result.model_name,
                "token_estimate": token_est,
            })

    # Upgrade metadata on existing sessions when a better capture arrives later.
    if is_existing:
        upgrade_session_metadata(
            session_id,
            provider=result.provider,
            tool_family=result.tool_family,
            title=result.title_hint,
            created_via=created_via,
            db_path=db_path,
        )

    # Auto-tag session metadata (language, topics, tokens, models)
    try:
        from ..tagger import tag_session
        tag_session(session_id, db_path=db_path)
    except Exception as exc:
        logger.debug("Auto-tag failed for session %s (non-fatal)", session_id[:8])
        record_pipeline_error(
            "tag", f"tag_session: {type(exc).__name__}: {exc}",
            level="WARNING", source_id=source_id, pair_id=pair_id,
            details={"session_id": session_id},
        )

    # Refresh session-level OpenInference attribute cache (cheap; the
    # session_to_oi_attributes mapper is pure Python on ~10 small fields).
    _refresh_session_oi_cache(
        session_id=session_id,
        source_id=source_id,
        result=result,
        created_via=created_via,
        db_path=db_path,
    )

    # Surface provider-specific session metadata (e.g. Claude Writing
    # Style, file_uuid manifest) into the same OI attribute JSON under a
    # ``pce.layer_meta`` namespace. No schema change required — the column
    # is plain TEXT/JSON. Closes fu_recon_join items 2/3/5 by making
    # ``personalized_styles`` and the unified ``files`` manifest visible
    # at the session level.
    if getattr(result, "layer_meta", None):
        _merge_layer_meta_into_session(
            session_id=session_id,
            layer_meta=result.layer_meta or {},
            db_path=db_path,
        )

    # Secondary OTLP channel (ADR-007). Fails-open if exporter is off or
    # anything throws. Only emits for pairs that produced at least one
    # newly-inserted message — enrichment-only runs don't spam Phoenix.
    if msg_count > 0:
        _emit_pair_otlp_span(
            pair_id=pair_id,
            session_id=session_id,
            source_id=source_id,
            result=result,
            created_via=created_via,
            db_path=db_path,
        )

    log_event(
        logger, "normalize.persisted",
        pair_id=pair_id[:8], session_id=session_id[:8],
        new_msgs=msg_count, enriched=enriched_count,
        provider=result.provider,
        is_existing_session=is_existing,
        normalizer=getattr(result, "normalizer_name", None),
        confidence=getattr(result, "confidence", None),
    )
    return session_id


# ---------------------------------------------------------------------------
# OpenInference helpers (P1)
# ---------------------------------------------------------------------------

def _compute_message_oi_fields(
    *,
    msg: NormalizedMessage,
    session_id: str,
    result: NormalizedResult,
    source_id: str,
    pair_id: str,
    token_est: Optional[int],
) -> dict:
    """Build the four OI fields written alongside each message.

    Shape: ``{oi_role_raw, oi_input_tokens, oi_output_tokens, oi_attributes_json}``.
    Failures are swallowed; the message still gets written without the
    cache, and the export layer can backfill later.
    """
    try:
        role_raw = str(msg.role or "")
        # Token allocation convention: assistant → completion, everyone else → prompt
        if (msg.role or "").lower() == "assistant":
            oi_in: Optional[int] = None
            oi_out: Optional[int] = token_est
        else:
            oi_in = token_est
            oi_out = None

        synthetic_session = {
            "id": session_id,
            "provider": result.provider,
            "tool_family": result.tool_family,
            "model_names": result.model_name,
            "source_id": source_id,
        }
        synthetic_message = {
            "role": msg.role,
            "content_text": msg.content_text,
            "content_json": msg.content_json,
            "model_name": msg.model_name or result.model_name,
            "token_estimate": token_est,
            "capture_pair_id": pair_id,
            "oi_input_tokens": oi_in,
            "oi_output_tokens": oi_out,
        }
        attrs = message_to_oi_attributes(synthetic_message, synthetic_session)
        return {
            "oi_role_raw": role_raw or None,
            "oi_input_tokens": oi_in,
            "oi_output_tokens": oi_out,
            "oi_attributes_json": json.dumps(attrs, ensure_ascii=False, sort_keys=True),
        }
    except Exception as exc:
        logger.debug("OI mapper failed for message: %s", exc)
        return {
            "oi_role_raw": str(msg.role or "") or None,
            "oi_input_tokens": None,
            "oi_output_tokens": None,
            "oi_attributes_json": None,
        }


def _merge_layer_meta_into_session(
    *,
    session_id: str,
    layer_meta: dict,
    db_path: Optional[Path],
) -> None:
    """Merge ``layer_meta`` into ``sessions.oi_attributes_json``.

    Stored under the ``pce.layer_meta.<key>`` flat-attribute namespace so
    it round-trips through the OpenInference exporter without polluting
    canonical OI fields.  Existing keys are union-merged so newer captures
    don't clobber an already-recorded style or file manifest.
    """
    if not layer_meta:
        return
    try:
        conn = get_connection(db_path)
        try:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT oi_attributes_json FROM sessions WHERE id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            current_json = row["oi_attributes_json"] if row else None
        finally:
            conn.close()

        try:
            attrs = json.loads(current_json) if current_json else {}
        except (json.JSONDecodeError, TypeError):
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = {}

        existing_meta = attrs.get("pce.layer_meta")
        if not isinstance(existing_meta, dict):
            existing_meta = {}

        for key, value in layer_meta.items():
            if value in (None, "", [], {}):
                continue
            # Deep-merge lists by appending unique entries (file manifest
            # accumulates across captures) and shallow-overwrite scalars
            # / dicts (newer style wins).
            if isinstance(value, list) and isinstance(existing_meta.get(key), list):
                merged_list = list(existing_meta[key])
                seen = {
                    json.dumps(x, sort_keys=True, ensure_ascii=False)
                    for x in merged_list if isinstance(x, dict)
                }
                for entry in value:
                    if isinstance(entry, dict):
                        sig = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                        if sig in seen:
                            continue
                        seen.add(sig)
                    merged_list.append(entry)
                existing_meta[key] = merged_list
            else:
                existing_meta[key] = value

        attrs["pce.layer_meta"] = existing_meta

        update_session_oi_attributes(
            session_id,
            json.dumps(attrs, ensure_ascii=False, sort_keys=True),
            db_path=db_path,
        )
    except Exception as exc:
        logger.debug("layer_meta merge failed for session %s: %s", session_id[:8], exc)


def _refresh_session_oi_cache(
    *,
    session_id: str,
    source_id: str,
    result: NormalizedResult,
    created_via: str,
    db_path: Optional[Path],
) -> None:
    try:
        # Try to pull the real row so tagger-populated fields (language,
        # topic_tags, total_tokens, model_names) are reflected.
        rows = query_sessions(last=1, db_path=db_path) if False else []
        # query_sessions has no id filter; use get_connection directly.
        conn = get_connection(db_path)
        try:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ? LIMIT 1", (session_id,),
            ).fetchone()
            session_dict = dict(row) if row else {}
        finally:
            conn.close()

        if not session_dict:
            session_dict = {
                "id": session_id,
                "source_id": source_id,
                "provider": result.provider,
                "tool_family": result.tool_family,
                "title_hint": result.title_hint,
                "created_via": created_via,
                "session_key": result.session_key,
                "model_names": result.model_name,
            }

        # Preserve any sticky non-OI namespaces (e.g. ``pce.layer_meta``)
        # that prior captures wrote into the attribute blob. The OI mapper
        # only emits canonical OI fields, so without this carry-over the
        # ``pce.*`` extensions get clobbered on every cache refresh.
        prior_attrs: dict = {}
        try:
            prior_json = session_dict.get("oi_attributes_json")
            if prior_json:
                parsed = json.loads(prior_json)
                if isinstance(parsed, dict):
                    prior_attrs = parsed
        except (json.JSONDecodeError, TypeError):
            prior_attrs = {}

        attrs = session_to_oi_attributes(session_dict)
        for key, value in prior_attrs.items():
            if key.startswith("pce.") and key not in attrs:
                attrs[key] = value

        update_session_oi_attributes(
            session_id,
            json.dumps(attrs, ensure_ascii=False, sort_keys=True),
            db_path=db_path,
        )
    except Exception as exc:
        logger.debug("session OI cache refresh failed: %s", exc)


def _emit_pair_otlp_span(
    *,
    pair_id: str,
    session_id: str,
    source_id: str,
    result: NormalizedResult,
    created_via: str,
    db_path: Optional[Path],
) -> None:
    """Emit one OpenInference-shaped span for this capture pair.

    Fails open – never raises back to ``persist_result``.
    """
    try:
        from ..db import query_messages_by_pair, query_by_pair
        from ..otel_exporter import emit_pair_span, is_enabled

        if not is_enabled():
            return  # cheap short-circuit: no OTLP target configured

        pair_messages = query_messages_by_pair(pair_id, db_path=db_path) or []
        if not pair_messages:
            return

        # Reuse the cached session attributes if present
        conn = get_connection(db_path)
        try:
            conn.row_factory = __import__("sqlite3").Row
            sess_row = conn.execute(
                "SELECT * FROM sessions WHERE id = ? LIMIT 1", (session_id,),
            ).fetchone()
            session_dict = dict(sess_row) if sess_row else {
                "id": session_id,
                "provider": result.provider,
                "tool_family": result.tool_family,
            }
        finally:
            conn.close()

        raw_rows = query_by_pair(pair_id, db_path=db_path) or []
        raw_request = next((r for r in raw_rows if r.get("direction") == "request"), None)
        raw_response = next((r for r in raw_rows if r.get("direction") == "response"), None)

        span = pair_to_oi_span(
            pair_id=pair_id,
            session=session_dict,
            messages=pair_messages,
            raw_request=raw_request,
            raw_response=raw_response,
        )
        emitted = emit_pair_span(span)
        if emitted:
            log_event(
                logger, "otel.pair_emitted",
                pair_id=pair_id[:8], session_id=session_id[:8],
                provider=result.provider,
            )
    except Exception as exc:
        logger.debug("OTLP span emit failed for pair=%s: %s", pair_id[:8], exc)
        record_pipeline_error(
            "otel_emit",
            f"{type(exc).__name__}: {exc}",
            level="WARNING", source_id=source_id, pair_id=pair_id,
            details={"session_id": session_id},
            db_path=db_path,
        )
