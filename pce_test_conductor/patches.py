# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.patches — 3 patch templates (per ADR-017 §3.5).

Each template returns a ``PatchProposal`` containing a ``unified_diff``
string + ``rationale`` + ``confidence``. The conductor **never applies
the diff itself** (per ADR-019 §3.1 contract D — patches-as-data, not
action). Callers (Cascade, Claude Code, etc.) use their own ``edit``
tool to land the diff, then call ``verify_patch`` to re-run.

Templates:

| ID | Triggers on FailureKind | Touches |
|---|---|---|
| ``add_content_block_type`` | CONTENT_BLOCK_UNKNOWN (soft) | ``pce_core/normalizer/<provider>.py`` elif chain |
| ``add_url_path``           | URL_PATTERN_DRIFT (hard)     | ``pce_core/normalizer/<provider>.py`` ``_PATHS`` set |
| ``widen_schema_field``     | SCHEMA_DRIFT (soft)          | ``pce_core/models.py`` field annotation |

The diffs are deliberately **template-shaped** (not regex-edited
directly into the live source) — agents review the rationale + the
diff before mutating a live file, and the conductor's role is to
produce structured proposals that a downstream LLM call (P5.C.4) can
either bless directly or refine into something more specific.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class PatchProposal:
    """JSON-serialisable patch suggestion. Mirrors ADR-017 §3.2 example."""

    patch_id: str               # "p-001" / "add_content_block_type-T03"
    kind: str                   # "add_content_block_type" | ...
    files: list[str]            # repo-relative paths the diff touches
    unified_diff: str           # full unified diff body
    rationale: str              # one-paragraph explanation
    confidence: float           # 0.0..1.0; templates default to 0.6 (medium)
    test_targets: list[str] = field(default_factory=list)  # "<target>:<case>"
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Template 1 — add_content_block_type
# ---------------------------------------------------------------------------

def add_content_block_type(
    *,
    provider: str,
    new_type: str,
    sample_payload: Optional[dict] = None,
    target_case: Optional[str] = None,
) -> PatchProposal:
    """Generate a diff that adds an ``elif btype == "<new_type>"`` branch.

    Used for CONTENT_BLOCK_UNKNOWN (soft severity). The generated diff
    is a stub that copies the ``"tool_use"`` branch and renames it —
    agent reviewer fills in the actual extraction logic.
    """
    file_path = f"pce_core/normalizer/{provider}.py"
    diff = (
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -395,6 +395,12 @@\n"
        f"         elif btype == \"tool_use\":\n"
        f"             # ... existing handler ...\n"
        f"             pass\n"
        f"+        elif btype == \"{new_type}\":\n"
        f"+            # P5.C-auto-suggested handler for new content_block.type\n"
        f"+            # Mirrors the 'tool_use' shape; agent reviewer should refine\n"
        f"+            # to extract the type-specific fields.\n"
        f"+            attachments.append({{\"type\": \"tool_call\", \"name\": block.get(\"name\"), \"arguments\": block.get(\"input\")}})\n"
        f"+            continue\n"
        f"     return attachments\n"
    )
    rationale = (
        f"Provider {provider!r} exposed a new content_block.type={new_type!r}. "
        f"The current normalizer's elif chain doesn't handle it, so attachments "
        f"land empty for affected messages (lossy capture, but not breaking). "
        f"Adding a 'tool_use'-shaped handler is a safe default; the agent "
        f"reviewer should verify the actual schema before merging."
    )
    return PatchProposal(
        patch_id=f"add_content_block_type-{new_type}",
        kind="add_content_block_type",
        files=[file_path],
        unified_diff=diff,
        rationale=rationale,
        confidence=0.55,
        test_targets=[target_case] if target_case else [],
        notes={"new_type": new_type, "sample_payload_keys":
               sorted((sample_payload or {}).keys())},
    )


# ---------------------------------------------------------------------------
# Template 2 — add_url_path
# ---------------------------------------------------------------------------

def add_url_path(
    *,
    provider: str,
    new_path_regex: str,
    target_case: Optional[str] = None,
) -> PatchProposal:
    """Generate a diff that extends the ``_PATHS`` / ``_CHAT_PATH_RE``."""
    file_path = f"pce_core/normalizer/{provider}.py"
    diff = (
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -45,6 +45,7 @@ _CHAT_PATH_RE = re.compile(\n"
        f"     r\"/v1/chat/completions|\"\n"
        f"     r\"/backend-api/conversation|\"\n"
        f"+    r\"{new_path_regex}|\"\n"
        f"     r\"/v1/responses\"\n"
        f" )\n"
    )
    rationale = (
        f"Endpoint pattern {new_path_regex!r} returned 404 / unmatched route, "
        f"meaning the upstream renamed or sharded the chat path. Adding it to "
        f"the existing _CHAT_PATH_RE alternation is the minimal fix; the change "
        f"is additive (existing patterns still match)."
    )
    return PatchProposal(
        patch_id=f"add_url_path-{new_path_regex.replace('/', '_')[:20]}",
        kind="add_url_path",
        files=[file_path],
        unified_diff=diff,
        rationale=rationale,
        confidence=0.7,
        test_targets=[target_case] if target_case else [],
        notes={"new_path_regex": new_path_regex},
    )


# ---------------------------------------------------------------------------
# Template 3 — widen_schema_field
# ---------------------------------------------------------------------------

def widen_schema_field(
    *,
    model_name: str,
    field_name: str,
    target_case: Optional[str] = None,
) -> PatchProposal:
    """Generate a diff that wraps a Pydantic field in ``Optional[...]``.

    Used for SCHEMA_DRIFT (soft severity — required field disappeared,
    but adding ``Optional[]`` keeps validation passing while the deeper
    upstream schema discussion happens.
    """
    file_path = "pce_core/models.py"
    diff = (
        f"--- a/{file_path}\n"
        f"+++ b/{file_path}\n"
        f"@@ -1,4 +1,5 @@\n"
        f" from typing import Optional\n"
        f" from pydantic import BaseModel, Field\n"
        f"\n"
        f"@@ class {model_name}(BaseModel):\n"
        f"-    {field_name}: str = Field(...)\n"
        f"+    # P5.C-auto-suggested: upstream payload sometimes omits this\n"
        f"+    # field; widening to Optional keeps validation passing.\n"
        f"+    {field_name}: Optional[str] = Field(default=None)\n"
    )
    rationale = (
        f"Pydantic model {model_name}.{field_name} is currently required, "
        f"but a recent upstream payload arrived without it. Widening to "
        f"Optional[str] (default=None) keeps validation passing without "
        f"silently dropping captures. The deeper question — is this field "
        f"genuinely going away, or is the upstream just temporarily flaky? "
        f"— is left to the agent reviewer."
    )
    return PatchProposal(
        patch_id=f"widen_schema_field-{model_name}-{field_name}",
        kind="widen_schema_field",
        files=[file_path],
        unified_diff=diff,
        rationale=rationale,
        confidence=0.5,
        test_targets=[target_case] if target_case else [],
        notes={"model_name": model_name, "field_name": field_name},
    )


# ---------------------------------------------------------------------------
# Dispatch — pick the right template based on FailureRecord
# ---------------------------------------------------------------------------

def propose_patches_for_failure(
    failure_kind: str,
    *,
    field_path: Optional[str] = None,
    actual: Any = None,
    expected: Any = None,
    target_case: Optional[str] = None,
    provider_hint: str = "anthropic",
) -> list[PatchProposal]:
    """Map a FailureKind to one or more patch proposals.

    Empty list means the conductor has no template for this failure
    kind (LOGIN_WALL / INFRA / RACE_TIMEOUT / UNKNOWN — these are
    operational issues, not schema/UI drift, so templates have no ROI
    per ADR-017 §4.6).
    """
    if failure_kind == "CONTENT_BLOCK_UNKNOWN":
        new_type = _extract_new_value(actual, expected) or "unknown_block_type"
        return [add_content_block_type(
            provider=provider_hint,
            new_type=str(new_type),
            target_case=target_case,
        )]
    if failure_kind == "URL_PATTERN_DRIFT":
        new_regex = field_path or "/api/v2/chat"
        return [add_url_path(
            provider=provider_hint,
            new_path_regex=str(new_regex),
            target_case=target_case,
        )]
    if failure_kind == "SCHEMA_DRIFT":
        # field_path looks like "$.content[*].type" or "missing_field_name"
        field_name = _extract_field_leaf(field_path) or "unknown_field"
        # Caller sets provider_hint via target_id heuristic; default
        # model_name comes from PCE Core's most-edited Pydantic model.
        return [widen_schema_field(
            model_name="CaptureIn",
            field_name=field_name,
            target_case=target_case,
        )]
    return []


def _extract_new_value(actual: Any, expected: Any) -> Optional[str]:
    """If actual is a list/set strictly larger than expected, return the new value."""
    try:
        actual_set = set(actual or [])
        expected_set = set(expected or [])
        diff = actual_set - expected_set
        if diff:
            return sorted(diff)[0]
    except TypeError:
        pass
    return None


def _extract_field_leaf(field_path: Optional[str]) -> Optional[str]:
    """Pull the trailing identifier from a JSONPath-ish field_path."""
    if not field_path:
        return None
    # ``$.content[*].type`` -> ``type``;  ``msg.body`` -> ``body``
    for sep in (".", "/"):
        if sep in field_path:
            tail = field_path.rsplit(sep, 1)[-1]
            return tail.strip("[]*$ ").strip() or None
    return field_path.strip("[]*$ ").strip() or None
