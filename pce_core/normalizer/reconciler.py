# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Capture Reconciler.

Merges L2 (DOM-extracted) and L3 (network-intercepted) captures that cover
the same conversation, picking the best content from each source.

Quality scoring heuristics per message:
- Network captures provide: model name, token counts, tool_call IDs,
  structured JSON, exact API payloads.
- DOM captures provide: visible rendered text (including LaTeX/markdown
  rendered as plain text), UI-extracted attachments (images, files),
  thinking blocks, streaming-safe final content.

The reconciler runs *after* normalization produces NormalizedResult objects
from each channel.  It merges at the message level: for each (role, content)
pair that appears in both sources, it picks the higher-quality version and
merges metadata from the other.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from .base import NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.reconciler")


# ---------------------------------------------------------------------------
# Message quality scoring
# ---------------------------------------------------------------------------

@dataclass
class MessageQuality:
    """Quality assessment of a single normalized message."""
    score: float          # 0.0–1.0 overall quality
    has_model: bool       # model_name is set
    has_tokens: bool      # token_estimate is set (from API usage)
    has_attachments: bool # content_json has attachments
    content_len: int      # length of content_text
    has_tool_calls: bool  # contains [Tool call: ...] markers
    has_thinking: bool    # contains <thinking> blocks
    is_clean_text: bool   # text doesn't look like raw JSON


def score_message(msg: NormalizedMessage) -> MessageQuality:
    """Score the quality of a single normalized message."""
    text = msg.content_text or ""
    content_len = len(text.strip())

    has_model = bool(msg.model_name)
    has_tokens = msg.token_estimate is not None and msg.token_estimate > 0
    has_tool_calls = "[Tool call:" in text
    has_thinking = "<thinking>" in text

    # Check for attachments in content_json
    has_attachments = False
    if msg.content_json:
        try:
            cj = json.loads(msg.content_json)
            atts = cj.get("attachments", [])
            has_attachments = isinstance(atts, list) and len(atts) > 0
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    # Clean text check: not raw JSON, not truncated platform artifacts
    is_clean = True
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        is_clean = False
    if stripped.startswith("[{") and stripped.endswith("}]"):
        is_clean = False
    if "...[truncated]" in stripped:
        is_clean = False

    # Compute overall score
    score = 0.0

    # Content length (longer = usually better, up to a point)
    if content_len > 0:
        score += min(content_len / 500, 0.25)  # max 0.25

    # Structural metadata
    if has_model:
        score += 0.15
    if has_tokens:
        score += 0.15
    if has_attachments:
        score += 0.10
    if has_tool_calls:
        score += 0.05
    if has_thinking:
        score += 0.05

    # Clean text bonus
    if is_clean and content_len > 10:
        score += 0.15

    # Penalty for very short content
    if content_len < 5:
        score *= 0.5

    score = min(score, 1.0)

    return MessageQuality(
        score=score,
        has_model=has_model,
        has_tokens=has_tokens,
        has_attachments=has_attachments,
        content_len=content_len,
        has_tool_calls=has_tool_calls,
        has_thinking=has_thinking,
        is_clean_text=is_clean,
    )


# ---------------------------------------------------------------------------
# Result-level quality scoring
# ---------------------------------------------------------------------------

@dataclass
class ResultQuality:
    """Quality assessment of a full NormalizedResult."""
    score: float
    msg_count: int
    has_both_roles: bool
    has_session_key: bool
    has_model: bool
    avg_msg_quality: float
    source_type: str       # "dom", "network", "api", "unknown"


def score_result(result: NormalizedResult) -> ResultQuality:
    """Score the overall quality of a NormalizedResult."""
    msgs = result.messages
    msg_count = len(msgs)

    roles = {m.role for m in msgs}
    has_both = "user" in roles and "assistant" in roles
    has_session_key = bool(result.session_key)
    has_model = bool(result.model_name)

    # Average message quality
    if msgs:
        msg_scores = [score_message(m).score for m in msgs]
        avg_msg_quality = sum(msg_scores) / len(msg_scores)
    else:
        avg_msg_quality = 0.0

    # Determine source type from tool_family
    tf = (result.tool_family or "").lower()
    if "web" in tf or "dom" in tf:
        source_type = "dom"
    elif "network" in tf:
        source_type = "network"
    elif "api" in tf:
        source_type = "api"
    else:
        source_type = "unknown"

    # Overall score
    score = avg_msg_quality * 0.50  # message quality dominates

    if has_both:
        score += 0.15
    if has_session_key:
        score += 0.10
    if has_model:
        score += 0.10
    if msg_count >= 4:
        score += 0.10
    elif msg_count >= 2:
        score += 0.05

    # Confidence from normalizer
    score = score * 0.7 + result.confidence * 0.3

    score = min(score, 1.0)

    return ResultQuality(
        score=score,
        msg_count=msg_count,
        has_both_roles=has_both,
        has_session_key=has_session_key,
        has_model=has_model,
        avg_msg_quality=avg_msg_quality,
        source_type=source_type,
    )


# ---------------------------------------------------------------------------
# Message-level merge
# ---------------------------------------------------------------------------

def _message_key(msg: NormalizedMessage) -> str:
    """Produce a merge key for matching messages across sources.

    Uses role + first 200 chars of normalized content (same logic as
    pipeline._message_hash but without the file-upload normalization
    since we're pre-persist).
    """
    text = msg.content_text or ""
    # Strip raw JSON wrappers for matching
    stripped = text.strip()
    if stripped.startswith("{") and "parts" in stripped[:50]:
        try:
            import ast
            parsed = ast.literal_eval(stripped)
            if isinstance(parsed, dict) and isinstance(parsed.get("parts"), list):
                parts = []
                for p in parsed["parts"]:
                    if isinstance(p, str) and p:
                        parts.append(p)
                    elif isinstance(p, dict):
                        if p.get("content_type") == "image_asset_pointer":
                            parts.append("[Image]")
                        elif isinstance(p.get("text"), str):
                            parts.append(p["text"])
                text = " ".join(parts).strip()
        except Exception:
            pass
    return f"{msg.role}:{text[:200]}"


def merge_messages(
    primary: NormalizedMessage,
    secondary: NormalizedMessage,
) -> NormalizedMessage:
    """Merge two messages covering the same content, picking best fields.

    The primary message is the higher-scored one; secondary fills gaps.
    """
    pq = score_message(primary)
    sq = score_message(secondary)

    # Pick the better content_text
    if pq.is_clean_text and not sq.is_clean_text:
        text = primary.content_text
    elif sq.is_clean_text and not pq.is_clean_text:
        text = secondary.content_text
    elif pq.content_len >= sq.content_len:
        text = primary.content_text
    else:
        text = secondary.content_text

    # Merge content_json: combine attachments from both
    content_json = _merge_content_json(primary.content_json, secondary.content_json)

    # Pick model_name: prefer explicit over None
    model = primary.model_name or secondary.model_name

    # Pick token_estimate: prefer API-provided (non-None)
    tokens = primary.token_estimate if primary.token_estimate else secondary.token_estimate

    # Timestamp: prefer earlier (more accurate)
    ts = primary.ts or secondary.ts
    if primary.ts and secondary.ts:
        ts = min(primary.ts, secondary.ts)

    return NormalizedMessage(
        role=primary.role,
        content_text=text,
        content_json=content_json,
        model_name=model,
        token_estimate=tokens,
        ts=ts,
    )


def _merge_content_json(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Merge two content_json strings, combining their attachment lists."""
    if not a:
        return b
    if not b:
        return a

    try:
        a_data = json.loads(a)
        b_data = json.loads(b)
    except (json.JSONDecodeError, TypeError):
        return a

    a_atts = a_data.get("attachments", []) if isinstance(a_data, dict) else []
    b_atts = b_data.get("attachments", []) if isinstance(b_data, dict) else []

    if not b_atts:
        return a
    if not a_atts:
        return b

    # Deduplicate by type + key fields
    seen: set[str] = set()
    merged: list[dict] = []
    for att in a_atts + b_atts:
        if not isinstance(att, dict):
            continue
        key = _att_dedup_key(att)
        if key in seen:
            continue
        seen.add(key)
        merged.append(att)

    return json.dumps({"attachments": merged}, ensure_ascii=False)


def _att_dedup_key(att: dict) -> str:
    """Create a dedup key for an attachment."""
    atype = att.get("type", "")
    if atype == "tool_call":
        return f"tool_call:{att.get('id', '')}:{att.get('name', '')}"
    if atype == "image_url":
        return f"image_url:{att.get('url', '')}{att.get('file_id', '')}"
    if atype == "file":
        return f"file:{att.get('name', '')}"
    if atype == "citation":
        return f"citation:{att.get('url', '')}"
    return json.dumps(att, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Result-level reconciliation
# ---------------------------------------------------------------------------

def reconcile(
    dom_result: Optional[NormalizedResult],
    network_result: Optional[NormalizedResult],
) -> Optional[NormalizedResult]:
    """Reconcile a DOM capture result with a network capture result.

    Returns a single merged NormalizedResult that combines the best
    content from both sources.  If only one source is available,
    returns it directly.

    Both results should cover the same conversation (same session_key
    or overlapping message content).
    """
    if dom_result is None and network_result is None:
        return None
    if dom_result is None:
        return network_result
    if network_result is None:
        return dom_result

    dom_q = score_result(dom_result)
    net_q = score_result(network_result)

    logger.debug(
        "Reconciling: DOM(score=%.2f, msgs=%d) vs Network(score=%.2f, msgs=%d)",
        dom_q.score, dom_q.msg_count, net_q.score, net_q.msg_count,
    )

    # Pick the primary (higher-scored) result as the base
    if net_q.score >= dom_q.score:
        primary, secondary = network_result, dom_result
        primary_q, secondary_q = net_q, dom_q
    else:
        primary, secondary = dom_result, network_result
        primary_q, secondary_q = dom_q, net_q

    # Index secondary messages by merge key
    sec_by_key: dict[str, NormalizedMessage] = {}
    for msg in secondary.messages:
        key = _message_key(msg)
        if key not in sec_by_key:
            sec_by_key[key] = msg

    # Merge messages
    merged_msgs: list[NormalizedMessage] = []
    used_sec_keys: set[str] = set()

    for msg in primary.messages:
        key = _message_key(msg)
        if key in sec_by_key:
            merged_msgs.append(merge_messages(msg, sec_by_key[key]))
            used_sec_keys.add(key)
        else:
            merged_msgs.append(msg)

    # Add any secondary messages not in primary (unique to that source)
    for msg in secondary.messages:
        key = _message_key(msg)
        if key not in used_sec_keys:
            merged_msgs.append(msg)

    # Build merged result using primary metadata
    merged_confidence = max(primary.confidence, secondary.confidence)
    # Boost confidence when both sources agree
    overlap_ratio = len(used_sec_keys) / max(len(primary.messages), 1)
    if overlap_ratio > 0.5:
        merged_confidence = min(merged_confidence + 0.10, 1.0)

    merged = NormalizedResult(
        provider=primary.provider if primary.provider != "unknown" else secondary.provider,
        tool_family=primary.tool_family,
        model_name=primary.model_name or secondary.model_name,
        session_key=primary.session_key or secondary.session_key,
        title_hint=primary.title_hint or secondary.title_hint,
        messages=merged_msgs,
        confidence=merged_confidence,
        normalizer_name=f"Reconciler({primary_q.source_type}+{secondary_q.source_type})",
    )

    logger.info(
        "Reconciled: %d primary + %d secondary → %d merged msgs "
        "(overlap=%d, confidence=%.2f)",
        len(primary.messages), len(secondary.messages),
        len(merged_msgs), len(used_sec_keys), merged_confidence,
    )

    return merged


# ---------------------------------------------------------------------------
# Session-level reconciliation (for use by pipeline)
# ---------------------------------------------------------------------------

def reconcile_into_session(
    existing_messages: list[dict],
    incoming_result: NormalizedResult,
) -> list[NormalizedMessage]:
    """Reconcile incoming normalized messages against existing session messages.

    For each incoming message, checks if it matches an existing one.
    If so, produces a merged message that picks the best content.
    If not, includes the incoming message as new.

    Returns the list of messages to persist (new + enriched).
    """
    # Build lookup from existing messages
    existing_by_key: dict[str, dict] = {}
    for emsg in existing_messages:
        text = emsg.get("content_text", "")
        role = emsg.get("role", "")
        key = f"{role}:{(text or '')[:200]}"
        if key not in existing_by_key:
            existing_by_key[key] = emsg

    output: list[NormalizedMessage] = []

    for msg in incoming_result.messages:
        key = _message_key(msg)
        if key in existing_by_key:
            # Already exists — check if incoming is higher quality
            existing = existing_by_key[key]
            existing_msg = NormalizedMessage(
                role=existing.get("role", ""),
                content_text=existing.get("content_text"),
                content_json=existing.get("content_json"),
                model_name=existing.get("model_name"),
                token_estimate=existing.get("token_estimate"),
            )
            eq = score_message(existing_msg)
            iq = score_message(msg)

            if iq.score > eq.score:
                merged = merge_messages(msg, existing_msg)
                merged._existing_id = existing.get("id")  # type: ignore[attr-defined]
                output.append(merged)
            elif iq.has_attachments and not eq.has_attachments:
                # Even if overall lower, import attachments
                merged = merge_messages(existing_msg, msg)
                merged._existing_id = existing.get("id")  # type: ignore[attr-defined]
                output.append(merged)
            # else: existing is better, skip
        else:
            output.append(msg)

    return output
