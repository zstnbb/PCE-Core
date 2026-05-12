# SPDX-License-Identifier: Apache-2.0
"""tools/auto_issue_on_fail.py — open GitHub issues for failed conductor runs.

Per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.3 deliverable:

    nightly-probe.yml runs the conductor across the 4-lane core case
    set, then this script reads the resulting RunRecord JSON files,
    classifies each non-pass outcome, and shells out to ``gh issue
    create`` with the canonical broken-adapter label + assignee.

Per ADR-019 §3.1 contract D + ADR-017 §3.5: this script is
**read-only against the codebase**. It NEVER applies a patch — it
attaches the conductor's ``propose_patch`` proposals into the issue
body for the human / agent reviewer to evaluate.

Usage:

    python -m tools.auto_issue_on_fail \\
        --hours-back 24 \\
        --runs-dir pce_test_conductor/runs \\
        --label broken-adapter \\
        --label auto-detected \\
        --assignee "@CODEOWNERS" \\
        [--dry-run] [--quiet]

``--dry-run`` is the default behaviour locally. Pass ``--no-dry-run``
on a CI runner where ``gh`` is authenticated. The dry-run output is
the exact ``gh issue create`` command that would be invoked + the
proposed body — useful for human triage from a desktop terminal.

Idempotency: each run keys on ``run_id`` and tags the issue title
with that prefix; re-running does NOT create duplicates as long as
the GitHub API rejects identical-titled issues with state=open
(default repo behaviour).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("pce.tools.auto_issue_on_fail")


# ---------------------------------------------------------------------------
# Issue body composition
# ---------------------------------------------------------------------------

def build_issue_title(record: dict[str, Any]) -> str:
    """One-line title; deterministic so duplicates can be detected."""
    target = record.get("target_id", "?")
    case_id = record.get("case_id", "?")
    status = record.get("status", "?")
    return f"[broken-adapter] {target}/{case_id} {status} ({record.get('run_id', '?')})"


def build_issue_body(
    record: dict[str, Any],
    *,
    classification: Optional[dict[str, Any]] = None,
    proposals: Optional[list[dict[str, Any]]] = None,
    health_dashboard_url: str = "http://127.0.0.1:9800/dashboard/lane-health",
) -> str:
    """Compose the full issue body, including optional patch proposals.

    The body follows the structure spec'd in PCE-PIPELINE-HEALTH-MATRIX.md
    §5.3 plus a section reserved for ``propose_patch`` data when the
    classifier returned a known FailureKind.
    """
    target = record.get("target_id", "?")
    case_id = record.get("case_id", "?")
    cmd = " ".join(record.get("cmd", []) or [])
    started = record.get("started_at", 0)
    started_str = (
        time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(started))
        if started else "?"
    )

    # ----- header -----
    lines: list[str] = [
        f"**Lane**: `{record.get('evidence', {}).get('lane', '?')}` · ",
        f"**Tier**: `{record.get('evidence', {}).get('tier', '?')}` · ",
        f"**Status**: `{record.get('status', '?')}` · ",
        f"**Elapsed**: `{record.get('elapsed_s', 0):.1f}s` · ",
        f"**Started**: `{started_str}`",
        "",
        "---",
        "",
    ]

    # ----- classification -----
    if classification:
        lines.extend([
            "## Classification",
            "",
            f"- **Kind**: `{classification.get('kind', 'UNKNOWN')}`",
            f"- **Severity**: `{classification.get('severity', 'info')}`",
            f"- **Hint**: {classification.get('hint', '(none)')}",
        ])
        if classification.get("field_path"):
            lines.append(f"- **Field path**: `{classification['field_path']}`")
        excerpt = classification.get("evidence_excerpt") or ""
        if excerpt:
            lines.extend(["", "**Evidence excerpt:**", "```", excerpt[:500], "```"])
        lines.append("")

    # ----- proposed patches -----
    if proposals:
        lines.append("## Proposed patches (data only — apply with your edit tool)")
        lines.append("")
        for p in proposals:
            lines.extend([
                f"### `{p.get('patch_id', '?')}` ({p.get('kind', '?')})",
                "",
                p.get("rationale", ""),
                "",
                f"_Confidence: {p.get('confidence', 0.0):.2f} · "
                f"Files: {', '.join(p.get('files') or [])}_",
                "",
                "```diff",
                p.get("unified_diff", ""),
                "```",
                "",
            ])
        lines.append("> Per ADR-019 §3.1 contract D: the conductor **never** applies "
                     "the diff. Use Cascade / Claude Code's edit tool, then run "
                     "`verify_patch` to confirm the fix.")
        lines.append("")

    # ----- reproduction -----
    lines.extend([
        "## Reproduction",
        "",
        "```powershell",
        f"# Re-run the failing case",
        f"{cmd}",
        "",
        "# Or via the conductor MCP tool surface",
        f"python -m pce_test_conductor --tool run_case "
        f"--args '{{\"target_id\":\"{target}\",\"case_id\":\"{case_id}\"}}'",
        "```",
        "",
        "## Tail of stderr",
        "",
        "```",
        (record.get("stderr") or "(empty)")[-1000:],
        "```",
        "",
        f"**Health dashboard**: {health_dashboard_url}?lane={record.get('evidence', {}).get('lane', '')}&target={target}",
        "",
        "_Auto-generated by `tools/auto_issue_on_fail.py` (P5.C.3)_",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RunRecord scanning + classification
# ---------------------------------------------------------------------------

def collect_failed_runs(
    runs_dir: Path,
    *,
    hours_back: float = 24.0,
    now: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Return run records with status != pass within the time window."""
    cutoff = (now if now is not None else time.time()) - hours_back * 3600.0
    out: list[dict[str, Any]] = []
    if not runs_dir.exists():
        return out
    for path in runs_dir.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("skipping unreadable run record %s: %r", path, exc)
            continue
        if data.get("started_at", 0) < cutoff:
            continue
        if data.get("status") in ("pass", None):
            continue
        out.append(data)
    out.sort(key=lambda d: d.get("started_at", 0), reverse=True)
    return out


def classify_record(record: dict[str, Any]) -> dict[str, Any]:
    """Wrap pce_test_conductor.classifier.classify_run for one record."""
    try:
        from pce_test_conductor.classifier import classify_run
    except ImportError:  # pragma: no cover — defensive
        return {"kind": "UNKNOWN", "severity": "info", "hint": "classifier unavailable"}
    failure = classify_run(
        exit_code=int(record.get("exit_code", 1)),
        stdout=record.get("stdout", ""),
        stderr=record.get("stderr", ""),
        elapsed_s=record.get("elapsed_s"),
        timeout_s=record.get("timeout_s"),
    )
    return failure.to_dict()


def proposals_for_record(record: dict[str, Any], classification: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate patch proposals if the failure kind has a registered template."""
    try:
        from pce_test_conductor.patches import propose_patches_for_failure
    except ImportError:  # pragma: no cover
        return []
    target_id = record.get("target_id", "")
    provider_hint = "anthropic" if "claude" in target_id else (
        "openai" if "chatgpt" in target_id or "openai" in target_id else "anthropic"
    )
    proposals = propose_patches_for_failure(
        failure_kind=classification.get("kind", "UNKNOWN"),
        field_path=classification.get("field_path"),
        actual=classification.get("actual"),
        expected=classification.get("expected"),
        target_case=f"{target_id}:{record.get('case_id', '?')}",
        provider_hint=provider_hint,
    )
    return [p.to_dict() for p in proposals]


# ---------------------------------------------------------------------------
# gh CLI invocation
# ---------------------------------------------------------------------------

def build_gh_command(
    *,
    title: str,
    body: str,
    labels: list[str],
    assignee: Optional[str] = None,
    repo: Optional[str] = None,
) -> list[str]:
    """Build the canonical ``gh issue create`` argv list."""
    cmd: list[str] = ["gh", "issue", "create", "--title", title, "--body", body]
    for lbl in labels:
        cmd.extend(["--label", lbl])
    if assignee:
        cmd.extend(["--assignee", assignee])
    if repo:
        cmd.extend(["--repo", repo])
    return cmd


def invoke_gh(cmd: list[str], *, dry_run: bool = True) -> dict[str, Any]:
    """Invoke ``gh`` if available + not dry-run; otherwise return the planned cmd.

    Returns a dict with:
        {"dry_run": bool, "exit_code": int, "stdout": str, "stderr": str, "cmd": [...]}
    """
    if dry_run:
        return {"dry_run": True, "exit_code": 0, "stdout": "", "stderr": "", "cmd": cmd}
    if not shutil.which("gh"):
        return {
            "dry_run": False, "exit_code": 127,
            "stdout": "", "stderr": "gh CLI not on PATH; install + authenticate first.",
            "cmd": cmd,
        }
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return {"dry_run": False, "exit_code": -1, "stdout": "",
                "stderr": "gh command timed out (30 s)", "cmd": cmd}
    return {
        "dry_run": False,
        "exit_code": int(proc.returncode),
        "stdout": (proc.stdout or "")[:4096],
        "stderr": (proc.stderr or "")[:4096],
        "cmd": cmd,
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def process_runs(
    *,
    runs_dir: Path,
    hours_back: float,
    labels: list[str],
    assignee: Optional[str],
    repo: Optional[str],
    dry_run: bool,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Top-level orchestration. Returns a summary dict for CLI output."""
    failed = collect_failed_runs(runs_dir, hours_back=hours_back, now=now)
    summary: dict[str, Any] = {
        "scanned_runs_dir": str(runs_dir),
        "hours_back": hours_back,
        "fail_count": len(failed),
        "issues": [],
        "dry_run": dry_run,
    }
    for record in failed:
        classification = classify_record(record)
        proposals = proposals_for_record(record, classification)
        title = build_issue_title(record)
        body = build_issue_body(record, classification=classification, proposals=proposals)
        cmd = build_gh_command(
            title=title, body=body, labels=labels, assignee=assignee, repo=repo,
        )
        result = invoke_gh(cmd, dry_run=dry_run)
        summary["issues"].append({
            "run_id": record.get("run_id"),
            "target_id": record.get("target_id"),
            "case_id": record.get("case_id"),
            "title": title,
            "kind": classification.get("kind"),
            "severity": classification.get("severity"),
            "exit_code": result["exit_code"],
            "dry_run": result["dry_run"],
            "stdout": result.get("stdout", "")[:200],
            "stderr": result.get("stderr", "")[:200],
            "proposal_count": len(proposals),
        })
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.auto_issue_on_fail")
    parser.add_argument("--runs-dir", default="pce_test_conductor/runs")
    parser.add_argument("--hours-back", type=float, default=24.0)
    parser.add_argument("--label", action="append", default=None,
                        help="apply label (repeatable, default: ['broken-adapter','auto-detected'])")
    parser.add_argument("--assignee", default="@CODEOWNERS")
    parser.add_argument("--repo", default=None,
                        help="explicit owner/repo for `gh --repo` (CI sets via GITHUB_REPOSITORY)")
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="actually invoke `gh issue create` (default is dry-run)",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    labels = args.label or ["broken-adapter", "auto-detected"]
    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_absolute():
        runs_dir = _REPO_ROOT / runs_dir

    summary = process_runs(
        runs_dir=runs_dir,
        hours_back=args.hours_back,
        labels=labels,
        assignee=args.assignee,
        repo=args.repo,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    # Exit non-zero if any non-dry-run gh invocation failed.
    if not args.dry_run:
        bad = [i for i in summary["issues"] if i.get("exit_code", 0) != 0]
        if bad:
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
