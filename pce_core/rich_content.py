# SPDX-License-Identifier: Apache-2.0
"""Rich content envelope helpers.

The legacy storage contract is ``content_json.attachments``. Keep that field
stable, and add ``content_json.rich_content`` as the typed render contract.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

RICH_CONTENT_SCHEMA = "pce.rich_content.v1"
THREADING_SCHEMA = "pce.threading.v1"

_ATTACHMENT_BLOCK_TYPES = {
    "image_url": "image",
    "image_generation": "image",
    "code_block": "code",
    "code_output": "code_output",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "citation": "citation",
    "file": "file",
    "document": "file",
    "audio": "audio",
    "canvas": "canvas",
}


def normalize_attachments(attachments: Optional[list[dict]]) -> list[dict[str, Any]]:
    """Return JSON-safe attachment dictionaries, preserving order."""
    normalized: list[dict[str, Any]] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        cloned = copy.deepcopy(att)
        try:
            json.dumps(cloned, ensure_ascii=False)
        except TypeError:
            cloned = json.loads(json.dumps(cloned, ensure_ascii=False, default=str))
        normalized.append(cloned)
    return normalized


def attachment_to_block(att: dict[str, Any], index: int) -> dict[str, Any]:
    """Convert a legacy attachment into a typed rich-content block."""
    att_type = str(att.get("type") or "unknown")
    block_type = _ATTACHMENT_BLOCK_TYPES.get(att_type, att_type)
    block: dict[str, Any] = {
        "type": block_type,
        "source": "attachment",
        "attachment_index": index,
        "attachment_type": att_type,
        "data": copy.deepcopy(att),
    }

    for key in (
        "name",
        "title",
        "url",
        "file_id",
        "media_type",
        "source_type",
        "language",
        "code",
        "output",
        "alt",
        "detail",
        "text",
        "arguments",
        "transcript",
        "format",
        "content",
        "is_error",
    ):
        if key in att and att[key] not in (None, "", [], {}):
            block[key] = copy.deepcopy(att[key])

    return block


def attachment_to_asset(att: dict[str, Any], index: int) -> Optional[dict[str, Any]]:
    """Extract a local/remote renderable asset reference from an attachment."""
    att_type = str(att.get("type") or "")
    if att_type in ("image_url", "image_generation"):
        kind = "image"
    elif att_type in ("file", "document"):
        kind = "file"
    elif att_type == "audio":
        kind = "audio"
    else:
        return None

    asset: dict[str, Any] = {
        "id": f"attachment:{index}",
        "kind": kind,
        "attachment_index": index,
    }
    for key in ("name", "title", "url", "file_id", "media_type", "source_type", "format"):
        if key in att and att[key] not in (None, "", [], {}):
            asset[key] = copy.deepcopy(att[key])
    return asset


def build_rich_content_envelope(
    plain_text: Optional[str],
    attachments: Optional[list[dict]],
    *,
    source_evidence: Optional[dict[str, Any]] = None,
    threading: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the canonical rich-content render envelope."""
    normalized = normalize_attachments(attachments)
    blocks = [attachment_to_block(att, index) for index, att in enumerate(normalized)]
    assets = [
        asset
        for index, att in enumerate(normalized)
        if (asset := attachment_to_asset(att, index)) is not None
    ]

    envelope: dict[str, Any] = {
        "schema": RICH_CONTENT_SCHEMA,
        "plain_text": plain_text or "",
        "blocks": blocks,
    }
    if assets:
        envelope["assets"] = assets
    if source_evidence:
        envelope["source_evidence"] = copy.deepcopy(source_evidence)
    if threading:
        envelope.update(_threading_render_contract(threading))
    return envelope


def build_content_json(
    attachments: Optional[list[dict]],
    *,
    plain_text: Optional[str] = None,
    source_evidence: Optional[dict[str, Any]] = None,
    threading: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Build storage JSON while preserving the legacy attachments field."""
    normalized = normalize_attachments(attachments)
    threading_payload = _normalize_threading(threading)
    if not normalized and not threading_payload:
        return None

    payload: dict[str, Any] = {
        "attachments": normalized,
        "rich_content": build_rich_content_envelope(
            plain_text,
            normalized,
            source_evidence=source_evidence,
            threading=threading_payload,
        ),
    }
    if threading_payload:
        payload["threading"] = threading_payload
    return json.dumps(payload, ensure_ascii=False)


def _normalize_threading(threading: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(threading, dict):
        return None
    cleaned = {
        key: copy.deepcopy(value)
        for key, value in threading.items()
        if value not in (None, "", [], {})
    }
    if not cleaned:
        return None
    cleaned.setdefault("schema", THREADING_SCHEMA)
    return cleaned


def _threading_render_contract(threading: dict[str, Any]) -> dict[str, Any]:
    """Return render-facing branch/variant keys for the dashboard contract."""
    out: dict[str, Any] = {}
    ttype = str(threading.get("type") or threading.get("kind") or "")
    if "variant" in ttype or any(k in threading for k in ("variant_group_id", "variant_id")):
        group_id = threading.get("variant_group_id")
        variant_id = threading.get("variant_id") or threading.get("current_variant_id")
        current = threading.get("current_variant_id") or variant_id
        out["variant_group"] = {
            "id": group_id,
            "current_variant_id": current,
        }
        out["current_variant"] = {
            "id": current,
            "index": threading.get("variant_index"),
        }
        out["variants"] = [
            {
                "id": variant_id,
                "index": threading.get("variant_index"),
                "current": True,
            }
        ]
        out["variant_controls"] = {
            "current_variant_id": current,
            "variant_group_id": group_id,
        }
    if "branch" in ttype or any(k in threading for k in ("branch_group_id", "branch_id")):
        branch_id = threading.get("branch_id") or threading.get("current_branch_id")
        current = threading.get("current_branch_id") or branch_id
        out["branch_tree"] = {
            "id": threading.get("branch_group_id"),
            "current_branch_id": current,
            "parent_message_id": threading.get("parent_message_id"),
        }
        out["current_branch"] = {
            "id": current,
            "index": threading.get("branch_index"),
        }
        out["branches"] = [
            {
                "id": branch_id,
                "index": threading.get("branch_index"),
                "current": True,
            }
        ]
        out["branch_choices"] = {
            "current_branch_id": current,
            "branch_group_id": threading.get("branch_group_id"),
        }
    return out


def load_threading_from_content_json(content_json: Optional[str]) -> dict[str, Any]:
    """Read the structured branch/variant contract from content_json."""
    if not content_json:
        return {}
    try:
        data = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    threading = data.get("threading")
    return threading if isinstance(threading, dict) else {}


def merge_content_json_contracts(
    existing_json: Optional[str],
    incoming_json: Optional[str],
    *,
    plain_text: Optional[str] = None,
) -> Optional[str]:
    """Merge attachments plus branch/variant contracts without dropping either."""
    if not existing_json:
        return incoming_json
    if not incoming_json:
        return existing_json

    existing_threading = load_threading_from_content_json(existing_json)
    incoming_threading = load_threading_from_content_json(incoming_json)
    threading = dict(existing_threading)
    threading.update({k: v for k, v in incoming_threading.items() if v not in (None, "", [], {})})
    attachments = load_attachments_from_content_json(existing_json) + load_attachments_from_content_json(incoming_json)
    deduped = normalize_attachments(attachments)
    if deduped or threading:
        return build_content_json(
            deduped,
            plain_text=plain_text,
            threading=threading or None,
        )
    return existing_json or incoming_json


def load_attachments_from_content_json(content_json: Optional[str]) -> list[dict[str, Any]]:
    """Read attachments from either legacy content_json or the v1 envelope."""
    if not content_json:
        return []
    try:
        data = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    attachments = data.get("attachments")
    if isinstance(attachments, list):
        return normalize_attachments(attachments)

    rich_content = data.get("rich_content")
    blocks = rich_content.get("blocks") if isinstance(rich_content, dict) else None
    if not isinstance(blocks, list):
        return []

    out: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        legacy = block.get("data")
        if isinstance(legacy, dict):
            out.append(legacy)
    return normalize_attachments(out)
