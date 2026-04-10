"""Rich content envelope helpers.

The legacy storage contract is ``content_json.attachments``. Keep that field
stable, and add ``content_json.rich_content`` as the typed render contract.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Optional

RICH_CONTENT_SCHEMA = "pce.rich_content.v1"

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
    return envelope


def build_content_json(
    attachments: Optional[list[dict]],
    *,
    plain_text: Optional[str] = None,
    source_evidence: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Build storage JSON while preserving the legacy attachments field."""
    normalized = normalize_attachments(attachments)
    if not normalized:
        return None

    return json.dumps(
        {
            "attachments": normalized,
            "rich_content": build_rich_content_envelope(
                plain_text,
                normalized,
                source_evidence=source_evidence,
            ),
        },
        ensure_ascii=False,
    )


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
