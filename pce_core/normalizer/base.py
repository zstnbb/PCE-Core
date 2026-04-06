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
    """Convenience: run the appropriate normalizer on a capture pair.

    Looks up the normalizer from the registry based on provider/host/path.
    """
    from .registry import get_normalizer

    provider = request_row.get("provider", "")
    host = request_row.get("host", "")
    path = request_row.get("path", "")

    normalizer = get_normalizer(provider, host, path)
    if normalizer is None:
        logger.debug("No normalizer found for %s %s %s", provider, host, path)
        return None

    request_body = request_row.get("body_text_or_json", "")
    response_body = response_row.get("body_text_or_json", "")

    try:
        return normalizer.normalize(
            request_body,
            response_body,
            provider=provider,
            host=host,
            path=path,
            model_name=request_row.get("model_name"),
            created_at=request_row.get("created_at"),
        )
    except Exception:
        logger.exception("Normalizer failed for %s %s %s", provider, host, path)
        return None
