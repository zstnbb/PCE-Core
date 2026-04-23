# SPDX-License-Identifier: Apache-2.0
"""Full-fidelity Gemini capture E2E matrix for PCE.

Mirrors ``test_chatgpt_full.py`` / ``test_claude_full.py`` but
customised for Gemini's surfaces: Gems, Canvas, Imagen, Deep
Research, Extensions, 2.5 Pro Thinking, /share/ skip.

Set ``PCE_GEMINI_CASES=G01,G02,...`` to run a subset.
Set ``PCE_GEMINI_GEM_URL=https://gemini.google.com/gem/<slug>`` for G15.
Set ``PCE_GEMINI_SHARE_URL=https://gemini.google.com/share/<hex>`` for G17.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from .capture_verifier import (
    assert_canvas_in_screenshot,
    get_stats,
    pce_is_running,
    reset_baseline,
    verify_message_quality,
    verify_rich_content,
    wait_for_conversation_capture_matching,
    wait_for_new_captures,
    wait_for_no_new_captures,
    wait_for_session_matching,
)
from .sites.gemini import GeminiAdapter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.e2e.gemini_full")

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures" / "rich_content"
GENERATED_FIXTURES_DIR = ROOT / "fixtures" / "generated"
REPORTS_ROOT = ROOT / "reports" / "gemini"

SAMPLE_IMAGE = FIXTURES_DIR / "sample_square.png"

ACCOUNT_TIER = os.environ.get("GEMINI_ACCOUNT_TIER", "advanced").strip().lower() or "advanced"
GEM_URL_ENV = "PCE_GEMINI_GEM_URL"
SHARE_URL_ENV = "PCE_GEMINI_SHARE_URL"


class CaseSkip(RuntimeError):
    pass


class CaseFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class GeminiCase:
    id: str
    description: str
    action: str
    prompt_template: str = ""
    raw_required_attachment_types: dict[str, set[str]] = field(default_factory=dict)
    session_required_attachment_types: dict[str, set[str]] = field(default_factory=dict)
    required_text_fragments: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ("user", "assistant")
    response_timeout_s: float = 60
    capture_timeout_s: float = 35
    session_timeout_s: float = 40
    visual_review_required: bool = False
    expect_no_new_captures: bool = False


CASES: list[GeminiCase] = [
    GeminiCase(
        id="G01",
        description="Vanilla chat",
        action="vanilla_text",
        prompt_template="PCE-{id}-{token}. Reply with exactly: ACK {token}",
    ),
    GeminiCase(
        id="G02",
        description="Long streaming response",
        action="stream_complete",
        prompt_template=(
            "PCE-{id}-{token}. Write 18 short bullet points about testing. "
            "Include {token} in the first bullet."
        ),
    ),
    GeminiCase(
        id="G03",
        description="Stop button visible mid-stream",
        action="stream_start",
        prompt_template=(
            "PCE-{id}-{token}. Stream a numbered list from 1 to 80. "
            "Include {token} on the first line."
        ),
    ),
    GeminiCase(
        id="G04",
        description="New chat from /app",
        action="new_chat",
        prompt_template="PCE-{id}-{token}. Reply with NEW-CHAT {token}",
    ),
    GeminiCase(
        id="G05",
        description="Assistant code block",
        action="code_block",
        prompt_template=(
            "PCE-{id}-{token}. Return only a fenced python code block that prints '{token}'."
        ),
        raw_required_attachment_types={"assistant": {"code_block"}},
        session_required_attachment_types={"assistant": {"code_block"}},
    ),
    GeminiCase(
        id="G06",
        description="2.5 Pro Thinking panel",
        action="thinking_model",
        prompt_template=(
            "PCE-{id}-{token}. Solve 17 * 19 step-by-step, showing your reasoning. "
            "End the final answer with {token}."
        ),
        required_text_fragments=("<thinking>",),
        response_timeout_s=90,
        session_timeout_s=60,
    ),
    GeminiCase(
        id="G07",
        description="Edit user message",
        action="edit_user_message",
    ),
    GeminiCase(
        id="G08",
        description="Regenerate assistant reply",
        action="regenerate",
        prompt_template=(
            "PCE-{id}-{token}. Reply with one short sentence that includes {token}."
        ),
        response_timeout_s=75,
    ),
    GeminiCase(
        id="G09",
        description="Draft flip (alternative responses)",
        action="branch_flip",
        response_timeout_s=75,
    ),
    GeminiCase(
        id="G10",
        description="PDF upload",
        action="pdf_upload",
        prompt_template=(
            "PCE-{id}-{token}. Confirm the PDF filename in one short sentence and include {token}."
        ),
        raw_required_attachment_types={"user": {"file"}},
        session_required_attachment_types={"user": {"file"}},
    ),
    GeminiCase(
        id="G11",
        description="Image upload (vision)",
        action="image_upload",
        prompt_template=(
            "PCE-{id}-{token}. Describe the uploaded image in one short sentence and include {token}."
        ),
        raw_required_attachment_types={"user": {"image_url"}},
        session_required_attachment_types={"user": {"image_url"}},
    ),
    GeminiCase(
        id="G12",
        description="Imagen image generation",
        action="image_generation",
        prompt_template=(
            "PCE-{id}-{token}. Generate a simple image of a blue square. Also include a "
            "one-sentence caption containing {token}."
        ),
        raw_required_attachment_types={"assistant": {"image_url"}},
        session_required_attachment_types={"assistant": {"image_url"}},
        response_timeout_s=180,
        capture_timeout_s=60,
        session_timeout_s=60,
    ),
    GeminiCase(
        id="G13",
        description="Deep Research",
        action="deep_research",
        prompt_template=(
            "PCE-{id}-{token}. Research a short one-paragraph summary of the Python GIL. "
            "Include {token} and at least one citation link."
        ),
        response_timeout_s=300,
        capture_timeout_s=60,
        session_timeout_s=90,
        visual_review_required=True,
    ),
    GeminiCase(
        id="G14",
        description="Canvas surface",
        action="canvas",
        prompt_template=(
            "PCE-{id}-{token}. Open this in Canvas and draft a five-line outline titled {token}."
        ),
        response_timeout_s=120,
        capture_timeout_s=60,
        session_timeout_s=60,
        visual_review_required=True,
    ),
    GeminiCase(
        id="G15",
        description="Gems (custom persona) route",
        action="gem",
        prompt_template="PCE-{id}-{token}. Reply with GEM {token}",
    ),
    GeminiCase(
        id="G16",
        description="Extensions (@Gmail / @Docs / @Drive)",
        action="extensions",
        prompt_template=(
            "PCE-{id}-{token}. Tell me one fact about PCE test run {token}. "
            "Do not actually read my email."
        ),
        visual_review_required=True,
    ),
    GeminiCase(
        id="G17",
        description="Shared conversation — must NOT capture",
        action="shared_view",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=12,
    ),
    GeminiCase(
        id="G18",
        description="Settings page negative capture",
        action="settings_page",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=8,
    ),
    GeminiCase(
        id="G19",
        description="Frontend error state",
        action="error_state",
        prompt_template="PCE-{id}-{token}. This request should fail offline and not be captured.",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=12,
    ),
    GeminiCase(
        id="G20",
        description="Activity page negative capture",
        action="activity_page",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=8,
    ),
]


def _jsonify(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, set):
        return sorted(_jsonify(v) for v in value)
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonify(payload), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _selected_cases() -> list[GeminiCase]:
    raw = os.environ.get("PCE_GEMINI_CASES", "").strip()
    if not raw:
        return CASES
    wanted = {part.strip().upper() for part in raw.split(",") if part.strip()}
    return [case for case in CASES if case.id.upper() in wanted]


def _build_minimal_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n",
        f"4 0 obj << /Length {len(content)} >> stream\n{content}\nendstream\nendobj\n",
        "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj.encode("latin-1"))
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(pdf)


def _ensure_generated_fixtures() -> dict[str, Path]:
    GENERATED_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = GENERATED_FIXTURES_DIR / "sample_report.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(_build_minimal_pdf("PCE PDF fixture"))
    return {"pdf": pdf_path, "image": SAMPLE_IMAGE}


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
        return
    result = reset_baseline()
    logger.info(
        "Baseline reset: %d captures, %d sessions, %d messages deleted",
        result.get("captures_deleted", 0),
        result.get("sessions_deleted", 0),
        result.get("messages_deleted", 0),
    )


@pytest.fixture(scope="session")
def generated_fixtures() -> dict[str, Path]:
    return _ensure_generated_fixtures()


@pytest.fixture(scope="session")
def gemini_report_dir() -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    report_dir = REPORTS_ROOT / ts
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


@pytest.fixture(scope="session")
def gemini_adapter() -> GeminiAdapter:
    return GeminiAdapter()


@pytest.fixture(scope="session")
def gemini_preflight(driver, gemini_adapter: GeminiAdapter, gemini_report_dir: Path):
    features = gemini_adapter.detect_features(driver)
    payload = {
        "generated_at": time.time(),
        "account_tier": ACCOUNT_TIER,
        "features": features,
        "gem_url_env_present": bool(os.environ.get(GEM_URL_ENV, "").strip()),
        "share_url_env_present": bool(os.environ.get(SHARE_URL_ENV, "").strip()),
    }
    _write_json(gemini_report_dir / "preflight.json", payload)
    return payload


@pytest.fixture(scope="session", autouse=True)
def write_summary(gemini_report_dir: Path):
    yield
    cases: list[dict[str, Any]] = []
    for path in sorted(gemini_report_dir.glob("G*.json")):
        try:
            cases.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            cases.append({"case_id": path.stem, "status": "broken_report", "error": str(exc)})
    status_counts: dict[str, int] = {}
    visual_review = []
    for case in cases:
        status = case.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if case.get("visual_review_required"):
            visual_review.append(case.get("case_id"))
    summary = {
        "generated_at": time.time(),
        "account_tier": ACCOUNT_TIER,
        "case_count": len(cases),
        "status_counts": status_counts,
        "visual_review_required": visual_review,
        "cases": cases,
    }
    _write_json(gemini_report_dir / "summary.json", summary)


def _begin_observation(provider: str) -> dict[str, Any]:
    stats = get_stats()
    return {
        "started_at": time.time(),
        "initial_count": stats["total_captures"],
        "initial_provider_count": stats.get("by_provider", {}).get(provider, 0),
    }


def _ensure_input_ready(adapter: GeminiAdapter, driver, *, retry_navigation: bool = True) -> None:
    if adapter.find_input(driver):
        return
    if retry_navigation:
        time.sleep(2)
        if not adapter.navigate(driver):
            raise CaseFailure("navigation_failed")
        if adapter.find_input(driver):
            return
    raise CaseFailure("input_not_found")


def _navigate_home(adapter: GeminiAdapter, driver) -> dict[str, Any]:
    if not adapter.navigate(driver):
        raise CaseFailure("navigation_failed")
    _ensure_input_ready(adapter, driver, retry_navigation=True)
    return {"current_url": driver.current_url}


def _perform_send(
    case: GeminiCase,
    adapter: GeminiAdapter,
    driver,
    *,
    prompt: str,
    file_paths: list[str] | None = None,
    image_paths: list[str] | None = None,
    wait_stream: bool = False,
    wait_response: bool = True,
    trigger_manual_capture: bool = True,
    screenshot_prefix: str | None = None,
    response_timeout_s: float | None = None,
) -> dict[str, Any]:
    prefix = screenshot_prefix or case.id.lower()
    screenshots = {
        "before": adapter.take_screenshot(driver, f"{prefix}_before_send"),
    }
    assistant_before = adapter.turn_count(driver, role="assistant")
    old_timeout = adapter.response_timeout_s
    if response_timeout_s:
        adapter.response_timeout_s = max(adapter.response_timeout_s, response_timeout_s)

    stream_seen = False
    try:
        if file_paths or image_paths:
            sent = adapter.send_rich_message(
                driver,
                message=prompt,
                file_paths=file_paths or None,
                image_paths=image_paths or None,
            )
        else:
            sent = adapter.send_message(driver, message=prompt)
        if not sent:
            raise CaseFailure("send_failed")

        stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=12) if wait_stream else False
        response_ok = adapter.wait_for_response(driver) if wait_response else True
    finally:
        adapter.response_timeout_s = old_timeout

    screenshots["after"] = adapter.take_screenshot(driver, f"{prefix}_after_response")
    if wait_response and not response_ok:
        raise CaseFailure("response_not_received")

    if trigger_manual_capture:
        adapter.trigger_manual_capture(driver)
        time.sleep(2)

    return {
        "screenshots": screenshots,
        "notes": {
            "stream_seen": stream_seen,
            "assistant_turns_before": assistant_before,
            "assistant_turns_after": adapter.turn_count(driver, role="assistant"),
            "response_received": response_ok,
        },
    }


def _action_vanilla_text(case, adapter, driver, token, _):
    notes = _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_stream_complete(case, adapter, driver, token, _):
    notes = _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_stream_start(case, adapter, driver, token, _):
    notes = _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    if not send["notes"].get("stream_seen"):
        raise CaseFailure("stop_button_not_seen")
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_new_chat(case, adapter, driver, token, _):
    old_conv = adapter.current_conversation_id(driver)
    if not adapter.click_new_chat(driver):
        raise CaseFailure("new_chat_click_failed")
    _ensure_input_ready(adapter, driver, retry_navigation=True)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"].update(
        {
            "conversation_id_before": old_conv,
            "conversation_id_after": adapter.current_conversation_id(driver),
        }
    )
    return {**observation, **send, "contains_text": token}


def _action_code_block(case, adapter, driver, token, _):
    return _action_vanilla_text(case, adapter, driver, token, _)


def _action_thinking_model(case, adapter, driver, token, _):
    notes = _navigate_home(adapter, driver)
    if not adapter.switch_model(driver, ["2.5 Pro Thinking", "Pro Thinking", "Thinking"]):
        raise CaseSkip("thinking_model_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_edit_user_message(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    original_token = f"{token}-ORIG"
    original_prompt = (
        f"PCE-{case.id}-{original_token}. Reply with one sentence containing {original_token}."
    )
    _perform_send(
        case, adapter, driver,
        prompt=original_prompt,
        trigger_manual_capture=False,
        screenshot_prefix=f"{case.id.lower()}_seed",
    )
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_edit")
    updated_prompt = (
        f"PCE-{case.id}-{token}. Reply with one sentence containing {token}."
    )
    if not adapter.edit_last_user_message(driver, updated_prompt):
        raise CaseFailure("edit_message_failed")
    response_ok = adapter.wait_for_response(driver)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_edit")
    if not response_ok:
        raise CaseFailure("edited_response_not_received")
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"original_token": original_token, "edited_token": token},
    }


def _action_regenerate(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    prompt = case.prompt_template.format(id=case.id, token=token)
    _perform_send(
        case, adapter, driver, prompt=prompt,
        trigger_manual_capture=False,
        screenshot_prefix=f"{case.id.lower()}_seed",
        response_timeout_s=case.response_timeout_s,
    )
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_regenerate")
    if not adapter.click_regenerate(driver):
        raise CaseFailure("regenerate_click_failed")
    stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=12)
    response_ok = adapter.wait_for_response(driver)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_regenerate")
    if not response_ok:
        raise CaseFailure("regenerated_response_not_received")
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"stream_seen": stream_seen, "response_received": response_ok},
    }


def _action_branch_flip(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    branch_a = f"{token}-A"
    first_prompt = (
        f"PCE-{case.id}-{branch_a}. Reply with one sentence containing BRANCH-A {branch_a}."
    )
    _perform_send(
        case, adapter, driver,
        prompt=first_prompt,
        trigger_manual_capture=False,
        screenshot_prefix=f"{case.id.lower()}_branch_a",
        response_timeout_s=case.response_timeout_s,
    )
    if not adapter.click_regenerate(driver):
        raise CaseFailure("branch_regenerate_unavailable")
    if not adapter.wait_for_response(driver):
        raise CaseFailure("branch_regenerate_response_not_received")
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_flip")
    if not adapter.flip_branch(driver, direction="prev"):
        raise CaseSkip("branch_flip_controls_not_available_after_regenerate")
    time.sleep(2)
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_flip")
    return {
        **observation,
        "contains_text": branch_a,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"branch_a": branch_a},
    }


def _action_pdf_upload(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        file_paths=[str(fixtures["pdf"])],
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_image_upload(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        image_paths=[str(fixtures["image"])],
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_image_generation(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_deep_research(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    if not adapter.trigger_deep_research(driver):
        raise CaseSkip("deep_research_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_canvas(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    if not adapter.trigger_canvas(driver):
        # Canvas may not be pre-enabled; prompt may still open it automatically
        logger.info("canvas toggle unavailable; sending prompt anyway")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["canvas_ui_hint"] = adapter.wait_for_page_text(driver, ["Canvas", "画布"], timeout_s=8)
    return {**observation, **send, "contains_text": token}


def _action_gem(case, adapter, driver, token, _):
    gem_url = os.environ.get(GEM_URL_ENV, "").strip() or None
    if not adapter.navigate_to_gem(driver, gem_url=gem_url):
        raise CaseSkip(f"gem_unavailable; set {GEM_URL_ENV} or keep at least one Gem visible")
    _ensure_input_ready(adapter, driver, retry_navigation=False)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"]["gem_url"] = driver.current_url
    send["notes"]["gem_id"] = adapter.current_gem_id(driver)
    return {**observation, **send, "contains_text": token}


def _action_extensions(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    # Prefix with @Gmail to hint Extensions
    send = _perform_send(
        case, adapter, driver,
        prompt=f"@Gmail {prompt}",
    )
    send["notes"]["extensions_hint"] = adapter.page_contains_any_text(
        driver, ["@Gmail", "@Drive", "@Docs"]
    )
    return {**observation, **send, "contains_text": token}


def _action_shared_view(case, adapter, driver, _token, __):
    share_url = os.environ.get(SHARE_URL_ENV, "").strip()
    if not share_url:
        raise CaseSkip(f"share_url_not_set; set {SHARE_URL_ENV}")
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_share")
    if not adapter.navigate_to_shared(driver, share_url):
        raise CaseFailure("shared_navigation_failed")
    time.sleep(3)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_share")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"share_url": driver.current_url},
        "expect_no_new_captures": True,
    }


def _action_settings_page(case, adapter, driver, _, __):
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_settings")
    if not adapter.navigate_to_settings(driver):
        raise CaseFailure("settings_navigation_failed")
    time.sleep(2)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_settings")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"current_url": driver.current_url},
        "expect_no_new_captures": True,
    }


def _action_error_state(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_error")
    prompt = case.prompt_template.format(id=case.id, token=token)
    if not adapter.force_error(driver, prompt):
        raise CaseSkip("error_state_not_detectable_current_ui")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_error")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"error_prompt": token},
        "expect_no_new_captures": True,
    }


def _action_activity_page(case, adapter, driver, _, __):
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_activity")
    if not adapter.navigate_to_activity(driver):
        raise CaseFailure("activity_navigation_failed")
    time.sleep(2)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_activity")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"current_url": driver.current_url},
        "expect_no_new_captures": True,
    }


ACTIONS = {
    "vanilla_text": _action_vanilla_text,
    "stream_complete": _action_stream_complete,
    "stream_start": _action_stream_start,
    "new_chat": _action_new_chat,
    "code_block": _action_code_block,
    "thinking_model": _action_thinking_model,
    "edit_user_message": _action_edit_user_message,
    "regenerate": _action_regenerate,
    "branch_flip": _action_branch_flip,
    "pdf_upload": _action_pdf_upload,
    "image_upload": _action_image_upload,
    "image_generation": _action_image_generation,
    "deep_research": _action_deep_research,
    "canvas": _action_canvas,
    "gem": _action_gem,
    "extensions": _action_extensions,
    "shared_view": _action_shared_view,
    "settings_page": _action_settings_page,
    "error_state": _action_error_state,
    "activity_page": _action_activity_page,
}


def _verify_case(case: GeminiCase, action_result: dict[str, Any]) -> dict[str, Any]:
    verification: dict[str, Any] = {}
    provider = GeminiAdapter.provider
    expect_no_new = action_result.get("expect_no_new_captures", case.expect_no_new_captures)

    if expect_no_new:
        result = wait_for_no_new_captures(
            initial_count=action_result["initial_count"],
            initial_provider_count=action_result["initial_provider_count"],
            timeout_s=case.capture_timeout_s,
            poll_interval=1.5,
            provider=provider,
        )
        if not result["success"]:
            raise CaseFailure(
                f"unexpected_new_capture provider_new={result['provider_new_count']}"
            )
        verification["no_new_captures"] = result
        return verification

    capture_result = wait_for_new_captures(
        initial_count=action_result["initial_count"],
        initial_provider_count=action_result["initial_provider_count"],
        timeout_s=case.capture_timeout_s,
        poll_interval=2,
        min_new=1,
        provider=provider,
    )
    if not capture_result["success"]:
        raise CaseFailure(
            f"no_new_captures provider_new={capture_result.get('provider_new_count')}"
        )
    verification["new_captures"] = capture_result

    contains_text = action_result.get("contains_text")
    raw_required = action_result.get("raw_required_attachment_types", case.raw_required_attachment_types)
    if contains_text or raw_required:
        raw_result = wait_for_conversation_capture_matching(
            provider=provider,
            contains_text=contains_text,
            required_attachment_types=raw_required,
            timeout_s=case.capture_timeout_s,
            poll_interval=2,
            created_after=action_result["started_at"] - 5,
        )
        if not raw_result["success"]:
            raise CaseFailure(
                f"raw_capture_mismatch attachments={raw_result['attachment_types_by_role']}"
            )
        verification["raw_capture"] = raw_result

    session_required = action_result.get(
        "session_required_attachment_types",
        case.session_required_attachment_types,
    )
    session_result = wait_for_session_matching(
        provider=provider,
        contains_text=contains_text,
        required_roles=set(case.required_roles),
        required_attachment_types=session_required,
        min_messages=2,
        timeout_s=case.session_timeout_s,
        poll_interval=2,
        started_after=action_result["started_at"] - 5,
    )
    if not session_result["success"]:
        raise CaseFailure(
            f"session_mismatch attachments={session_result['attachment_types_by_role']}"
        )
    verification["session"] = session_result

    quality = verify_message_quality(session_result["messages"])
    if not quality["ok"]:
        raise CaseFailure(f"message_quality_failed issues={quality['issues']}")
    verification["quality"] = quality

    rich = verify_rich_content(session_result["messages"])
    verification["rich_content"] = rich

    if case.required_text_fragments:
        joined = "\n".join((m.get("content_text") or "") for m in session_result["messages"])
        missing = [frag for frag in case.required_text_fragments if frag not in joined]
        if missing:
            raise CaseFailure(f"missing_text_fragments {missing}")
        verification["required_text_fragments"] = list(case.required_text_fragments)

    return verification


def _finalize_visual_review(case: GeminiCase, action_result: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    after_path = (action_result.get("screenshots") or {}).get("after")
    if case.visual_review_required and after_path:
        checks["after_png"] = assert_canvas_in_screenshot(after_path)
    return checks


@pytest.mark.parametrize("case", _selected_cases(), ids=lambda c: c.id)
def test_gemini_full(case: GeminiCase, driver, generated_fixtures, gemini_adapter: GeminiAdapter, gemini_report_dir: Path, gemini_preflight):
    token = f"{case.id}-{int(time.time())}"
    report_path = gemini_report_dir / f"{case.id}.json"
    record: dict[str, Any] = {
        "case_id": case.id,
        "description": case.description,
        "account_tier": ACCOUNT_TIER,
        "status": "started",
        "started_at": time.time(),
        "token": token,
        "visual_review_required": case.visual_review_required,
        "preflight": gemini_preflight,
    }

    try:
        action_result = ACTIONS[case.action](case, gemini_adapter, driver, token, generated_fixtures)
        verification = _verify_case(case, action_result)
        visual = _finalize_visual_review(case, action_result)
        record.update({
            "status": "pass",
            "action": case.action,
            "action_result": action_result,
            "verification": verification,
            "visual_checks": visual,
        })
        _write_json(report_path, record)
    except CaseSkip as exc:
        record.update({"status": "skip", "reason": str(exc)})
        _write_json(report_path, record)
        pytest.skip(f"{case.id}: {exc}")
    except Exception as exc:
        record.update({"status": "fail", "reason": str(exc)})
        try:
            record["failure_screenshot"] = gemini_adapter.take_screenshot(
                driver, f"{case.id.lower()}_failure",
            )
        except Exception:
            pass
        _write_json(report_path, record)
        raise
    finally:
        record["finished_at"] = time.time()
        record["elapsed_s"] = round(record["finished_at"] - record["started_at"], 1)
        _write_json(report_path, record)
