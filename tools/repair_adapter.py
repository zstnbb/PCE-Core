# SPDX-License-Identifier: Apache-2.0
"""tools/repair_adapter.py — CLI for LLM-refined selector repair.

P5.C.4.3 deliverable per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.4 step 3.
Wraps ``pce_test_conductor.llm_repair`` in a turnkey CLI so the user
can drive the repair loop from a terminal without writing a Python
script. Workflow:

    1. Conductor's nightly probe captures a failing run record under
       ``pce_test_conductor/runs/<run_id>.json``.
    2. User runs ``python -m tools.repair_adapter --target browser_chatgpt``.
    3. This script: reads the most recent failed run for that target,
       classifies the failure, loads the YAML manifest, asks
       ``repair_selector`` for a proposed YAML diff, prints the diff.
    4. User reviews the diff. If acceptable, opens the YAML in their
       editor and applies the diff manually (or via Cascade's edit tool).
    5. User runs ``python -m pce_test_conductor --tool verify_patch``
       to confirm the fix.

Safety guarantees per ADR-019 §3.1 contract D:

  - This script is **read-only** against the codebase. It NEVER
    writes to the YAML, NEVER applies the diff, NEVER touches
    ``pce_core/adapters/*.yaml``.
  - ``--no-dry-run`` is required to make a real API call. Default
    is ``--dry-run`` which uses the deterministic mock provider.
  - API keys come from env vars (``ANTHROPIC_API_KEY`` /
    ``OPENAI_API_KEY``) or ``--api-key`` flag; never persisted.

Usage:

    python -m tools.repair_adapter --target browser_chatgpt
    python -m tools.repair_adapter --target browser_claude --case T01
    python -m tools.repair_adapter --target browser_chatgpt \\
        --no-dry-run --provider openai

Exit codes:
  0 — proposal produced (including mock fallback)
  1 — no failed run found for the target (nothing to repair)
  2 — manifest YAML missing or unreadable
  3 — repair_selector returned an error field (HTTP / provider issue)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("pce.tools.repair_adapter")


# ---------------------------------------------------------------------------
# Run record + YAML loading
# ---------------------------------------------------------------------------

def find_latest_failed_run(
    runs_dir: Path,
    *,
    target_id: str,
    case_id: Optional[str] = None,
    hours_back: float = 168.0,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Return the most recent non-pass run for ``target_id`` (+ optional case_id).

    Window defaults to 7 days; longer than auto_issue's 24 h because
    repair often happens after a developer has been away for a few days.
    """
    cutoff = (now if now is not None else time.time()) - hours_back * 3600.0
    candidates: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return None
    for path in runs_dir.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("skipping unreadable run %s: %r", path, exc)
            continue
        if data.get("target_id") != target_id:
            continue
        if case_id and data.get("case_id") != case_id:
            continue
        if data.get("started_at", 0) < cutoff:
            continue
        if data.get("status") in ("pass", None):
            continue
        candidates.append(data)
    candidates.sort(key=lambda d: d.get("started_at", 0), reverse=True)
    return candidates[0] if candidates else None


def derive_yaml_path(target_id: str, *, adapters_dir: Optional[Path] = None) -> Path:
    """Map a target_id to its YAML manifest path.

    Convention: ``<lane>_<site>`` → ``pce_core/adapters/<site>.yaml``.
    For target_ids without a lane prefix the full id is used as the stem.
    """
    base = adapters_dir or (_REPO_ROOT / "pce_core" / "adapters")
    stem = target_id.split("_", 1)[1] if "_" in target_id else target_id
    return base / f"{stem}.yaml"


def read_yaml_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# W5-T4 — supervisor /status → conductor target_id resolver
# ---------------------------------------------------------------------------

# Map supervisor scenario_id → conductor target_id. Conductor target_ids
# are <lane>_<site> (e.g. ``browser_chatgpt``); supervisor scenario_ids
# are flatter (``f1_chatgpt_web``). We keep the mapping explicit so a
# rename on either side fails loudly rather than silently routing the
# wrong case.
_SCENARIO_TO_CONDUCTOR_TARGET: dict[str, str] = {
    # Web 5
    "f1_chatgpt_web":          "browser_chatgpt",
    "f1_claude_web":           "browser_claude",
    "f1_gemini_web":           "browser_gemini",
    "f1_gas":                  "browser_gas",
    "f1_grok_web":             "browser_grok",
    # Desktop 8
    "f4_p1_claude_desktop":    "desktop_claude_desktop",
    "f4_p2_chatgpt_desktop":   "desktop_chatgpt_desktop",
    "f5_p3_cursor":            "desktop_cursor",
    "f5_p4_windsurf":          "desktop_windsurf",
    "f5_p5_github_copilot":    "desktop_github_copilot",
    "f6_p6_claude_code_cli":   "cli_claude_code",
    "f6_p7_codex_cli":         "cli_codex",
    "f6_p8_gemini_cli":        "cli_gemini",
}


def _resolve_target_from_supervisor(
    *,
    scenario_id: str,
    status_url: str,
    timeout: float = 5.0,
) -> Optional[str]:
    """Read /supervisor/status, confirm scenario is impaired/down, return target."""
    import urllib.error
    import urllib.request

    target_id = _SCENARIO_TO_CONDUCTOR_TARGET.get(scenario_id)
    if target_id is None:
        logger.warning("no conductor target mapping for scenario %r", scenario_id)
        return None

    try:
        req = urllib.request.Request(status_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            snapshot = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        # Supervisor offline → still let the user proceed via --target,
        # but the --redundancy-degraded shortcut returns None.
        logger.warning("supervisor /status unreachable: %r", exc)
        return None

    for sc in snapshot.get("scenarios", []):
        if sc.get("id") != scenario_id:
            continue
        # Only suggest target when actually impaired.
        if sc.get("status") in ("redundant", None):
            logger.info("scenario %r is %r — nothing to repair",
                        scenario_id, sc.get("status"))
            return None
        return target_id
    logger.warning("scenario %r not in /status response", scenario_id)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _classify(record: dict[str, Any]) -> dict[str, Any]:
    try:
        from pce_test_conductor.classifier import classify_run
    except ImportError:
        return {"kind": "UNKNOWN", "severity": "info", "hint": "classifier unavailable"}
    failure = classify_run(
        exit_code=int(record.get("exit_code", 1)),
        stdout=record.get("stdout", ""),
        stderr=record.get("stderr", ""),
        elapsed_s=record.get("elapsed_s"),
        timeout_s=record.get("timeout_s"),
    )
    return failure.to_dict()


def _render_summary(
    *,
    record: dict[str, Any],
    classification: dict[str, Any],
    result: Any,  # LLMRepairResult — typed loosely to avoid circular import at top
) -> str:
    """Format a human-friendly summary for terminal output."""
    started = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(record.get("started_at", 0)))
    head_lines = [
        f"Run        : {record.get('run_id', '?')}",
        f"Target     : {record.get('target_id', '?')}  ·  Case: {record.get('case_id', '?')}",
        f"Status     : {record.get('status', '?')}  ·  Started: {started}",
        f"Failure    : {classification.get('kind', '?')}  ·  Severity: {classification.get('severity', '?')}",
        f"Hint       : {classification.get('hint', '(none)')}",
        f"Provider   : {result.provider.value}  ·  Model: {result.model}",
        f"Dry-run    : {result.dry_run}",
        f"Confidence : {result.confidence:.2f}",
    ]
    if result.error:
        head_lines.append(f"Error      : {result.error}")
    out = ["", "─" * 60, "\n".join(head_lines), "─" * 60, "", "RATIONALE:", result.rationale, "", "PROPOSED YAML DIFF:", ""]
    out.append("```diff")
    out.append(result.proposed_yaml_diff or "(empty — provider returned no parsable diff)")
    out.append("```")
    out.append("")
    out.append("Next steps:")
    out.append(f"  1. Open the YAML manifest in your editor:")
    out.append(f"     {derive_yaml_path(record.get('target_id', ''))}")
    out.append("  2. Apply the proposed diff (Cascade's edit tool / git apply).")
    out.append("  3. Run: python -m pce_test_conductor --tool verify_patch \\")
    out.append(f"           --args '{{\"target_id\":\"{record.get('target_id', '')}\",\"case_id\":\"{record.get('case_id', '')}\"}}'")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    from pce_test_conductor.llm_repair import LLMProvider, repair_selector

    parser = argparse.ArgumentParser(
        prog="python -m tools.repair_adapter",
        description="LLM-refined selector repair (read-only; never applies the diff).",
    )
    parser.add_argument("--target", required=False,
                        help="conductor target_id (e.g. browser_chatgpt)")
    parser.add_argument(
        "--redundancy-degraded", default=None,
        help=(
            "supervisor scenario id (e.g. f4_p1_claude_desktop) — "
            "W5-T4 path. Reads /api/v1/supervisor/status, picks the "
            "fail/unknown leg, maps to the matching conductor case via "
            "scenarios.yaml, then runs the same llm_repair flow as --target."
        ),
    )
    parser.add_argument(
        "--status-url",
        default="http://127.0.0.1:9800/api/v1/supervisor/status",
        help="supervisor /status endpoint (used by --redundancy-degraded)",
    )
    parser.add_argument("--case", default=None,
                        help="case_id filter (default: latest failed run for the target)")
    parser.add_argument("--runs-dir", default="pce_test_conductor/runs")
    parser.add_argument("--hours-back", type=float, default=168.0,
                        help="window to scan for failed runs (default 168 h = 7 d)")
    parser.add_argument(
        "--provider",
        default="auto",
        choices=[p.value for p in LLMProvider],
        help="LLM provider (default: auto — picked from target_id)",
    )
    parser.add_argument("--model", default=None,
                        help="override default model name for the chosen provider")
    parser.add_argument("--api-key", default=None,
                        help="override API key (default: env vars)")
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="actually call the LLM API (default is dry-run mock)",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument("--json", dest="json_out", action="store_true",
                        help="emit machine-readable JSON instead of human summary")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_absolute():
        runs_dir = _REPO_ROOT / runs_dir

    # Resolve target via --redundancy-degraded if given (W5-T4 path).
    target_id = args.target
    if args.redundancy_degraded:
        resolved = _resolve_target_from_supervisor(
            scenario_id=args.redundancy_degraded,
            status_url=args.status_url,
        )
        if resolved is None:
            print(
                f"Could not resolve a conductor target for scenario "
                f"{args.redundancy_degraded!r}. Either supervisor /status "
                f"is unreachable, the scenario id is unknown, or no leg is "
                f"currently failing. See `tools/check_redundancy_targets.py` "
                f"for the live snapshot.",
                file=sys.stderr,
            )
            return 1
        target_id = resolved
        if not args.quiet:
            print(f"--redundancy-degraded {args.redundancy_degraded!r} → "
                  f"target_id={target_id!r}", file=sys.stderr)
    if not target_id:
        parser.error("either --target or --redundancy-degraded is required")

    record = find_latest_failed_run(
        runs_dir, target_id=target_id, case_id=args.case,
        hours_back=args.hours_back,
    )
    if not record:
        msg = f"No failed run found for target={target_id!r}"
        if args.case:
            msg += f", case={args.case!r}"
        msg += f" within {args.hours_back:.0f} h."
        print(msg, file=sys.stderr)
        return 1

    yaml_path = derive_yaml_path(record.get("target_id", target_id))
    if not yaml_path.exists():
        print(f"YAML manifest missing: {yaml_path}", file=sys.stderr)
        return 2
    yaml_text = read_yaml_text(yaml_path)

    classification = _classify(record)

    result = repair_selector(
        target_id=record.get("target_id", target_id),
        failure_kind=classification.get("kind", "UNKNOWN"),
        failure_hint=classification.get("hint") or "",
        stderr_excerpt=record.get("stderr", "") or "",
        adapter_yaml_text=yaml_text,
        case_id=record.get("case_id"),
        provider=LLMProvider(args.provider),
        api_key=args.api_key,
        dry_run=args.dry_run,
        model=args.model,
    )

    if args.json_out:
        out = {
            "run": {k: record.get(k) for k in ("run_id", "target_id", "case_id", "status")},
            "classification": classification,
            "repair": result.to_dict(),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(_render_summary(record=record, classification=classification, result=result))

    if result.error:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
