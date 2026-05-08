# SPDX-License-Identifier: Apache-2.0
"""Full-fidelity Perplexity capture E2E matrix for PCE.

Set ``PCE_PERPLEXITY_CASES=P01,P02,...`` to run a subset.
Set ``PERPLEXITY_ACCOUNT_TIER=free|pro|max|enterprise`` to gate paid cases.
Set ``PCE_PERPLEXITY_SPACE_URL`` / ``PCE_PERPLEXITY_SHARE_URL`` for manual URL
cases. Set ``PCE_PERPLEXITY_RUN_MEDIA=1`` to submit expensive media generation
instead of entry-only evidence.
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
from .sites.perplexity import PerplexityAdapter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.e2e.perplexity_full")

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures" / "rich_content"
GENERATED_FIXTURES_DIR = ROOT / "fixtures" / "generated"
REPORTS_ROOT = ROOT / "reports" / "perplexity"

SAMPLE_IMAGE = FIXTURES_DIR / "sample_square.png"
ACCOUNT_TIER = os.environ.get("PERPLEXITY_ACCOUNT_TIER", "pro").strip().lower() or "pro"
SPACE_URL_ENV = "PCE_PERPLEXITY_SPACE_URL"
SHARE_URL_ENV = "PCE_PERPLEXITY_SHARE_URL"
MEDIA_FILE_ENV = "PCE_PERPLEXITY_MEDIA_FILE"
RUN_MEDIA = os.environ.get("PCE_PERPLEXITY_RUN_MEDIA", "").strip() == "1"

FREE_ACCESS_TIERS = {
    "free_core",
    "free_or_pro_mode",
    "no_capture",
    "manual_url",
    "free_error_state",
}

MODE_PRO = ("Pro", "Search", "Quick")
MODE_ACADEMIC = ("Academic",)
MODE_FINANCE = ("Finance", "SEC")
MODE_SOCIAL = ("Social", "Reddit")
MODE_RESEARCH = ("Research", "Deep Research")
MODE_CREATE = ("Create", "Create files and apps", "Images", "Charts")
MODEL_LABELS = ("Sonar", "Best", "GPT", "Claude", "Gemini")


class CaseSkip(RuntimeError):
    pass


class CaseFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class PerplexityCase:
    id: str
    description: str
    action: str
    access_tier: str = "free_core"
    access_note: str = "Expected to work on a free Perplexity account when search execution is available."
    prompt_template: str = ""
    raw_required_attachment_types: dict[str, set[str]] = field(default_factory=dict)
    session_required_attachment_types: dict[str, set[str]] = field(default_factory=dict)
    required_roles: tuple[str, ...] = ("user", "assistant")
    min_messages: int = 2
    response_timeout_s: float = 75
    capture_timeout_s: float = 35
    session_timeout_s: float = 45
    visual_review_required: bool = False
    expect_no_new_captures: bool = False
    expect_no_error_assistant: bool = False


CASES: list[PerplexityCase] = [
    PerplexityCase(
        id="P01",
        description="Quick/default search",
        action="vanilla_search",
        prompt_template="PCE-{id}-{token}. In one sentence, define Python and include {token}.",
    ),
    PerplexityCase(
        id="P02",
        description="Streaming answer",
        action="stream_complete",
        prompt_template=(
            "PCE-{id}-{token}. Write 18 short bullets about search result quality. "
            "Include {token} in bullet one."
        ),
    ),
    PerplexityCase(
        id="P03",
        description="New thread",
        action="new_thread",
        prompt_template="PCE-{id}-{token}. Reply with NEW THREAD {token}.",
    ),
    PerplexityCase(
        id="P04",
        description="Follow-up in same thread",
        action="follow_up",
        min_messages=4,
    ),
    PerplexityCase(
        id="P05",
        description="Assistant code block",
        action="code_block",
        prompt_template=(
            "PCE-{id}-{token}. Return only a fenced python code block that prints '{token}'."
        ),
        raw_required_attachment_types={"assistant": {"code_block"}},
        session_required_attachment_types={"assistant": {"code_block"}},
    ),
    PerplexityCase(
        id="P06",
        description="Pro/Search mode",
        action="mode_pro",
        access_tier="pro_core",
        access_note="Requires Pro/Search mode visibility; skip on free-only accounts if unavailable.",
        prompt_template="PCE-{id}-{token}. Use search to answer what Perplexity is. Include {token}.",
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P07",
        description="Model selector",
        action="model_selector",
        access_tier="pro_core",
        access_note="Requires account plan/UI exposing model selector.",
        prompt_template="PCE-{id}-{token}. Reply with MODEL {token}.",
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P08",
        description="Academic source/mode",
        action="mode_academic",
        access_tier="free_or_pro_mode",
        prompt_template="PCE-{id}-{token}. Find one academic-style fact about testing and include {token}.",
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P09",
        description="Finance/SEC source/mode",
        action="mode_finance",
        access_tier="free_or_pro_mode",
        prompt_template="PCE-{id}-{token}. Give one finance data caveat and include {token}.",
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P10",
        description="Social/Reddit source/mode",
        action="mode_social",
        access_tier="free_or_pro_mode",
        prompt_template="PCE-{id}-{token}. Summarize one social discussion caveat and include {token}.",
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P11",
        description="PDF upload",
        action="pdf_upload",
        access_tier="pro_upload",
        access_note="File upload may require Pro, plan quota, or workspace policy.",
        prompt_template="PCE-{id}-{token}. Confirm the PDF filename and include {token}.",
        raw_required_attachment_types={"user": {"file"}},
        session_required_attachment_types={"user": {"file"}},
    ),
    PerplexityCase(
        id="P12",
        description="Image upload",
        action="image_upload",
        access_tier="pro_upload",
        access_note="Image upload may require Pro, plan quota, or workspace policy.",
        prompt_template="PCE-{id}-{token}. Describe the uploaded image and include {token}.",
        raw_required_attachment_types={"user": {"image_url"}},
        session_required_attachment_types={"user": {"image_url"}},
    ),
    PerplexityCase(
        id="P13",
        description="Audio/video upload",
        action="media_upload",
        access_tier="optional_upload",
        access_note=f"Optional; provide a real media file via {MEDIA_FILE_ENV}.",
        prompt_template="PCE-{id}-{token}. Summarize the uploaded media if possible and include {token}.",
        raw_required_attachment_types={"user": {"file"}},
        session_required_attachment_types={"user": {"file"}},
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P14",
        description="Structured citations",
        action="citations",
        prompt_template=(
            "PCE-{id}-{token}. In two sentences, explain what the Python Software Foundation is. "
            "Include {token} and cite sources."
        ),
        raw_required_attachment_types={"assistant": {"citation"}},
        session_required_attachment_types={"assistant": {"citation"}},
    ),
    PerplexityCase(
        id="P15",
        description="Related question follow-up",
        action="related_question",
        min_messages=4,
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P16",
        description="Research mode",
        action="research_mode",
        access_tier="research",
        access_note="Requires Research/Deep Research mode visibility and quota.",
        prompt_template=(
            "PCE-{id}-{token}. Use Research mode for a concise report on browser extension testing. "
            "Include {token}."
        ),
        response_timeout_s=300,
        capture_timeout_s=90,
        session_timeout_s=120,
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P17",
        description="Create files and apps",
        action="create_entry",
        access_tier="pro_create",
        access_note="Create files/apps may be Pro/Max gated; entry evidence is enough unless RUN_MEDIA=1.",
        prompt_template="PCE-{id}-{token}. Create a tiny checklist file titled {token}.",
        response_timeout_s=180,
        visual_review_required=True,
        expect_no_new_captures=not RUN_MEDIA,
    ),
    PerplexityCase(
        id="P18",
        description="Space-scoped thread",
        action="space_thread",
        access_tier="space_surface",
        access_note=f"Uses {SPACE_URL_ENV} if set, otherwise first visible Space.",
        prompt_template="PCE-{id}-{token}. Reply with SPACE {token}.",
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P19",
        description="Shared/read-only thread no capture",
        action="shared_view",
        access_tier="manual_url",
        access_note=f"Requires {SHARE_URL_ENV}.",
        expect_no_new_captures=True,
        visual_review_required=True,
        capture_timeout_s=12,
    ),
    PerplexityCase(
        id="P20",
        description="Library/history no capture",
        action="library_page",
        access_tier="no_capture",
        expect_no_new_captures=True,
        visual_review_required=True,
        capture_timeout_s=10,
    ),
    PerplexityCase(
        id="P21",
        description="Settings/profile no capture",
        action="settings_page",
        access_tier="no_capture",
        expect_no_new_captures=True,
        visual_review_required=True,
        capture_timeout_s=10,
    ),
    PerplexityCase(
        id="P22",
        description="Error/quota state",
        action="error_state",
        access_tier="free_error_state",
        prompt_template="PCE-{id}-{token}. Offline request should not become assistant content.",
        expect_no_error_assistant=True,
        visual_review_required=True,
        capture_timeout_s=12,
    ),
    PerplexityCase(
        id="P23",
        description="Image generation",
        action="image_generation",
        access_tier="pro_media",
        access_note="Generated image path may be Pro/Max gated or quota limited.",
        prompt_template="PCE-{id}-{token}. Generate a simple image of a blue square and caption it with {token}.",
        raw_required_attachment_types={"assistant": {"image_url"}},
        session_required_attachment_types={"assistant": {"image_url"}},
        response_timeout_s=300,
        capture_timeout_s=90,
        session_timeout_s=120,
        visual_review_required=True,
    ),
    PerplexityCase(
        id="P24",
        description="Video generation",
        action="video_generation",
        access_tier="pro_media_expensive",
        access_note="Entry-only unless PCE_PERPLEXITY_RUN_MEDIA=1.",
        prompt_template="PCE-{id}-{token}. Generate a very short abstract video and caption it with {token}.",
        response_timeout_s=420,
        capture_timeout_s=90 if RUN_MEDIA else 10,
        session_timeout_s=120,
        visual_review_required=True,
        expect_no_new_captures=not RUN_MEDIA,
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


def _selected_cases() -> list[PerplexityCase]:
    raw = os.environ.get("PCE_PERPLEXITY_CASES", "").strip()
    if raw:
        wanted = {part.strip().upper() for part in raw.split(",") if part.strip()}
        return [case for case in CASES if case.id.upper() in wanted]
    if os.environ.get("PCE_PERPLEXITY_FREE_ONLY", "").strip() == "1":
        return [case for case in CASES if case.access_tier in FREE_ACCESS_TIERS]
    return CASES


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


def _ensure_generated_fixtures() -> dict[str, Path | None]:
    GENERATED_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = GENERATED_FIXTURES_DIR / "perplexity_sample.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(_build_minimal_pdf("PCE Perplexity PDF fixture"))
    media_env = os.environ.get(MEDIA_FILE_ENV, "").strip()
    media_path = Path(media_env).resolve() if media_env else None
    if media_path and not media_path.is_file():
        media_path = None
    return {
        "pdf": pdf_path,
        "image": SAMPLE_IMAGE if SAMPLE_IMAGE.is_file() else None,
        "media": media_path,
    }


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
def generated_fixtures() -> dict[str, Path | None]:
    return _ensure_generated_fixtures()


@pytest.fixture(scope="session")
def perplexity_report_dir() -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    report_dir = REPORTS_ROOT / ts
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


@pytest.fixture(scope="session")
def perplexity_adapter() -> PerplexityAdapter:
    return PerplexityAdapter()


@pytest.fixture(scope="session")
def perplexity_preflight(driver, perplexity_adapter: PerplexityAdapter, perplexity_report_dir: Path):
    features = perplexity_adapter.detect_features(driver)
    payload = {
        "generated_at": time.time(),
        "account_tier": ACCOUNT_TIER,
        "features": features,
        "space_url_env_present": bool(os.environ.get(SPACE_URL_ENV, "").strip()),
        "share_url_env_present": bool(os.environ.get(SHARE_URL_ENV, "").strip()),
        "run_media": RUN_MEDIA,
    }
    _write_json(perplexity_report_dir / "preflight.json", payload)
    return payload


@pytest.fixture(scope="session", autouse=True)
def write_summary(perplexity_report_dir: Path):
    yield
    cases: list[dict[str, Any]] = []
    case_paths = [
        path
        for path in perplexity_report_dir.glob("*.json")
        if path.stem.upper().startswith("P") and path.stem[1:].isdigit()
    ]
    for path in sorted(case_paths):
        try:
            cases.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            cases.append({"case_id": path.stem, "status": "broken_report", "error": str(exc)})
    status_counts: dict[str, int] = {}
    access_tier_counts: dict[str, dict[str, int]] = {}
    visual_review = []
    for case in cases:
        status = case.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        access_tier = case.get("access_tier", "unknown")
        bucket = access_tier_counts.setdefault(access_tier, {})
        bucket[status] = bucket.get(status, 0) + 1
        if case.get("visual_review_required"):
            visual_review.append(case.get("case_id"))
    summary = {
        "generated_at": time.time(),
        "account_tier": ACCOUNT_TIER,
        "case_count": len(cases),
        "status_counts": status_counts,
        "access_tier_counts": access_tier_counts,
        "visual_review_required": visual_review,
        "cases": cases,
    }
    _write_json(perplexity_report_dir / "summary.json", summary)


def _begin_observation(provider: str) -> dict[str, Any]:
    stats = get_stats()
    return {
        "started_at": time.time(),
        "initial_count": stats["total_captures"],
        "initial_provider_count": stats.get("by_provider", {}).get(provider, 0),
    }


def _messages_from_capture(capture: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(capture.get("body_text_or_json") or "{}")
    except Exception:
        return []
    messages = payload.get("messages")
    return messages if isinstance(messages, list) else []


def _skip_if_account_gated(case: PerplexityCase) -> None:
    if ACCOUNT_TIER in {"free", "free_core"} and case.access_tier not in FREE_ACCESS_TIERS:
        raise CaseSkip(f"account_tier_{ACCOUNT_TIER}_cannot_run_{case.access_tier}")


def _skip_if_environment_gated(case: PerplexityCase, preflight: dict[str, Any]) -> None:
    features = preflight.get("features") or {}
    if features.get("security_challenge") and case.action != "library_page":
        raise CaseSkip("perplexity_security_challenge")


def _navigate_home(adapter: PerplexityAdapter, driver) -> None:
    if not adapter.navigate_to_new_thread(driver):
        if adapter.is_security_challenge(driver):
            raise CaseSkip("perplexity_security_challenge")
        raise CaseFailure("navigation_failed")
    if not adapter.find_input(driver):
        if adapter.is_security_challenge(driver):
            raise CaseSkip("perplexity_security_challenge")
        raise CaseFailure("input_not_found")


def _perform_send(
    case: PerplexityCase,
    adapter: PerplexityAdapter,
    driver,
    *,
    prompt: str,
    response_timeout_s: float | None = None,
    trigger_manual_capture: bool = True,
    screenshot_prefix: str | None = None,
) -> dict[str, Any]:
    prefix = screenshot_prefix or case.id.lower()
    before_ss = adapter.take_screenshot(driver, f"{prefix}_before_send")
    old_timeout = adapter.response_timeout_s
    assistant_before = adapter.turn_count(driver, "assistant")
    assistant_text_before = adapter.last_turn_text(driver, "assistant")
    if response_timeout_s:
        adapter.response_timeout_s = max(adapter.response_timeout_s, response_timeout_s)
    try:
        if not adapter.send_message(driver, message=prompt):
            raise CaseFailure("send_failed")
        stream_seen = adapter.wait_for_stop_button_visible(driver, timeout_s=10)
        response_ok = adapter.wait_for_response(
            driver,
            previous_assistant_count=assistant_before,
            previous_assistant_text=assistant_text_before,
        )
    finally:
        adapter.response_timeout_s = old_timeout
    after_ss = adapter.take_screenshot(driver, f"{prefix}_after_response")
    if not response_ok:
        raise CaseFailure("response_not_received")
    if trigger_manual_capture:
        adapter.trigger_manual_capture(driver)
        time.sleep(2)
    return {
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {
            "stream_seen": stream_seen,
            "response_received": response_ok,
            "current_url": driver.current_url,
            "thread_id": adapter.current_thread_id(driver),
        },
    }


def _perform_upload_send(
    case: PerplexityCase,
    adapter: PerplexityAdapter,
    driver,
    *,
    prompt: str,
    paths: list[str],
    kind: str,
) -> dict[str, Any]:
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_upload")
    uploaded = adapter.upload_paths(driver, paths, kind=kind)
    if not uploaded:
        uploaded = adapter.upload_via_paste(driver, paths, kind=kind)
    if not uploaded:
        raise CaseSkip(f"{kind}_upload_unavailable")
    send = _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        response_timeout_s=case.response_timeout_s,
        screenshot_prefix=f"{case.id.lower()}_uploaded",
    )
    send["screenshots"]["before"] = before_ss
    return send


def _action_vanilla_search(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    return {**observation, **send, "contains_text": token}


def _action_stream_complete(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        response_timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_new_thread(case, adapter, driver, token, _):
    return _action_vanilla_search(case, adapter, driver, token, _)


def _action_follow_up(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    seed_token = f"{case.id}-SEED-{int(time.time())}"
    seed = f"PCE-{case.id}-{seed_token}. Answer with one sentence containing {seed_token}."
    _perform_send(case, adapter, driver, prompt=seed, screenshot_prefix=f"{case.id.lower()}_seed")
    observation = _begin_observation(adapter.provider)
    follow = f"PCE-{case.id}-{token}. Follow up: repeat only FOLLOW {token}."
    send = _perform_send(case, adapter, driver, prompt=follow)
    send["notes"]["seed_token"] = seed_token
    return {**observation, **send, "contains_text": token}


def _action_code_block(case, adapter, driver, token, _):
    return _action_vanilla_search(case, adapter, driver, token, _)


def _action_mode(case, adapter, driver, token, _, labels: tuple[str, ...], reason: str):
    _navigate_home(adapter, driver)
    details = adapter.select_mode(driver, labels)
    if not details.get("clicked"):
        raise CaseSkip(reason)
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        case,
        adapter,
        driver,
        prompt=prompt,
        response_timeout_s=case.response_timeout_s,
    )
    send["notes"]["mode"] = details
    return {**observation, **send, "contains_text": token}


def _action_mode_pro(case, adapter, driver, token, fixtures):
    return _action_mode(case, adapter, driver, token, fixtures, MODE_PRO, "pro_search_mode_unavailable")


def _action_mode_academic(case, adapter, driver, token, fixtures):
    return _action_mode(case, adapter, driver, token, fixtures, MODE_ACADEMIC, "academic_mode_unavailable")


def _action_mode_finance(case, adapter, driver, token, fixtures):
    return _action_mode(case, adapter, driver, token, fixtures, MODE_FINANCE, "finance_mode_unavailable")


def _action_mode_social(case, adapter, driver, token, fixtures):
    return _action_mode(case, adapter, driver, token, fixtures, MODE_SOCIAL, "social_mode_unavailable")


def _action_model_selector(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = adapter.switch_model(driver, MODEL_LABELS)
    if not details.get("opened"):
        raise CaseSkip("model_selector_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt)
    send["notes"]["model_selector"] = details
    return {**observation, **send, "contains_text": token}


def _action_pdf_upload(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    pdf = fixtures.get("pdf")
    if not pdf:
        raise CaseSkip("pdf_fixture_missing")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_upload_send(case, adapter, driver, prompt=prompt, paths=[str(pdf)], kind="file")
    return {**observation, **send, "contains_text": token}


def _action_image_upload(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    image = fixtures.get("image")
    if not image:
        raise CaseSkip("image_fixture_missing")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_upload_send(case, adapter, driver, prompt=prompt, paths=[str(image)], kind="image")
    return {**observation, **send, "contains_text": token}


def _action_media_upload(case, adapter, driver, token, fixtures):
    _navigate_home(adapter, driver)
    media = fixtures.get("media")
    if not media:
        raise CaseSkip(f"media_fixture_missing_set_{MEDIA_FILE_ENV}")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_upload_send(case, adapter, driver, prompt=prompt, paths=[str(media)], kind="file")
    return {**observation, **send, "contains_text": token}


def _action_citations(case, adapter, driver, token, fixtures):
    return _action_vanilla_search(case, adapter, driver, token, fixtures)


def _action_related_question(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    seed_token = f"{case.id}-SEED-{int(time.time())}"
    seed = f"PCE-{case.id}-{seed_token}. Explain what browser automation is. Include {seed_token}."
    _perform_send(case, adapter, driver, prompt=seed, screenshot_prefix=f"{case.id.lower()}_seed")
    observation = _begin_observation(adapter.provider)
    assistant_before = adapter.turn_count(driver, "assistant")
    if not adapter.click_related_question(driver):
        raise CaseSkip("related_question_unavailable")
    if not adapter.wait_for_response(driver, previous_assistant_count=assistant_before):
        raise CaseFailure("related_response_not_received")
    adapter.trigger_manual_capture(driver)
    time.sleep(2)
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_related")
    return {
        **observation,
        "contains_text": seed_token,
        "screenshots": {"after": after_ss},
        "notes": {"seed_token": seed_token, "related_clicked": True},
    }


def _action_research_mode(case, adapter, driver, token, fixtures):
    return _action_mode(case, adapter, driver, token, fixtures, MODE_RESEARCH, "research_mode_unavailable")


def _action_create_entry(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_create")
    details = adapter.click_create_entry(driver, MODE_CREATE)
    if not details.get("clicked"):
        raise CaseSkip("create_entry_unavailable")
    if not RUN_MEDIA:
        after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_create_entry")
        return {
            **observation,
            "screenshots": {"before": before_ss, "after": after_ss},
            "notes": {**details, "entry_only": True},
            "expect_no_new_captures": True,
        }
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt, response_timeout_s=case.response_timeout_s)
    send["notes"]["create_entry"] = details
    return {**observation, **send, "contains_text": token}


def _action_space_thread(case, adapter, driver, token, _):
    space_url = os.environ.get(SPACE_URL_ENV, "").strip() or None
    if not adapter.navigate_to_space_thread(driver, space_url):
        raise CaseSkip("space_thread_unavailable")
    if not adapter.find_input(driver):
        raise CaseSkip("space_thread_input_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt, response_timeout_s=case.response_timeout_s)
    send["notes"]["space_url"] = driver.current_url
    return {**observation, **send, "contains_text": token}


def _action_shared_view(case, adapter, driver, _token, __):
    share_url = os.environ.get(SHARE_URL_ENV, "").strip()
    if not share_url:
        raise CaseSkip(f"{SHARE_URL_ENV}_not_set")
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_share")
    if not adapter.navigate_to_shared(driver, share_url):
        raise CaseFailure("shared_navigation_failed")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_share")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"current_url": driver.current_url},
        "expect_no_new_captures": True,
    }


def _action_library_page(case, adapter, driver, _token, __):
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_library")
    if not adapter.navigate_to_library(driver):
        raise CaseFailure("library_navigation_failed")
    after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_library")
    return {
        **observation,
        "screenshots": {"before": before_ss, "after": after_ss},
        "notes": {"current_url": driver.current_url},
        "expect_no_new_captures": True,
    }


def _action_settings_page(case, adapter, driver, _token, __):
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_settings")
    if not adapter.navigate_to_settings(driver):
        if adapter.is_security_challenge(driver):
            raise CaseSkip("perplexity_security_challenge")
        raise CaseFailure("settings_navigation_failed")
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
        "expect_no_error_assistant": True,
    }


def _action_image_generation(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    details = adapter.click_create_entry(driver, ("Image", "Create image", "Generate image"))
    if not details.get("clicked"):
        raise CaseSkip("image_generation_entry_unavailable")
    observation = _begin_observation(adapter.provider)
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt, response_timeout_s=case.response_timeout_s)
    send["notes"]["image_generation_entry"] = details
    return {**observation, **send, "contains_text": token}


def _action_video_generation(case, adapter, driver, token, _):
    _navigate_home(adapter, driver)
    observation = _begin_observation(adapter.provider)
    before_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_before_video")
    details = adapter.click_create_entry(driver, ("Video", "Create video", "Generate video"))
    if not details.get("clicked"):
        raise CaseSkip("video_generation_entry_unavailable")
    if not RUN_MEDIA:
        after_ss = adapter.take_screenshot(driver, f"{case.id.lower()}_after_video_entry")
        return {
            **observation,
            "screenshots": {"before": before_ss, "after": after_ss},
            "notes": {**details, "entry_only": True},
            "expect_no_new_captures": True,
        }
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(case, adapter, driver, prompt=prompt, response_timeout_s=case.response_timeout_s)
    send["notes"]["video_generation_entry"] = details
    return {**observation, **send, "contains_text": token}


ACTIONS = {
    "vanilla_search": _action_vanilla_search,
    "stream_complete": _action_stream_complete,
    "new_thread": _action_new_thread,
    "follow_up": _action_follow_up,
    "code_block": _action_code_block,
    "mode_pro": _action_mode_pro,
    "model_selector": _action_model_selector,
    "mode_academic": _action_mode_academic,
    "mode_finance": _action_mode_finance,
    "mode_social": _action_mode_social,
    "pdf_upload": _action_pdf_upload,
    "image_upload": _action_image_upload,
    "media_upload": _action_media_upload,
    "citations": _action_citations,
    "related_question": _action_related_question,
    "research_mode": _action_research_mode,
    "create_entry": _action_create_entry,
    "space_thread": _action_space_thread,
    "shared_view": _action_shared_view,
    "library_page": _action_library_page,
    "settings_page": _action_settings_page,
    "error_state": _action_error_state,
    "image_generation": _action_image_generation,
    "video_generation": _action_video_generation,
}


def _verify_case(case: PerplexityCase, action_result: dict[str, Any]) -> dict[str, Any]:
    verification: dict[str, Any] = {}
    provider = PerplexityAdapter.provider
    expect_no_new = action_result.get("expect_no_new_captures", case.expect_no_new_captures)

    if action_result.get("expect_no_error_assistant", case.expect_no_error_assistant):
        result = wait_for_new_captures(
            initial_count=action_result["initial_count"],
            initial_provider_count=action_result["initial_provider_count"],
            timeout_s=case.capture_timeout_s,
            poll_interval=1.5,
            min_new=1,
            provider=provider,
        )
        error_needles = (
            "offline",
            "network error",
            "try again",
            "something went wrong",
            "rate limit",
            "quota",
            "unavailable",
        )
        assistant_errors = []
        for capture in result.get("captures", []):
            for message in _messages_from_capture(capture):
                if (message.get("role") or "").lower() != "assistant":
                    continue
                content = (message.get("content") or "").lower()
                if any(needle in content for needle in error_needles):
                    assistant_errors.append(
                        {"capture_id": capture.get("id"), "content": message.get("content")}
                    )
        if assistant_errors:
            raise CaseFailure(f"error_banner_captured_as_assistant {assistant_errors}")
        verification["error_state_no_assistant_error"] = {
            "new_capture_seen": result.get("success", False),
            "provider_new_count": result.get("provider_new_count", 0),
            "assistant_error_count": 0,
            "captures_checked": len(result.get("captures", [])),
        }
        return verification

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
        min_messages=case.min_messages,
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
    verification["rich_content"] = verify_rich_content(session_result["messages"])
    return verification


def _finalize_visual_review(case: PerplexityCase, action_result: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    screenshots = action_result.get("screenshots") or {}
    if case.visual_review_required:
        candidates = [screenshots.get("after"), screenshots.get("before")]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                checks["png"] = assert_canvas_in_screenshot(candidate)
                break
            except AssertionError as exc:
                if not case.expect_no_new_captures:
                    raise exc
                checks["png_optional_warning"] = str(exc)
    return checks


@pytest.mark.parametrize("case", _selected_cases(), ids=lambda c: c.id)
def test_perplexity_full(
    case: PerplexityCase,
    driver,
    generated_fixtures,
    perplexity_adapter: PerplexityAdapter,
    perplexity_report_dir: Path,
    perplexity_preflight,
):
    token = f"{case.id}-{int(time.time())}"
    report_path = perplexity_report_dir / f"{case.id}.json"
    record: dict[str, Any] = {
        "case_id": case.id,
        "description": case.description,
        "account_tier": ACCOUNT_TIER,
        "access_tier": case.access_tier,
        "access_note": case.access_note,
        "status": "started",
        "started_at": time.time(),
        "token": token,
        "visual_review_required": case.visual_review_required,
        "preflight": perplexity_preflight,
    }

    try:
        _skip_if_account_gated(case)
        _skip_if_environment_gated(case, perplexity_preflight)
        action_result = ACTIONS[case.action](
            case,
            perplexity_adapter,
            driver,
            token,
            generated_fixtures,
        )
        record.update({"action": case.action, "action_result": action_result})
        _write_json(report_path, record)
        verification = _verify_case(case, action_result)
        visual = _finalize_visual_review(case, action_result)
        record.update({
            "status": "pass",
            "verification": verification,
            "visual_checks": visual,
        })
        _write_json(report_path, record)
    except CaseSkip as exc:
        record.update({"status": "skip", "reason": str(exc)})
        try:
            record["skip_screenshot"] = perplexity_adapter.take_screenshot(
                driver, f"{case.id.lower()}_skip",
            )
        except Exception:
            pass
        _write_json(report_path, record)
        pytest.skip(f"{case.id}: {exc}")
    except Exception as exc:
        record.update({"status": "fail", "reason": str(exc)})
        try:
            record["failure_screenshot"] = perplexity_adapter.take_screenshot(
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
