# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.server — FastMCP server registering the 8 tools.

Per ADR-017 §3.2, each tool returns a JSON-serialisable structure
(not a Python object). FastMCP serialises dict returns automatically.

The 8 tools (mirror the order in ADR-017 §3.2):

  1. ``list_targets()``        — enumerate manifests + roll-up health colour
  2. ``list_cases(target)``    — import case_standard_module, list cases
  3. ``run_case(target, case, mode)`` — pytest subprocess + persist record
  4. ``get_run(run_id)``       — load a previously-persisted record
  5. ``diff_canary(target, case, payload?)`` — schema-drift detection
  6. ``classify_failure(run_id)`` — heuristic classify into FailureKind
  7. ``propose_patch(run_id)`` — generate patch proposals (data only)
  8. ``verify_patch(target, case)`` — re-run after agent applied a patch

Tools are pure orchestration on top of:

- ``pce_test_conductor.manifest``    (yaml loader)
- ``pce_test_conductor.runner``      (pytest subprocess)
- ``pce_test_conductor.classifier``  (FailureKind heuristic)
- ``pce_test_conductor.canary``      (schema infer + diff)
- ``pce_test_conductor.patches``     (3 templates)
- ``pce_core.health``                (matrix rollup, P5.C.1)

The module also exposes the underlying functions as plain Python
callables (``_list_targets_impl`` etc.) so tests can drive them
without a FastMCP server. The ``@mcp.tool()`` decorators provide
the network surface.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import is_dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from .canary import (
    diff_schemas,
    infer_schema,
    load_canary,
    update_canary,
)
from .classifier import classify_run as _classify_run
from .manifest import (
    DEFAULT_TARGETS_DIR,
    Target,
    list_target_ids,
    load_all,
    load_manifest,
)
from .patches import propose_patches_for_failure
from .runner import (
    DEFAULT_RUNS_DIR,
    RunRecord,
    RunStatus,
    list_recent_runs,
    load_run,
    run_case as _runner_run_case,
)

logger = logging.getLogger("pce.conductor.server")


# ---------------------------------------------------------------------------
# Lazy FastMCP instance — import only when needed so import-time
# `pce_test_conductor` doesn't drag in the mcp package for tests that
# only want the helpers.
# ---------------------------------------------------------------------------

_mcp_instance = None


def get_mcp():  # pragma: no cover — exercised by __main__ smoke
    """Return the FastMCP instance, registering tools on first call."""
    global _mcp_instance
    if _mcp_instance is not None:
        return _mcp_instance
    from mcp.server.fastmcp import FastMCP  # local import — cf. pce_mcp/server.py

    mcp = FastMCP("PCE Test Conductor")
    _register_tools(mcp)
    _mcp_instance = mcp
    return mcp


# ---------------------------------------------------------------------------
# Tool 1 — list_targets
# ---------------------------------------------------------------------------

def _list_targets_impl(*, include_health: bool = True) -> dict[str, Any]:
    """Enumerate target manifests + (optionally) roll-up health colour.

    The health rollup is best-effort; if `pce_core.health` can't be
    imported (e.g. health table not yet migrated), targets simply
    carry ``health_color: 'grey'``.
    """
    targets = load_all()
    summaries = [t.to_summary() for t in targets]

    health_colors: dict[tuple[str, str], str] = {}
    if include_health:
        try:
            from pce_core.health import compute_matrix
            matrix = compute_matrix()
            lanes = matrix.get("lanes", {})
            for lane_name, lane_data in lanes.items():
                for target_name, target_data in (lane_data.get("targets", {}) or {}).items():
                    health_colors[(lane_name, target_name)] = target_data.get("color", "grey")
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("health matrix lookup failed: %r", exc)

    for s in summaries:
        f = s.get("health_beacon_filter") or {}
        key = (f.get("lane") or "", f.get("target") or "")
        s["health_color"] = health_colors.get(key, "grey")

    return {
        "targets": summaries,
        "count": len(summaries),
        "schema_version": 1,
    }


# ---------------------------------------------------------------------------
# Tool 2 — list_cases
# ---------------------------------------------------------------------------

def _list_cases_impl(target_id: str) -> dict[str, Any]:
    """Import the target's case_standard_module and project CASE_STANDARDS."""
    try:
        target = load_manifest(target_id)
    except FileNotFoundError as exc:
        return {"error": "target_not_found", "message": str(exc), "cases": []}

    if not target.case_standard_module:
        return {
            "target_id": target_id,
            "cases": [],
            "module": None,
            "note": "case_standard_module not declared in manifest "
                    "(P5.C.5 deliverable for cli/mcp/desktop lanes).",
        }

    try:
        mod = importlib.import_module(target.case_standard_module)
    except ImportError as exc:
        return {
            "target_id": target_id,
            "cases": [],
            "module": target.case_standard_module,
            "error": "module_not_found",
            "message": str(exc),
        }

    standards = getattr(mod, "CASE_STANDARDS", None)
    if not isinstance(standards, dict):
        return {
            "target_id": target_id,
            "cases": [],
            "module": target.case_standard_module,
            "error": "no_case_standards",
            "message": f"{target.case_standard_module}.CASE_STANDARDS is missing or malformed.",
        }

    cases: list[dict[str, Any]] = []
    for case_id, std in standards.items():
        cases.append({
            "case_id": str(case_id),
            "name": getattr(std, "name", ""),
            "capture": getattr(std, "capture", ""),
            "storage": getattr(std, "storage", ""),
            "render": getattr(std, "render", ""),
            "pass_gate": getattr(std, "pass_gate", ""),
            "strict_gap_on_skip": bool(getattr(std, "strict_gap_on_skip", False)),
            "region": getattr(std, "region", "default"),
        })
    cases.sort(key=lambda c: c["case_id"])
    return {
        "target_id": target_id,
        "module": target.case_standard_module,
        "version": getattr(mod, "STANDARD_VERSION", "unknown"),
        "cases": cases,
        "count": len(cases),
    }


# ---------------------------------------------------------------------------
# Tool 3 — run_case
# ---------------------------------------------------------------------------

def _run_case_impl(
    target_id: str,
    case_id: str,
    *,
    mode: str = "live",
    runs_dir: Optional[Path] = None,
) -> dict[str, Any]:
    try:
        target = load_manifest(target_id)
    except FileNotFoundError as exc:
        return {"error": "target_not_found", "message": str(exc)}

    if mode not in ("live", "replay"):
        return {"error": "invalid_mode", "message": f"mode must be 'live' or 'replay', got {mode!r}"}

    if mode == "replay":
        # P5.C.2 stub — replay loop arrives in P5.C.4. Surface this
        # cleanly rather than running live silently.
        return {
            "target_id": target_id,
            "case_id": case_id,
            "mode": "replay",
            "status": "infra_error",
            "error": "replay_not_implemented",
            "message": "Replay mode is a P5.C.4 deliverable. Use mode='live'.",
        }

    record = _runner_run_case(target, case_id, mode=mode, runs_dir=runs_dir)
    return record.to_dict()


# ---------------------------------------------------------------------------
# Tool 4 — get_run
# ---------------------------------------------------------------------------

def _get_run_impl(run_id: str, *, runs_dir: Optional[Path] = None) -> dict[str, Any]:
    record = load_run(run_id, runs_dir=runs_dir)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}
    return record.to_dict()


# ---------------------------------------------------------------------------
# Tool 5 — diff_canary
# ---------------------------------------------------------------------------

def _diff_canary_impl(
    target_id: str,
    case_id: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    endpoint: str = "default",
    update: bool = False,
) -> dict[str, Any]:
    """Diff a fresh payload against the stored canary schema.

    If ``payload`` is None: just load the existing canary and return
    its schema (no diff).

    If ``payload`` is provided + ``update=False``: infer schema, diff
    vs stored, but DON'T write back. Read-only check.

    If ``payload`` is provided + ``update=True``: merge into the
    canary and persist (per ADR-017 §3.4 ``selector_changed_pending_review``
    flow).
    """
    try:
        target = load_manifest(target_id)
    except FileNotFoundError as exc:
        return {"error": "target_not_found", "message": str(exc)}

    canary_dir = Path(target.canary_dir)
    if not canary_dir.is_absolute():
        canary_dir = Path(__file__).resolve().parent.parent / canary_dir

    if payload is None:
        stored = load_canary(canary_dir, case_id, endpoint=endpoint)
        return {
            "target_id": target_id,
            "case_id": case_id,
            "endpoint": endpoint,
            "canary_present": stored is not None,
            "schema": stored,
            "diff": [],
        }

    new_schema = infer_schema(payload)
    stored = load_canary(canary_dir, case_id, endpoint=endpoint)
    if stored is None:
        if update:
            from .canary import save_canary
            save_canary(canary_dir, case_id, new_schema, endpoint=endpoint)
        return {
            "target_id": target_id,
            "case_id": case_id,
            "endpoint": endpoint,
            "canary_present": stored is not None,
            "diff": [],
            "note": "no prior canary — saved as new baseline" if update else "no prior canary — payload accepted as baseline candidate",
        }

    if update:
        merged, diff_entries = update_canary(canary_dir, case_id, payload, endpoint=endpoint)
        return {
            "target_id": target_id,
            "case_id": case_id,
            "endpoint": endpoint,
            "canary_present": True,
            "diff": [d.to_dict() for d in diff_entries],
            "schema": merged,
        }

    diff_entries = diff_schemas(stored, new_schema)
    return {
        "target_id": target_id,
        "case_id": case_id,
        "endpoint": endpoint,
        "canary_present": True,
        "diff": [d.to_dict() for d in diff_entries],
    }


# ---------------------------------------------------------------------------
# Tool 6 — classify_failure
# ---------------------------------------------------------------------------

def _classify_failure_impl(run_id: str, *, runs_dir: Optional[Path] = None) -> dict[str, Any]:
    record = load_run(run_id, runs_dir=runs_dir)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id}
    if record.status == RunStatus.PASS:
        return {
            "run_id": run_id,
            "status": "pass",
            "kind": "UNKNOWN",
            "severity": "info",
            "hint": "Run was successful — classifier should not have been invoked.",
        }
    failure = _classify_run(
        exit_code=record.exit_code,
        stdout=record.stdout,
        stderr=record.stderr,
        elapsed_s=record.elapsed_s,
        timeout_s=record.timeout_s,
    )
    return {
        "run_id": run_id,
        "target_id": record.target_id,
        "case_id": record.case_id,
        **failure.to_dict(),
    }


# ---------------------------------------------------------------------------
# Tool 7 — propose_patch
# ---------------------------------------------------------------------------

def _propose_patch_impl(run_id: str, *, runs_dir: Optional[Path] = None) -> dict[str, Any]:
    record = load_run(run_id, runs_dir=runs_dir)
    if record is None:
        return {"error": "run_not_found", "run_id": run_id, "proposals": []}
    if record.status == RunStatus.PASS:
        return {
            "run_id": run_id,
            "proposals": [],
            "note": "Run passed — no patch needed.",
        }
    failure = _classify_run(
        exit_code=record.exit_code,
        stdout=record.stdout,
        stderr=record.stderr,
        elapsed_s=record.elapsed_s,
        timeout_s=record.timeout_s,
    )
    provider_hint = _provider_from_target(record.target_id)
    proposals = propose_patches_for_failure(
        failure_kind=failure.kind.value,
        field_path=failure.field_path,
        actual=failure.actual,
        expected=failure.expected,
        target_case=f"{record.target_id}:{record.case_id}",
        provider_hint=provider_hint,
    )
    return {
        "run_id": run_id,
        "target_id": record.target_id,
        "case_id": record.case_id,
        "failure_kind": failure.kind.value,
        "proposals": [p.to_dict() for p in proposals],
        "note": (
            "P5.C.2 ships templates only; LLM-refined proposals arrive in P5.C.4. "
            "Per ADR-019 §3.1 contract D, the conductor never applies the diff — "
            "the calling agent uses its own edit tool, then calls verify_patch."
        ),
    }


def _provider_from_target(target_id: str) -> str:
    """Heuristic: map target_id to the most likely normalizer module name."""
    if "claude" in target_id or "anthropic" in target_id:
        return "anthropic"
    if "chatgpt" in target_id or "openai" in target_id:
        return "openai"
    if "gemini" in target_id or "google" in target_id:
        return "google"
    return "anthropic"  # safe default — covers desktop/cli Claude Code lanes


# ---------------------------------------------------------------------------
# Tool 8 — verify_patch
# ---------------------------------------------------------------------------

def _verify_patch_impl(
    target_id: str,
    case_id: str,
    *,
    runs_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Re-run a case after the agent applied a patch.

    Per ADR-017 §3.5 / §4.2, verify_patch is the closing half of the
    propose-apply-verify loop:

      1. propose_patch → returns unified_diff (data)
      2. agent applies the diff via its own edit tool
      3. verify_patch → re-runs the same case
      4. caller compares old vs new run records to confirm the fix

    The implementation is a thin wrapper around run_case with a
    ``verify_after_patch=True`` evidence marker.
    """
    result = _run_case_impl(target_id, case_id, mode="live", runs_dir=runs_dir)
    if isinstance(result, dict) and "evidence" in result:
        result["evidence"] = {**result.get("evidence", {}), "verify_after_patch": True}
    result.setdefault("verify_marker", True)
    return result


# ---------------------------------------------------------------------------
# Convenience: tool registry dispatch (used by tests + __main__ --tool)
# ---------------------------------------------------------------------------

TOOL_DISPATCH = {
    "list_targets": _list_targets_impl,
    "list_cases": _list_cases_impl,
    "run_case": _run_case_impl,
    "get_run": _get_run_impl,
    "diff_canary": _diff_canary_impl,
    "classify_failure": _classify_failure_impl,
    "propose_patch": _propose_patch_impl,
    "verify_patch": _verify_patch_impl,
}

ALL_TOOL_NAMES: tuple[str, ...] = tuple(TOOL_DISPATCH.keys())


# ---------------------------------------------------------------------------
# FastMCP registration — wraps the pure functions with @mcp.tool().
# Kept separate so tests can use ALL_TOOL_NAMES + TOOL_DISPATCH without
# spinning up a FastMCP runtime (and the heavy import that comes with it).
# ---------------------------------------------------------------------------

def _register_tools(mcp) -> None:  # pragma: no cover — covered by __main__ smoke
    @mcp.tool()
    def list_targets(include_health: bool = True) -> dict:
        """Enumerate every PCE test target with health roll-up."""
        return _list_targets_impl(include_health=include_health)

    @mcp.tool()
    def list_cases(target_id: str) -> dict:
        """List the cases known to a target's case_standard_module."""
        return _list_cases_impl(target_id)

    @mcp.tool()
    def run_case(target_id: str, case_id: str, mode: str = "live") -> dict:
        """Run one case via pytest subprocess + persist a run record."""
        return _run_case_impl(target_id, case_id, mode=mode)

    @mcp.tool()
    def get_run(run_id: str) -> dict:
        """Read a previously-persisted run record by id."""
        return _get_run_impl(run_id)

    @mcp.tool()
    def diff_canary(
        target_id: str,
        case_id: str,
        payload: dict | None = None,
        endpoint: str = "default",
        update: bool = False,
    ) -> dict:
        """Diff a fresh payload against the stored canary schema."""
        return _diff_canary_impl(
            target_id, case_id, payload=payload, endpoint=endpoint, update=update,
        )

    @mcp.tool()
    def classify_failure(run_id: str) -> dict:
        """Map a failed run's evidence onto a FailureKind."""
        return _classify_failure_impl(run_id)

    @mcp.tool()
    def propose_patch(run_id: str) -> dict:
        """Generate patch proposals (data, never applied)."""
        return _propose_patch_impl(run_id)

    @mcp.tool()
    def verify_patch(target_id: str, case_id: str) -> dict:
        """Re-run a case after the agent applied a patch."""
        return _verify_patch_impl(target_id, case_id)
