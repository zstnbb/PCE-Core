# SPDX-License-Identifier: Apache-2.0
"""Full-fidelity Google AI Studio capture E2E matrix for PCE.

Mirrors ``test_chatgpt_full.py`` / ``test_claude_full.py`` /
``test_gemini_full.py`` but customised for Google AI Studio's
developer-facing surfaces: chat / freeform / structured prompt
variants, tools (grounding / URL context / code exec / function
calling), system instructions, /gallery / /library / /tune / /apikey
browsing (must not capture), modals (Get code / Save to Drive).

Set ``PCE_GAS_CASES=A01,A02,...`` to run a subset.
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
from .sites.google_ai_studio import GoogleAIStudioAdapter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.e2e.gas_full")

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures" / "rich_content"
GENERATED_FIXTURES_DIR = ROOT / "fixtures" / "generated"
REPORTS_ROOT = ROOT / "reports" / "googleaistudio"

SAMPLE_IMAGE = FIXTURES_DIR / "sample_square.png"

ACCOUNT_TIER = os.environ.get("GAS_ACCOUNT_TIER", "default").strip().lower() or "default"


class CaseSkip(RuntimeError):
    pass


class CaseFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class GASCase:
    id: str
    description: str
    action: str
    prompt_template: str = ""
    raw_required_attachment_types: dict[str, set[str]] = field(default_factory=dict)
    session_required_attachment_types: dict[str, set[str]] = field(default_factory=dict)
    required_text_fragments: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ("user", "assistant")
    response_timeout_s: float = 90
    capture_timeout_s: float = 35
    session_timeout_s: float = 45
    visual_review_required: bool = False
    expect_no_new_captures: bool = False


CASES: list[GASCase] = [
    GASCase(
        id="A01",
        description="Chat prompt (multi-turn)",
        action="vanilla_chat",
        prompt_template="PCE-{id}-{token}. Reply with exactly: ACK {token}",
    ),
    GASCase(
        id="A02",
        description="Freeform (Create) prompt",
        action="freeform",
        prompt_template=(
            "PCE-{id}-{token}. Summarize the following: 'Hello world' in one sentence containing {token}."
        ),
    ),
    GASCase(
        id="A03",
        description="Structured prompt extraction",
        action="structured",
        prompt_template="PCE-{id}-{token}. Reply with STRUCTURED {token}",
        visual_review_required=True,
    ),
    GASCase(
        id="A04",
        description="Streaming response",
        action="stream_complete",
        prompt_template=(
            "PCE-{id}-{token}. Write 15 short bullet points about testing. "
            "Include {token} in the first bullet."
        ),
    ),
    GASCase(
        id="A05",
        description="Code block in reply",
        action="code_block",
        prompt_template=(
            "PCE-{id}-{token}. Return only a fenced python code block that prints '{token}'."
        ),
        raw_required_attachment_types={"assistant": {"code_block"}},
        session_required_attachment_types={"assistant": {"code_block"}},
    ),
    GASCase(
        id="A06",
        description="2.5 Pro Thinking visibility",
        action="thinking_model",
        prompt_template=(
            "PCE-{id}-{token}. Think step-by-step about 17*19 and show your reasoning. "
            "End the answer with {token}."
        ),
        required_text_fragments=("<thinking>",),
        response_timeout_s=120,
        session_timeout_s=60,
    ),
    GASCase(
        id="A07",
        description="PDF upload",
        action="pdf_upload",
        prompt_template=(
            "PCE-{id}-{token}. Confirm the PDF filename in one sentence containing {token}."
        ),
        raw_required_attachment_types={"user": {"file"}},
        session_required_attachment_types={"user": {"file"}},
    ),
    GASCase(
        id="A08",
        description="Image upload",
        action="image_upload",
        prompt_template=(
            "PCE-{id}-{token}. Describe the uploaded image in one sentence containing {token}."
        ),
        raw_required_attachment_types={"user": {"image_url"}},
        session_required_attachment_types={"user": {"image_url"}},
    ),
    GASCase(
        id="A09",
        description="Video upload (optional)",
        action="video_upload",
        prompt_template=(
            "PCE-{id}-{token}. Describe what you see in the uploaded video in one sentence with {token}."
        ),
        visual_review_required=True,
    ),
    GASCase(
        id="A10",
        description="Audio upload (optional)",
        action="audio_upload",
        prompt_template=(
            "PCE-{id}-{token}. Transcribe the uploaded audio in one sentence containing {token}."
        ),
        visual_review_required=True,
    ),
    GASCase(
        id="A11",
        description="System instructions in layer_meta",
        action="system_instructions",
        prompt_template="PCE-{id}-{token}. Reply with SYSTEM {token}",
        visual_review_required=True,
    ),
    GASCase(
        id="A12",
        description="Grounding with Google Search citations",
        action="grounding_search",
        prompt_template=(
            "PCE-{id}-{token}. Using grounded search, tell me one fact about PCE "
            "test run {token}. Include at least one citation."
        ),
        raw_required_attachment_types={"assistant": {"citation"}},
        session_required_attachment_types={"assistant": {"citation"}},
        response_timeout_s=120,
    ),
    GASCase(
        id="A13",
        description="URL context tool",
        action="url_context",
        prompt_template=(
            "PCE-{id}-{token}. Summarize https://example.com in one sentence containing {token}."
        ),
        response_timeout_s=120,
    ),
    GASCase(
        id="A14",
        description="Code execution tool",
        action="code_execution",
        prompt_template=(
            "PCE-{id}-{token}. Compute the sum of squares of 1..10 using code execution. "
            "Include {token}."
        ),
        raw_required_attachment_types={"assistant": {"code_block"}},
        session_required_attachment_types={"assistant": {"code_block"}},
        response_timeout_s=120,
    ),
    GASCase(
        id="A15",
        description="Function calling tool",
        action="function_calling",
        prompt_template="PCE-{id}-{token}. If you can, call a simple function. Include {token}.",
        response_timeout_s=120,
        visual_review_required=True,
    ),
    GASCase(
        id="A16",
        description="Imagen image generation",
        action="image_generation",
        prompt_template=(
            "PCE-{id}-{token}. Generate a simple blue square image. Caption with {token}."
        ),
        raw_required_attachment_types={"assistant": {"image_url"}},
        session_required_attachment_types={"assistant": {"image_url"}},
        response_timeout_s=180,
        capture_timeout_s=60,
        session_timeout_s=60,
    ),
    GASCase(
        id="A17",
        description="Edit / regenerate user prompt",
        action="edit_and_regenerate",
    ),
    GASCase(
        id="A18",
        description="Modals must NOT capture (Get code)",
        action="modal_get_code",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=10,
    ),
    GASCase(
        id="A19",
        description="Browse /gallery /library /tune /apikey — no captures",
        action="browse_pages",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=12,
    ),
    GASCase(
        id="A20",
        description="Frontend error state",
        action="error_state",
        prompt_template="PCE-{id}-{token}. Offline request should not capture.",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=12,
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


def _selected_cases() -> list[GASCase]:
    raw = os.environ.get("PCE_GAS_CASES", "").strip()
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
        pytest.fail("PCE Core server not reachable at http://127.0.0.1:9800")


@pytest.fixture(scope="session", autouse=True)
def optional_reset(ensure_pce_running):
    if os.environ.get("PCE_E2E_NO_RESET", "").strip() == "1":
        return
    reset_baseline()


@pytest.fixture(scope="session")
def generated_fixtures() -> dict[str, Path]:
    return _ensure_generated_fixtures()


@pytest.fixture(scope="session")
def gas_report_dir() -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    report_dir = REPORTS_ROOT / ts
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


@pytest.fixture(scope="session")
def gas_adapter() -> GoogleAIStudioAdapter:
    return GoogleAIStudioAdapter()


@pytest.fixture(scope="session")
def gas_preflight(driver, gas_adapter: GoogleAIStudioAdapter, gas_report_dir: Path):
    features = gas_adapter.detect_features(driver)
    payload = {
        "generated_at": time.time(),
        "account_tier": ACCOUNT_TIER,
        "features": features,
    }
    _write_json(gas_report_dir / "preflight.json", payload)
    return payload


@pytest.fixture(scope="session", autouse=True)
def write_summary(gas_report_dir: Path):
    yield
    cases: list[dict[str, Any]] = []
    for path in sorted(gas_report_dir.glob("A*.json")):
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
    _write_json(gas_report_dir / "summary.json", summary)


def _begin_observation(provider: str) -> dict[str, Any]:
    stats = get_stats()
    return {
        "started_at": time.time(),
        "initial_count": stats["total_captures"],
        "initial_provider_count": stats.get("by_provider", {}).get(provider, 0),
    }


def _ensure_input_ready(adapter: GoogleAIStudioAdapter, driver, *, retry: bool = True) -> None:
    if adapter.find_input(driver):
        return
    if retry:
        time.sleep(2)
        if not adapter.navigate(driver):
            raise CaseFailure("navigation_failed")
        if adapter.find_input(driver):
            return
    raise CaseFailure("input_not_found")


def _navigate_chat(adapter: GoogleAIStudioAdapter, driver) -> dict[str, Any]:
    if not adapter.navigate_to_new_chat(driver):
        raise CaseFailure("navigation_failed")
    _ensure_input_ready(adapter, driver, retry=True)
    return {"current_url": driver.current_url}


def _perform_send(
    case: GASCase,
    adapter: GoogleAIStudioAdapter,
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
    screenshots = {"before": adapter.take_screenshot(driver, f"{prefix}_before_send")}
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


def _action_vanilla_chat(case, adapter, driver, token, _):
    notes = _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_freeform(case, adapter, driver, token, _):
    if not adapter.navigate_to_new_freeform(driver):
        raise CaseFailure("freeform_navigation_failed")
    _ensure_input_ready(adapter, driver, retry=True)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"]["url"] = driver.current_url
    return {**observation, **send, "contains_text": token}


def _action_structured(case, adapter, driver, token, _):
    if not adapter.navigate_to_new_structured(driver):
        raise CaseFailure("structured_navigation_failed")
    _ensure_input_ready(adapter, driver, retry=True)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"]["url"] = driver.current_url
    return {**observation, **send, "contains_text": token}


def _action_stream_complete(case, adapter, driver, token, _):
    notes = _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_code_block(case, adapter, driver, token, _):
    return _action_vanilla_chat(case, adapter, driver, token, _)


def _action_thinking_model(case, adapter, driver, token, _):
    notes = _navigate_chat(adapter, driver)
    if not adapter.switch_model(driver, ["2.5 Pro", "Pro Thinking", "Thinking"]):
        raise CaseSkip("thinking_model_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_pdf_upload(case, adapter, driver, token, fixtures):
    _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        file_paths=[str(fixtures["pdf"])],
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_image_upload(case, adapter, driver, token, fixtures):
    _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        image_paths=[str(fixtures["image"])],
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_video_upload(case, adapter, driver, token, fixtures):
    # Reuse image fixture as placeholder; GAS accepts generic file uploads
    _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        file_paths=[str(fixtures["image"])],
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_audio_upload(case, adapter, driver, token, fixtures):
    _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt,
        file_paths=[str(fixtures["image"])],
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_system_instructions(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    sys_text = f"You are a PCE test bot. Always include the token {token} verbatim."
    if not adapter.set_system_instructions(driver, sys_text):
        raise CaseSkip("system_instructions_input_not_found")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"]["system_instructions"] = sys_text
    return {**observation, **send, "contains_text": token}


def _action_grounding_search(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    if not adapter.toggle_tool(driver, ["Grounding", "Google Search"]):
        raise CaseSkip("grounding_tool_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_url_context(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    if not adapter.toggle_tool(driver, ["URL context", "URL"]):
        raise CaseSkip("url_context_tool_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_code_execution(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    if not adapter.toggle_tool(driver, ["Code execution", "Code exec"]):
        raise CaseSkip("code_execution_tool_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_function_calling(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    if not adapter.toggle_tool(driver, ["Function calling", "Functions"]):
        raise CaseSkip("function_calling_tool_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_image_generation(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    # Try to switch to Imagen; skip if not offered
    if not adapter.switch_model(driver, ["Imagen", "imagen"]):
        raise CaseSkip("imagen_model_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_edit_and_regenerate(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    original_token = f"{token}-ORIG"
    original_prompt = (
        f"PCE-{case.id}-{original_token}. Reply with one sentence containing {original_token}."
    )
    _perform_send(
        case, adapter, driver, prompt=original_prompt,
        trigger_manual_capture=False,
        screenshot_prefix=f"{case.id.lower()}_seed",
    )
    observation = _begin_observation(adapter.provider)
    updated_prompt = (
        f"PCE-{case.id}-{token}. Reply with one sentence containing {token}."
    )
    if not adapter.edit_last_user_message(driver, updated_prompt):
        # Fall back to regenerate if edit UI not available
        if not adapter.click_regenerate(driver):
            raise CaseFailure("edit_or_regenerate_failed")
    if not adapter.wait_for_response(driver):
        raise CaseFailure("edited_response_not_received")
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_edit")
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"after": after_ss},
        "notes": {"original_token": original_token, "edited_token": token},
    }


def _action_modal_get_code(case, adapter, driver, _token, __):
    _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_modal")
    if not adapter.click_get_code(driver):
        raise CaseSkip("get_code_button_unavailable")
    time.sleep(2)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_modal_open")
    adapter.close_modal(driver)
    time.sleep(1)
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "expect_no_new_captures": True,
    }


def _action_browse_pages(case, adapter, driver, _token, __):
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_browse")
    visited = []
    for navigator in (
        adapter.navigate_to_gallery,
        adapter.navigate_to_library,
        adapter.navigate_to_tune,
        adapter.navigate_to_apikey,
    ):
        try:
            if navigator(driver):
                visited.append(driver.current_url)
            time.sleep(2)
        except Exception as exc:
            logger.info("browse navigation failed for %s: %s", navigator.__name__, exc)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_browse")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"visited": visited},
        "expect_no_new_captures": True,
    }


def _action_error_state(case, adapter, driver, token, _):
    _navigate_chat(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_error")
    prompt = case.prompt_template.format(id=case.id, token=token)
    if not adapter.force_error(driver, prompt):
        raise CaseFailure("error_state_not_detected")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_error")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"error_prompt": token},
        "expect_no_new_captures": True,
    }


ACTIONS = {
    "vanilla_chat": _action_vanilla_chat,
    "freeform": _action_freeform,
    "structured": _action_structured,
    "stream_complete": _action_stream_complete,
    "code_block": _action_code_block,
    "thinking_model": _action_thinking_model,
    "pdf_upload": _action_pdf_upload,
    "image_upload": _action_image_upload,
    "video_upload": _action_video_upload,
    "audio_upload": _action_audio_upload,
    "system_instructions": _action_system_instructions,
    "grounding_search": _action_grounding_search,
    "url_context": _action_url_context,
    "code_execution": _action_code_execution,
    "function_calling": _action_function_calling,
    "image_generation": _action_image_generation,
    "edit_and_regenerate": _action_edit_and_regenerate,
    "modal_get_code": _action_modal_get_code,
    "browse_pages": _action_browse_pages,
    "error_state": _action_error_state,
}


def _verify_case(case: GASCase, action_result: dict[str, Any]) -> dict[str, Any]:
    verification: dict[str, Any] = {}
    provider = GoogleAIStudioAdapter.provider
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


def _finalize_visual_review(case: GASCase, action_result: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    after_path = (action_result.get("screenshots") or {}).get("after")
    if case.visual_review_required and after_path:
        checks["after_png"] = assert_canvas_in_screenshot(after_path)
    return checks


@pytest.mark.parametrize("case", _selected_cases(), ids=lambda c: c.id)
def test_gas_full(case: GASCase, driver, generated_fixtures, gas_adapter: GoogleAIStudioAdapter, gas_report_dir: Path, gas_preflight):
    token = f"{case.id}-{int(time.time())}"
    report_path = gas_report_dir / f"{case.id}.json"
    record: dict[str, Any] = {
        "case_id": case.id,
        "description": case.description,
        "account_tier": ACCOUNT_TIER,
        "status": "started",
        "started_at": time.time(),
        "token": token,
        "visual_review_required": case.visual_review_required,
        "preflight": gas_preflight,
    }

    try:
        action_result = ACTIONS[case.action](case, gas_adapter, driver, token, generated_fixtures)
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
            record["failure_screenshot"] = gas_adapter.take_screenshot(
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
