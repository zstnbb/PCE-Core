# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Windsurf/Codeium management plane normalizer.

Handles gRPC captures from ``server.codeium.com`` management endpoints.
These are NOT chat captures (chat is cert-pinned), but provide useful
metadata about the user's Windsurf environment:

- ``GetCliTeamSettings`` → available model list
- ``GetUserStatus`` → user identity, team, plan tier
- ``GetPlanStatus`` → subscription details
- ``GetCliModelConfigs`` → model configuration

The bodies are protobuf-encoded (gRPC). We use a lightweight wire-format
parser to extract string fields without requiring a .proto schema.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.windsurf_management")

_HOST = "server.codeium.com"
_HOST_NEW = "server.self-serve.windsurf.com"
_PROVIDER = "codeium"
_TOOL_FAMILY = "windsurf-management"

_MANAGEMENT_PATHS = frozenset({
    "/exa.seat_management_pb.SeatManagementService/GetCliTeamSettings",
    "/exa.seat_management_pb.SeatManagementService/GetUserStatus",
    "/exa.seat_management_pb.SeatManagementService/GetPlanStatus",
    "/exa.api_server_pb.ApiServerService/GetCliModelConfigs",
})


def _extract_proto_strings(data: bytes | str) -> list[str]:
    """Extract readable ASCII strings from protobuf wire-format data.

    The data in SQLite has been round-tripped through text encoding, so
    binary protobuf varints are corrupted. We fall back to a simple
    regex that finds runs of printable ASCII (the model names, emails,
    and identifiers we care about are all ASCII).
    """
    import re
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = data
    # Find runs of printable ASCII >= 3 chars, bounded by non-printable
    return re.findall(r"[\x20-\x7e]{3,}", text)


def _extract_models_from_team_settings(body: bytes | str) -> list[str]:
    """Extract model name strings from GetCliTeamSettings response."""
    strings = _extract_proto_strings(body)
    models = []
    for s in strings:
        # Strip trailing protobuf field markers
        s = s.rstrip(":").strip()
        if not s:
            continue
        if any(prefix in s for prefix in (
            "claude-", "gpt-", "deepseek", "kimi-", "swe-", "adaptive",
            "copilot", "gemini-",
        )):
            # Skip enum-style constants (MODEL_GOOGLE_...)
            if s.startswith("MODEL_") or s.startswith("%"):
                continue
            models.append(s)
    return models


def _extract_user_info(body: bytes | str) -> dict:
    """Extract user identity fields from GetUserStatus response."""
    import re
    strings = _extract_proto_strings(body)
    info = {}
    for s in strings:
        # Email: look for valid email pattern
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", s)
        if email_match and "email" not in info:
            info["email"] = email_match.group(0)
        elif s.startswith("devin-team$"):
            info["team"] = s.replace("devin-team$", "").split("\x00")[0]
        elif s in ("Trial", "Pro", "Enterprise", "Team"):
            info["plan"] = s
    return info


class WindsurfManagementNormalizer(BaseNormalizer):
    """Normalise Windsurf/Codeium management plane gRPC into metadata."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        return (host == _HOST or host == _HOST_NEW) and path in _MANAGEMENT_PATHS

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
        if not response_body:
            return None

        endpoint = path.rsplit("/", 1)[-1] if path else ""

        layer_meta: dict = {"endpoint": endpoint}

        if "GetCliTeamSettings" in path:
            models = _extract_models_from_team_settings(response_body)
            if models:
                layer_meta["available_models"] = models
        elif "GetUserStatus" in path:
            info = _extract_user_info(response_body)
            if info:
                layer_meta.update(info)

        messages = [NormalizedMessage(
            role="system",
            content_text=f"[windsurf-management] {endpoint}",
            model_name=None,
            ts=created_at,
            interaction_kind="management",
        )]

        return NormalizedResult(
            provider=_PROVIDER,
            tool_family=_TOOL_FAMILY,
            model_name=None,
            session_key=f"windsurf-management-{endpoint}",
            title_hint=f"Windsurf {endpoint}",
            messages=messages,
            confidence=0.85,
            layer_meta=layer_meta,
        )
