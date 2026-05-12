# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.runner — pytest subprocess wrapper + RunRecord persistence.

Per ADR-017 §3.1, the conductor never executes test code in-process.
It shells out to ``python -m pytest`` with the per-target ``runner.args``
+ a ``-k`` expression scoped by ``case_id``. Each invocation produces
a ``RunRecord`` written to ``pce_test_conductor/runs/<run_id>.json``
that ``get_run`` and ``classify_failure`` can later read back.

Why subprocess and not ``pytest.main``?

- pytest mutates global state (sys.modules, plugin registry); fresh
  subprocess gives clean slate per invocation.
- pytest hooks defer per-test reporting; subprocess + parsing lets the
  conductor stay agnostic to plugin-ecosystem changes.
- Future-proof against pytest 9/10 API churn.
- Matches ADR-017 §3.6: "subprocess `python -m pce_probe matrix ...`".
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .manifest import Target

logger = logging.getLogger("pce.conductor.runner")

#: Default location for run records (alongside this module).
DEFAULT_RUNS_DIR: Path = Path(__file__).parent / "runs"

#: Truncation cap for stdout / stderr capture (avoid 100 MB JSON files).
_MAX_OUTPUT_BYTES: int = 256 * 1024


class RunStatus(str, Enum):
    """High-level run outcome. JSON-serialisable string enum."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    INFRA_ERROR = "infra_error"        # pytest itself couldn't be invoked
    TIMEOUT = "timeout"


@dataclass
class RunRecord:
    """JSON-serialisable record of one ``run_case`` invocation.

    The shape matches ADR-017 §3.2 ``run_case`` return contract +
    extends with ``stdout`` / ``stderr`` / ``cmd`` for ``classify_failure``
    and human triage.
    """

    run_id: str                        # "20260512-143200-browser_chatgpt-T01"
    target_id: str
    case_id: str
    mode: str                          # "live" | "replay"
    status: RunStatus
    started_at: float                  # unix epoch
    elapsed_s: float
    exit_code: int
    cmd: list[str]
    cwd: str
    timeout_s: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=d["run_id"],
            target_id=d["target_id"],
            case_id=d["case_id"],
            mode=d.get("mode", "live"),
            status=RunStatus(d["status"]),
            started_at=float(d["started_at"]),
            elapsed_s=float(d["elapsed_s"]),
            exit_code=int(d["exit_code"]),
            cmd=list(d.get("cmd", [])),
            cwd=str(d.get("cwd", "")),
            timeout_s=int(d.get("timeout_s", 600)),
            stdout=str(d.get("stdout", "")),
            stderr=str(d.get("stderr", "")),
            timed_out=bool(d.get("timed_out", False)),
            evidence=d.get("evidence", {}),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_run_id(target_id: str, case_id: str, *, now: Optional[float] = None) -> str:
    """Stable monotonic run_id: ``YYYYMMDD-HHMMSS-<target>-<case>``."""
    epoch = now if now is not None else time.time()
    ts = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{target_id}-{case_id}"


def build_pytest_command(
    target: Target,
    case_id: str,
    *,
    python_exe: Optional[str] = None,
) -> list[str]:
    """Build the canonical pytest command line for one (target, case_id).

    Form:  ``python -m pytest <runner.args> -k "<marker_filter> and <case_id>" --tb=short -q``

    If ``marker_filter`` is missing (e.g. mcp_filesystem.yaml), the
    ``-k`` expression carries only the case_id; if that evaluates to
    empty the command still runs (pytest treats empty filter as "all
    cases collected").
    """
    py = python_exe or sys.executable
    cmd: list[str] = [py, "-m", "pytest", *target.runner.args]

    # Build -k expression — match by case_id (substring) AND optional marker.
    k_expr = case_id
    if target.runner.marker_filter:
        k_expr = f"{target.runner.marker_filter} and {case_id}"
    cmd.extend(["-k", k_expr])
    cmd.extend(["--tb=short", "-q", "--no-header"])
    return cmd


def run_case(
    target: Target,
    case_id: str,
    *,
    mode: str = "live",
    runs_dir: Optional[Path] = None,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    python_exe: Optional[str] = None,
    persist: bool = True,
) -> RunRecord:
    """Run one case via pytest subprocess and persist the result.

    Args:
        target: parsed manifest from ``manifest.load_manifest``
        case_id: e.g. ``"T01"`` / ``"D03"``
        mode: ``"live"`` (default) or ``"replay"`` — replay support is a
              P5.C.4 deliverable; in P5.C.2 we accept the arg but always
              run live.
        runs_dir: where to persist the JSON record (default DEFAULT_RUNS_DIR)
        cwd: working directory for the subprocess (defaults to repo root)
        env: extra env vars (merged on top of os.environ)
        python_exe: override python interpreter (tests use this to point
                    at sys.executable explicitly)
        persist: write the JSON record to disk; tests sometimes opt out
    """
    if mode not in ("live", "replay"):
        raise ValueError(f"mode must be 'live' or 'replay', got {mode!r}")

    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    cwd_path = cwd or Path(__file__).resolve().parent.parent  # repo root
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    cmd = build_pytest_command(target, case_id, python_exe=python_exe)
    started = time.time()
    run_id = make_run_id(target.target_id, case_id, now=started)

    timeout_s = target.runner.timeout_s
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code = -1
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd_path),
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        exit_code = int(completed.returncode)
        stdout = (completed.stdout or "")[:_MAX_OUTPUT_BYTES]
        stderr = (completed.stderr or "")[:_MAX_OUTPUT_BYTES]
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "")[:_MAX_OUTPUT_BYTES] if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "")[:_MAX_OUTPUT_BYTES] if isinstance(exc.stderr, str) else ""
        exit_code = -2
    except FileNotFoundError as exc:
        # Python interpreter or pytest path bad — INFRA failure.
        stderr = f"runner failed to spawn pytest: {exc!r}"
        exit_code = -3
    except Exception as exc:  # pragma: no cover — defensive
        stderr = f"runner unexpected exception: {exc!r}"
        exit_code = -4

    elapsed = time.time() - started
    status = _status_from_exit(exit_code, stdout, timed_out=timed_out)

    record = RunRecord(
        run_id=run_id,
        target_id=target.target_id,
        case_id=case_id,
        mode=mode,
        status=status,
        started_at=started,
        elapsed_s=elapsed,
        exit_code=exit_code,
        cmd=cmd,
        cwd=str(cwd_path),
        timeout_s=timeout_s,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        evidence={
            "lane": target.lane,
            "tier": target.tier,
            "marker_filter": target.runner.marker_filter,
            "adapter_class": target.adapter_class,
        },
    )

    if persist:
        save_run(record, runs_dir=runs_dir)

    logger.info(
        "run_case completed",
        extra={"event": "conductor.run_case.done", "pce_fields": {
            "run_id": run_id, "status": status.value, "elapsed_s": round(elapsed, 2),
            "exit_code": exit_code, "timed_out": timed_out,
        }},
    )
    return record


def _status_from_exit(exit_code: int, stdout: str, *, timed_out: bool) -> RunStatus:
    """Map pytest exit code to our high-level RunStatus.

    pytest exit codes:
      0 = all tests collected and passed
      1 = some tests failed
      2 = test execution interrupted by user (SIGINT)
      3 = internal error
      4 = pytest command line usage error
      5 = no tests collected
    Plus our own:
     -2 = subprocess.TimeoutExpired
     -3 / -4 = couldn't even spawn
    """
    if timed_out:
        return RunStatus.TIMEOUT
    if exit_code == 0:
        # Distinguish a "passed" outcome from "no tests collected":
        # "no tests" is more usefully reported as SKIP (case_id had no
        # matching test). We detect via stdout's "no tests ran" footer.
        if stdout and "no tests ran" in stdout.lower():
            return RunStatus.SKIP
        return RunStatus.PASS
    if exit_code == 5:
        return RunStatus.SKIP                  # explicit "no tests collected"
    if exit_code in (3, 4, -3, -4):
        return RunStatus.INFRA_ERROR
    return RunStatus.FAIL


# ---------------------------------------------------------------------------
# Persistence — runs/<run_id>.json
# ---------------------------------------------------------------------------

def save_run(record: RunRecord, *, runs_dir: Optional[Path] = None) -> Path:
    """Write a RunRecord to ``runs_dir/<run_id>.json``."""
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{record.run_id}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(record.to_dict(), fh, indent=2, ensure_ascii=False)
    return path


def load_run(run_id: str, *, runs_dir: Optional[Path] = None) -> Optional[RunRecord]:
    """Read a RunRecord by run_id, or ``None`` if absent."""
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    path = runs_dir / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return RunRecord.from_dict(json.load(fh))
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        logger.warning("load_run failed at %s: %r", path, exc)
        return None


def list_recent_runs(
    *,
    target_id: Optional[str] = None,
    case_id: Optional[str] = None,
    limit: int = 50,
    runs_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Return the most recent run summaries (filtered, sorted ts DESC)."""
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    if not runs_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in runs_dir.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if target_id and data.get("target_id") != target_id:
            continue
        if case_id and data.get("case_id") != case_id:
            continue
        out.append(data)
    out.sort(key=lambda d: d.get("started_at", 0), reverse=True)
    return out[: max(1, int(limit))]
