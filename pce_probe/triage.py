# SPDX-License-Identifier: Apache-2.0
"""PCE Probe matrix triage \u2014 turn ``summary.json`` into a fix-ready view.

The matrix runner (``tests/e2e_probe/test_matrix.py``) writes one
``summary.json`` per session under
``tests/e2e_probe/reports/<UTC-timestamp>/``. That JSON has every cell's
status + details, but reading it raw is hostile to a triage agent: you
have to mentally pivot from "row in a dict" to "edit ``sites/<site>.py``
line N".

This module collapses the JSON into one block per failing cell that
tells the agent, in order:

    1. WHAT broke               (cell id + phase + error code)
    2. WHY                      (one-line summary + agent_hint if any)
    3. EVIDENCE                 (recent capture events, dom_excerpt, ...)
    4. WHERE TO EDIT            (full path to the site adapter file)
    5. HOW TO REPLAY            (pytest -k "<cell>" command)

Run modes::

    python -m pce_probe.triage
        Auto-locate the latest summary under tests/e2e_probe/reports/.

    python -m pce_probe.triage <path/to/summary.json>
        Triage a specific report.

    python -m pce_probe.triage --json
        Emit the triaged data as machine-readable JSON instead of text.
        Useful when an outer agent wants to ingest it.

    python -m pce_probe.triage --include-skip
        Also surface SKIP cells (default: only FAIL).

    python -m pce_probe.triage --reports-dir tests/e2e_probe/reports
        Override the default reports root.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# The matrix lives under tests/e2e_probe/. Both the reports dir and the
# site-adapter sources are relative to the repo root that contains
# ``tests/e2e_probe/``. The CLI auto-discovers this by walking up from
# the cwd when no explicit path is supplied.
_DEFAULT_REPORTS_RELATIVE = Path("tests") / "e2e_probe" / "reports"
_DEFAULT_SITES_RELATIVE = Path("tests") / "e2e_probe" / "sites"


# ---------------------------------------------------------------------------
# Triaged-row shape (also the JSON output schema)
# ---------------------------------------------------------------------------


@dataclass
class TriagedFailure:
    cell_id: str  # e.g. "chatgpt-T01"
    site_name: str
    case_id: str
    status: str  # "fail" | "skip"
    phase: str | None  # e.g. "send_prompt", "wait_for_token"
    code: str | None  # e.g. "selector_not_found"
    summary: str
    agent_hint: str | None
    edit_target: str  # absolute path to sites/<name>.py
    replay_cmd: str
    evidence: dict[str, Any]
    duration_ms: int


# ---------------------------------------------------------------------------
# Locate the report
# ---------------------------------------------------------------------------


def _walk_up_for_reports(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a directory that contains
    ``tests/e2e_probe/reports/``. Returns the repo-root candidate or
    ``None`` if not found.
    """
    cur = start.resolve()
    for _ in range(10):  # bounded; repos rarely nest deeper than this
        if (cur / _DEFAULT_REPORTS_RELATIVE).is_dir():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _latest_summary(reports_dir: Path) -> Path | None:
    """Return the newest ``summary.json`` under ``reports_dir`` (each
    immediate subdir is a UTC-timestamped run). Returns ``None`` if the
    dir is empty or missing.
    """
    if not reports_dir.is_dir():
        return None
    candidates = sorted(
        (p for p in reports_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,  # UTC-timestamp lexsort == chronological
        reverse=True,
    )
    for c in candidates:
        s = c / "summary.json"
        if s.is_file():
            return s
    return None


# ---------------------------------------------------------------------------
# Triage logic
# ---------------------------------------------------------------------------


def triage_summary(
    summary_path: Path,
    *,
    sites_dir: Path,
    include_skip: bool = False,
) -> dict[str, Any]:
    """Read ``summary.json`` and produce a triaged view.

    Returns a dict with::

        {
          "report":   {"path": "...", "started_at_utc": "...",
                       "duration_s": ..., "n_results": ...},
          "by_status": {"pass": N, "fail": M, "skip": K},
          "failures": [TriagedFailure, ...]   # sorted by site then case
        }
    """
    # ``utf-8-sig`` so we tolerate a BOM if some Windows tool wrote
    # the JSON. The conftest writer uses plain utf-8 (no BOM), but a
    # hand-edited file may carry one and the triage CLI shouldn't die
    # on that.
    raw = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    rows: list[dict] = raw.get("results") or []

    failures: list[TriagedFailure] = []
    for row in rows:
        status = str(row.get("status") or "").lower()
        if status not in ("fail", "skip"):
            continue
        if status == "skip" and not include_skip:
            continue
        details = row.get("details") or {}
        site = str(row.get("site_name") or "?")
        case = str(row.get("case_id") or "?")
        cell_id = f"{site}-{case}"

        edit_target = (sites_dir / f"{site}.py").resolve()

        replay_cmd = (
            f'pytest tests/e2e_probe/test_matrix.py -k "{cell_id}" -v -s'
        )

        # Strip very-large fields out of `details` for evidence.
        # `dom_excerpt` and `last_capture_events` are the two big ones;
        # we keep them but truncated.
        evidence = _redact_evidence(details)

        failures.append(
            TriagedFailure(
                cell_id=cell_id,
                site_name=site,
                case_id=case,
                status=status,
                phase=_strify(details.get("phase")),
                code=_strify(details.get("code")),
                summary=str(row.get("summary") or ""),
                agent_hint=_strify(details.get("agent_hint")),
                edit_target=str(edit_target),
                replay_cmd=replay_cmd,
                evidence=evidence,
                duration_ms=int(row.get("duration_ms") or 0),
            )
        )

    failures.sort(key=lambda f: (f.site_name, f.case_id))

    return {
        "report": {
            "path": str(summary_path),
            "started_at_utc": raw.get("started_at_utc"),
            "duration_s": raw.get("duration_s"),
            "n_results": raw.get("n_results"),
        },
        "by_status": raw.get("by_status") or {},
        "failures": failures,
    }


def _strify(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "value"):
        v = v.value
    s = str(v)
    return s if s else None


def _redact_evidence(details: dict[str, Any]) -> dict[str, Any]:
    """Keep evidence but trim the volume so the triage view stays
    readable. The original ``summary.json`` still has the full payload.
    """
    out: dict[str, Any] = {}
    for k, v in details.items():
        if k in ("agent_hint", "phase", "code"):
            # Promoted to top-level fields on TriagedFailure.
            continue
        if k == "dom_excerpt" and isinstance(v, str):
            out[k] = v[:600] + ("\u2026" if len(v) > 600 else "")
        elif k == "recent_events" and isinstance(v, list):
            out[k] = [
                {
                    "kind": e.get("kind"),
                    "host": e.get("host"),
                    "provider": e.get("provider"),
                    "session_hint": e.get("session_hint"),
                    "ts": e.get("ts"),
                }
                for e in v[-10:]
            ]
        elif k == "prompt" and isinstance(v, str):
            out[k] = v[:200] + ("\u2026" if len(v) > 200 else "")
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------


_RULE = "\u2500" * 72


def render_text(triaged: dict[str, Any]) -> str:
    """Human-readable rendering. Returns a single string ready for
    ``print``.
    """
    lines: list[str] = []
    rep = triaged["report"]
    by_status = triaged["by_status"]
    failures: list[TriagedFailure] = triaged["failures"]

    lines.append("PCE Probe matrix triage")
    lines.append(_RULE)
    lines.append(f"  report:       {rep['path']}")
    lines.append(f"  started_utc:  {rep['started_at_utc']}")
    dur = rep.get("duration_s")
    if isinstance(dur, (int, float)):
        lines.append(f"  duration:     {dur:.1f}s")
    lines.append(f"  n_results:    {rep['n_results']}")
    lines.append(f"  by_status:    {by_status}")

    if not failures:
        lines.append("")
        lines.append("  No failures (FAIL/SKIP) to triage.")
        lines.append(_RULE)
        return "\n".join(lines)

    lines.append("")
    lines.append(f"Triaged failures ({len(failures)}):")

    for f in failures:
        lines.append("")
        lines.append(_RULE)
        head = f"[{f.status.upper()}] {f.cell_id}"
        if f.phase:
            head += f"   phase={f.phase}"
        if f.code:
            head += f"   code={f.code}"
        head += f"   ({f.duration_ms}ms)"
        lines.append(head)
        lines.append(_RULE)
        lines.append(f"  summary:     {f.summary}")
        if f.agent_hint:
            lines.append(f"  agent_hint:  {f.agent_hint}")
        lines.append(f"  edit:        {f.edit_target}")
        lines.append(f"  replay:      {f.replay_cmd}")
        if f.evidence:
            lines.append("  evidence:")
            for k, v in f.evidence.items():
                if isinstance(v, str) and "\n" in v:
                    lines.append(f"    {k}:")
                    for sub in v.splitlines():
                        lines.append(f"      | {sub}")
                elif isinstance(v, list):
                    lines.append(f"    {k}: ({len(v)} items)")
                    for item in v[:5]:
                        lines.append(f"      - {item}")
                    if len(v) > 5:
                        lines.append(f"      ... +{len(v) - 5} more")
                else:
                    lines.append(f"    {k}: {v}")

    lines.append("")
    lines.append(_RULE)
    return "\n".join(lines)


def render_json(triaged: dict[str, Any]) -> str:
    """Machine-readable rendering. Convert dataclasses to dicts."""
    failures = [
        {
            "cell_id": f.cell_id,
            "site_name": f.site_name,
            "case_id": f.case_id,
            "status": f.status,
            "phase": f.phase,
            "code": f.code,
            "summary": f.summary,
            "agent_hint": f.agent_hint,
            "edit_target": f.edit_target,
            "replay_cmd": f.replay_cmd,
            "evidence": f.evidence,
            "duration_ms": f.duration_ms,
        }
        for f in triaged["failures"]
    ]
    out = {
        "report": triaged["report"],
        "by_status": triaged["by_status"],
        "failures": failures,
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_paths(
    summary_arg: str | None,
    reports_arg: str | None,
    sites_arg: str | None,
) -> tuple[Path, Path]:
    """Resolve the (summary.json, sites_dir) pair from CLI args + env.

    If neither is given, walk up from cwd looking for a checkout that
    contains ``tests/e2e_probe/``.
    """
    if summary_arg:
        summary_path = Path(summary_arg).resolve()
        if not summary_path.is_file():
            raise SystemExit(f"summary.json not found: {summary_path}")
    else:
        if reports_arg:
            reports_dir = Path(reports_arg).resolve()
        else:
            root = _walk_up_for_reports(Path.cwd())
            if root is None:
                raise SystemExit(
                    "Could not locate tests/e2e_probe/reports/ from "
                    f"cwd={Path.cwd()}. Pass an explicit summary.json "
                    "or --reports-dir."
                )
            reports_dir = root / _DEFAULT_REPORTS_RELATIVE
        latest = _latest_summary(reports_dir)
        if latest is None:
            raise SystemExit(
                f"No summary.json under {reports_dir}. Run the matrix "
                "first: pytest tests/e2e_probe/test_matrix.py -v"
            )
        summary_path = latest

    if sites_arg:
        sites_dir = Path(sites_arg).resolve()
    else:
        # sites/ lives at <repo>/tests/e2e_probe/sites/. Derive from
        # summary path: <repo>/tests/e2e_probe/reports/<ts>/summary.json.
        sites_dir = (summary_path.parent.parent.parent / "sites").resolve()

    if not sites_dir.is_dir():
        raise SystemExit(
            f"sites dir not found at {sites_dir}; pass --sites-dir."
        )

    return summary_path, sites_dir


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pce_probe.triage",
        description=(
            "Read a PCE Probe matrix summary.json and emit a fix-ready "
            "view per FAIL/SKIP cell."
        ),
    )
    parser.add_argument(
        "summary",
        nargs="?",
        default=None,
        help=(
            "Path to summary.json. If omitted, the latest under "
            "tests/e2e_probe/reports/ is used."
        ),
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="Override the reports root (default: tests/e2e_probe/reports).",
    )
    parser.add_argument(
        "--sites-dir",
        default=None,
        help="Override the site-adapter dir (default: tests/e2e_probe/sites).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    parser.add_argument(
        "--include-skip",
        action="store_true",
        help=(
            "Also surface SKIP cells (default: only FAIL). Useful for "
            "spotting login-wall rows that need manual relogin."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    summary_path, sites_dir = _resolve_paths(
        args.summary, args.reports_dir, args.sites_dir
    )

    triaged = triage_summary(
        summary_path,
        sites_dir=sites_dir,
        include_skip=args.include_skip,
    )

    if args.json:
        print(render_json(triaged))
    else:
        print(render_text(triaged))

    # Exit code: 1 if anything is FAIL (so a CI runner can branch on it).
    by_status = triaged["by_status"] or {}
    return 1 if int(by_status.get("fail", 0)) > 0 else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
