# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Windsurf Cascade chat normalizer.

Handles gRPC captures from Windsurf's Cascade AI chat, captured via
L1 mitmproxy with NODE_EXTRA_CA_CERTS injection.

Primary data source: ``RecordCortexTrajectoryStep`` request bodies on
``server.self-serve.windsurf.com`` (post-Cognition acquisition domain).
These contain the full conversation content (user prompt, assistant
thinking, assistant response, model name, trajectory ID) in protobuf
wire format with embedded plaintext strings.

Secondary sources:
- ``RecordCortexGeneratorMetadata``: prompt template with XML tags
- ``RecordTrajectorySegmentAnalytics``: model + message summary
- ``RecordStateInitializationData``: context initialization

The bodies are protobuf-encoded (gRPC). Since we don't have the .proto
schema, we use heuristic string extraction + structural patterns to
pull out the conversation content.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.windsurf_cascade")

# Post-Cognition acquisition domain (2026-Q2). Falls back to legacy
# server.codeium.com for older Windsurf versions.
_HOSTS = frozenset({
    "server.self-serve.windsurf.com",
    "server.codeium.com",
})

_PROVIDER = "codeium"
_TOOL_FAMILY = "windsurf-cascade"

# gRPC paths that carry chat content (trajectory steps contain the
# full conversation; generator metadata has the prompt template).
_CHAT_PATHS = frozenset({
    "/exa.analytics_pb.AnalyticsService/RecordCortexTrajectoryStep",
    "/exa.api_server_pb.ApiServerService/RecordCortexGeneratorMetadata",
    "/exa.api_server_pb.ApiServerService/RecordTrajectorySegmentAnalytics",
    "/exa.api_server_pb.ApiServerService/RecordStateInitializationData",
})

# Known model name patterns in Windsurf/Codeium ecosystem.
_MODEL_PATTERNS = re.compile(
    r"\b(swe-[\w.-]+|claude-[\w.-]+|gpt-[\w.-]+|kimi-[\w.-]+|"
    r"deepseek-[\w.-]+|gemini-[\w.-]+|adaptive[\w.-]*|"
    r"copilot[\w.-]*|o[134]-[\w.-]+)\b"
)

# UUID pattern for trajectory IDs.
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

# XML-style user request tags used in RecordCortexGeneratorMetadata.
_USER_REQUEST_RE = re.compile(
    r"<user_request>(.*?)</user_request>", re.DOTALL
)


def _to_text(data: bytes | str) -> str:
    """Ensure we have a UTF-8 string."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _extract_readable_strings(text: str, min_len: int = 5) -> list[str]:
    """Extract runs of printable ASCII/Unicode from protobuf data."""
    # Match runs of printable chars (ASCII + common Unicode)
    return re.findall(r"[\x20-\x7e\u4e00-\u9fff\u3000-\u303f]{" + str(min_len) + r",}", text)


def _extract_model_name(text: str) -> Optional[str]:
    """Extract the AI model name from protobuf body text."""
    # Look for model patterns in the readable strings
    match = _MODEL_PATTERNS.search(text)
    if match:
        return match.group(1)
    return None


def _extract_trajectory_id(text: str) -> Optional[str]:
    """Extract the trajectory UUID (used as session key)."""
    # Trajectory IDs appear as UUIDs in the protobuf
    matches = _UUID_RE.findall(text)
    # Return the first UUID that looks like a trajectory (skip very
    # common patterns like session IDs that appear in headers)
    for m in matches:
        return m
    return None


def _extract_user_prompt_from_trajectory_step(text: str) -> Optional[str]:
    """Extract user prompt from RecordCortexTrajectoryStep body.

    The user prompt appears as a repeated string in the protobuf,
    typically in the pattern: ...model_name......<prompt>....<prompt>"...
    We look for the longest string that appears twice consecutively
    (the protobuf encodes it in both the step content and the message
    history echo).
    """
    strings = _extract_readable_strings(text, min_len=5)
    if not strings:
        return None

    # Strategy 1: Look for XML user_request tags (from generator metadata)
    xml_match = _USER_REQUEST_RE.search(text)
    if xml_match:
        return xml_match.group(1).strip()

    # Strategy 1.5: Look for quoted prompt patterns like ..."What is 2+2?"...
    # The prompt often appears in assistant thinking as a quoted reference
    quoted_match = re.search(r'["\u201c]([^"\u201d]{3,100})["\u201d]\?', text)
    if quoted_match:
        candidate = quoted_match.group(1).strip() + "?"
        # Verify it's not boilerplate
        if not any(skip in candidate.lower() for skip in (
            "windsurf", "codeium", "devin", "windows",
        )):
            return candidate

    # Strategy 1.6: Look for "asked" + quoted text pattern
    # e.g. 'has asked "What is 2+2?"'
    asked_match = re.search(r'(?:asked|asking)\s+["\u201c]([^"\u201d]{3,200})["\u201d]', text)
    if asked_match:
        candidate = asked_match.group(1).strip()
        if not any(skip in candidate.lower() for skip in (
            "windsurf", "codeium", "devin", "windows",
        )):
            return candidate

    # Strategy 2: Find repeated strings that look like user prompts.
    # In RecordCortexTrajectoryStep, the user prompt appears multiple
    # times (in the step content + in the message history echo).
    # We look for strings that appear at least twice and are not
    # model names, UUIDs, or common protobuf boilerplate.
    seen: dict[str, int] = {}
    for s in strings:
        s_stripped = s.strip().rstrip('"').strip()
        if len(s_stripped) < 3:
            continue
        # Skip known non-prompt patterns
        if any(skip in s_stripped.lower() for skip in (
            "windsurf", "windows", "chisel", "codeium", "devin",
            "intel", "genuineintel", "appdata", "file:///",
            "redacted", ".py", ".ts", ".md", "http", "version",
            "numcores", "numsockets", "numthreads", "vendorid",
            "productname", "majorversion", "minorversion",
            "memory", "build", "arch", "amd64",
            "resource_exhausted", "error", "failed",
        )):
            continue
        if _MODEL_PATTERNS.match(s_stripped):
            continue
        if _UUID_RE.match(s_stripped):
            continue
        # Skip hex strings (encrypted data / hashes)
        if re.match(r'^[0-9a-f]{20,}$', s_stripped):
            continue
        # Skip strings that start with $ (variable references)
        if s_stripped.startswith("$"):
            continue
        seen[s_stripped] = seen.get(s_stripped, 0) + 1

    # The user prompt is typically the string that appears 2+ times
    # and is the longest non-boilerplate string
    candidates = [(s, count) for s, count in seen.items() if count >= 2]
    if candidates:
        # Sort by count descending first, then length descending
        candidates.sort(key=lambda x: (x[1], len(x[0])), reverse=True)
        return candidates[0][0]

    return None


def _extract_assistant_response(text: str) -> Optional[str]:
    """Extract assistant thinking/response from trajectory step body.

    The assistant response appears as longer text blocks in the protobuf,
    typically containing reasoning ("The user is asking...") or the
    final answer.
    """
    strings = _extract_readable_strings(text, min_len=20)
    if not strings:
        return None

    # Look for strings that look like assistant responses:
    # - Start with "The user is asking..." (thinking)
    # - Contain reasoning patterns
    # - Are significantly longer than other strings
    response_candidates = []
    for s in strings:
        s_stripped = s.strip()
        # Skip boilerplate
        if any(skip in s_stripped for skip in (
            "windsurf", "windows", "chisel", "codeium",
            "GenuineIntel", "AppData", "file:///",
            "REDACTED", "devin-session-token",
            "resource_exhausted", "CORTEX_STEP_",
        )):
            continue
        if _MODEL_PATTERNS.match(s_stripped):
            continue
        # Likely assistant content if it's long and contains natural language
        if len(s_stripped) > 30 and " " in s_stripped:
            response_candidates.append(s_stripped)

    if not response_candidates:
        return None

    # Return the longest candidate (most likely to be the full response)
    response_candidates.sort(key=len, reverse=True)
    return response_candidates[0]


def _extract_from_generator_metadata(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract user prompt and context from RecordCortexGeneratorMetadata.

    This endpoint contains the full prompt template with XML tags:
    <user_request>What is 2+2?</user_request>
    """
    user_prompt = None
    xml_match = _USER_REQUEST_RE.search(text)
    if xml_match:
        user_prompt = xml_match.group(1).strip()

    # Also extract additional_metadata if present
    meta_match = re.search(
        r"<additional_metadata>(.*?)</additional_metadata>", text, re.DOTALL
    )
    context = meta_match.group(1).strip() if meta_match else None

    return user_prompt, context


def _extract_from_segment_analytics(text: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract model, user prompt, and messages from RecordTrajectorySegmentAnalytics.

    This endpoint contains a summary with model name and message list.
    Pattern: ...Model..SWE-1.6 Slow......<prompt>....<prompt>"...
    """
    model = None
    user_prompt = None

    # Model name after "Model.." marker
    model_match = re.search(r"Model\.\.([^\x00-\x1f.]{3,30})", text)
    if model_match:
        model = model_match.group(1).strip()

    # User prompt (same repeated-string strategy)
    user_prompt = _extract_user_prompt_from_trajectory_step(text)

    return model, user_prompt, None


class WindsurfCascadeNormalizer(BaseNormalizer):
    """Normalise Windsurf Cascade chat gRPC captures into messages."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        return host in _HOSTS and path in _CHAT_PATHS

    def normalize(
        self,
        request_body: str,
        response_body: str,
        *,
        provider: str,
        host: str,
        path: str,
        model_name: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Optional[NormalizedResult]:
        # Primary data is in the request body (client → server telemetry)
        body = request_body or ""
        if not body:
            return None

        text = _to_text(body)

        # Skip very small bodies (pings, empty acks)
        if len(text) < 50:
            return None

        # Route to endpoint-specific extraction
        if "RecordCortexTrajectoryStep" in path:
            return self._normalize_trajectory_step(text, model_name, created_at)
        elif "RecordCortexGeneratorMetadata" in path:
            return self._normalize_generator_metadata(text, model_name, created_at)
        elif "RecordTrajectorySegmentAnalytics" in path:
            return self._normalize_segment_analytics(text, model_name, created_at)
        elif "RecordStateInitializationData" in path:
            return self._normalize_state_init(text, model_name, created_at)

        return None

    def _normalize_trajectory_step(
        self, text: str, model_name: Optional[str], created_at: Optional[float]
    ) -> Optional[NormalizedResult]:
        """Normalize a RecordCortexTrajectoryStep request."""
        # Extract model name
        resolved_model = model_name or _extract_model_name(text)

        # Extract trajectory ID as session key
        trajectory_id = _extract_trajectory_id(text)

        # Extract user prompt
        user_prompt = _extract_user_prompt_from_trajectory_step(text)

        # Extract assistant response/thinking
        assistant_text = _extract_assistant_response(text)

        # Need at least one of user prompt or assistant response
        if not user_prompt and not assistant_text:
            return None

        messages: list[NormalizedMessage] = []

        if user_prompt:
            messages.append(NormalizedMessage(
                role="user",
                content_text=user_prompt,
                model_name=resolved_model,
                ts=created_at,
            ))

        if assistant_text:
            messages.append(NormalizedMessage(
                role="assistant",
                content_text=assistant_text,
                model_name=resolved_model,
                ts=created_at,
                interaction_kind="thinking" if assistant_text.startswith("The user") else None,
            ))

        if not messages:
            return None

        # Build layer_meta with trajectory context
        layer_meta: dict = {}
        if trajectory_id:
            layer_meta["trajectory_id"] = trajectory_id
        if resolved_model:
            layer_meta["model"] = resolved_model

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=trajectory_id or f"windsurf-cascade-{(created_at or 0):.0f}",
            title_hint=user_prompt[:60] if user_prompt else None,
            messages=messages,
            confidence=0.88,
            layer_meta=layer_meta,
        )

    def _normalize_generator_metadata(
        self, text: str, model_name: Optional[str], created_at: Optional[float]
    ) -> Optional[NormalizedResult]:
        """Normalize a RecordCortexGeneratorMetadata request."""
        user_prompt, context = _extract_from_generator_metadata(text)
        if not user_prompt:
            return None

        resolved_model = model_name or _extract_model_name(text)
        trajectory_id = _extract_trajectory_id(text)

        messages = [NormalizedMessage(
            role="user",
            content_text=user_prompt,
            model_name=resolved_model,
            ts=created_at,
        )]

        layer_meta: dict = {"source_endpoint": "RecordCortexGeneratorMetadata"}
        if trajectory_id:
            layer_meta["trajectory_id"] = trajectory_id
        if context:
            layer_meta["additional_metadata"] = context[:500]

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=trajectory_id or f"windsurf-generator-{(created_at or 0):.0f}",
            title_hint=user_prompt[:60],
            messages=messages,
            confidence=0.92,
            layer_meta=layer_meta,
        )

    def _normalize_segment_analytics(
        self, text: str, model_name: Optional[str], created_at: Optional[float]
    ) -> Optional[NormalizedResult]:
        """Normalize a RecordTrajectorySegmentAnalytics request."""
        model, user_prompt, _ = _extract_from_segment_analytics(text)
        if not user_prompt:
            return None

        resolved_model = model_name or model or _extract_model_name(text)
        trajectory_id = _extract_trajectory_id(text)

        messages = [NormalizedMessage(
            role="user",
            content_text=user_prompt,
            model_name=resolved_model,
            ts=created_at,
        )]

        layer_meta: dict = {"source_endpoint": "RecordTrajectorySegmentAnalytics"}
        if trajectory_id:
            layer_meta["trajectory_id"] = trajectory_id

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=trajectory_id or f"windsurf-segment-{(created_at or 0):.0f}",
            title_hint=user_prompt[:60],
            messages=messages,
            confidence=0.85,
            layer_meta=layer_meta,
        )

    def _normalize_state_init(
        self, text: str, model_name: Optional[str], created_at: Optional[float]
    ) -> Optional[NormalizedResult]:
        """Normalize a RecordStateInitializationData request."""
        # This endpoint contains context/history — extract any user prompts
        user_prompt = _extract_user_prompt_from_trajectory_step(text)
        if not user_prompt:
            return None

        resolved_model = model_name or _extract_model_name(text)
        trajectory_id = _extract_trajectory_id(text)

        messages = [NormalizedMessage(
            role="user",
            content_text=user_prompt,
            model_name=resolved_model,
            ts=created_at,
        )]

        layer_meta: dict = {"source_endpoint": "RecordStateInitializationData"}
        if trajectory_id:
            layer_meta["trajectory_id"] = trajectory_id

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=resolved_model,
            session_key=trajectory_id or f"windsurf-init-{(created_at or 0):.0f}",
            title_hint=user_prompt[:60],
            messages=messages,
            confidence=0.80,
            layer_meta=layer_meta,
        )
