# SPDX-License-Identifier: Apache-2.0
"""Rich-content E2E capture tests against real logged-in browser sessions.

These tests are opt-in because they require:
  - a logged-in Chrome profile
  - the browser extension loaded
  - live external AI websites

Enable with:
    $env:PCE_RICH_E2E='1'
    pytest tests/e2e/test_rich_capture.py -v -s
"""

import os
import time
from pathlib import Path

import pytest

from .capture_verifier import (
    get_recent_captures,
    get_stats,
    wait_for_conversation_capture_matching,
    wait_for_new_captures,
    wait_for_session_matching,
)
from .sites.chatgpt import ChatGPTAdapter
from .sites.deepseek import DeepSeekAdapter
from .sites.google_ai_studio import GoogleAIStudioAdapter
from .sites.grok import GrokAdapter
from .sites.perplexity import PerplexityAdapter
from .sites.poe import PoeAdapter
from .sites.zhipu import ZhiPuAdapter


pytestmark = pytest.mark.skipif(
    os.environ.get("PCE_RICH_E2E", "").strip() != "1",
    reason="Set PCE_RICH_E2E=1 to run live rich-content browser E2E",
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "rich_content"
SAMPLE_NOTE = FIXTURES_DIR / "sample_note.txt"
SAMPLE_IMAGE = FIXTURES_DIR / "sample_square.png"

RICH_SITES = {
    "chatgpt": ChatGPTAdapter(),
    "deepseek": DeepSeekAdapter(),
    "googleaistudio": GoogleAIStudioAdapter(),
    "grok": GrokAdapter(),
    "perplexity": PerplexityAdapter(),
    "poe": PoeAdapter(),
    "zhipu": ZhiPuAdapter(),
}

SCENARIOS = {
    "file": {
        "file_paths": [str(SAMPLE_NOTE)],
        "image_paths": [],
        "raw_required_attachment_types": {
            "user": {"file"},
            "assistant": {"code_block", "citation"},
        },
        "session_required_attachment_types": {
            "user": {"file"},
            "assistant": {"code_block", "citation"},
        },
    },
    "image": {
        "file_paths": [],
        "image_paths": [str(SAMPLE_IMAGE)],
        "raw_required_attachment_types": {
            "user": {"image_url"},
            "assistant": {"code_block", "citation"},
        },
        "session_required_attachment_types": {
            "user": {"image_url"},
            "assistant": {"code_block", "citation"},
        },
    },
}

SITE_SCENARIO_OVERRIDES = {
    ("deepseek", "image"): {
        "raw_required_attachment_types": {
            "user": {"file"},
            "assistant": {"code_block", "citation"},
        },
        "session_required_attachment_types": {
            "user": {"file"},
            "assistant": {"code_block", "citation"},
        },
    },
}


def _selected_sites() -> list[str]:
    env = os.environ.get("PCE_RICH_E2E_SITES", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip() in RICH_SITES]
    return list(RICH_SITES.keys())


def _selected_scenarios() -> list[str]:
    env = os.environ.get("PCE_RICH_E2E_TYPES", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip() in SCENARIOS]
    return list(SCENARIOS.keys())


def _rich_prompt(site_name: str, scenario_name: str, token: str) -> str:
    return (
        f"{token}\n"
        "Confirm which attachment you received in one short sentence. "
        f"Then include this markdown link exactly: [Source](https://example.com/{site_name}/{scenario_name}/{token}) "
        f"and a fenced python code block that prints '{token}'."
    )


def _scenario_for_site(site_name: str, scenario_name: str) -> dict:
    scenario = dict(SCENARIOS[scenario_name])
    override = SITE_SCENARIO_OVERRIDES.get((site_name, scenario_name))
    if override:
        scenario.update(override)
    return scenario


@pytest.mark.parametrize("site_name", _selected_sites())
@pytest.mark.parametrize("scenario_name", _selected_scenarios())
def test_rich_capture_matrix(site_name, scenario_name, driver):
    adapter = RICH_SITES[site_name]
    scenario = _scenario_for_site(site_name, scenario_name)

    if scenario_name == "file" and not adapter.supports_file_upload:
        pytest.skip(f"{site_name} does not expose file uploads in the adapter yet")
    if scenario_name == "image" and not adapter.supports_image_upload:
        pytest.skip(f"{site_name} does not expose image uploads in the adapter yet")

    token = f"PCE-RICH-{site_name}-{scenario_name}-{int(time.time())}"
    prompt = _rich_prompt(site_name, scenario_name, token)
    started_at = time.time()
    adapter.response_timeout_s = max(adapter.response_timeout_s, 120)

    initial_stats = get_stats()
    initial_count = initial_stats["total_captures"]
    initial_provider_count = len(
        get_recent_captures(last=200, provider=adapter.provider)
    )

    assert adapter.navigate(driver), f"[{site_name}] navigation_failed"
    assert adapter.find_input(driver), f"[{site_name}] input_not_found"
    adapter.take_screenshot(driver, f"{scenario_name}_before_send")

    sent = adapter.send_rich_message(
        driver,
        message=prompt,
        file_paths=scenario["file_paths"],
        image_paths=scenario["image_paths"],
    )
    assert sent, f"[{site_name}] rich_send_failed"

    response_received = adapter.wait_for_response(driver)
    adapter.take_screenshot(driver, f"{scenario_name}_after_response")
    assert response_received, f"[{site_name}] no_response_for_{scenario_name}"

    adapter.trigger_manual_capture(driver)
    time.sleep(3)

    verify = wait_for_new_captures(
        initial_count=initial_count,
        initial_provider_count=initial_provider_count,
        timeout_s=25,
        poll_interval=2,
        min_new=1,
        provider=adapter.provider,
    )
    assert verify["success"], f"[{site_name}] no_new_captures_for_{scenario_name}"

    raw_capture_result = wait_for_conversation_capture_matching(
        provider=adapter.provider,
        contains_text=token,
        required_attachment_types=scenario["raw_required_attachment_types"],
        timeout_s=25,
        poll_interval=2,
        created_after=started_at - 5,
    )
    assert raw_capture_result["success"], (
        f"[{site_name}] raw conversation capture missing requirements for {scenario_name}. "
        f"attachment_types_by_role={raw_capture_result['attachment_types_by_role']}"
    )

    session_result = wait_for_session_matching(
        provider=adapter.provider,
        contains_text=token,
        required_roles={"user", "assistant"},
        required_attachment_types=scenario["session_required_attachment_types"],
        min_messages=2,
        timeout_s=30,
        poll_interval=2,
        started_after=started_at - 5,
    )
    assert session_result["success"], (
        f"[{site_name}] rich session missing requirements for {scenario_name}. "
        f"attachment_types_by_role={session_result['attachment_types_by_role']}"
    )
