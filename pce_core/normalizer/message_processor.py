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
    update_message_enrichment,
)
from ..rich_content import build_content_json, load_attachments_from_content_json
from .base import NormalizedMessage, NormalizedResult

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


def get_existing_message_hash_map(
    session_id: str,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    """Return a dict mapping dedup hash → msg info for a session.

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
                "SELECT id, role, content_text, content_json, model_name, token_estimate FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            for r in rows:
                h = message_hash(r["role"], r["content_text"])
                hash_map[h] = {
                    "id": r["id"],
                    "role": r["role"],
                    "content_text": r["content_text"],
                    "content_json": r["content_json"],
                    "model_name": r["model_name"],
                    "token_estimate": r["token_estimate"],
                }
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to fetch existing message hashes")
    return hash_map


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
        logger.error("Failed to create session for pair %s", pair_id)
        return None

    # Build dedup map from existing messages (only for existing sessions)
    existing_hash_map: dict[str, dict] = {}
    if is_existing:
        existing_hash_map = get_existing_message_hash_map(session_id, db_path=db_path)

    # Insert new messages or enrich existing ones (quality-aware via reconciler)
    msg_count = 0
    enriched_count = 0
    for msg in result.messages:
        msg_h = message_hash(msg.role, msg.content_text)

        if msg_h in existing_hash_map:
            # Message already exists — use reconciler quality scoring to
            # decide whether the incoming capture improves on the existing.
            existing = existing_hash_map[msg_h]
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

        existing_hash_map[msg_h] = {
            "id": "",
            "content_text": msg.content_text,
            "content_json": msg.content_json,
        }  # prevent within-batch dupes

        token_est = msg.token_estimate or estimate_tokens(msg.content_text)
        msg_id = insert_message(
            session_id=session_id,
            ts=msg.ts or created_at,
            role=msg.role,
            content_text=msg.content_text,
            content_json=msg.content_json,
            model_name=msg.model_name or result.model_name,
            capture_pair_id=pair_id,
            token_estimate=token_est,
            db_path=db_path,
        )
        if msg_id:
            msg_count += 1
            existing_hash_map[msg_h]["id"] = msg_id

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
    except Exception:
        logger.debug("Auto-tag failed for session %s (non-fatal)", session_id[:8])

    logger.info(
        "Normalized %s -> session %s (%d new, %d enriched, provider=%s, existing=%s)",
        pair_id[:8], session_id[:8], msg_count, enriched_count, result.provider, is_existing,
    )
    return session_id
