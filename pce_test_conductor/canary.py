# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.canary — JSON Schema canary store + diff.

ADR-017 §3.4 specifies genson + jsonschema-diff. To stay zero-dep
beyond the project's existing pyyaml + jsonschema, we ship a small,
deterministic in-house schema inferrer + a structured diff that emits
the same severity ladder ADR-017 contracts (added_property=soft,
removed_property=hard, changed_type=hard, enum_extension=soft).

Schema files live under
``pce_test_conductor/canaries/<target>/<case_id>_<endpoint>.schema.json``
and are git-tracked per ADR-017 §3.4.

The implementation deliberately avoids genson because:

- Adding a new top-level dependency to PCE Core is a noticeable
  ecosystem cost vs the ~80 LOC of code below.
- genson's merge semantics (``SchemaBuilder.add_object``) treat lists
  as union-of-item-schemas which is exactly what we want, and is easy
  to reproduce manually.
- jsonschema-diff is a thin Python wrapper that we similarly bake in,
  staying inside the conductor's own contract surface.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pce.conductor.canary")


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def infer_schema(payload: Any) -> dict[str, Any]:
    """Infer a JSON Schema (Draft 7-ish) from a single Python value.

    Output is intentionally minimal — only ``type``, ``properties``,
    ``required``, ``items``, and ``enum`` (auto-collected for short
    string lists). Sufficient for diffing against future captures
    without exploding into 200+ keyword variants.
    """
    if isinstance(payload, dict):
        return _infer_object(payload)
    if isinstance(payload, list):
        return _infer_array(payload)
    return _infer_primitive(payload)


def _infer_primitive(v: Any) -> dict[str, Any]:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):  # bool BEFORE int (bool is a subclass of int)
        return {"type": "boolean"}
    if isinstance(v, int):
        return {"type": "integer"}
    if isinstance(v, float):
        return {"type": "number"}
    if isinstance(v, str):
        return {"type": "string"}
    return {"type": "object"}


def _infer_object(d: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, dict[str, Any]] = {}
    for k, v in d.items():
        props[str(k)] = infer_schema(v)
    return {
        "type": "object",
        "properties": props,
        "required": sorted(str(k) for k in d.keys()),
    }


def _infer_array(arr: list[Any]) -> dict[str, Any]:
    if not arr:
        return {"type": "array", "items": {}}
    # Merge schemas of all items to build a union-friendly description.
    items_schema = infer_schema(arr[0])
    for item in arr[1:]:
        items_schema = merge_schemas(items_schema, infer_schema(item))
    return {"type": "array", "items": items_schema}


# ---------------------------------------------------------------------------
# Merge (union over multiple observations)
# ---------------------------------------------------------------------------

def merge_schemas(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Combine two schemas into one that accepts both inputs.

    Keeps:
      - same type → recurse into properties / items
      - different types → ``anyOf`` of the two
      - object: union of properties; required = intersection
      - string with enum: union enums; if values exceed 16 distinct,
        drop the enum (keep "string")
    """
    if not a:
        return dict(b)
    if not b:
        return dict(a)

    type_a = a.get("type")
    type_b = b.get("type")
    if type_a != type_b:
        # Heterogeneous — anyOf is the safe fallback; future inferences
        # will then merge against either side.
        return {"anyOf": [a, b]}

    if type_a == "object":
        return _merge_objects(a, b)
    if type_a == "array":
        merged_items = merge_schemas(a.get("items") or {}, b.get("items") or {})
        return {"type": "array", "items": merged_items}
    if type_a == "string":
        return _merge_strings(a, b)
    return dict(a)


def _merge_objects(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    pa = a.get("properties", {}) or {}
    pb = b.get("properties", {}) or {}
    keys = sorted(set(pa.keys()) | set(pb.keys()))
    merged_props: dict[str, dict[str, Any]] = {}
    for k in keys:
        if k in pa and k in pb:
            merged_props[k] = merge_schemas(pa[k], pb[k])
        elif k in pa:
            merged_props[k] = pa[k]
        else:
            merged_props[k] = pb[k]
    # Required is the intersection — a key only counts as "required"
    # when both observations had it.
    req_a = set(a.get("required", []))
    req_b = set(b.get("required", []))
    return {
        "type": "object",
        "properties": merged_props,
        "required": sorted(req_a & req_b),
    }


def _merge_strings(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    enum_a = list(a.get("enum", []))
    enum_b = list(b.get("enum", []))
    if not enum_a and not enum_b:
        return {"type": "string"}
    union = sorted(set(enum_a + enum_b))
    if len(union) > 16:
        return {"type": "string"}        # too varied to track as enum
    return {"type": "string", "enum": union}


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

@dataclass
class SchemaDiffEntry:
    """One JSON-Schema-level difference. Mirrors ADR-017 §3.4 vocabulary."""

    kind: str                 # "added_property" | "removed_property" | "changed_type" | "enum_extension"
    severity: str             # "hard" | "soft" | "info"
    field_path: str           # JSONPath-ish, e.g. "$.content[*].type"
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "field_path": self.field_path,
            "expected": self.expected,
            "actual": self.actual,
            "note": self.note,
        }


def diff_schemas(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    base_path: str = "$",
) -> list[SchemaDiffEntry]:
    """Compute structured diff entries between two schemas.

    Returns a list of ``SchemaDiffEntry`` describing each individual
    difference. ``base_path`` is the JSONPath prefix used in
    ``field_path`` and recursed into for nested objects.
    """
    entries: list[SchemaDiffEntry] = []
    _diff_recurse(old, new, base_path, entries)
    return entries


def _diff_recurse(
    old: dict[str, Any],
    new: dict[str, Any],
    path: str,
    out: list[SchemaDiffEntry],
) -> None:
    type_old = old.get("type")
    type_new = new.get("type")
    if type_old and type_new and type_old != type_new:
        out.append(SchemaDiffEntry(
            kind="changed_type",
            severity="hard",
            field_path=path,
            expected=type_old,
            actual=type_new,
            note=f"type changed {type_old!r} -> {type_new!r}",
        ))
        return

    if type_old == "object" or type_new == "object":
        po = old.get("properties", {}) or {}
        pn = new.get("properties", {}) or {}

        added = sorted(set(pn) - set(po))
        removed = sorted(set(po) - set(pn))
        common = sorted(set(po) & set(pn))

        for k in added:
            sub_path = f"{path}.{k}"
            out.append(SchemaDiffEntry(
                kind="added_property",
                severity="soft",
                field_path=sub_path,
                actual=pn[k].get("type"),
                note=f"new property {k!r} appeared",
            ))

        # ``removed_property`` is hard ONLY when the property was
        # ``required`` in the old schema — optional fields disappearing
        # are allowed by definition.
        old_required = set(old.get("required", []))
        for k in removed:
            sub_path = f"{path}.{k}"
            sev = "hard" if k in old_required else "soft"
            out.append(SchemaDiffEntry(
                kind="removed_property",
                severity=sev,
                field_path=sub_path,
                expected=po[k].get("type"),
                note=f"property {k!r} removed from upstream",
            ))

        for k in common:
            _diff_recurse(po[k], pn[k], f"{path}.{k}", out)
        return

    if type_old == "array" or type_new == "array":
        items_old = old.get("items") or {}
        items_new = new.get("items") or {}
        _diff_recurse(items_old, items_new, f"{path}[*]", out)
        return

    # Primitive — check for enum extension on strings.
    if type_old == "string" and type_new == "string":
        eo = list(old.get("enum", []))
        en = list(new.get("enum", []))
        if eo and en and set(en) > set(eo):
            added = sorted(set(en) - set(eo))
            out.append(SchemaDiffEntry(
                kind="enum_extension",
                severity="soft",
                field_path=path,
                expected=eo,
                actual=en,
                note=f"new enum values: {added}",
            ))
        elif eo and en and set(eo) > set(en):
            removed = sorted(set(eo) - set(en))
            out.append(SchemaDiffEntry(
                kind="enum_extension",
                severity="hard",
                field_path=path,
                expected=eo,
                actual=en,
                note=f"enum values disappeared: {removed}",
            ))


# ---------------------------------------------------------------------------
# Store I/O
# ---------------------------------------------------------------------------

def canary_path(canary_dir: Path, case_id: str, endpoint: str = "default") -> Path:
    """Return the on-disk path for one (case_id, endpoint) schema."""
    safe_endpoint = endpoint.replace("/", "_").replace(":", "_")
    return canary_dir / f"{case_id}_{safe_endpoint}.schema.json"


def load_canary(canary_dir: Path, case_id: str, endpoint: str = "default") -> Optional[dict[str, Any]]:
    """Load a stored canary schema, or ``None`` if not present."""
    path = canary_path(canary_dir, case_id, endpoint)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("canary load failed at %s: %r", path, exc)
        return None


def save_canary(
    canary_dir: Path,
    case_id: str,
    schema: dict[str, Any],
    *,
    endpoint: str = "default",
) -> Path:
    """Persist a canary schema. Caller is responsible for git-add."""
    canary_dir.mkdir(parents=True, exist_ok=True)
    path = canary_path(canary_dir, case_id, endpoint)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(schema, fh, indent=2, sort_keys=True, ensure_ascii=False)
    return path


def update_canary(
    canary_dir: Path,
    case_id: str,
    payload: Any,
    *,
    endpoint: str = "default",
) -> tuple[dict[str, Any], list[SchemaDiffEntry]]:
    """Merge a fresh observation into the on-disk canary schema.

    Returns ``(merged_schema, diff_entries)``. ``diff_entries`` is the
    list of new structured differences vs the **previous** stored
    schema; an empty list means the new payload was already a subset
    of the canary's known shape.

    Per ADR-017 §3.4: callers should treat any returned diff as a
    ``selector_changed_pending_review`` event (no fail count
    pollution).
    """
    new_schema = infer_schema(payload)
    old_schema = load_canary(canary_dir, case_id, endpoint=endpoint)
    if old_schema is None:
        save_canary(canary_dir, case_id, new_schema, endpoint=endpoint)
        return new_schema, []
    merged = merge_schemas(old_schema, new_schema)
    if json.dumps(merged, sort_keys=True) == json.dumps(old_schema, sort_keys=True):
        return old_schema, []  # no observable change
    diff = diff_schemas(old_schema, merged)
    save_canary(canary_dir, case_id, merged, endpoint=endpoint)
    return merged, diff
