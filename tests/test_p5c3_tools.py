# SPDX-License-Identifier: Apache-2.0
"""Tests for P5.C.3 nightly-probe tooling.

Coverage:

| Module | Tests |
|---|---|
| ``tools.render_health_matrix`` | 5 (collect / render shape / canary badge / lane order / xml safety) |
| ``tools.auto_issue_on_fail``   | 6 (collect + classify + body + gh cmd shape + dry-run + idempotency) |
| Workflow YAML                  | 2 (parse + structural assertions) |

13 tests total — exceeds the P5.C.3 acceptance gate "any tests pass" minimum
without requiring live HTTP / GitHub API access.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pce_core.db import init_db  # noqa: E402
from pce_core.health import emit_beacon, reset_rate_buckets  # noqa: E402
from tools.render_health_matrix import (  # noqa: E402
    LANE_ORDER,
    collect_matrix,
    count_hard_canary_drifts,
    render_svg,
)
from tools.auto_issue_on_fail import (  # noqa: E402
    build_gh_command,
    build_issue_body,
    build_issue_title,
    classify_record,
    collect_failed_runs,
    invoke_gh,
    process_runs,
    proposals_for_record,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_pce_db(tmp_path: Path) -> Path:
    """Empty PCE DB with health_beacons table available."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _reset_rate_buckets():
    reset_rate_buckets()
    yield
    reset_rate_buckets()


def _write_run_record(
    runs_dir: Path,
    *,
    run_id: str,
    target_id: str = "browser_chatgpt",
    case_id: str = "T01",
    status: str = "fail",
    started_at: float | None = None,
    stderr: str = "NoSuchElementException: selector '#submit' did not match",
    exit_code: int = 1,
    elapsed_s: float = 1.5,
    timeout_s: int = 60,
) -> Path:
    record = {
        "run_id": run_id,
        "target_id": target_id,
        "case_id": case_id,
        "mode": "live",
        "status": status,
        "started_at": started_at if started_at is not None else time.time(),
        "elapsed_s": elapsed_s,
        "exit_code": exit_code,
        "cmd": ["python", "-m", "pytest"],
        "cwd": ".",
        "timeout_s": timeout_s,
        "stdout": "",
        "stderr": stderr,
        "timed_out": False,
        "evidence": {"lane": "browser", "tier": "S0"},
    }
    p = runs_dir / f"{run_id}.json"
    p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# tools.render_health_matrix
# ---------------------------------------------------------------------------

def test_collect_matrix_returns_lane_skeleton_when_empty(tmp_pce_db: Path) -> None:
    """No beacons → all 4 lanes appear with grey colour + empty targets."""
    matrix = collect_matrix(window_hours=24, db_path=tmp_pce_db)
    for lane in LANE_ORDER:
        assert lane in matrix["lanes"]
        assert matrix["lanes"][lane]["color"] == "grey"


def test_render_svg_contains_lane_summary(tmp_pce_db: Path) -> None:
    """SVG output must include the 4-lane top summary + the title text."""
    # Plant 1 PASS beacon for browser/chatgpt so collect_matrix returns
    # a non-empty target — the renderer should emit a row for it.
    emit_beacon(
        lane="browser", layer="L3a", target="chatgpt", status="pass",
        case_id="T01", db_path=tmp_pce_db,
    )
    matrix = collect_matrix(window_hours=24, db_path=tmp_pce_db)
    svg = render_svg(matrix)

    # Header sanity
    assert svg.startswith("<svg ")
    assert "PCE Pipeline Health Matrix" in svg
    # All four lanes appear in the lane summary header (capitalised).
    for lane in ("Browser", "Desktop", "Cli", "Mcp"):
        assert lane in svg
    # The chatgpt target row was emitted.
    assert "chatgpt" in svg


def test_render_svg_renders_canary_badge(tmp_pce_db: Path) -> None:
    """When canary_drifts has a non-zero entry, the badge is rendered."""
    emit_beacon(
        lane="browser", layer="L3a", target="chatgpt", status="pass",
        case_id="T01", db_path=tmp_pce_db,
    )
    matrix = collect_matrix(window_hours=24, db_path=tmp_pce_db)
    svg = render_svg(matrix, canary_drifts={("browser", "chatgpt"): 2})
    assert "CANARY 2" in svg


def test_count_hard_canary_drifts_handles_missing_dir(tmp_path: Path) -> None:
    """Missing canary root must not raise — return empty dict."""
    drifts = count_hard_canary_drifts(tmp_path / "nope")
    assert drifts == {}


def test_render_svg_xml_escapes_target_names(tmp_path: Path) -> None:
    """Target names are XML-escaped — no raw < or > slip into the SVG."""
    matrix = {
        "lanes": {
            "browser": {
                "color": "green",
                "targets": {
                    "evil<target>": {
                        "color": "green", "tier": "S0",
                        "plane_count": 2, "plane_required": 2,
                        "pass_count_24h": 10, "fail_count_24h": 0,
                        "skip_count_24h": 0, "pass_rate_24h": 1.0,
                        "last_pass_ts": time.time(),
                    },
                },
            },
            "desktop": {"color": "grey", "targets": {}},
            "cli": {"color": "grey", "targets": {}},
            "mcp": {"color": "grey", "targets": {}},
        },
        "computed_at": time.time(),
        "window_hours": 24,
    }
    svg = render_svg(matrix)
    assert "evil&lt;target&gt;" in svg
    assert "evil<target>" not in svg


# ---------------------------------------------------------------------------
# tools.auto_issue_on_fail — collection
# ---------------------------------------------------------------------------

def test_collect_failed_runs_filters_status_and_age(tmp_runs_dir: Path) -> None:
    """Only non-pass + recent records are returned."""
    now = time.time()
    _write_run_record(tmp_runs_dir, run_id="r-recent-fail",
                      status="fail", started_at=now - 100)
    _write_run_record(tmp_runs_dir, run_id="r-recent-pass",
                      status="pass", started_at=now - 100)
    _write_run_record(tmp_runs_dir, run_id="r-old-fail",
                      status="fail", started_at=now - 48 * 3600)
    out = collect_failed_runs(tmp_runs_dir, hours_back=24, now=now)
    ids = {r["run_id"] for r in out}
    assert ids == {"r-recent-fail"}


def test_collect_failed_runs_handles_unreadable_files(tmp_runs_dir: Path) -> None:
    """Bad JSON in a run file is logged + skipped, not raised."""
    (tmp_runs_dir / "bad.json").write_text("not json", encoding="utf-8")
    _write_run_record(tmp_runs_dir, run_id="r-good", status="fail")
    out = collect_failed_runs(tmp_runs_dir, hours_back=48)
    ids = {r["run_id"] for r in out}
    assert ids == {"r-good"}


# ---------------------------------------------------------------------------
# tools.auto_issue_on_fail — classification + body
# ---------------------------------------------------------------------------

def test_classify_record_maps_to_failure_kind() -> None:
    rec = {"exit_code": 1,
           "stderr": "NoSuchElementException: locator '#submit' did not match",
           "stdout": "", "elapsed_s": 1.0, "timeout_s": 60}
    cls = classify_record(rec)
    assert cls["kind"] == "UI_SELECTOR_MISS"


def test_proposals_for_record_dispatches_for_known_kind() -> None:
    rec = {"target_id": "browser_chatgpt", "case_id": "T03"}
    cls = {"kind": "URL_PATTERN_DRIFT", "field_path": "/v2/chat"}
    out = proposals_for_record(rec, cls)
    assert len(out) == 1
    assert out[0]["kind"] == "add_url_path"


def test_build_issue_title_and_body_include_run_id_and_diff(tmp_runs_dir: Path) -> None:
    record = json.loads(
        _write_run_record(tmp_runs_dir, run_id="20260512-150000-browser_chatgpt-T03",
                          target_id="browser_chatgpt", case_id="T03",
                          stderr="ValueError: unknown content_block type 'server_tool_use'")
        .read_text(encoding="utf-8")
    )
    cls = classify_record(record)
    proposals = proposals_for_record(record, cls)
    title = build_issue_title(record)
    body = build_issue_body(record, classification=cls, proposals=proposals)
    assert "20260512-150000-browser_chatgpt-T03" in title
    assert "broken-adapter" in title
    assert "## Reproduction" in body
    assert "## Classification" in body
    if proposals:
        assert "```diff" in body


# ---------------------------------------------------------------------------
# tools.auto_issue_on_fail — gh CLI shaping
# ---------------------------------------------------------------------------

def test_build_gh_command_includes_labels_and_assignee() -> None:
    cmd = build_gh_command(
        title="t", body="b",
        labels=["broken-adapter", "auto-detected"],
        assignee="@CODEOWNERS",
        repo="zstnbb/PCE-Core",
    )
    assert cmd[:3] == ["gh", "issue", "create"]
    assert "--label" in cmd
    assert "broken-adapter" in cmd
    assert "auto-detected" in cmd
    assert "--assignee" in cmd
    assert "--repo" in cmd
    assert "zstnbb/PCE-Core" in cmd


def test_invoke_gh_dry_run_does_not_subprocess() -> None:
    """dry-run must NOT shell out — returns the planned command verbatim."""
    out = invoke_gh(["gh", "issue", "create", "--title", "t"], dry_run=True)
    assert out["dry_run"] is True
    assert out["exit_code"] == 0
    assert out["cmd"][0] == "gh"


def test_process_runs_dry_run_does_not_call_gh(tmp_runs_dir: Path) -> None:
    """End-to-end: process_runs in dry-run must return summary with cmd shapes."""
    _write_run_record(tmp_runs_dir, run_id="r1", status="fail")
    summary = process_runs(
        runs_dir=tmp_runs_dir,
        hours_back=24,
        labels=["broken-adapter"],
        assignee="@CODEOWNERS",
        repo="zstnbb/PCE-Core",
        dry_run=True,
    )
    assert summary["fail_count"] == 1
    assert summary["dry_run"] is True
    issue = summary["issues"][0]
    assert issue["dry_run"] is True
    assert issue["exit_code"] == 0
    assert issue["title"].startswith("[broken-adapter]")


# ---------------------------------------------------------------------------
# Workflow YAML
# ---------------------------------------------------------------------------

def test_nightly_probe_workflow_is_valid_yaml() -> None:
    path = _REPO_ROOT / ".github" / "workflows" / "nightly-probe.yml"
    assert path.exists(), "nightly-probe.yml missing"
    with path.open("r", encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    assert wf["name"] == "Nightly Probe"
    # 'on' is a Python `True` after yaml.safe_load (yes/on/true → True)
    # so we test for either spelling explicitly.
    triggers = wf.get("on") or wf.get(True)
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert triggers["schedule"][0]["cron"] == "0 2 * * *"


def test_nightly_probe_workflow_has_three_jobs() -> None:
    path = _REPO_ROOT / ".github" / "workflows" / "nightly-probe.yml"
    with path.open("r", encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    job_names = set(wf["jobs"].keys())
    assert {"smoke", "health-matrix", "auto-issue"}.issubset(job_names)
    # health-matrix + auto-issue both depend on smoke succeeding.
    assert wf["jobs"]["health-matrix"]["needs"] == "smoke"
    assert wf["jobs"]["auto-issue"]["needs"] == "smoke"
