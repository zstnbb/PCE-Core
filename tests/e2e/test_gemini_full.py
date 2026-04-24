# SPDX-License-Identifier: Apache-2.0
"""Full-fidelity Gemini capture E2E matrix for PCE.

Mirrors ``test_chatgpt_full.py`` / ``test_claude_full.py`` but
customised for Gemini's surfaces: Gems, Canvas, Imagen, Deep
Research, Extensions, 2.5 Pro Thinking, /share/ skip.

Set ``PCE_GEMINI_CASES=G01,G02,...`` to run a subset.
Set ``PCE_GEMINI_GEM_URL=https://gemini.google.com/gem/<slug>`` for G15.
Set ``PCE_GEMINI_SHARE_URL=https://gemini.google.com/share/<hex>`` for G17.
Set ``PCE_GEMINI_RUN_EXPENSIVE_MEDIA=1`` to let G29/G30 submit video/music
generation prompts instead of smoke-testing the menu entry only.
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
from selenium.webdriver.common.by import By

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
RUN_EXPENSIVE_MEDIA = os.environ.get("PCE_GEMINI_RUN_EXPENSIVE_MEDIA", "").strip() == "1"

PLUS_UPLOAD_FILE = ("Upload files", "Upload file", "\u4e0a\u4f20\u6587\u4ef6")
PLUS_DRIVE = (
    "Add from Drive",
    "Google Drive",
    "Drive",
    "\u4ece\u4e91\u7aef\u786c\u76d8\u6dfb\u52a0",
    "\u4e91\u7aef\u786c\u76d8",
)
PLUS_PHOTOS = ("Photos", "Google Photos", "\u76f8\u518c")
PLUS_IMPORT_CODE = ("Import code", "Code", "\u5bfc\u5165\u4ee3\u7801")
PLUS_NOTEBOOKLM = ("NotebookLM", "Notebook LM")

TOOL_IMAGE = ("Create image", "Make image", "Generate image", "\u5236\u4f5c\u56fe\u7247")
TOOL_CANVAS = ("Canvas",)
TOOL_DEEP_RESEARCH = ("Deep Research", "\u6df1\u5ea6\u7814\u7a76")
TOOL_VIDEO = (
    "Create video",
    "Make video",
    "Video",
    "\u5236\u4f5c\u89c6\u9891",
    "\u521b\u4f5c\u89c6\u9891",
)
TOOL_MUSIC = (
    "Create music",
    "Make music",
    "Music",
    "\u5236\u4f5c\u97f3\u4e50",
    "\u521b\u4f5c\u97f3\u4e50",
)
TOOL_LEARNING = (
    "Learning tutor",
    "Learning coach",
    "Learning",
    "Help me learn",
    "\u5b66\u4e60\u8f85\u5bfc",
    "\u5e2e\u6211\u5b66\u4e60",
)
TOOL_LABS_PERSONALIZATION = (
    "Personalization",
    "Personalized help",
    "Labs",
    "\u4e2a\u6027\u5316\u667a\u80fd\u670d\u52a1",
    "\u4e2a\u6027\u5316\u6709\u5e2e\u52a9\u65f6",
)
ZERO_STATE_BOOTSTRAP = (
    "\u968f\u4fbf\u5199\u70b9\u4ec0\u4e48",
    "Write anything",
    "Just write something",
    "\u7ed9\u6211\u7684\u4e00\u5929\u6ce8\u5165\u6d3b\u529b",
)


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
        response_timeout_s=90,
        session_timeout_s=60,
        visual_review_required=True,
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
        response_timeout_s=420,
        capture_timeout_s=120,
        session_timeout_s=120,
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
    GeminiCase(
        id="G21",
        description="+ menu upload file entry",
        action="plus_upload_file",
        prompt_template=(
            "PCE-{id}-{token}. Confirm the uploaded PDF filename in one short "
            "sentence and include {token}."
        ),
        raw_required_attachment_types={"user": {"file"}},
        session_required_attachment_types={"user": {"file"}},
        visual_review_required=True,
    ),
    GeminiCase(
        id="G22",
        description="+ menu Google Drive picker entry",
        action="plus_drive_entry",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=8,
    ),
    GeminiCase(
        id="G23",
        description="+ menu Google Photos picker entry",
        action="plus_photos_entry",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=8,
    ),
    GeminiCase(
        id="G24",
        description="+ menu import code entry",
        action="plus_import_code",
        prompt_template=(
            "PCE-{id}-{token}. Summarize the uploaded code file in one short "
            "sentence and include {token}."
        ),
        raw_required_attachment_types={"user": {"file"}},
        session_required_attachment_types={"user": {"file"}},
        visual_review_required=True,
    ),
    GeminiCase(
        id="G25",
        description="+ menu NotebookLM entry",
        action="plus_notebooklm_entry",
        visual_review_required=True,
        expect_no_new_captures=True,
        capture_timeout_s=8,
    ),
    GeminiCase(
        id="G26",
        description="Tools menu Create image entry",
        action="tool_create_image",
        prompt_template=(
            "PCE-{id}-{token}. Create a simple image of a green circle. "
            "Also include a one-sentence caption containing {token}."
        ),
        raw_required_attachment_types={"assistant": {"image_url"}},
        session_required_attachment_types={"assistant": {"image_url"}},
        response_timeout_s=420,
        capture_timeout_s=120,
        session_timeout_s=120,
        visual_review_required=True,
    ),
    GeminiCase(
        id="G27",
        description="Tools menu Canvas entry",
        action="tool_canvas",
        prompt_template=(
            "PCE-{id}-{token}. Use Canvas to draft a short project checklist "
            "titled {token}."
        ),
        response_timeout_s=120,
        capture_timeout_s=60,
        session_timeout_s=60,
        visual_review_required=True,
    ),
    GeminiCase(
        id="G28",
        description="Tools menu Deep Research entry",
        action="tool_deep_research",
        prompt_template=(
            "PCE-{id}-{token}. Use Deep Research to give a concise summary of "
            "browser extension testing. Include {token}."
        ),
        response_timeout_s=360,
        capture_timeout_s=90,
        session_timeout_s=120,
        visual_review_required=True,
    ),
    GeminiCase(
        id="G29",
        description="Tools menu Create video entry",
        action="tool_video",
        prompt_template=(
            "PCE-{id}-{token}. Create a very short abstract video of a blue square "
            "moving slowly. Include {token} in the caption."
        ),
        visual_review_required=True,
        expect_no_new_captures=not RUN_EXPENSIVE_MEDIA,
        response_timeout_s=420,
        capture_timeout_s=90 if RUN_EXPENSIVE_MEDIA else 8,
        session_timeout_s=120,
    ),
    GeminiCase(
        id="G30",
        description="Tools menu Create music entry",
        action="tool_music",
        prompt_template=(
            "PCE-{id}-{token}. Create a very short calm instrumental loop. "
            "Include {token} in the caption."
        ),
        visual_review_required=True,
        expect_no_new_captures=not RUN_EXPENSIVE_MEDIA,
        response_timeout_s=420,
        capture_timeout_s=90 if RUN_EXPENSIVE_MEDIA else 8,
        session_timeout_s=120,
    ),
    GeminiCase(
        id="G31",
        description="Tools menu Learning tutor entry",
        action="tool_learning_tutor",
        prompt_template=(
            "PCE-{id}-{token}. Use learning tutor mode to explain one testing "
            "concept in two sentences. Include {token}."
        ),
        response_timeout_s=120,
        capture_timeout_s=60,
        session_timeout_s=60,
        visual_review_required=True,
    ),
    GeminiCase(
        id="G32",
        description="Tools menu Labs personalization entry",
        action="tool_labs_personalization",
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
    code_path = GENERATED_FIXTURES_DIR / "sample_code.py"
    if not code_path.exists():
        code_path.write_text(
            "def pce_fixture_token():\n"
            "    return 'PCE Gemini import-code fixture'\n",
            encoding="utf-8",
        )
    return {"pdf": pdf_path, "image": SAMPLE_IMAGE, "code": code_path}


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
    adapter.dismiss_blocking_dialogs(driver)
    adapter.clear_selected_tool(driver)
    if adapter.find_input(driver):
        return
    if retry_navigation:
        time.sleep(2)
        if not adapter.navigate(driver):
            raise CaseFailure("navigation_failed")
        adapter.dismiss_blocking_dialogs(driver)
        adapter.clear_selected_tool(driver)
        if adapter.find_input(driver):
            return
    raise CaseFailure("input_not_found")


def _navigate_home(adapter: GeminiAdapter, driver) -> dict[str, Any]:
    if not adapter.navigate(driver):
        raise CaseFailure("navigation_failed")
    adapter.dismiss_blocking_dialogs(driver)
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
    assistant_text_before = adapter.last_turn_text(driver, role="assistant")
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
        response_ok = (
            adapter.wait_for_response(
                driver,
                previous_assistant_count=assistant_before,
                previous_assistant_text=assistant_text_before,
            )
            if wait_response else True
        )
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


def _bootstrap_zero_state_response(adapter: GeminiAdapter, driver) -> None:
    assistant_before = adapter.turn_count(driver, role="assistant")
    assistant_text_before = adapter.last_turn_text(driver, role="assistant")
    if not adapter._click_text_entry(driver, ZERO_STATE_BOOTSTRAP, timeout_s=3):
        raise CaseSkip("zero_state_seed_unavailable")
    if not adapter.wait_for_response(
        driver,
        previous_assistant_count=assistant_before,
        previous_assistant_text=assistant_text_before,
    ):
        raise CaseFailure("zero_state_seed_response_not_received")
    time.sleep(2)


def _perform_send_resilient(
    case: GeminiCase,
    adapter: GeminiAdapter,
    driver,
    *,
    attempts: int = 2,
    reset_fn=None,
    **kwargs,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _perform_send(case, adapter, driver, **kwargs)
        except CaseFailure as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            if reset_fn is not None:
                reset_fn()
            time.sleep(2)
    if last_exc is not None:
        raise last_exc
    raise CaseFailure("send_retry_exhausted")


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
    if not adapter.switch_model(driver, ["思考", "Thinking", "Think"]):
        raise CaseSkip("thinking_model_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    try:
        send = _perform_send(
            case, adapter, driver, prompt=prompt, wait_stream=True,
            response_timeout_s=case.response_timeout_s,
        )
    except CaseFailure as exc:
        raise CaseSkip(f"thinking_response_unavailable:{exc}") from exc
    finally:
        try:
            adapter.switch_model(driver, ["快速", "Fast"])
        except Exception:
            pass
    send["notes"]["thinking_ui_hint"] = adapter.page_contains_any_text(
        driver,
        ["思考", "Thinking"],
    )
    if not send["notes"]["thinking_ui_hint"]:
        raise CaseSkip("thinking_indicator_not_visible")
    send["notes"].update(notes)
    return {**observation, **send, "contains_text": token}


def _action_edit_user_message(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    original_token = f"{token}-ORIG"
    original_prompt = f"PCE-{case.id}-{original_token}. Reply with exactly: {original_token}"
    _perform_send_resilient(
        case,
        adapter,
        driver,
        prompt=original_prompt,
        trigger_manual_capture=False,
        screenshot_prefix=f"{case.id.lower()}_seed",
        reset_fn=lambda: _navigate_home(adapter, driver),
    )
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_edit")
    updated_prompt = f"PCE-{case.id}-{token}. Reply with exactly: {token}"
    assistant_before = adapter.turn_count(driver, role="assistant")
    assistant_text_before = adapter.last_turn_text(driver, role="assistant")
    if not adapter.edit_last_user_message(driver, updated_prompt):
        raise CaseFailure("edit_message_failed")
    response_ok = adapter.wait_for_response(
        driver,
        previous_assistant_count=assistant_before,
        previous_assistant_text=assistant_text_before,
    )
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_edit")
    if not response_ok:
        raise CaseFailure("edited_response_not_received")
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"edited_token": token},
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
    assistant_before = adapter.turn_count(driver, role="assistant")
    assistant_text_before = adapter.last_turn_text(driver, role="assistant")
    if not adapter.click_regenerate(driver):
        raise CaseFailure("regenerate_click_failed")
    stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=12)
    response_ok = adapter.wait_for_response(
        driver,
        previous_assistant_count=assistant_before,
        previous_assistant_text=assistant_text_before,
    )
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
    first_prompt = f"PCE-{case.id}-{branch_a}. Reply with exactly: BRANCH-A {branch_a}"
    _perform_send_resilient(
        case,
        adapter,
        driver,
        prompt=first_prompt,
        trigger_manual_capture=False,
        screenshot_prefix=f"{case.id.lower()}_branch_a",
        response_timeout_s=case.response_timeout_s,
        reset_fn=lambda: _navigate_home(adapter, driver),
    )
    if not adapter.click_regenerate(driver):
        raise CaseFailure("branch_regenerate_unavailable")
    stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=12)
    time.sleep(4)
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
        "notes": {"branch_a": branch_a, "stream_seen": stream_seen},
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


def _perform_image_generation(case, adapter, driver, token, *, tool_details=None):
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_send")
    assistant_before = adapter.turn_count(driver, role="assistant")
    assistant_text_before = adapter.last_turn_text(driver, role="assistant")
    old_timeout = adapter.response_timeout_s
    adapter.response_timeout_s = max(adapter.response_timeout_s, case.response_timeout_s)
    try:
        if not adapter.send_message(driver, message=prompt):
            raise CaseFailure("send_failed")
        stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=15)
        response_ok = adapter.wait_for_response(
            driver,
            previous_assistant_count=assistant_before,
            previous_assistant_text=assistant_text_before,
        )
        image_ok = adapter.wait_for_image_generation_complete(
            driver,
            timeout_s=case.response_timeout_s,
        )
    finally:
        adapter.response_timeout_s = old_timeout
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_response")
    if not response_ok:
        raise CaseFailure("response_not_received")
    if not image_ok:
        raise CaseFailure("image_generation_not_completed")
    adapter.trigger_manual_capture(driver)
    time.sleep(3)
    notes = {
        "stream_seen": stream_seen,
        "response_received": response_ok,
        "image_generation_complete": image_ok,
    }
    if tool_details is not None:
        notes["tool_entry"] = tool_details
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": notes,
    }


def _action_image_generation(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = adapter.select_tool_entry(driver, labels=TOOL_IMAGE)
    return _perform_image_generation(case, adapter, driver, token, tool_details=details)


def _action_deep_research(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = _select_tool_or_skip(adapter, driver, TOOL_DEEP_RESEARCH, "deep_research_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case, adapter, driver, prompt=prompt, wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["tool_entry"] = details
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
    send = _perform_send_resilient(
        case,
        adapter,
        driver,
        prompt=prompt,
        reset_fn=lambda: (
            adapter.navigate_to_gem(driver, gem_url=gem_url),
            _ensure_input_ready(adapter, driver, retry_navigation=False),
        ),
    )
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


def _prepare_share_url(case, adapter, driver) -> str:
    share_url = os.environ.get(SHARE_URL_ENV, "").strip()
    if share_url:
        return share_url

    share_url = adapter.create_share_url(driver)
    if share_url:
        return share_url

    _navigate_home(adapter, driver)
    seed_token = f"{case.id}-share-seed-{int(time.time())}"
    prompt = f"PCE-{case.id}-{seed_token}. Reply with one sentence containing {seed_token}."
    _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        screenshot_prefix=f"{case.id.lower()}_share_seed",
        response_timeout_s=case.response_timeout_s,
    )
    share_url = adapter.create_share_url(driver)
    if not share_url:
        for candidate in adapter._find_elements_safe(driver, By.CSS_SELECTOR, 'a[data-test-id="conversation"], a[href*="/app/"]'):
            try:
                href = candidate.get_attribute("href") or ""
                if "/app/" not in href:
                    continue
                driver.get(href)
                time.sleep(2)
                share_url = adapter.create_share_url(driver)
                if share_url:
                    break
            except Exception:
                continue
    if not share_url:
        raise CaseSkip("share_url_unavailable_current_ui")
    return share_url


def _action_shared_view(case, adapter, driver, _token, __):
    share_url = _prepare_share_url(case, adapter, driver)
    settle_stats = get_stats()
    wait_for_no_new_captures(
        initial_count=settle_stats["total"],
        initial_provider_count=settle_stats["by_provider"].get(adapter.provider, 0),
        timeout_s=4,
        poll_interval=1.0,
        provider=adapter.provider,
    )
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


def _send_prepared_prompt(
    case: GeminiCase,
    adapter: GeminiAdapter,
    driver,
    *,
    prompt: str,
    before_ss: str,
    response_timeout_s: float | None = None,
) -> dict[str, Any]:
    old_timeout = adapter.response_timeout_s
    assistant_before = adapter.turn_count(driver, role="assistant")
    assistant_text_before = adapter.last_turn_text(driver, role="assistant")
    if response_timeout_s:
        adapter.response_timeout_s = max(adapter.response_timeout_s, response_timeout_s)
    try:
        if not adapter.send_message(driver, message=prompt):
            raise CaseFailure("send_failed")
        stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=12)
        response_ok = adapter.wait_for_response(
            driver,
            previous_assistant_count=assistant_before,
            previous_assistant_text=assistant_text_before,
        )
    finally:
        adapter.response_timeout_s = old_timeout

    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_response")
    if not response_ok:
        raise CaseFailure("response_not_received")
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    return {
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"stream_seen": stream_seen, "response_received": response_ok},
    }


def _require_entry_clicked(details: dict[str, Any], reason: str) -> None:
    if not details.get("clicked"):
        raise CaseSkip(reason)


def _action_plus_upload_file(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_upload_menu")
    uploaded = adapter.upload_from_attachment_menu(
        driver,
        labels=PLUS_UPLOAD_FILE,
        paths=[str(fixtures["pdf"])],
    )
    if not uploaded:
        raise CaseSkip("plus_upload_file_entry_unavailable_or_file_input_missing")
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _send_prepared_prompt(
        case,
        adapter,
        driver,
        prompt=prompt,
        before_ss=before_ss,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["entry_labels"] = list(PLUS_UPLOAD_FILE)
    return {**observation, **send, "contains_text": token}


def _action_plus_drive_entry(case, adapter, driver, _token, __):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_drive_entry")
    details = adapter.activate_attachment_entry(
        driver,
        labels=PLUS_DRIVE,
        evidence_labels=PLUS_DRIVE,
    )
    _require_entry_clicked(details, "plus_drive_entry_unavailable")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_drive_entry")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": details,
        "expect_no_new_captures": True,
    }


def _action_plus_photos_entry(case, adapter, driver, _token, __):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_photos_entry")
    details = adapter.activate_attachment_entry(
        driver,
        labels=PLUS_PHOTOS,
        evidence_labels=PLUS_PHOTOS,
    )
    _require_entry_clicked(details, "plus_photos_entry_unavailable")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_photos_entry")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": details,
        "expect_no_new_captures": True,
    }


def _action_plus_import_code(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_import_code")
    uploaded = adapter.upload_from_attachment_menu(
        driver,
        labels=PLUS_IMPORT_CODE,
        paths=[str(fixtures["code"])],
    )
    if not uploaded:
        raise CaseSkip("plus_import_code_entry_unavailable_or_file_input_missing")
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _send_prepared_prompt(
        case,
        adapter,
        driver,
        prompt=prompt,
        before_ss=before_ss,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["entry_labels"] = list(PLUS_IMPORT_CODE)
    return {**observation, **send, "contains_text": token}


def _action_plus_notebooklm_entry(case, adapter, driver, _token, __):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_notebooklm")
    details = adapter.activate_attachment_entry(
        driver,
        labels=PLUS_NOTEBOOKLM,
        evidence_labels=PLUS_NOTEBOOKLM,
        allow_new_tab=True,
    )
    _require_entry_clicked(details, "plus_notebooklm_entry_unavailable")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_notebooklm")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": details,
        "expect_no_new_captures": True,
    }


def _select_tool_or_skip(adapter, driver, labels: tuple[str, ...], reason: str) -> dict[str, Any]:
    details = adapter.select_tool_entry(driver, labels=labels)
    _require_entry_clicked(details, reason)
    if not details.get("labels_seen"):
        raise CaseSkip(reason)
    return details


def _action_tool_create_image(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = _select_tool_or_skip(adapter, driver, TOOL_IMAGE, "tool_create_image_entry_unavailable")
    return _perform_image_generation(case, adapter, driver, token, tool_details=details)


def _action_tool_canvas(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = _select_tool_or_skip(adapter, driver, TOOL_CANVAS, "tool_canvas_entry_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["tool_entry"] = details
    send["notes"]["canvas_ui_hint"] = adapter.wait_for_page_text(driver, ["Canvas"], timeout_s=8)
    return {**observation, **send, "contains_text": token}


def _action_tool_deep_research(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = _select_tool_or_skip(
        adapter,
        driver,
        TOOL_DEEP_RESEARCH,
        "tool_deep_research_entry_unavailable",
    )
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        wait_stream=True,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["tool_entry"] = details
    return {**observation, **send, "contains_text": token}


def _action_tool_media(case, adapter, driver, token, labels, reason):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_tool_entry")
    details = _select_tool_or_skip(adapter, driver, labels, reason)
    if not RUN_EXPENSIVE_MEDIA:
        after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_tool_entry")
        adapter.clear_selected_tool(driver)
        return {
            **observation,
            "screenshots": {"before": before_ss, "after": after_ss},
            "notes": {
                **details,
                "entry_only": True,
                "reason": "expensive_media_generation_disabled",
            },
            "expect_no_new_captures": True,
        }

    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _send_prepared_prompt(
        case,
        adapter,
        driver,
        prompt=prompt,
        before_ss=before_ss,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["tool_entry"] = details
    return {**observation, **send, "contains_text": token}


def _action_tool_video(case, adapter, driver, token, _):
    return _action_tool_media(
        case,
        adapter,
        driver,
        token,
        TOOL_VIDEO,
        "tool_video_entry_unavailable",
    )


def _action_tool_music(case, adapter, driver, token, _):
    return _action_tool_media(
        case,
        adapter,
        driver,
        token,
        TOOL_MUSIC,
        "tool_music_entry_unavailable",
    )


def _action_tool_learning_tutor(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = _select_tool_or_skip(
        adapter,
        driver,
        TOOL_LEARNING,
        "tool_learning_tutor_entry_unavailable",
    )
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["tool_entry"] = details
    return {**observation, **send, "contains_text": token}


def _action_tool_labs_personalization(case, adapter, driver, _token, __):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_labs")
    details = adapter.inspect_tools_entry(driver, labels=TOOL_LABS_PERSONALIZATION)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_labs")
    if not details.get("opened"):
        raise CaseSkip("tools_menu_unavailable_for_labs_personalization")
    if not details.get("labels_seen"):
        raise CaseSkip("labs_personalization_entry_unavailable")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": details,
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
    "plus_upload_file": _action_plus_upload_file,
    "plus_drive_entry": _action_plus_drive_entry,
    "plus_photos_entry": _action_plus_photos_entry,
    "plus_import_code": _action_plus_import_code,
    "plus_notebooklm_entry": _action_plus_notebooklm_entry,
    "tool_create_image": _action_tool_create_image,
    "tool_canvas": _action_tool_canvas,
    "tool_deep_research": _action_tool_deep_research,
    "tool_video": _action_tool_video,
    "tool_music": _action_tool_music,
    "tool_learning_tutor": _action_tool_learning_tutor,
    "tool_labs_personalization": _action_tool_labs_personalization,
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
    verification["new_captures"] = capture_result

    contains_text = action_result.get("contains_text")
    raw_required = action_result.get("raw_required_attachment_types", case.raw_required_attachment_types)
    raw_ok = False
    raw_result: dict[str, Any] | None = None
    if contains_text or raw_required:
        raw_result = wait_for_conversation_capture_matching(
            provider=provider,
            contains_text=contains_text,
            required_attachment_types=raw_required,
            timeout_s=case.capture_timeout_s,
            poll_interval=2,
            created_after=action_result["started_at"] - 5,
        )
        raw_ok = bool(raw_result["success"])
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
    session_ok = bool(session_result["success"])

    if not capture_result["success"] and not raw_ok and not session_ok:
        raise CaseFailure(
            f"no_new_captures provider_new={capture_result.get('provider_new_count')}"
        )

    if (contains_text or raw_required) and not raw_ok:
        raise CaseFailure(
            f"raw_capture_mismatch attachments={raw_result['attachment_types_by_role']}"
        )

    if not session_ok:
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
