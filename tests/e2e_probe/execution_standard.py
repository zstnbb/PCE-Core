# SPDX-License-Identifier: Apache-2.0
"""Executable completion standard for the probe E2E matrix."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


STANDARD_VERSION = "pce-probe-e2e-standard-2026-05-04"


@dataclass(frozen=True)
class CaseStandard:
    case_id: str
    name: str
    capture: str
    storage: str
    render: str
    pass_gate: str
    allowed_skip: tuple[str, ...] = ()
    strict_gap_on_skip: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def brief(self) -> dict[str, str]:
        return {
            "version": STANDARD_VERSION,
            "capture": self.capture,
            "storage": self.storage,
            "render": self.render,
            "pass_gate": self.pass_gate,
        }


_EXTERNAL_SKIP_KEYWORDS = (
    "not logged in",
    "quota",
    "rate limit",
    "rate-limit",
    "no native surface",
    "not available",
    "feature unavailable",
    "account",
    "model does not",
    "provider did not",
)

_STRICT_EXTERNAL_SKIP_KEYWORDS = (
    "not logged in",
    "quota",
    "rate limit",
    "rate-limit",
)


CASE_STANDARDS: dict[str, CaseStandard] = {
    "T00": CaseStandard(
        "T00",
        "smoke",
        "Extension/probe is attached to the already logged-in profile.",
        "PCE Core health endpoint is reachable.",
        "Health/report metadata is available for the run.",
        "Do not pass if probe/core attachment is only assumed.",
    ),
    "T01": CaseStandard(
        "T01",
        "basic_chat",
        "Token-bearing user prompt and assistant reply are captured.",
        "Session contains user and assistant message rows.",
        "Dashboard can render both message texts.",
        "Pass requires capture, storage, and renderable text.",
    ),
    "T02": CaseStandard(
        "T02",
        "streaming_complete",
        "Final post-stream assistant state is captured after streaming ends.",
        "Stored assistant text is complete, not a partial prefix.",
        "Dashboard renders the final text without streaming artifacts.",
        "Pass requires a completed final answer in PCE Core.",
    ),
    "T03": CaseStandard(
        "T03",
        "streaming_stop",
        "Stop action and resulting visible state are captured.",
        "Stored assistant row reflects the stopped output.",
        "Dashboard renders the stopped output without stale continuation.",
        "Pass requires the stopped state to be the stored/rendered state.",
    ),
    "T04": CaseStandard(
        "T04",
        "new_chat_url",
        "New chat transition is captured after durable session identity appears.",
        "Session key is stable and is not merged into the prior chat.",
        "Dashboard opens the correct new session detail.",
        "Pass requires stable session identity plus stored messages.",
    ),
    "T05": CaseStandard(
        "T05",
        "code_block",
        "Code block content and language are captured.",
        "Code block is stored in content_json/rich_content or equivalent.",
        "Dashboard renders the block as code.",
        "Pass requires code-specific storage, not only plain text.",
    ),
    "T06": CaseStandard(
        "T06",
        "thinking_model",
        "Visible thinking/reasoning surface is captured when exposed.",
        "Thinking is tagged or separated in storage.",
        "Dashboard renders thinking predictably.",
        "Pass requires thinking-specific evidence when the site exposes it.",
        allowed_skip=("feature unavailable", "model does not expose thinking"),
    ),
    "T07": CaseStandard(
        "T07",
        "edit_user_message",
        "Edited prompt and resulting assistant state are captured.",
        "Storage keeps the edited user text and resulting assistant state.",
        "Dashboard renders the edited conversation state without stale leakage.",
        "Pass requires the edited state in PCE Core.",
    ),
    "T08": CaseStandard(
        "T08",
        "regenerate_assistant_variant",
        "Regenerate fires on the latest assistant turn and a fresh capture lands.",
        "Storage preserves assistant variants for one logical prompt.",
        "Render contract identifies variant group and current variant.",
        "Pass requires structured variant evidence; content delta alone is a gap.",
        strict_gap_on_skip=True,
    ),
    "T09": CaseStandard(
        "T09",
        "branch_flip_currently_shown",
        "Capture reflects the branch currently visible after a branch flip.",
        "Storage preserves branch identity and parent/child relation.",
        "Render contract reconstructs the branch tree/current branch.",
        "Pass requires structured branch evidence; latest-token proof alone is a gap.",
        strict_gap_on_skip=True,
    ),
    "T10": CaseStandard(
        "T10",
        "pdf_upload",
        "PDF paste/input upload chip and prompt are captured.",
        "User message stores file/document attachment metadata.",
        "Dashboard renders a file/document card.",
        "Pass requires attachment metadata, not only filename text.",
    ),
    "T11": CaseStandard(
        "T11",
        "image_upload",
        "Image paste/input upload chip and prompt are captured.",
        "User message stores image attachment metadata.",
        "Dashboard renders an image card or image reference.",
        "Pass requires image attachment metadata, not only prompt text.",
    ),
    "T12": CaseStandard(
        "T12",
        "image_generation",
        "Generated image/tool result is captured when the tool actually runs.",
        "Assistant message stores image_generation or image asset metadata.",
        "Dashboard renders the generated image/card.",
        "Pass requires a generated-image artifact in storage.",
        allowed_skip=("quota", "provider did not generate image", "feature unavailable"),
    ),
    "T13": CaseStandard(
        "T13",
        "code_interpreter",
        "Tool call/output or interpreter artifact is captured.",
        "Tool call/result/code output metadata is stored.",
        "Dashboard renders tool/code output cards.",
        "Pass requires tool-specific evidence when the tool runs.",
        allowed_skip=("quota", "rate limit", "feature unavailable", "no native surface"),
    ),
    "T14": CaseStandard(
        "T14",
        "web_search",
        "Search/citation surface is captured when the tool actually runs.",
        "Citations or search tool metadata are stored.",
        "Dashboard renders citation/search cards.",
        "Pass requires citation/search metadata when the tool runs.",
        allowed_skip=("quota", "rate limit", "feature unavailable", "no native surface"),
    ),
    "T15": CaseStandard(
        "T15",
        "canvas_artifact",
        "Canvas/artifact content is captured.",
        "Canvas/artifact block is stored in rich_content.",
        "Dashboard renders artifact/canvas card.",
        "Pass requires artifact-specific storage.",
    ),
    "T16": CaseStandard(
        "T16",
        "custom_gpt",
        "Account-scoped custom assistant surface is captured when available.",
        "Session metadata identifies the custom surface.",
        "Dashboard preserves custom assistant context/name.",
        "Pass requires account-scope metadata when the surface is available.",
        allowed_skip=("account", "feature unavailable", "no native surface"),
    ),
    "T17": CaseStandard(
        "T17",
        "project_chat",
        "Project-scoped chat is captured when available.",
        "Session metadata identifies project/workspace scope.",
        "Dashboard preserves project context.",
        "Pass requires project-scope metadata when the surface is available.",
        allowed_skip=("account", "feature unavailable", "no native surface"),
    ),
    "T18": CaseStandard(
        "T18",
        "temporary_chat",
        "Temporary/private chat state is captured or explicitly suppressed.",
        "Storage omits it or marks it temporary/private.",
        "Dashboard makes temporary/private status explicit if stored.",
        "Pass requires no durable unmarked leak.",
        allowed_skip=("feature unavailable", "no native surface"),
    ),
    "T19": CaseStandard(
        "T19",
        "error_state",
        "Provider error/refusal/quota UI is captured.",
        "Error state is stored as error/tool/status metadata.",
        "Dashboard renders the error state distinctly.",
        "Pass requires error-specific storage, not a fake assistant answer.",
    ),
    "T20": CaseStandard(
        "T20",
        "settings_silent",
        "Settings/account surfaces do not emit chat captures.",
        "No new session/message rows are created.",
        "Dashboard remains unchanged except diagnostics.",
        "Pass requires silence in chat storage.",
    ),
}


def standard_for_case(case_id: str) -> CaseStandard | None:
    return CASE_STANDARDS.get(case_id.upper())


def standards_for_case_ids(case_ids: Iterable[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for case_id in case_ids:
        std = standard_for_case(case_id)
        if std is not None:
            out[std.case_id] = std.to_dict()
    return out


def is_external_skip(case_id: str, summary: str) -> bool:
    text = summary.lower()
    std = standard_for_case(case_id)
    if std and std.strict_gap_on_skip:
        return any(key in text for key in _STRICT_EXTERNAL_SKIP_KEYWORDS)
    allowed = tuple(std.allowed_skip) if std else ()
    return any(key.lower() in text for key in allowed + _EXTERNAL_SKIP_KEYWORDS)
