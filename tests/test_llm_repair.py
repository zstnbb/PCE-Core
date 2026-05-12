# SPDX-License-Identifier: Apache-2.0
"""Tests for P5.C.4.3 LLM-refined selector repair.

Coverage:

| Module / behaviour | Tests |
|---|---|
| `select_provider_for_target` heuristic | 4 |
| `build_repair_prompt` shape + truncation | 2 |
| `_call_mock` deterministic output | 1 |
| `parse_repair_response` well-formed + drift | 3 |
| `_extract_text_from_provider_response` for both providers | 2 |
| `repair_selector` dry-run path | 1 |
| `repair_selector` real path (monkeypatched _post_json) | 2 |
| `repair_selector` HTTP error fallback | 1 |
| `repair_selector` missing API key fallback | 1 |
| `widen_yaml_selectors` template | 2 |
| Dispatcher `UI_SELECTOR_MISS` → YAML diff | 1 |
| `tools.repair_adapter` `find_latest_failed_run` | 2 |
| `tools.repair_adapter` `derive_yaml_path` | 2 |
| `tools.repair_adapter.main` CLI paths | 3 |

27 tests total — covers the P5.C.4.3 acceptance gate without any
real network call (every provider invocation is mocked).
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pce_test_conductor import llm_repair  # noqa: E402
from pce_test_conductor.llm_repair import (  # noqa: E402
    ANTHROPIC_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    LLMProvider,
    LLMRepairResult,
    _call_mock,
    _extract_text_from_provider_response,
    build_repair_prompt,
    parse_repair_response,
    repair_selector,
    select_provider_for_target,
)
from pce_test_conductor.patches import (  # noqa: E402
    propose_patches_for_failure,
    widen_yaml_selectors,
)
from tools import repair_adapter  # noqa: E402


# ---------------------------------------------------------------------------
# Provider selection heuristic
# ---------------------------------------------------------------------------

def test_select_provider_browser_chatgpt_is_openai() -> None:
    assert select_provider_for_target("browser_chatgpt") == LLMProvider.OPENAI


def test_select_provider_browser_claude_is_anthropic() -> None:
    assert select_provider_for_target("desktop_claude_chat") == LLMProvider.ANTHROPIC


def test_select_provider_gemini_is_mock() -> None:
    """Gemini has no LLM-side provider wired — fall back to mock."""
    assert select_provider_for_target("browser_gemini") == LLMProvider.MOCK


def test_select_provider_ambiguous_target_is_mock() -> None:
    """Ambiguous target IDs default to MOCK so we never accidentally bill an API."""
    assert select_provider_for_target("mcp_filesystem") == LLMProvider.MOCK
    assert select_provider_for_target("unknown_thing") == LLMProvider.MOCK


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def test_build_repair_prompt_contains_all_fields() -> None:
    out = build_repair_prompt(
        target_id="browser_chatgpt",
        case_id="T01",
        failure_kind="UI_SELECTOR_MISS",
        failure_hint="selector did not match",
        stderr_excerpt="NoSuchElementException: '#prompt-textarea'",
        adapter_yaml_text="schema_version: 1\nname: chatgpt\n",
    )
    assert "browser_chatgpt" in out
    assert "T01" in out
    assert "UI_SELECTOR_MISS" in out
    assert "selector did not match" in out
    assert "#prompt-textarea" in out
    assert "schema_version: 1" in out


def test_build_repair_prompt_truncates_long_stderr() -> None:
    """Tail of stderr must be ≤ 1500 chars (caller's slice)."""
    huge = "X" * 9000 + "TAIL_MARKER"
    out = build_repair_prompt(
        target_id="t", case_id=None, failure_kind="X",
        failure_hint="", stderr_excerpt=huge,
        adapter_yaml_text="",
    )
    assert "TAIL_MARKER" in out
    # 9000+ char input → ≤ 1500 char excerpt; prompt scaffold adds ~400-ish
    # so total should stay under ~2200 chars.
    assert len(out) < 2500


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

def test_call_mock_is_deterministic() -> None:
    status, body = _call_mock(prompt="anything", target_id="browser_chatgpt")
    assert status == 200
    assert "RATIONALE:" in body
    assert "CONFIDENCE:" in body
    assert "```diff" in body
    # Second call returns identical output for the same target_id.
    status2, body2 = _call_mock(prompt="something else", target_id="browser_chatgpt")
    assert body == body2


# ---------------------------------------------------------------------------
# parse_repair_response
# ---------------------------------------------------------------------------

WELL_FORMED_RESPONSE = """
RATIONALE: The composer input selector drifted; adding a fallback is safe.
CONFIDENCE: 0.78
DIFF:
```diff
--- a/pce_core/adapters/chatgpt.yaml
+++ b/pce_core/adapters/chatgpt.yaml
@@ selectors.input @@
   input:
     - '#prompt-textarea'
+    - '[data-testid="composer-text-input"]'
```
"""


def test_parse_repair_response_well_formed() -> None:
    diff, conf, rationale = parse_repair_response(WELL_FORMED_RESPONSE)
    assert "composer-text-input" in diff
    assert abs(conf - 0.78) < 1e-6
    assert "drifted" in rationale


def test_parse_repair_response_missing_confidence_falls_back() -> None:
    body = "RATIONALE: hi\nDIFF:\n```diff\n--- a\n+++ b\n```\n"
    _, conf, _ = parse_repair_response(body, fallback_confidence=0.42)
    assert conf == 0.42


def test_parse_repair_response_missing_diff_returns_empty() -> None:
    body = "RATIONALE: hi\nCONFIDENCE: 0.9\n"
    diff, conf, _ = parse_repair_response(body)
    assert diff == ""
    assert abs(conf - 0.9) < 1e-6


# ---------------------------------------------------------------------------
# Response unwrap per provider
# ---------------------------------------------------------------------------

def test_extract_text_anthropic_shape() -> None:
    body = json.dumps({"content": [
        {"type": "text", "text": "RATIONALE: x\nCONFIDENCE: 0.5\nDIFF:\n```diff\n--- a\n+++ b\n```"}
    ]})
    text = _extract_text_from_provider_response(LLMProvider.ANTHROPIC, body)
    assert "RATIONALE: x" in text


def test_extract_text_openai_shape() -> None:
    body = json.dumps({"choices": [
        {"message": {"role": "assistant",
                     "content": "RATIONALE: y\nCONFIDENCE: 0.6\nDIFF:\n```diff\n--- a\n+++ b\n```"}}
    ]})
    text = _extract_text_from_provider_response(LLMProvider.OPENAI, body)
    assert "RATIONALE: y" in text


# ---------------------------------------------------------------------------
# repair_selector — dry-run + real-path + fallback
# ---------------------------------------------------------------------------

def test_repair_selector_dry_run_never_hits_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """dry_run=True must NOT touch _post_json, period."""
    called = {"flag": False}

    def _explode(**kwargs: Any) -> Any:
        called["flag"] = True
        raise AssertionError("_post_json must not be called in dry-run mode")

    monkeypatch.setattr(llm_repair, "_post_json", _explode)
    result = repair_selector(
        target_id="browser_chatgpt", failure_kind="UI_SELECTOR_MISS",
        failure_hint="x", stderr_excerpt="", adapter_yaml_text="",
        dry_run=True,
    )
    assert called["flag"] is False
    assert result.provider == LLMProvider.MOCK
    assert result.dry_run is True
    assert "RATIONALE" not in result.proposed_yaml_diff  # diff is just the diff body
    assert result.error is None


def test_repair_selector_anthropic_path_via_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-dry-run + ANTHROPIC: shape the response, verify parsing flows."""
    captured: dict[str, Any] = {}

    def _fake_post(*, url: str, headers: dict, payload: dict, timeout: float = 30.0):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        body = json.dumps({"content": [
            {"type": "text",
             "text": "RATIONALE: ok\nCONFIDENCE: 0.65\nDIFF:\n```diff\n--- a\n+++ b\n@@ x @@\n+y\n```"}
        ]})
        return 200, body

    monkeypatch.setattr(llm_repair, "_post_json", _fake_post)
    result = repair_selector(
        target_id="browser_claude", failure_kind="UI_SELECTOR_MISS",
        failure_hint="x", stderr_excerpt="", adapter_yaml_text="",
        provider=LLMProvider.ANTHROPIC,
        api_key="sk-ant-test",
        dry_run=False,
    )
    assert captured["url"] == llm_repair.ANTHROPIC_ENDPOINT
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["payload"]["model"] == ANTHROPIC_DEFAULT_MODEL
    assert result.provider == LLMProvider.ANTHROPIC
    assert abs(result.confidence - 0.65) < 1e-6
    assert "+y" in result.proposed_yaml_diff
    assert result.error is None


def test_repair_selector_openai_path_via_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(*, url: str, headers: dict, payload: dict, timeout: float = 30.0):
        captured["url"] = url
        captured["headers"] = headers
        body = json.dumps({"choices": [
            {"message": {"role": "assistant",
                         "content": "RATIONALE: ok\nCONFIDENCE: 0.55\nDIFF:\n```diff\n--- a\n+++ b\n@@ x @@\n+z\n```"}}
        ]})
        return 200, body

    monkeypatch.setattr(llm_repair, "_post_json", _fake_post)
    result = repair_selector(
        target_id="browser_chatgpt", failure_kind="UI_SELECTOR_MISS",
        failure_hint="", stderr_excerpt="", adapter_yaml_text="",
        provider=LLMProvider.OPENAI,
        api_key="sk-openai-test",
        dry_run=False,
    )
    assert captured["url"] == llm_repair.OPENAI_ENDPOINT
    assert captured["headers"]["Authorization"] == "Bearer sk-openai-test"
    assert result.provider == LLMProvider.OPENAI
    assert "+z" in result.proposed_yaml_diff


def test_repair_selector_http_error_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_post(**kwargs: Any):
        return 503, "Service Unavailable"

    monkeypatch.setattr(llm_repair, "_post_json", _fake_post)
    result = repair_selector(
        target_id="browser_chatgpt", failure_kind="UI_SELECTOR_MISS",
        failure_hint="", stderr_excerpt="", adapter_yaml_text="",
        provider=LLMProvider.OPENAI,
        api_key="sk-test",
        dry_run=False,
    )
    assert result.error is not None
    assert "503" in result.error
    assert result.provider == LLMProvider.MOCK
    assert result.proposed_yaml_diff  # mock still produces a placeholder


def test_repair_selector_missing_api_key_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no API key is supplied or in env, fall back to MOCK with no HTTP call."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _explode(**kwargs: Any) -> Any:
        raise AssertionError("must not be called without API key")

    monkeypatch.setattr(llm_repair, "_post_json", _explode)
    result = repair_selector(
        target_id="browser_chatgpt", failure_kind="UI_SELECTOR_MISS",
        failure_hint="", stderr_excerpt="", adapter_yaml_text="",
        provider=LLMProvider.OPENAI,
        api_key=None,
        dry_run=False,
    )
    assert result.provider == LLMProvider.MOCK
    assert result.error is None  # graceful fallback, not an error


# ---------------------------------------------------------------------------
# widen_yaml_selectors template + dispatcher
# ---------------------------------------------------------------------------

def test_widen_yaml_selectors_default_suggestions() -> None:
    proposal = widen_yaml_selectors(site="chatgpt", target_case="browser_chatgpt:T01")
    assert proposal.kind == "widen_yaml_selectors"
    assert proposal.files == ["pce_core/adapters/chatgpt.yaml"]
    assert "selectors.input" in proposal.unified_diff
    assert proposal.confidence < 0.5  # low — template-only
    assert proposal.notes["suggested_count"] == 2


def test_widen_yaml_selectors_custom_suggestions() -> None:
    proposal = widen_yaml_selectors(
        site="claude",
        group="send_button",
        suggested_selectors=['button[data-testid="custom"]'],
    )
    assert "send_button" in proposal.unified_diff
    assert 'button[data-testid="custom"]' in proposal.unified_diff
    assert proposal.notes["suggested_count"] == 1


def test_propose_patches_dispatches_ui_selector_miss_to_yaml() -> None:
    """UI_SELECTOR_MISS now produces a YAML diff (not Python)."""
    proposals = propose_patches_for_failure(
        "UI_SELECTOR_MISS",
        field_path="selectors.send_button",
        target_case="browser_chatgpt:T01",
        provider_hint="openai",
    )
    assert len(proposals) == 1
    p = proposals[0]
    assert p.kind == "widen_yaml_selectors"
    assert p.files == ["pce_core/adapters/chatgpt.yaml"]
    assert "send_button" in p.unified_diff


# ---------------------------------------------------------------------------
# tools.repair_adapter
# ---------------------------------------------------------------------------

def _write_run(runs_dir: Path, **kwargs: Any) -> Path:
    record = {
        "run_id": kwargs.get("run_id", "r1"),
        "target_id": kwargs.get("target_id", "browser_chatgpt"),
        "case_id": kwargs.get("case_id", "T01"),
        "mode": "live",
        "status": kwargs.get("status", "fail"),
        "started_at": kwargs.get("started_at", time.time()),
        "elapsed_s": 1.0,
        "exit_code": 1,
        "cmd": ["python", "-m", "pytest"],
        "cwd": ".",
        "timeout_s": 60,
        "stdout": "",
        "stderr": kwargs.get("stderr", "NoSuchElementException: locator did not match"),
        "timed_out": False,
        "evidence": {"lane": "browser", "tier": "S0"},
    }
    p = runs_dir / f"{record['run_id']}.json"
    p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return p


def test_find_latest_failed_run_filters_target_and_case(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    now = time.time()
    _write_run(runs, run_id="a", started_at=now - 100, target_id="browser_chatgpt", case_id="T01")
    _write_run(runs, run_id="b", started_at=now - 50, target_id="browser_chatgpt", case_id="T02")
    _write_run(runs, run_id="c", started_at=now - 10, target_id="browser_claude", case_id="T01")
    _write_run(runs, run_id="d", started_at=now - 5,  target_id="browser_chatgpt",
               case_id="T01", status="pass")

    out = repair_adapter.find_latest_failed_run(runs, target_id="browser_chatgpt", now=now)
    # latest failed = b (T02 @ -50)
    assert out is not None
    assert out["run_id"] == "b"

    out_case = repair_adapter.find_latest_failed_run(
        runs, target_id="browser_chatgpt", case_id="T01", now=now,
    )
    assert out_case is not None
    assert out_case["run_id"] == "a"


def test_find_latest_failed_run_returns_none_when_empty(tmp_path: Path) -> None:
    out = repair_adapter.find_latest_failed_run(tmp_path, target_id="x")
    assert out is None


def test_derive_yaml_path_strips_lane_prefix() -> None:
    p = repair_adapter.derive_yaml_path("browser_chatgpt")
    assert p.name == "chatgpt.yaml"
    assert p.parent.name == "adapters"


def test_derive_yaml_path_no_lane_prefix() -> None:
    p = repair_adapter.derive_yaml_path("chatgpt")
    assert p.name == "chatgpt.yaml"


def test_cli_no_failed_run_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """No matching failed run → exit 1, stderr explains."""
    empty_runs = tmp_path / "runs"
    empty_runs.mkdir()
    rc = repair_adapter.main([
        "--target", "browser_chatgpt",
        "--runs-dir", str(empty_runs),
        "--quiet",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "No failed run found" in captured.err


def test_cli_missing_yaml_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Failed run found but the YAML manifest is missing → exit 2."""
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_run(runs, run_id="r1", target_id="future_site", case_id="T01")
    # Point derive_yaml_path at an empty dir so future_site.yaml is missing.
    empty_adapters = tmp_path / "no_adapters"
    empty_adapters.mkdir()
    monkeypatch.setattr(repair_adapter, "derive_yaml_path",
                        lambda target_id, **kw: empty_adapters / f"{target_id.split('_', 1)[-1]}.yaml")
    rc = repair_adapter.main([
        "--target", "future_site",
        "--runs-dir", str(runs),
        "--quiet",
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "missing" in captured.err.lower()


def test_cli_dry_run_emits_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: dry-run mode → exit 0 + JSON summary on stdout."""
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_run(runs, run_id="r1", target_id="browser_chatgpt", case_id="T01")
    rc = repair_adapter.main([
        "--target", "browser_chatgpt",
        "--runs-dir", str(runs),
        "--json",
        "--quiet",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["run"]["run_id"] == "r1"
    assert data["repair"]["dry_run"] is True
    assert data["repair"]["provider"] == "mock"
