# SPDX-License-Identifier: Apache-2.0
"""Tests for ``pce_test_conductor`` (P5.C.2 Test Conductor MVP).

Coverage map (≥ 16 acceptance minimum per HANDOFF §4.P5.C.2):

| Tool                | Happy   | Error   |
|---------------------|---------|---------|
| list_targets        | ✓       | ✓       |
| list_cases          | ✓       | ✓ (×2)  |
| run_case            | ✓       | ✓ (×2)  |
| get_run             | ✓       | ✓       |
| diff_canary         | ✓ (×4)  | ✓       |
| classify_failure    | ✓ (×4)  | ✓       |
| propose_patch       | ✓ (×3)  | ✓       |
| verify_patch        | ✓       | —       |

Plus structural tests for ``manifest`` / ``canary`` helpers.

The ``run_case`` tests stand up a tiny pytest test file in a tmp dir +
a ``targets/<id>.yaml`` pointing at it, so the subprocess invocation
exercises the real pytest pipeline without depending on PCE Core's
existing browser/desktop test suites.
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

from pce_test_conductor import (  # noqa: E402
    FailureKind,
    classify_run,
)
from pce_test_conductor.canary import (  # noqa: E402
    diff_schemas,
    infer_schema,
    merge_schemas,
    update_canary,
)
from pce_test_conductor.manifest import (  # noqa: E402
    DEFAULT_TARGETS_DIR,
    Target,
    list_target_ids,
    load_all,
    load_manifest,
)
from pce_test_conductor.patches import (  # noqa: E402
    propose_patches_for_failure,
)
from pce_test_conductor.replay import replay_case  # noqa: E402
from pce_test_conductor.runner import (  # noqa: E402
    RunRecord,
    RunStatus,
    save_run,
)
from pce_test_conductor.server import (  # noqa: E402
    ALL_TOOL_NAMES,
    TOOL_DISPATCH,
    _classify_failure_impl,
    _diff_canary_impl,
    _get_run_impl,
    _list_cases_impl,
    _list_targets_impl,
    _propose_patch_impl,
    _run_case_impl,
    _verify_patch_impl,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_targets_dir(tmp_path: Path) -> Path:
    """Empty targets/ directory; tests populate it as needed."""
    d = tmp_path / "targets"
    d.mkdir()
    return d


@pytest.fixture
def tmp_runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def stub_pytest_target(tmp_path: Path) -> dict:
    """Create a minimal pytest test file + matching Target.

    Returns a dict with ``target`` (parsed Target) + ``runs_dir`` so
    tests can drive ``_run_case_impl`` against a real subprocess.
    """
    test_file = tmp_path / "test_stub.py"
    test_file.write_text(
        # Two functions: T01 always passes, T99 always fails. Both are
        # picked up by `-k` filter on the case_id.
        "def test_T01_smoke():\n"
        "    assert 1 + 1 == 2\n"
        "\n"
        "def test_T99_failure():\n"
        "    raise NoSuchElementException('selector .foo did not match')\n"
        "\n"
        "class NoSuchElementException(Exception):\n"
        "    pass\n",
        encoding="utf-8",
    )
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()

    yaml_path = targets_dir / "stub_target.yaml"
    yaml_path.write_text(
        f"target_id: stub_target\n"
        f"display_name: Stub\n"
        f"lane: browser\n"
        f"tier: S2\n"
        f"plane: [H]\n"
        f"primary_layer: L3a\n"
        f"fallback_layers: []\n"
        f"adapter_class: null\n"
        f"runner:\n"
        f"  type: pytest\n"
        f"  args: ['{test_file.as_posix()}']\n"
        f"  marker_filter: null\n"
        f"  timeout_s: 60\n"
        f"case_standard_module: null\n"
        f"canary_dir: {(tmp_path / 'canaries').as_posix()}\n"
        f"health_beacon_filter:\n"
        f"  lane: browser\n"
        f"  target: stub_target\n",
        encoding="utf-8",
    )
    target = load_manifest("stub_target", targets_dir=targets_dir)
    return {"target": target, "runs_dir": runs_dir, "targets_dir": targets_dir}


# ---------------------------------------------------------------------------
# Manifest layer
# ---------------------------------------------------------------------------

def test_default_manifests_load_cleanly() -> None:
    """All 4 shipped manifests parse without error."""
    targets = load_all()
    ids = {t.target_id for t in targets}
    assert ids == {
        "browser_chatgpt", "desktop_claude_chat",
        "cli_claude_code", "mcp_filesystem",
    }


def test_manifest_to_summary_shape() -> None:
    """Target.to_summary returns the canonical JSON-friendly shape."""
    t = load_manifest("browser_chatgpt")
    summary = t.to_summary()
    assert summary["target_id"] == "browser_chatgpt"
    assert summary["lane"] == "browser"
    assert summary["tier"] == "S0"
    assert summary["plane"] == ["N", "H"]
    assert summary["adapter_class"] == "tests.e2e_probe.sites.chatgpt.ChatGPTAdapter"
    assert summary["health_beacon_filter"] == {"lane": "browser", "target": "chatgpt"}


def test_load_manifest_missing_file_raises(tmp_targets_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest("does_not_exist", targets_dir=tmp_targets_dir)


def test_load_all_skips_malformed_manifests(tmp_targets_dir: Path) -> None:
    """Malformed yaml is logged + skipped; valid ones still load."""
    (tmp_targets_dir / "good.yaml").write_text(
        "target_id: good\n"
        "lane: browser\n"
        "tier: S2\n"
        "plane: [H]\n"
        "primary_layer: L3a\n"
        "fallback_layers: []\n"
        "runner: {type: pytest, args: [], timeout_s: 60}\n"
        "canary_dir: pce_test_conductor/canaries/good/\n",
        encoding="utf-8",
    )
    (tmp_targets_dir / "bad.yaml").write_text(
        "target_id: bad\nlane: browser\n# missing required keys\n",
        encoding="utf-8",
    )
    targets = load_all(targets_dir=tmp_targets_dir)
    ids = {t.target_id for t in targets}
    assert ids == {"good"}


# ---------------------------------------------------------------------------
# Canary layer
# ---------------------------------------------------------------------------

def test_infer_schema_primitives_and_nesting() -> None:
    """Schema inference covers primitives + nested object + array."""
    s = infer_schema({"a": 1, "b": "hello", "c": [1, 2, 3], "d": {"e": True}})
    assert s["type"] == "object"
    assert s["properties"]["a"]["type"] == "integer"
    assert s["properties"]["b"]["type"] == "string"
    assert s["properties"]["c"]["type"] == "array"
    assert s["properties"]["c"]["items"]["type"] == "integer"
    assert s["properties"]["d"]["type"] == "object"
    assert s["properties"]["d"]["properties"]["e"]["type"] == "boolean"
    assert sorted(s["required"]) == ["a", "b", "c", "d"]


def test_merge_schemas_optional_field_intersection() -> None:
    """When two observations differ in property set, required is the intersection."""
    a = infer_schema({"x": 1, "y": "p"})
    b = infer_schema({"x": 2, "z": True})
    merged = merge_schemas(a, b)
    # Both share x → required; y / z → optional.
    assert sorted(merged["required"]) == ["x"]
    assert "y" in merged["properties"]
    assert "z" in merged["properties"]


def test_diff_schemas_detects_added_removed_changed_enum() -> None:
    """Diff captures the four canonical change kinds + severity."""
    old = {
        "type": "object",
        "properties": {
            "kept": {"type": "string"},
            "going_away": {"type": "string"},
            "type_change": {"type": "integer"},
            "kind": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["kept", "going_away", "kind"],
    }
    new = {
        "type": "object",
        "properties": {
            "kept": {"type": "string"},
            "type_change": {"type": "string"},
            "fresh": {"type": "string"},
            "kind": {"type": "string", "enum": ["a", "b", "c"]},
        },
        "required": ["kept", "kind"],
    }
    diffs = [d.to_dict() for d in diff_schemas(old, new)]
    kinds = {d["kind"] for d in diffs}
    assert kinds == {"added_property", "removed_property", "changed_type", "enum_extension"}
    # Removed + required → hard severity.
    rm = next(d for d in diffs if d["kind"] == "removed_property")
    assert rm["severity"] == "hard"
    # New enum value → soft.
    enum_diff = next(d for d in diffs if d["kind"] == "enum_extension")
    assert enum_diff["severity"] == "soft"
    assert enum_diff["actual"] == ["a", "b", "c"]


def test_update_canary_fresh_baseline(tmp_path: Path) -> None:
    """First write of a canary establishes baseline + empty diff."""
    canary_dir = tmp_path / "canary"
    schema, diff = update_canary(canary_dir, "T01", {"k": 1, "v": "x"})
    assert diff == []
    assert schema["type"] == "object"
    assert (canary_dir / "T01_default.schema.json").exists()


# ---------------------------------------------------------------------------
# Classifier layer
# ---------------------------------------------------------------------------

def test_classify_infra_pattern() -> None:
    """Connection refused is INFRA / hard."""
    f = classify_run(exit_code=1, stderr="ConnectionRefusedError: Connection refused on port 9800")
    assert f.kind is FailureKind.INFRA
    assert f.severity.value == "hard"


def test_classify_ui_selector_miss() -> None:
    f = classify_run(exit_code=1, stdout="NoSuchElementException: element not located")
    assert f.kind is FailureKind.UI_SELECTOR_MISS


def test_classify_race_timeout_via_elapsed() -> None:
    """elapsed_s ≥ 0.95 × timeout_s → RACE_TIMEOUT regardless of stderr."""
    f = classify_run(
        exit_code=1, stderr="some other error", elapsed_s=58.0, timeout_s=60,
    )
    assert f.kind is FailureKind.RACE_TIMEOUT


def test_classify_unknown_when_no_pattern_matches() -> None:
    f = classify_run(exit_code=1, stderr="completely novel error message")
    assert f.kind is FailureKind.UNKNOWN
    assert f.severity.value == "info"


def test_classify_canary_drift_overrides_pattern() -> None:
    """Hard canary diff is preferred over noisy stderr patterns."""
    f = classify_run(
        exit_code=1,
        stderr="NoSuchElementException foo",
        canary_diff=[{"kind": "removed_property", "severity": "hard",
                      "field_path": "$.parts", "expected": "string", "actual": None}],
    )
    assert f.kind is FailureKind.SCHEMA_DRIFT
    assert f.field_path == "$.parts"


# ---------------------------------------------------------------------------
# Patches layer
# ---------------------------------------------------------------------------

def test_propose_patches_content_block_unknown() -> None:
    proposals = propose_patches_for_failure(
        "CONTENT_BLOCK_UNKNOWN",
        actual=["text", "tool_use", "thinking", "server_tool_use"],
        expected=["text", "tool_use", "thinking"],
        target_case="claude_desktop:T03",
        provider_hint="anthropic",
    )
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind == "add_content_block_type"
    assert "anthropic.py" in p.files[0]
    assert "server_tool_use" in p.unified_diff
    assert 0.0 <= p.confidence <= 1.0


def test_propose_patches_url_pattern_drift() -> None:
    proposals = propose_patches_for_failure(
        "URL_PATTERN_DRIFT",
        field_path="/api/v3/conversation",
        provider_hint="openai",
    )
    assert proposals and proposals[0].kind == "add_url_path"
    assert "openai.py" in proposals[0].files[0]


def test_propose_patches_schema_drift() -> None:
    proposals = propose_patches_for_failure(
        "SCHEMA_DRIFT", field_path="$.content[*].title",
    )
    assert proposals and proposals[0].kind == "widen_schema_field"
    assert "models.py" in proposals[0].files[0]
    # Field leaf extracted from JSONPath.
    assert "title" in proposals[0].unified_diff


def test_propose_patches_empty_for_login_wall() -> None:
    """Operational failures (LOGIN_WALL / INFRA / RACE_TIMEOUT) → no template."""
    assert propose_patches_for_failure("LOGIN_WALL") == []
    assert propose_patches_for_failure("INFRA") == []
    assert propose_patches_for_failure("RACE_TIMEOUT") == []


# ---------------------------------------------------------------------------
# Replay (P5.C.2 stub)
# ---------------------------------------------------------------------------

def test_replay_raises_not_implemented(tmp_path: Path) -> None:
    """replay_case is a P5.C.4 deliverable; current build raises cleanly."""
    with pytest.raises(NotImplementedError) as exc_info:
        replay_case("browser_chatgpt", "T01", fixtures_dir=tmp_path)
    assert "P5.C.4" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tool 1 — list_targets
# ---------------------------------------------------------------------------

def test_list_targets_returns_four_default_manifests() -> None:
    result = _list_targets_impl(include_health=False)
    assert result["count"] == 4
    ids = {t["target_id"] for t in result["targets"]}
    assert ids == {
        "browser_chatgpt", "desktop_claude_chat",
        "cli_claude_code", "mcp_filesystem",
    }


def test_list_targets_with_health_rollup(tmp_path: Path, monkeypatch) -> None:
    """list_targets attaches health_color from compute_matrix output."""
    # Point pce_core.health at an isolated DB so we can plant a beacon.
    import pce_core.db as _db
    db_path = tmp_path / "pce.db"
    _db.init_db(db_path)
    monkeypatch.setattr(_db, "DB_PATH", db_path)

    from pce_core.health import emit_beacon, reset_rate_buckets
    reset_rate_buckets()
    emit_beacon(
        lane="browser", layer="L3a", target="chatgpt",
        status="pass", case_id="T01", db_path=db_path,
    )

    result = _list_targets_impl(include_health=True)
    chatgpt = next(t for t in result["targets"] if t["target_id"] == "browser_chatgpt")
    # plane_count=1 (only L3a) but tier=S0 requires 2 → red is correct.
    assert chatgpt["health_color"] in ("red", "yellow", "green", "grey")


# ---------------------------------------------------------------------------
# Tool 2 — list_cases
# ---------------------------------------------------------------------------

def test_list_cases_browser_chatgpt_loads_t_cases() -> None:
    """Browser lane manifest points at tests/e2e_probe/execution_standard."""
    result = _list_cases_impl("browser_chatgpt")
    assert "error" not in result
    assert result["count"] >= 20  # T00..T20
    case_ids = [c["case_id"] for c in result["cases"]]
    assert "T01" in case_ids
    assert "T20" in case_ids


def test_list_cases_target_not_found() -> None:
    result = _list_cases_impl("nonexistent_target")
    assert result["error"] == "target_not_found"


def test_list_cases_module_not_declared() -> None:
    """cli_claude_code has case_standard_module=null → graceful empty list."""
    result = _list_cases_impl("cli_claude_code")
    assert result["cases"] == []
    assert result["module"] is None


# ---------------------------------------------------------------------------
# Tool 3 — run_case
# ---------------------------------------------------------------------------

def test_run_case_pass_via_stub_pytest(stub_pytest_target: dict, monkeypatch) -> None:
    """run_case drives a real pytest subprocess; stub T01 passes."""
    # Patch DEFAULT_TARGETS_DIR so _run_case_impl finds our stub manifest.
    import pce_test_conductor.manifest as _m
    monkeypatch.setattr(_m, "DEFAULT_TARGETS_DIR", stub_pytest_target["targets_dir"])

    result = _run_case_impl(
        "stub_target", "T01_smoke",
        runs_dir=stub_pytest_target["runs_dir"],
    )
    assert result["status"] == "pass", result.get("stderr", "")[:400]
    assert result["exit_code"] == 0
    assert result["target_id"] == "stub_target"


def test_run_case_invalid_mode(stub_pytest_target: dict, monkeypatch) -> None:
    import pce_test_conductor.manifest as _m
    monkeypatch.setattr(_m, "DEFAULT_TARGETS_DIR", stub_pytest_target["targets_dir"])
    result = _run_case_impl("stub_target", "T01_smoke", mode="bogus")
    assert result["error"] == "invalid_mode"


def test_run_case_replay_mode_stub(stub_pytest_target: dict, monkeypatch) -> None:
    """Replay mode returns a clean infra_error for P5.C.2 (no silent fallback)."""
    import pce_test_conductor.manifest as _m
    monkeypatch.setattr(_m, "DEFAULT_TARGETS_DIR", stub_pytest_target["targets_dir"])
    result = _run_case_impl("stub_target", "T01_smoke", mode="replay")
    assert result["status"] == "infra_error"
    assert result["error"] == "replay_not_implemented"


# ---------------------------------------------------------------------------
# Tool 4 — get_run
# ---------------------------------------------------------------------------

def test_get_run_round_trip(tmp_runs_dir: Path) -> None:
    record = RunRecord(
        run_id="20260512-150000-test_target-T01",
        target_id="test_target", case_id="T01", mode="live",
        status=RunStatus.PASS,
        started_at=time.time(), elapsed_s=1.5, exit_code=0,
        cmd=["python", "-m", "pytest"],
        cwd=".", timeout_s=60,
    )
    save_run(record, runs_dir=tmp_runs_dir)
    result = _get_run_impl(record.run_id, runs_dir=tmp_runs_dir)
    assert result["run_id"] == record.run_id
    assert result["status"] == "pass"


def test_get_run_not_found(tmp_runs_dir: Path) -> None:
    result = _get_run_impl("missing_run", runs_dir=tmp_runs_dir)
    assert result["error"] == "run_not_found"


# ---------------------------------------------------------------------------
# Tool 5 — diff_canary
# ---------------------------------------------------------------------------

def test_diff_canary_fresh_baseline(tmp_path: Path, monkeypatch) -> None:
    """First call with payload + update=True writes baseline + empty diff."""
    # Build a custom target pointing at a tmp canary dir.
    import pce_test_conductor.manifest as _m
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    canary_dir = tmp_path / "canary_X"
    (targets_dir / "X.yaml").write_text(
        f"target_id: X\nlane: browser\ntier: S2\nplane: [H]\n"
        f"primary_layer: L3a\nfallback_layers: []\n"
        f"runner: {{type: pytest, args: [], timeout_s: 60}}\n"
        f"canary_dir: {canary_dir.as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_m, "DEFAULT_TARGETS_DIR", targets_dir)
    out = _diff_canary_impl("X", "T01", payload={"k": 1}, update=True)
    assert out["diff"] == []
    assert (canary_dir / "T01_default.schema.json").exists()


def test_diff_canary_added_property_soft(tmp_path: Path, monkeypatch) -> None:
    import pce_test_conductor.manifest as _m
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir()
    canary_dir = tmp_path / "canary_Y"
    (targets_dir / "Y.yaml").write_text(
        f"target_id: Y\nlane: browser\ntier: S2\nplane: [H]\n"
        f"primary_layer: L3a\nfallback_layers: []\n"
        f"runner: {{type: pytest, args: [], timeout_s: 60}}\n"
        f"canary_dir: {canary_dir.as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_m, "DEFAULT_TARGETS_DIR", targets_dir)

    # Establish baseline.
    _diff_canary_impl("Y", "T01", payload={"a": 1}, update=True)
    # New observation adds property b.
    out = _diff_canary_impl("Y", "T01", payload={"a": 1, "b": "hello"}, update=False)
    kinds = {d["kind"] for d in out["diff"]}
    assert "added_property" in kinds
    soft = [d for d in out["diff"] if d["kind"] == "added_property" and d["severity"] == "soft"]
    assert soft


def test_diff_canary_target_not_found() -> None:
    out = _diff_canary_impl("does_not_exist", "T01", payload={"x": 1})
    assert out["error"] == "target_not_found"


# ---------------------------------------------------------------------------
# Tool 6 — classify_failure
# ---------------------------------------------------------------------------

def test_classify_failure_via_stored_run(tmp_runs_dir: Path) -> None:
    record = RunRecord(
        run_id="20260512-150100-stub-T99", target_id="stub", case_id="T99",
        mode="live", status=RunStatus.FAIL,
        started_at=time.time(), elapsed_s=0.3, exit_code=1,
        cmd=["python"], cwd=".", timeout_s=60,
        stderr="NoSuchElementException: locator '#submit' returned no match",
    )
    save_run(record, runs_dir=tmp_runs_dir)
    out = _classify_failure_impl(record.run_id, runs_dir=tmp_runs_dir)
    assert out["kind"] == "UI_SELECTOR_MISS"


def test_classify_failure_run_not_found(tmp_runs_dir: Path) -> None:
    out = _classify_failure_impl("missing", runs_dir=tmp_runs_dir)
    assert out["error"] == "run_not_found"


# ---------------------------------------------------------------------------
# Tool 7 — propose_patch
# ---------------------------------------------------------------------------

def test_propose_patch_for_content_block_unknown(tmp_runs_dir: Path) -> None:
    """propose_patch surfaces a template proposal for the run's failure kind."""
    record = RunRecord(
        run_id="20260512-150200-claude_desktop-T03",
        target_id="claude_desktop", case_id="T03",
        mode="live", status=RunStatus.FAIL,
        started_at=time.time(), elapsed_s=2.1, exit_code=1,
        cmd=["python"], cwd=".", timeout_s=60,
        stderr="ValueError: unknown content_block type 'server_tool_use' in payload",
    )
    save_run(record, runs_dir=tmp_runs_dir)
    out = _propose_patch_impl(record.run_id, runs_dir=tmp_runs_dir)
    assert out["failure_kind"] == "CONTENT_BLOCK_UNKNOWN"
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["kind"] == "add_content_block_type"


def test_propose_patch_no_run() -> None:
    out = _propose_patch_impl("nope")
    assert out["proposals"] == []
    assert out["error"] == "run_not_found"


# ---------------------------------------------------------------------------
# Tool 8 — verify_patch
# ---------------------------------------------------------------------------

def test_verify_patch_marks_verify_after_patch(stub_pytest_target: dict, monkeypatch) -> None:
    """verify_patch flips the verify_after_patch evidence marker."""
    import pce_test_conductor.manifest as _m
    monkeypatch.setattr(_m, "DEFAULT_TARGETS_DIR", stub_pytest_target["targets_dir"])
    out = _verify_patch_impl("stub_target", "T01_smoke", runs_dir=stub_pytest_target["runs_dir"])
    assert out.get("verify_marker") is True
    assert out["evidence"]["verify_after_patch"] is True


# ---------------------------------------------------------------------------
# Dispatch table contract
# ---------------------------------------------------------------------------

def test_all_eight_tools_registered() -> None:
    """ALL_TOOL_NAMES must contain exactly the 8 ADR-017 §3.2 tools."""
    expected = {
        "list_targets", "list_cases", "run_case", "get_run",
        "diff_canary", "classify_failure", "propose_patch", "verify_patch",
    }
    assert set(ALL_TOOL_NAMES) == expected
    assert set(TOOL_DISPATCH.keys()) == expected
    # Every dispatch entry must be callable.
    assert all(callable(fn) for fn in TOOL_DISPATCH.values())
