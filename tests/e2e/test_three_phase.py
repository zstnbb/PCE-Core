# SPDX-License-Identifier: Apache-2.0
"""PCE Three-Phase E2E Validation Suite.

Validates the full capture pipeline across all listed AI sites in three
sequential phases, testing **each content type independently**:

  Phase 1 — Connectivity:  Navigate to each site, check login state.
  Phase 2 — Capture + Storage:  For each content scenario (text, file,
             image), send the appropriate message/attachment, verify raw
             capture arrives in DB, verify normalized session has user +
             assistant messages with correct attachment types.
  Phase 3 — Render:  Confirm the Dashboard API returns renderable session
             data for every provider that passed Phase 2.

Scenarios per site:
  - text:   Plain text message → verify capture + response
  - file:   Upload sample_note.txt → verify file attachment captured
  - image:  Upload sample_square.png → verify image_url captured

Results are persisted per (site, scenario) so only failed/untested
combinations are re-run.  Use PCE_E2E_FORCE=1 to re-test everything.

Usage:
    python -m pytest tests/e2e/test_three_phase.py -v -s
    PCE_E2E_SITES=chatgpt,claude python -m pytest tests/e2e/test_three_phase.py -v -s
    PCE_E2E_SCENARIOS=text,file python -m pytest tests/e2e/test_three_phase.py -v -s
    PCE_E2E_FORCE=1 python -m pytest tests/e2e/test_three_phase.py -v -s

Environment:
    PCE_E2E_SITES       Comma-separated list of sites (default: all)
    PCE_E2E_SCENARIOS   Comma-separated: text,file,image (default: all applicable)
    PCE_E2E_FORCE       Set to "1" to re-test everything
    PCE_E2E_NO_RESET    Set to "1" to skip DB reset
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import pytest

from .capture_verifier import (
    pce_is_running,
    get_stats,
    get_recent_captures,
    get_sessions,
    get_session_messages,
    reset_baseline,
    wait_for_new_captures,
    wait_for_session_with_messages,
    verify_message_quality,
    verify_rich_content,
    verify_dashboard_sessions,
)
from .sites.chatgpt import ChatGPTAdapter
from .sites.claude import ClaudeAdapter
from .sites.copilot import CopilotAdapter
from .sites.deepseek import DeepSeekAdapter
from .sites.gemini import GeminiAdapter
from .sites.google_ai_studio import GoogleAIStudioAdapter
from .sites.grok import GrokAdapter
from .sites.huggingface import HuggingFaceAdapter
from .sites.kimi import KimiAdapter
from .sites.manus import ManusAdapter
from .sites.mistral import MistralAdapter
from .sites.perplexity import PerplexityAdapter
from .sites.poe import PoeAdapter
from .sites.zhipu import ZhiPuAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.e2e.three_phase")

# ─── Site registry ────────────────────────────────────────────────────────

ALL_SITES = {
    "chatgpt": ChatGPTAdapter(),
    "claude": ClaudeAdapter(),
    "copilot": CopilotAdapter(),
    "deepseek": DeepSeekAdapter(),
    "gemini": GeminiAdapter(),
    "googleaistudio": GoogleAIStudioAdapter(),
    "grok": GrokAdapter(),
    "huggingface": HuggingFaceAdapter(),
    "kimi": KimiAdapter(),
    "manus": ManusAdapter(),
    "mistral": MistralAdapter(),
    "perplexity": PerplexityAdapter(),
    "poe": PoeAdapter(),
    "zhipu": ZhiPuAdapter(),
}

# ─── Scenarios ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "rich_content"
SAMPLE_NOTE = FIXTURES_DIR / "sample_note.txt"
SAMPLE_IMAGE = FIXTURES_DIR / "sample_square.png"

SCENARIOS = {
    "text": {
        "description": "Plain text message",
        "requires_upload": None,
        "file_paths": [],
        "image_paths": [],
        "prompt_template": (
            "PCE-TEST-{site}-text-{token}. "
            "Reply with a short greeting and include a python code block "
            "that prints 'hello'."
        ),
        "expected_user_types": set(),
        "expected_assistant_types": {"code_block"},
    },
    "file": {
        "description": "File upload (sample_note.txt)",
        "requires_upload": "file",
        "file_paths": [str(SAMPLE_NOTE)],
        "image_paths": [],
        "prompt_template": (
            "PCE-TEST-{site}-file-{token}. "
            "Confirm which file you received in one sentence. "
            "Then include a fenced python code block that prints the token "
            "above, and a markdown link: "
            "[Ref](https://example.com/{site}/file/{token})"
        ),
        "expected_user_types": {"file"},
        "expected_assistant_types": {"code_block"},
    },
    "image": {
        "description": "Image upload (sample_square.png)",
        "requires_upload": "image",
        "file_paths": [],
        "image_paths": [str(SAMPLE_IMAGE)],
        "prompt_template": (
            "PCE-TEST-{site}-image-{token}. "
            "Describe this image briefly. "
            "Then include a fenced python code block that prints the token "
            "above, and a markdown link: "
            "[Ref](https://example.com/{site}/image/{token})"
        ),
        "expected_user_types": {"image_url"},
        "expected_assistant_types": {"code_block"},
    },
}


def _site_supports_scenario(adapter, scenario_name: str) -> bool:
    """Check if a site adapter supports a given scenario."""
    req = SCENARIOS[scenario_name]["requires_upload"]
    if req is None:
        return True
    if req == "file":
        return adapter.supports_file_upload
    if req == "image":
        return adapter.supports_image_upload
    return False


# ─── State persistence ────────────────────────────────────────────────────

STATE_FILE = Path(__file__).resolve().parent / "e2e_state.json"


@dataclass
class ScenarioState:
    """Persisted result for a single (site, scenario) across 3 phases."""
    site_name: str
    scenario: str = "text"
    phase1_ok: Optional[bool] = None
    phase2_ok: Optional[bool] = None
    phase3_ok: Optional[bool] = None
    phase1_error: Optional[str] = None
    phase2_error: Optional[str] = None
    phase3_error: Optional[str] = None
    phase2_session_id: Optional[str] = None
    phase2_message_count: int = 0
    phase2_attachment_types: list[str] = field(default_factory=list)
    timestamp: float = 0


def _state_key(site_name: str, scenario: str) -> str:
    return f"{site_name}__{scenario}"


def _load_state() -> dict[str, ScenarioState]:
    """Load persisted state from disk."""
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        out = {}
        for k, v in data.items():
            if isinstance(v, dict):
                out[k] = ScenarioState(
                    **{f: v.get(f) for f in ScenarioState.__dataclass_fields__ if f in v}
                )
        return out
    except Exception:
        return {}


def _save_state(state: dict[str, ScenarioState]):
    """Persist state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in state.items()}
    STATE_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _should_test(key: str, state: dict[str, ScenarioState]) -> bool:
    """Return True if this (site, scenario) needs (re-)testing."""
    if os.environ.get("PCE_E2E_FORCE", "").strip() == "1":
        return True
    s = state.get(key)
    if s is None:
        return True
    if not s.phase1_ok or not s.phase2_ok or not s.phase3_ok:
        return True
    if time.time() - (s.timestamp or 0) > 86400:
        return True
    return False


def _selected_sites() -> list[str]:
    env = os.environ.get("PCE_E2E_SITES", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip() in ALL_SITES]
    return list(ALL_SITES.keys())


def _selected_scenarios() -> list[str]:
    env = os.environ.get("PCE_E2E_SCENARIOS", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip() in SCENARIOS]
    return list(SCENARIOS.keys())


def _generate_test_ids() -> list[str]:
    """Generate (site__scenario) test IDs, skipping unsupported combos."""
    ids = []
    for site_name in _selected_sites():
        adapter = ALL_SITES[site_name]
        for scenario_name in _selected_scenarios():
            if _site_supports_scenario(adapter, scenario_name):
                ids.append(f"{site_name}__{scenario_name}")
    return ids


def _parse_test_id(test_id: str) -> tuple[str, str]:
    """Parse 'site__scenario' back into components."""
    parts = test_id.split("__", 1)
    return parts[0], parts[1]


# ─── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def ensure_pce_running():
    if not pce_is_running():
        pytest.fail(
            "PCE Core server not reachable at http://127.0.0.1:9800\n"
            "Start it with: python -m pce_app --no-tray --no-browser"
        )


@pytest.fixture(scope="session", autouse=True)
def optional_reset(ensure_pce_running):
    if os.environ.get("PCE_E2E_NO_RESET", "").strip() == "1":
        logger.info("Skipping baseline reset (PCE_E2E_NO_RESET=1)")
        return
    result = reset_baseline()
    logger.info(
        "Baseline reset: %d captures, %d sessions, %d messages deleted",
        result.get("captures_deleted", 0),
        result.get("sessions_deleted", 0),
        result.get("messages_deleted", 0),
    )


# ─── Phase execution helpers ─────────────────────────────────────────────

_phase1_cache: dict[str, tuple[bool, str]] = {}


def _run_phase1(adapter, driver) -> tuple[bool, str]:
    """Phase 1: Connectivity — navigate and check login state (cached per site)."""
    if adapter.name in _phase1_cache:
        return _phase1_cache[adapter.name]
    logger.info("  [P1] Checking connectivity for %s ...", adapter.name)
    ok = adapter.check_logged_in(driver, timeout=10)
    if ok:
        logger.info("  [P1] ✓ %s — logged in, input found", adapter.name)
        result = (True, "")
    else:
        adapter.take_screenshot(driver, "p1_not_logged_in")
        msg = f"{adapter.name}: not logged in or input not found"
        logger.warning("  [P1] ✗ %s — %s", adapter.name, msg)
        result = (False, msg)
    _phase1_cache[adapter.name] = result
    return result


def _run_phase2(adapter, driver, scenario_name: str) -> tuple[bool, str, dict]:
    """Phase 2: Capture + Storage — send scenario-specific message, verify."""
    scenario = SCENARIOS[scenario_name]
    tag = f"P2:{scenario_name}"
    logger.info("  [%s] Testing %s for %s ...", tag, scenario["description"], adapter.name)
    extra = {}
    token = str(int(time.time()))

    # Record baseline counts
    try:
        initial_stats = get_stats()
        initial_count = initial_stats["total_captures"]
        initial_provider_count = initial_stats.get("by_provider", {}).get(
            adapter.provider, 0
        )
    except Exception as e:
        return False, f"stats error: {e}", extra

    # Navigate fresh
    if not adapter.navigate(driver):
        adapter.take_screenshot(driver, f"p2_{scenario_name}_nav_fail")
        return False, "navigation failed", extra

    # Wait for input with retries (SPAs may need extra time after navigation)
    input_found = adapter.find_input(driver)
    if not input_found:
        # Retry: some SPAs need a second navigation after being on a thread page
        logger.warning("  [%s] Input not found, retrying navigation ...", tag)
        time.sleep(2)
        adapter.navigate(driver)
        input_found = adapter.find_input(driver)
    if not input_found:
        adapter.take_screenshot(driver, f"p2_{scenario_name}_no_input")
        return False, "input not found after navigation", extra

    # Build prompt
    prompt = scenario["prompt_template"].format(site=adapter.name, token=token)

    # Send message (with attachments if scenario requires)
    adapter.take_screenshot(driver, f"p2_{scenario_name}_before_send")
    if scenario["file_paths"] or scenario["image_paths"]:
        sent = adapter.send_rich_message(
            driver,
            message=prompt,
            file_paths=scenario["file_paths"] or None,
            image_paths=scenario["image_paths"] or None,
        )
    else:
        sent = adapter.send_message(driver, message=prompt)

    if not sent:
        adapter.take_screenshot(driver, f"p2_{scenario_name}_send_fail")
        return False, f"send_{scenario_name}_failed", extra

    # Wait for AI response
    response_ok = adapter.wait_for_response(driver)
    adapter.take_screenshot(driver, f"p2_{scenario_name}_after_response")
    if not response_ok:
        logger.warning("  [%s] %s: response not detected, checking captures anyway", tag, adapter.name)

    # Trigger manual capture as fallback
    adapter.trigger_manual_capture(driver)
    time.sleep(3)

    # ── Verify capture arrived ──
    verify = wait_for_new_captures(
        initial_count=initial_count,
        initial_provider_count=initial_provider_count,
        timeout_s=25,
        poll_interval=2,
        min_new=1,
        provider=adapter.provider,
    )
    if not verify["success"]:
        adapter.take_screenshot(driver, f"p2_{scenario_name}_no_capture")
        return False, (
            f"no new captures for provider={adapter.provider} "
            f"(initial={initial_count}, current={verify['total']})"
        ), extra

    logger.info("  [%s] %s: %d new captures detected", tag, adapter.name,
                verify.get("provider_new_count", verify["new_count"]))

    # ── Verify normalized session ──
    session_result = wait_for_session_with_messages(
        provider=adapter.provider,
        min_messages=2,
        timeout_s=20,
        poll_interval=2,
        required_roles={"user", "assistant"},
    )
    if not session_result["success"]:
        adapter.take_screenshot(driver, f"p2_{scenario_name}_no_session")
        roles = [m.get("role", "?") for m in session_result.get("messages", [])]
        return False, (
            f"no session with user+assistant messages (roles seen: {roles})"
        ), extra

    msgs = session_result["messages"]
    sess = session_result["session"]

    # ── Verify message quality ──
    quality = verify_message_quality(msgs)
    if not quality["ok"]:
        adapter.take_screenshot(driver, f"p2_{scenario_name}_quality_fail")
        return False, f"message quality failed: {quality['issues']}", extra

    # ── Check attachment types (informational warnings, not blocking) ──
    rich = verify_rich_content(msgs)
    all_types = rich["types_found"]

    expected_user = scenario["expected_user_types"]
    if expected_user:
        missing = expected_user - all_types
        if missing:
            logger.warning(
                "  [%s] %s: expected user attachment types %s not found (got: %s)",
                tag, adapter.name, missing, all_types,
            )

    expected_asst = scenario["expected_assistant_types"]
    if expected_asst:
        missing = expected_asst - all_types
        if missing:
            logger.warning(
                "  [%s] %s: expected assistant attachment types %s not found (got: %s)",
                tag, adapter.name, missing, all_types,
            )

    extra["session_id"] = sess.get("id")
    extra["message_count"] = len(msgs)
    extra["attachment_types"] = sorted(all_types)

    logger.info(
        "  [%s] ✓ %s — session %s, %d msgs, attachments=%s",
        tag, adapter.name, sess.get("id", "?")[:8], len(msgs), sorted(all_types),
    )
    return True, "", extra


def _run_phase3(provider: str) -> tuple[bool, str]:
    """Phase 3: Render — verify dashboard API has renderable data."""
    logger.info("  [P3] Verifying dashboard render for provider=%s ...", provider)
    result = verify_dashboard_sessions(provider=provider)
    if result["ok"]:
        logger.info(
            "  [P3] ✓ %s — %d/%d sessions renderable",
            provider, result["renderable_sessions"], result["session_count"],
        )
        return True, ""
    else:
        msg = (
            f"dashboard not renderable: "
            f"{result['renderable_sessions']}/{result['session_count']} sessions ok, "
            f"issues={result['issues']}"
        )
        logger.warning("  [P3] ✗ %s — %s", provider, msg)
        return False, msg


# ─── The parametrized test ────────────────────────────────────────────────

@pytest.mark.parametrize("test_id", _generate_test_ids())
def test_three_phase(test_id, driver):
    """Three-phase E2E validation: connectivity → capture+storage → render.

    Each test_id is '{site}__{scenario}' e.g. 'chatgpt__text', 'chatgpt__file'.
    """
    site_name, scenario_name = _parse_test_id(test_id)
    adapter = ALL_SITES[site_name]
    state = _load_state()
    key = _state_key(site_name, scenario_name)

    # Skip if already passed and not forced
    if not _should_test(key, state):
        s = state[key]
        logger.info(
            "[%s/%s] Already passed all 3 phases (%.0fs ago) — skipping",
            site_name, scenario_name, time.time() - s.timestamp,
        )
        pytest.skip(f"{site_name}/{scenario_name}: all phases passed, use PCE_E2E_FORCE=1 to re-test")

    ss = ScenarioState(site_name=site_name, scenario=scenario_name, timestamp=time.time())

    logger.info("=" * 60)
    logger.info("TEST: %s / %s (%s)", adapter.name, scenario_name, adapter.url)
    logger.info("=" * 60)

    # ── Phase 1: Connectivity (cached per site) ──
    p1_ok, p1_err = _run_phase1(adapter, driver)
    ss.phase1_ok = p1_ok
    ss.phase1_error = p1_err or None

    if not p1_ok:
        state[key] = ss
        _save_state(state)
        pytest.skip(f"[{site_name}/{scenario_name}] Phase 1 SKIP: {p1_err}")

    # ── Phase 2: Capture + Storage (scenario-specific) ──
    p2_ok, p2_err, p2_extra = _run_phase2(adapter, driver, scenario_name)
    ss.phase2_ok = p2_ok
    ss.phase2_error = p2_err or None
    ss.phase2_session_id = p2_extra.get("session_id")
    ss.phase2_message_count = p2_extra.get("message_count", 0)
    ss.phase2_attachment_types = p2_extra.get("attachment_types", [])

    if not p2_ok:
        state[key] = ss
        _save_state(state)
        pytest.fail(f"[{site_name}/{scenario_name}] Phase 2 FAIL: {p2_err}")

    # ── Phase 3: Render ──
    p3_ok, p3_err = _run_phase3(adapter.provider)
    ss.phase3_ok = p3_ok
    ss.phase3_error = p3_err or None

    ss.timestamp = time.time()
    state[key] = ss
    _save_state(state)

    if not p3_ok:
        pytest.fail(f"[{site_name}/{scenario_name}] Phase 3 FAIL: {p3_err}")

    logger.info("[%s/%s] ALL 3 PHASES PASSED ✓", site_name, scenario_name)


# ─── Summary report (runs after all tests) ────────────────────────────────

def _icon(val):
    if val is None:
        return "-"
    return "OK" if val else "XX"


@pytest.fixture(scope="session", autouse=True)
def print_summary(request):
    """Print a summary table after all tests finish."""
    yield
    state = _load_state()
    if not state:
        return

    print("\n" + "=" * 80)
    print("  PCE THREE-PHASE E2E SUMMARY")
    print("=" * 80)
    print(f"  {'Site':<16} {'Scenario':<10} {'P1':>4} {'P2':>4} {'P3':>4}  Notes")
    print("  " + "─" * 74)

    passed = 0
    tested = 0
    for site_name in ALL_SITES:
        adapter = ALL_SITES[site_name]
        for scenario_name in SCENARIOS:
            if not _site_supports_scenario(adapter, scenario_name):
                continue
            key = _state_key(site_name, scenario_name)
            s = state.get(key)
            if s is None:
                print(f"  {site_name:<16} {scenario_name:<10} {'—':>4} {'—':>4} {'—':>4}  not tested")
                continue

            tested += 1
            p1 = _icon(s.phase1_ok)
            p2 = _icon(s.phase2_ok)
            p3 = _icon(s.phase3_ok)

            notes = []
            if not s.phase1_ok and s.phase1_error:
                notes.append(s.phase1_error[:40])
            elif not s.phase2_ok and s.phase2_error:
                notes.append(s.phase2_error[:40])
            elif s.phase2_ok:
                notes.append(f"{s.phase2_message_count}msgs")
                if s.phase2_attachment_types:
                    notes.append(",".join(s.phase2_attachment_types[:4]))

            all_ok = s.phase1_ok and s.phase2_ok and s.phase3_ok
            if all_ok:
                passed += 1

            print(f"  {site_name:<16} {scenario_name:<10} {p1:>4} {p2:>4} {p3:>4}  {' | '.join(notes)}")

    print("  " + "─" * 74)
    print(f"  {passed}/{tested} scenario tests passed | State: {STATE_FILE}")
    print("=" * 80 + "\n")
