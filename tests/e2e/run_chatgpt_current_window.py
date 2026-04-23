"""Drive the existing logged-in ChatGPT window without restarting Chrome.

Run with:
    python -m tests.e2e.run_chatgpt_current_window

Optional:
    $env:PCE_CHATGPT_CASES="T18,T19,T20"
    $env:PCE_CHATGPT_GPT_URL="https://chatgpt.com/g/..."
    $env:PCE_CHATGPT_PROJECT_URL="https://chatgpt.com/projects/..."
"""

from __future__ import annotations

import json
import logging
import os
import re
import binascii
import struct
import time
from pathlib import Path
from typing import Any

try:
    from . import test_chatgpt_full as matrix
    from .capture_verifier import (
        _capture_messages,
        get_recent_captures,
        get_sessions,
        get_stats,
        pce_is_running,
    )
    from .chatgpt_cdp import ChatGPTCDPClient
except ImportError:
    from tests.e2e import test_chatgpt_full as matrix
    from tests.e2e.capture_verifier import (
        _capture_messages,
        get_recent_captures,
        get_sessions,
        get_stats,
        pce_is_running,
    )
    from tests.e2e.chatgpt_cdp import ChatGPTCDPClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.e2e.chatgpt_current_window")

ROOT = Path(__file__).resolve().parent
REPORTS_ROOT = ROOT / "reports" / "chatgpt"
SCREENSHOTS_ROOT = ROOT / "screenshots"
GPT_URL_ENV = "PCE_CHATGPT_GPT_URL"
PROJECT_URL_ENV = "PCE_CHATGPT_PROJECT_URL"
REGENERATE_SEED_URL_ENV = "PCE_CHATGPT_REGENERATE_URL"
BRANCH_SEED_URL_ENV = "PCE_CHATGPT_BRANCH_URL"
PROVIDER = "openai"


def _png_text_chunk(keyword: str, text: str) -> bytes:
    data = keyword.encode("latin-1", errors="replace") + b"\x00" + text.encode("utf-8")
    chunk_type = b"tEXt"
    crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _write_unique_png(source: Path, target: Path, token: str) -> Path:
    raw = source.read_bytes()
    marker = b"\x00\x00\x00\x00IEND"
    iend = raw.rfind(marker)
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or iend < 0:
        target.write_bytes(raw + f"\nPCE unique upload {token}\n".encode("utf-8"))
        return target
    target.write_bytes(raw[:iend] + _png_text_chunk("pce-token", token) + raw[iend:])
    return target


def _unique_upload_fixture(fixtures: dict[str, Path], key: str, token: str) -> Path:
    target_dir = matrix.GENERATED_FIXTURES_DIR / "current_window_uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_token = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in token)
    if key == "pdf":
        target = target_dir / f"sample_report_{safe_token}.pdf"
        target.write_bytes(matrix._build_minimal_pdf(f"PCE PDF fixture {token}"))
        return target
    if key == "csv":
        target = target_dir / f"sample_data_{safe_token}.csv"
        target.write_text(
            f"item,value\nalpha-{token},10\nbeta-{token},20\ngamma-{token},30\n",
            encoding="utf-8",
        )
        return target
    if key == "image":
        target = target_dir / f"sample_square_{safe_token}.png"
        return _write_unique_png(fixtures["image"], target, token)
    return fixtures[key]


def _begin_observation() -> dict[str, Any]:
    stats = get_stats()
    return {
        "started_at": time.time(),
        "initial_count": stats["total_captures"],
        "initial_provider_count": stats.get("by_provider", {}).get(PROVIDER, 0),
        "initial_session_count": len(get_sessions(last=200, provider=PROVIDER)),
    }


def _report_dir() -> Path:
    target = REPORTS_ROOT / time.strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _screenshot_path(case_id: str, label: str, token: str) -> Path:
    SCREENSHOTS_ROOT.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS_ROOT / f"chatgpt_{case_id.lower()}_{label}_{token}.png"


def _wait_for_assistant_token_capture(
    token: str,
    *,
    created_after: float,
    timeout_s: float = 120,
) -> dict[str, Any] | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for cap in get_recent_captures(last=80, provider=PROVIDER):
            if cap.get("direction") != "conversation":
                continue
            if cap.get("created_at") and cap["created_at"] < created_after:
                continue
            for msg in _capture_messages(cap):
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                if token in (msg.get("content") or ""):
                    return {
                        "capture_id": cap.get("id"),
                        "created_at": cap.get("created_at"),
                        "path": cap.get("path"),
                    }
        time.sleep(2)
    return None


def _wait_for_token_or_capture(
    client: ChatGPTCDPClient,
    *,
    previous_assistant_count: int,
    token: str,
    started_at: float,
    timeout_s: float,
) -> dict[str, Any]:
    try:
        result = client.wait_for_response_complete(
            previous_assistant_count=previous_assistant_count,
            token=token,
            timeout_s=timeout_s,
        )
        result["completion_source"] = "dom"
        return result
    except Exception:
        fallback = _wait_for_assistant_token_capture(
            token,
            created_after=started_at - 5,
            timeout_s=180,
        )
        if not fallback:
            raise
        return {
            "assistant_turns_after": client.assistant_turn_count(),
            "token_seen": True,
            "completion_source": "capture_fallback",
            "capture_fallback": fallback,
        }


def _perform_send(
    client: ChatGPTCDPClient,
    case: matrix.ChatGPTCase,
    *,
    token: str,
    prompt: str,
    wait_stream: bool = False,
    trigger_manual_capture: bool = True,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    before = client.take_screenshot(_screenshot_path(case.id, "before_send", token))
    sent_at = time.time()
    send = client.send_message(prompt)
    stream_seen = client.wait_for_stop_button(timeout_s=12) if wait_stream else False
    complete = _wait_for_token_or_capture(
        client,
        previous_assistant_count=send["assistant_turns_before"],
        token=token,
        started_at=sent_at,
        timeout_s=timeout_s or case.response_timeout_s,
    )
    after = client.take_screenshot(_screenshot_path(case.id, "after_response", token))
    if trigger_manual_capture:
        client.trigger_manual_capture()
        time.sleep(2)
    return {
        "screenshots": {"before": before, "after": after},
        "notes": {
            "stream_seen": stream_seen,
            "assistant_turns_before": send["assistant_turns_before"],
            "assistant_turns_after": complete["assistant_turns_after"],
            "response_received": True,
            "completion_source": complete.get("completion_source", "dom"),
        },
    }


def _wait_for_visible_text(client: ChatGPTCDPClient, text: str, *, timeout_s: float = 20) -> bool:
    client.wait_for(
        f"""
        (() => {{
          const text = {json.dumps(text, ensure_ascii=False)};
          return (document.body && document.body.innerText || '').includes(text);
        }})()
        """,
        timeout_s=timeout_s,
        poll_s=0.8,
        description=f"visible_text:{text}",
    )
    return True


def _wait_for_last_assistant_image(
    client: ChatGPTCDPClient, *, timeout_s: float = 180
) -> bool:
    client.wait_for(
        """
        (() => {
          const turns = [...document.querySelectorAll('[data-testid^="conversation-turn"][data-turn="assistant"]')]
            .filter((el) => el.offsetParent !== null);
          const roleNodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
            .filter((el) => el.offsetParent !== null);
          const last = turns[turns.length - 1] || roleNodes[roleNodes.length - 1] || null;
          if (!last) return false;
          return [...last.querySelectorAll('img')].some((img) => {
            const src = img.src || img.getAttribute('src') || '';
            if (!src || /avatar|icon|logo|favicon|emoji/i.test(src)) return false;
            const width = img.naturalWidth || img.width || img.getBoundingClientRect().width || 0;
            const height = img.naturalHeight || img.height || img.getBoundingClientRect().height || 0;
            return width >= 80 && height >= 80;
          });
        })()
        """,
        timeout_s=timeout_s,
        poll_s=2,
        description="assistant_generated_image",
    )
    return True


def _wait_for_last_assistant_external_link(
    client: ChatGPTCDPClient, *, timeout_s: float = 60
) -> bool:
    client.wait_for(
        """
        (() => {
          const turns = [...document.querySelectorAll('[data-testid^="conversation-turn"][data-turn="assistant"]')]
            .filter((el) => el.offsetParent !== null);
          const roleNodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')]
            .filter((el) => el.offsetParent !== null);
          const last = turns[turns.length - 1] || roleNodes[roleNodes.length - 1] || null;
          if (!last) return false;
          return [...last.querySelectorAll('a[href]')].some((a) => {
            try {
              const url = new URL(a.href || a.getAttribute('href') || '', location.href);
              return url.hostname && url.hostname !== location.hostname;
            } catch {
              return false;
            }
          });
        })()
        """,
        timeout_s=timeout_s,
        poll_s=1,
        description="assistant_external_link",
    )
    return True


def _select_fast_model(client: ChatGPTCDPClient) -> bool:
    try:
        client.switch_model(["Instant"])
        return True
    except Exception:
        return False


def _action_prompt_case(
    client: ChatGPTCDPClient,
    case: matrix.ChatGPTCase,
    token: str,
    _: dict[str, Path],
    *,
    wait_stream: bool = False,
) -> dict[str, Any]:
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        client,
        case,
        token=token,
        prompt=prompt,
        wait_stream=wait_stream,
        timeout_s=case.response_timeout_s,
    )
    return {**observation, **send, "contains_text": token}


def _action_vanilla_text(client, case, token, fixtures):
    return _action_prompt_case(client, case, token, fixtures)


def _action_stream_start(client, case, token, fixtures):
    return _action_prompt_case(client, case, token, fixtures, wait_stream=True)


def _action_stream_complete(client, case, token, fixtures):
    return _action_prompt_case(client, case, token, fixtures, wait_stream=True)


def _action_new_chat(client, case, token, _):
    client.click_new_chat()
    client.wait_for_input()
    _select_fast_model(client)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(client, case, token=token, prompt=prompt)
    send["notes"]["current_url"] = client.page_state()["url"]
    return {**observation, **send, "contains_text": token}


def _action_reasoning_model(client, case, token, _):
    client.navigate_home()
    client.wait_for_input()
    try:
        client.switch_model(["o1", "o3", "Reasoning", "推理", "Thinking"])
    except Exception as exc:
        raise matrix.CaseSkip(f"reasoning_model_unavailable: {exc}") from exc
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    try:
        send = _perform_send(
            client,
            case,
            token=token,
            prompt=prompt,
            wait_stream=True,
            timeout_s=max(case.response_timeout_s, 180),
        )
    finally:
        _select_fast_model(client)
    send["notes"]["reasoning_ui_hint"] = client.body_contains(["Thinking", "Reasoning", "已思考", "思考"])
    send["required_text_fragments"] = ()
    return {**observation, **send, "contains_text": token}


def _action_edit_user_message(client, case, token, _):
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    original_token = f"{token}-ORIG"
    original_prompt = (
        f"PCE-{case.id}-{original_token}. Reply with one sentence containing {original_token}."
    )
    _perform_send(
        client,
        case,
        token=original_token,
        prompt=original_prompt,
        trigger_manual_capture=False,
    )
    before = client.take_screenshot(_screenshot_path(case.id, "before_edit", token))
    observation = _begin_observation()
    updated_prompt = f"PCE-{case.id}-{token}. Reply with one sentence containing {token}."
    assistant_before = client.assistant_turn_count()
    edited_at = time.time()
    client.edit_last_user_message(updated_prompt)
    complete = _wait_for_token_or_capture(
        client,
        previous_assistant_count=assistant_before,
        token=token,
        started_at=edited_at,
        timeout_s=case.response_timeout_s,
    )
    client.trigger_manual_capture()
    time.sleep(2)
    after = client.take_screenshot(_screenshot_path(case.id, "after_edit", token))
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before, "after": after},
        "notes": {
            "original_token": original_token,
            "edited_token": token,
            "response_received": True,
            "completion_source": complete.get("completion_source", "dom"),
        },
    }


def _action_regenerate(client, case, token, _):
    seed_url = os.environ.get(REGENERATE_SEED_URL_ENV, "").strip()
    if seed_url:
        client.navigate(seed_url)
        client.wait_for_input()
        seed_body = client.page_state().get("bodyText", "")
        seed_match = re.search(r"\bT08-\d+\b", seed_body)
        assert_token = seed_match.group(0) if seed_match else token
        before = client.take_screenshot(_screenshot_path(case.id, "before_regenerate_probe", token))
        observation = _begin_observation()
        try:
            regenerated_at = time.time()
            assistant_before_regenerate = client.assistant_turn_count()
            client.click_regenerate()
        except Exception as exc:
            raise matrix.CaseSkip(
                f"regenerate_unavailable_on_current_ui: {exc}; screenshot={before}"
            ) from exc
        stream_seen = client.wait_for_stop_button(timeout_s=12)
        complete = _wait_for_token_or_capture(
            client,
            previous_assistant_count=assistant_before_regenerate,
            token=assert_token,
            started_at=regenerated_at,
            timeout_s=case.response_timeout_s,
        )
        client.trigger_manual_capture()
        time.sleep(2)
        after = client.take_screenshot(_screenshot_path(case.id, "after_regenerate", token))
        return {
            **observation,
            "contains_text": assert_token,
            "session_started_after": None,
            "screenshots": {"before": before, "after": after},
            "notes": {
                "seed_url": seed_url,
                "assert_token": assert_token,
                "session_started_after_override": "existing_regenerated_session",
                "stream_seen": stream_seen,
                "response_received": True,
                "completion_source": complete.get("completion_source", "dom"),
            },
        }

    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    prompt = case.prompt_template.format(id=case.id, token=token)
    _perform_send(
        client,
        case,
        token=token,
        prompt=prompt,
        trigger_manual_capture=False,
        timeout_s=case.response_timeout_s,
    )
    before = client.take_screenshot(_screenshot_path(case.id, "before_regenerate", token))
    observation = _begin_observation()
    try:
        regenerated_at = time.time()
        assistant_before_regenerate = client.assistant_turn_count()
        client.click_regenerate()
    except Exception as exc:
        raise matrix.CaseSkip(
            f"regenerate_unavailable_on_current_ui: {exc}; screenshot={before}"
        ) from exc
    stream_seen = client.wait_for_stop_button(timeout_s=12)
    complete = _wait_for_token_or_capture(
        client,
        previous_assistant_count=assistant_before_regenerate,
        token=token,
        started_at=regenerated_at,
        timeout_s=case.response_timeout_s,
    )
    client.trigger_manual_capture()
    time.sleep(2)
    after = client.take_screenshot(_screenshot_path(case.id, "after_regenerate", token))
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before, "after": after},
        "notes": {
            "stream_seen": stream_seen,
            "response_received": True,
            "completion_source": complete.get("completion_source", "dom"),
        },
    }


def _action_branch_flip(client, case, token, _):
    seed_url = os.environ.get(BRANCH_SEED_URL_ENV, "").strip()
    if seed_url:
        client.navigate(seed_url)
        client.wait_for_input()
        before = client.take_screenshot(_screenshot_path(case.id, "before_flip_probe", token))
        observation = _begin_observation()
        try:
            client.flip_branch(direction="prev")
        except Exception as exc:
            raise matrix.CaseSkip(
                f"branch_flip_unavailable_on_current_ui: {exc}; screenshot={before}"
            ) from exc
        time.sleep(2)
        client.trigger_manual_capture()
        time.sleep(2)
        after = client.take_screenshot(_screenshot_path(case.id, "after_flip", token))
        return {
            **observation,
            "contains_text": token,
            "session_started_after": None,
            "screenshots": {"before": before, "after": after},
            "notes": {"seed_url": seed_url},
        }

    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    branch_a = f"{token}-A"
    branch_b = f"{token}-B"
    _perform_send(
        client,
        case,
        token=branch_a,
        prompt=f"PCE-{case.id}-{branch_a}. Reply with BRANCH-A {branch_a}.",
        trigger_manual_capture=False,
        timeout_s=case.response_timeout_s,
    )
    assistant_before_edit = client.assistant_turn_count()
    edited_at = time.time()
    client.edit_last_user_message(
        f"PCE-{case.id}-{branch_b}. Reply with BRANCH-B {branch_b}."
    )
    complete_b = _wait_for_token_or_capture(
        client,
        previous_assistant_count=assistant_before_edit,
        token=branch_b,
        started_at=edited_at,
        timeout_s=case.response_timeout_s,
    )
    before = client.take_screenshot(_screenshot_path(case.id, "before_flip", token))
    observation = _begin_observation()
    try:
        client.flip_branch(direction="prev")
    except Exception as exc:
        raise matrix.CaseSkip(
            f"branch_flip_unavailable_on_current_ui: {exc}; screenshot={before}"
        ) from exc
    time.sleep(2)
    client.trigger_manual_capture()
    time.sleep(2)
    after = client.take_screenshot(_screenshot_path(case.id, "after_flip", token))
    return {
        **observation,
        "contains_text": branch_a,
        "session_started_after": None,
        "screenshots": {"before": before, "after": after},
        "notes": {
            "branch_a": branch_a,
            "branch_b": branch_b,
            "completion_source_b": complete_b.get("completion_source", "dom"),
        },
    }


def _action_pdf_upload(client, case, token, fixtures):
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    client.dismiss_known_dialogs()
    pdf_path = _unique_upload_fixture(fixtures, "pdf", token)
    client.upload_files([str(pdf_path)], accept_image=False)
    client.dismiss_known_dialogs()
    _wait_for_visible_text(client, pdf_path.name, timeout_s=20)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        client,
        case,
        token=token,
        prompt=prompt,
        timeout_s=max(case.response_timeout_s, 240),
    )
    return {**observation, **send, "contains_text": token}


def _action_image_upload(client, case, token, fixtures):
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    client.dismiss_known_dialogs()
    image_path = _unique_upload_fixture(fixtures, "image", token)
    client.upload_files([str(image_path)], accept_image=True)
    client.dismiss_known_dialogs()
    try:
        _wait_for_visible_text(client, image_path.name, timeout_s=12)
    except Exception:
        pass
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(client, case, token=token, prompt=prompt, timeout_s=case.response_timeout_s)
    return {**observation, **send, "contains_text": token}


def _action_image_generation(client, case, token, fixtures):
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    before = client.take_screenshot(_screenshot_path(case.id, "before_send", token))
    send = client.send_message(prompt)
    stream_seen = client.wait_for_stop_button(timeout_s=12)
    _wait_for_last_assistant_image(client, timeout_s=case.response_timeout_s)
    time.sleep(3)
    after = client.take_screenshot(_screenshot_path(case.id, "after_response", token))
    client.trigger_manual_capture()
    time.sleep(2)
    client.trigger_manual_capture()
    time.sleep(2)
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before, "after": after},
        "notes": {
            "stream_seen": stream_seen,
            "assistant_turns_before": send["assistant_turns_before"],
            "image_visible": True,
            "response_received": True,
        },
    }


def _action_data_analysis(client, case, token, fixtures):
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    client.dismiss_known_dialogs()
    csv_path = _unique_upload_fixture(fixtures, "csv", token)
    client.upload_files([str(csv_path)], accept_image=False)
    client.dismiss_known_dialogs()
    _wait_for_visible_text(client, csv_path.name, timeout_s=20)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        client,
        case,
        token=token,
        prompt=prompt,
        timeout_s=max(case.response_timeout_s, 240),
    )
    return {**observation, **send, "contains_text": token}


def _action_citations(client, case, token, fixtures):
    client.navigate_home()
    client.wait_for_input()
    _select_fast_model(client)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    before = client.take_screenshot(_screenshot_path(case.id, "before_send", token))
    send = client.send_message(prompt)
    stream_seen = client.wait_for_stop_button(timeout_s=12)
    external_link_visible = True
    try:
        _wait_for_last_assistant_external_link(client, timeout_s=max(case.response_timeout_s, 60))
    except Exception:
        external_link_visible = False
    time.sleep(2)
    after = client.take_screenshot(_screenshot_path(case.id, "after_response", token))
    client.trigger_manual_capture()
    time.sleep(2)
    client.trigger_manual_capture()
    time.sleep(2)
    return {
        **observation,
        "contains_text": token,
        "screenshots": {"before": before, "after": after},
        "notes": {
            "stream_seen": stream_seen,
            "assistant_turns_before": send["assistant_turns_before"],
            "external_link_visible": external_link_visible,
            "response_received": True,
        },
    }


def _action_canvas(client, case, token, fixtures):
    result = _action_prompt_case(client, case, token, fixtures)
    result.setdefault("notes", {})
    result["notes"]["canvas_ui_hint"] = client.body_contains(["Canvas", "画布"])
    return result


def _action_custom_gpt(client, case, token, _):
    gpt_url = os.environ.get(GPT_URL_ENV, "").strip() or None
    try:
        client.navigate_to_gpt(url=gpt_url)
    except Exception as exc:
        raise matrix.CaseSkip(f"custom_gpt_unavailable: {exc}") from exc
    client.wait_for_input()
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(client, case, token=token, prompt=prompt)
    send["notes"]["gpt_url"] = client.page_state()["url"]
    return {**observation, **send, "contains_text": token}


def _action_project(client, case, token, _):
    project_url = os.environ.get(PROJECT_URL_ENV, "").strip() or None
    try:
        client.navigate_to_project(url=project_url)
    except Exception as exc:
        raise matrix.CaseSkip(f"project_unavailable: {exc}") from exc
    client.wait_for_input()
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(client, case, token=token, prompt=prompt)
    send["notes"]["project_url"] = client.page_state()["url"]
    return {**observation, **send, "contains_text": token}


def _action_temporary_chat(client, case, token, _):
    client.open_temporary_chat()
    client.wait_for_input(timeout_s=25)
    _select_fast_model(client)
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    send = _perform_send(
        client,
        case,
        token=token,
        prompt=prompt,
        timeout_s=max(case.response_timeout_s, 120),
    )
    send["notes"]["temporary_ui_hint"] = client.body_contains(["Temporary", "临时"])
    send["notes"]["temporary_url"] = client.page_state()["url"]
    return {**observation, **send, "contains_text": token}


def _action_error_state(client, case, token, _):
    client.open_existing_conversation()
    client.wait_for_input()
    before = client.take_screenshot(_screenshot_path(case.id, "before_error", token))
    observation = _begin_observation()
    prompt = case.prompt_template.format(id=case.id, token=token)
    client.set_offline(True)
    try:
        client.send_message(prompt)
        time.sleep(1.0)
        error_seen = client.wait_for_error_banner(timeout_s=20)
    finally:
        client.set_offline(False)
        time.sleep(1.0)
    if not error_seen:
        raise matrix.CaseFailure("error_state_not_detected")
    after = client.take_screenshot(_screenshot_path(case.id, "after_error", token))
    return {
        **observation,
        "screenshots": {"before": before, "after": after},
        "notes": {"error_prompt": token},
        "expect_no_new_captures": True,
    }


def _action_settings_page(client, case, token, _):
    client.navigate_home()
    client.wait_for_input()
    client.click_new_chat()
    client.wait_for_input()
    before = client.take_screenshot(_screenshot_path(case.id, "before_settings", token))
    observation = _begin_observation()
    client.open_settings()
    time.sleep(2.0)
    after = client.take_screenshot(_screenshot_path(case.id, "after_settings", token))
    return {
        **observation,
        "screenshots": {"before": before, "after": after},
        "notes": {
            "current_url": client.page_state()["url"],
            "settings_ui_hint": client.body_contains(["Settings", "设置", "General", "常规", "通知", "个性化"]),
        },
        "expect_no_new_captures": True,
    }


ACTIONS = {
    "vanilla_text": _action_vanilla_text,
    "stream_start": _action_stream_start,
    "stream_complete": _action_stream_complete,
    "new_chat": _action_new_chat,
    "code_block": _action_prompt_case,
    "reasoning_model": _action_reasoning_model,
    "edit_user_message": _action_edit_user_message,
    "regenerate": _action_regenerate,
    "branch_flip": _action_branch_flip,
    "pdf_upload": _action_pdf_upload,
    "image_upload": _action_image_upload,
    "image_generation": _action_image_generation,
    "data_analysis": _action_data_analysis,
    "citations": _action_citations,
    "canvas": _action_canvas,
    "custom_gpt": _action_custom_gpt,
    "project": _action_project,
    "temporary_chat": _action_temporary_chat,
    "error_state": _action_error_state,
    "settings_page": _action_settings_page,
}


def _preflight(client: ChatGPTCDPClient) -> dict[str, Any]:
    state = client.page_state()
    features = {
        "logged_in": state.get("hasInput", False),
        "temporary": client.body_contains(["Temporary", "临时"]),
        "projects": client.body_contains(["Projects", "项目"]),
        "canvas": client.body_contains(["Canvas", "画布"]),
        "settings": client.body_contains(["Settings", "设置"]),
        "image_generation": client.body_contains(["image", "图片", "生成图像"]),
    }
    return {
        "generated_at": time.time(),
        "account_tier": matrix.ACCOUNT_TIER,
        "features": features,
        "state": state,
        "gpt_url_env_present": bool(os.environ.get(GPT_URL_ENV, "").strip()),
        "project_url_env_present": bool(os.environ.get(PROJECT_URL_ENV, "").strip()),
    }


def _write_summary(report_dir: Path) -> None:
    cases: list[dict[str, Any]] = []
    for path in sorted(report_dir.glob("T*.json")):
        try:
            cases.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            cases.append({"case_id": path.stem, "status": "broken_report", "error": str(exc)})
    counts: dict[str, int] = {}
    for case in cases:
        status = case.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "generated_at": time.time(),
        "account_tier": matrix.ACCOUNT_TIER,
        "case_count": len(cases),
        "status_counts": counts,
        "cases": cases,
    }
    matrix._write_json(report_dir / "summary.json", summary)


def main() -> int:
    if not pce_is_running():
        raise SystemExit("PCE Core server not reachable at http://127.0.0.1:9800")

    client = ChatGPTCDPClient(port=int(os.environ.get("PCE_CHROME_DEBUG_PORT", "9222")))
    report_dir = _report_dir()
    fixtures = matrix._ensure_generated_fixtures()

    try:
        client.connect()
        preflight = _preflight(client)
        matrix._write_json(report_dir / "preflight.json", preflight)

        failures = 0
        for case in matrix._selected_cases():
            token = f"{case.id}-{int(time.time())}"
            report_path = report_dir / f"{case.id}.json"
            record: dict[str, Any] = {
                "case_id": case.id,
                "description": case.description,
                "account_tier": matrix.ACCOUNT_TIER,
                "status": "started",
                "started_at": time.time(),
                "token": token,
                "visual_review_required": case.visual_review_required,
                "preflight": preflight,
                "runner": "current_window_cdp",
            }
            logger.info("Running %s %s", case.id, case.description)
            try:
                action_result = ACTIONS[case.action](client, case, token, fixtures)
                verification = matrix._verify_case(case, action_result)
                visual = matrix._finalize_visual_review(case, action_result)
                record.update(
                    {
                        "status": "pass",
                        "action": case.action,
                        "action_result": action_result,
                        "verification": verification,
                        "visual_checks": visual,
                    }
                )
            except matrix.CaseSkip as exc:
                record.update({"status": "skip", "reason": str(exc)})
            except Exception as exc:
                failures += 1
                record.update({"status": "fail", "reason": str(exc)})
                try:
                    record["failure_screenshot"] = client.take_screenshot(
                        _screenshot_path(case.id, "failure", token)
                    )
                except Exception:
                    pass
                logger.exception("Case %s failed", case.id)
            finally:
                record["finished_at"] = time.time()
                record["elapsed_s"] = round(record["finished_at"] - record["started_at"], 1)
                matrix._write_json(report_path, record)

        _write_summary(report_dir)
        logger.info("Report directory: %s", report_dir)
        return 1 if failures else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
