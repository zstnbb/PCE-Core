# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Normalizer base interface.

A normalizer takes a request/response pair from raw_captures and produces
structured session + message records for Tier 1 storage.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("pce.normalizer")


@dataclass
class NormalizedMessage:
    """A single message extracted from a raw capture pair."""

    role: str  # user | assistant | system | tool
    content_text: Optional[str] = None
    content_json: Optional[str] = None
    model_name: Optional[str] = None
    token_estimate: Optional[int] = None
    ts: Optional[float] = None  # if None, will be set to capture created_at


@dataclass
class NormalizedResult:
    """Output of a normalizer: metadata + extracted messages."""

    provider: str
    tool_family: str  # e.g. "api-direct", "chatgpt-web", "cursor"
    model_name: Optional[str] = None
    session_key: Optional[str] = None  # for grouping into sessions
    title_hint: Optional[str] = None
    messages: list[NormalizedMessage] = field(default_factory=list)
    confidence: float = 0.5  # 0.0–1.0, higher = more confident parse
    normalizer_name: Optional[str] = None  # which normalizer produced this
    # Provider-specific session metadata captured from the request/response
    # envelope (NOT per-message). Surfaced into session.oi_attributes_json
    # under the ``pce.layer_meta`` namespace by ``persist_result``.
    # Examples: Anthropic ``personalized_styles``, file_uuid manifest,
    # system prompt, response_schema, etc.
    layer_meta: Optional[dict] = None


class BaseNormalizer(ABC):
    """Abstract base class for provider-specific normalizers."""

    @abstractmethod
    def can_handle(self, provider: str, host: str, path: str) -> bool:
        """Return True if this normalizer can process captures from the given provider/host/path."""
        ...

    @abstractmethod
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
        """Parse a request/response pair and return structured messages.

        Returns None if the pair cannot be meaningfully normalized
        (e.g. non-chat endpoint, malformed body).
        """
        ...


def normalize_pair(
    request_row: dict,
    response_row: dict,
) -> Optional[NormalizedResult]:
    """Try all matching normalizers and return the highest-confidence result.

    Falls back through the normalizer chain: if the primary normalizer
    returns None or a low-confidence result, subsequent normalizers get
    a chance.  The result with the highest confidence wins.
    """
    from .registry import get_all_normalizers

    provider = request_row.get("provider", "")
    host = request_row.get("host", "")
    path = request_row.get("path", "")
    request_body = request_row.get("body_text_or_json", "")
    response_body = response_row.get("body_text_or_json", "")

    candidates = get_all_normalizers(provider, host, path)
    if not candidates:
        logger.debug("No normalizer found for %s %s %s", provider, host, path)
        return None

    best: Optional[NormalizedResult] = None

    for normalizer in candidates:
        try:
            result = normalizer.normalize(
                request_body,
                response_body,
                provider=provider,
                host=host,
                path=path,
                model_name=request_row.get("model_name"),
                created_at=request_row.get("created_at"),
            )
        except Exception:
            logger.debug(
                "Normalizer %s failed for %s %s %s",
                type(normalizer).__name__, provider, host, path,
            )
            continue

        if result is None:
            continue

        # Tag which normalizer produced this
        if not result.normalizer_name:
            result.normalizer_name = type(normalizer).__name__

        # High confidence — use immediately without trying others
        if result.confidence >= 0.9:
            logger.debug(
                "Normalizer %s matched with high confidence %.2f",
                type(normalizer).__name__, result.confidence,
            )
            return result

        # Track the best result so far
        if best is None or result.confidence > best.confidence:
            best = result

    if best:
        logger.debug(
            "Best normalizer: %s (confidence=%.2f, msgs=%d)",
            best.normalizer_name, best.confidence, len(best.messages),
        )
    return best
