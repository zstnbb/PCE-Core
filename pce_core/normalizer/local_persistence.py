# SPDX-License-Identifier: Apache-2.0
"""PCE Core – L3g Cowork agent-mode JSONL transcript normalizer.

Discovered via Round 3 RECON (2026-05-11) — see
``Docs/research/2026-05-11-cowork-recon-findings.md`` Q5 closure.

Cowork persists every user / assistant / tool_use / tool_result turn
as a line in a JSONL transcript at::

    <agent_sessions_root>/<user_uuid>/<org_uuid>/local_<session_uuid>/
        .claude/projects/<encoded-cwd>/<session_uuid>.jsonl

``pce_persistence_watcher`` reads each line via
``iter_transcript_records`` (kind="transcript_line") and writes one
``raw_captures`` row per line with::

    host = "local-agent-mode"
    path = "/<app_id>/agent-transcript/<session_id>/<line_key>"
    direction = "conversation"
    body_text_or_json = <line as JSON string>

This normaliser then converts each row into the session + message
records that the dashboard / API surface.

Top-level JSONL line types observed (RECON 3, 38-line sample):

============== ================================================
``type``       Semantics
============== ================================================
``user``       User turn or tool_result reply
``assistant``  Assistant turn (text / thinking / tool_use blocks)
``ai-title``   Auto-generated session title update
``queue-op…``  enqueue/dequeue marker
``last-prompt``Cached last-prompt snapshot
``attachment`` User attachment metadata
============== ================================================

``user`` and ``assistant`` are the primary content channel. The
other four types are metadata that v1.1 ingests as raw_captures
rows but does NOT lift into the messages table — they can be
mined for analytics later without touching this normaliser.

Content blocks inside ``message.content`` for user / assistant lines
use the standard Anthropic Messages format and are decoded via the
existing :func:`pce_core.normalizer.anthropic._extract_rich_blocks`
helper (text / thinking / tool_use / tool_result / image / document
all supported per memory ``9e642209``).

Session metadata is derived from each line's ``sessionId`` /
``parentUuid`` / ``cwd`` / ``entrypoint`` / ``version`` fields and
surfaced via ``NormalizedResult.session_key`` + ``layer_meta``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pce_core.rich_content import build_content_json

from .anthropic import _extract_rich_blocks
from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.local_persistence")

# Path pattern emitted by ChromiumStateObserver for transcript_line
# records: ``/<app_id>/agent-transcript/<session_uuid>/<line_key>``.
# ``<line_key>`` is either the line's own ``uuid`` (preferred) or
# ``idx-<N>`` for lines without a stable uuid.
_TRANSCRIPT_PATH_RE = re.compile(
    r"^/[^/]+/agent-transcript/(?P<session>[^/]+)/(?P<line>[^/]+)$"
)

# Line types that carry actual conversation content (mapped to
# sessions+messages). Any other type is metadata-only and emits a
# NormalizedResult with zero messages so the L3g pair still produces
# a session-key linkage without polluting the messages table.
_CONTENT_LINE_TYPES = {"user", "assistant"}

# Default tool_family used when the JSONL line lacks a discriminator
# field (entrypoint absent / unknown). Empirically observed cowork
# records on real Claude Desktop fall through to this default.
_TOOL_FAMILY_DEFAULT = "cowork-local-agent"

# P5.B.7 (2026-05-11) — Inline Code-tab transcripts share the same
# JSONL format as cowork's local-agent-mode JSONL (same agent
# binary). The ``entrypoint`` field on each line is the documented
# discriminator:
#
#   - ``"claude-desktop"`` → P1 Claude Desktop Code-tab inline
#                            (this sub-phase, tool_family below)
#   - ``"cli"`` (or absent) → P6 standalone Claude Code CLI (deferred,
#                            keeps the legacy default for backwards
#                            compatibility with the existing 15+
#                            cowork sessions in the wild)
#
# See `Docs/research/2026-05-11-code-tab-recon-findings.md` Q5
# ("Distinguishing Desktop Code tab vs standalone CLI in the same
# JSONL store") for empirical evidence and reasoning.
_TOOL_FAMILY_BY_ENTRYPOINT: dict[str, str] = {
    "claude-desktop": "claude-desktop-code",
    # Add more mappings here when P6 stand-alone CLI work resumes
    # ("cli": "claude-code-cli"). Keeping the dict minimal for now
    # to avoid surprise re-classification of existing data.
}

_PROVIDER = "anthropic"


class LocalPersistenceNormalizer(BaseNormalizer):
    """Normalise one Cowork JSONL line into PCE NormalizedResult.

    Designed for ``direction="conversation"`` rows where the body is
    the parsed JSONL line as a JSON string. Both ``request_body`` and
    ``response_body`` are expected to be the same string (the watcher
    passes one body for both via :func:`normalize_conversation`).
    """

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        return host == "local-agent-mode" and bool(
            _TRANSCRIPT_PATH_RE.match(path or "")
        )

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
        body = _safe_json(request_body) or _safe_json(response_body)
        if not isinstance(body, dict):
            return None

        line_type = body.get("type", "")

        # Resolve session_key: path > line.sessionId > None. Reject the
        # literal placeholder "unknown" emitted by the observer when no
        # session_id was on the originating record.
        path_match = _TRANSCRIPT_PATH_RE.match(path or "")
        path_session = path_match.group("session") if path_match else None
        if path_session == "unknown":
            path_session = None
        session_key: Optional[str] = (
            path_session
            or body.get("sessionId")
            or body.get("session_id")
        )

        # P5.B.7 (2026-05-11) — select tool_family by entrypoint.
        # Defaults to cowork's family for backwards compatibility
        # with the existing 15+ in-the-wild cowork sessions whose
        # entrypoint field may be absent / different.
        ep_raw = body.get("entrypoint")
        ep = ep_raw if isinstance(ep_raw, str) else None
        tool_family = _TOOL_FAMILY_BY_ENTRYPOINT.get(ep or "", _TOOL_FAMILY_DEFAULT)

        # Always emit a result (even for metadata-only lines) so the
        # session row gets created/touched. Messages list stays empty
        # for non-content types — reconciler is a no-op for empties.
        result = NormalizedResult(
            provider=_PROVIDER,
            tool_family=tool_family,
            model_name=model_name,
            session_key=session_key,
            messages=[],
            confidence=0.95,
            normalizer_name="LocalPersistenceNormalizer",
            layer_meta=_build_layer_meta(body, line_type),
        )

        if line_type not in _CONTENT_LINE_TYPES:
            # Metadata line (ai-title / queue-operation / last-prompt /
            # attachment). Return the bare result with session_key set;
            # the message-processor will create or upgrade the session
            # row but emit no messages.
            return result

        # ----- Content line (user / assistant) -----
        msg = body.get("message")
        if not isinstance(msg, dict):
            # Some user lines have a flat "content" string at top-level
            # (queue-style enqueue payloads). Fall back to that shape.
            flat_content = body.get("content")
            if isinstance(flat_content, str) and flat_content.strip():
                text = flat_content
                attachments: list[dict] = []
                role = "user" if line_type == "user" else "assistant"
            else:
                return result  # nothing actionable
        else:
            role = msg.get("role") or ("assistant" if line_type == "assistant" else "user")
            content = msg.get("content")
            text, attachments = _decode_content(content)

        # Resolve per-message identity fields
        provider_message_uuid = body.get("uuid")
        provider_parent_uuid = body.get("parentUuid") or body.get("parent_uuid")

        # Model name on assistant message (lines override model_name arg)
        model = model_name
        if isinstance(msg, dict):
            m = msg.get("model")
            if isinstance(m, str) and m:
                model = m

        # Pack threading metadata so re-runs / branch flips can stitch
        threading = {
            "session_id": session_key,
            "line_type": line_type,
            "prompt_id": body.get("promptId"),
            "request_id": body.get("requestId"),
            "is_sidechain": body.get("isSidechain"),
            "leaf_uuid": body.get("leafUuid"),
        }
        threading = {k: v for k, v in threading.items() if v is not None}

        try:
            cj_str = build_content_json(
                attachments or [],
                plain_text=text or "",
                threading=threading or None,
            )
        except Exception as exc:  # pragma: no cover — build_content_json is well-tested
            logger.debug("build_content_json failed: %s", exc)
            cj_str = None

        result.model_name = model
        result.messages = [
            NormalizedMessage(
                role=role,
                content_text=text,
                content_json=cj_str,
                model_name=model if role == "assistant" else None,
                ts=created_at,
                provider_message_uuid=(
                    provider_message_uuid if isinstance(provider_message_uuid, str) else None
                ),
                provider_parent_uuid=(
                    provider_parent_uuid if isinstance(provider_parent_uuid, str) else None
                ),
            ),
        ]
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_json(s: str) -> Optional[dict]:
    if not s:
        return None
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _decode_content(content) -> tuple[Optional[str], list[dict]]:
    """Decode a JSONL line's ``message.content`` into (text, attachments).

    Reuses the existing Anthropic content-block decoder for full
    coverage of text / thinking / tool_use / tool_result / image /
    document / unknown blocks. Strings are passed through unchanged.
    Empty / missing content yields ``(None, [])``.
    """
    if content is None:
        return None, []
    if isinstance(content, str):
        return content, []
    if isinstance(content, list):
        text, attachments = _extract_rich_blocks(content)
        return text, attachments
    # Unexpected shape — coerce to JSON string preview for forensic
    # value, but no attachments.
    try:
        return json.dumps(content, ensure_ascii=False)[:2000], []
    except (TypeError, ValueError):
        return str(content)[:2000], []


def _build_layer_meta(body: dict, line_type: str) -> dict:
    """Pack JSONL line-level metadata for session.layer_meta.

    Always populated. Includes the line type, any free-text content,
    and a few standardised fields (cwd / entrypoint / version /
    permissionMode / gitBranch / userType). Useful for forensic
    queries and dashboard rendering of Cowork-specific context.
    """
    meta: dict = {
        "line_type": line_type,
        "channel": "l3g_transcript",
    }
    for field in (
        "cwd", "entrypoint", "version", "permissionMode", "gitBranch",
        "userType", "promptId", "requestId", "operation",
        "aiTitle", "lastPrompt",
    ):
        v = body.get(field)
        if v is None:
            continue
        # Truncate any long strings to keep layer_meta compact
        if isinstance(v, str) and len(v) > 4000:
            v = v[:4000] + "..."
        meta[field] = v
    return meta
