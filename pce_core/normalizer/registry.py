"""PCE Core – Normalizer registry.

Maintains a list of registered normalizers and selects the right one
for a given provider/host/path combination.
"""

import logging
from typing import Optional

from .base import BaseNormalizer

logger = logging.getLogger("pce.normalizer.registry")

_normalizers: list[BaseNormalizer] = []


def register_normalizer(normalizer: BaseNormalizer) -> None:
    """Register a normalizer instance."""
    _normalizers.append(normalizer)
    logger.debug("Registered normalizer: %s", type(normalizer).__name__)


def get_normalizer(provider: str, host: str, path: str) -> Optional[BaseNormalizer]:
    """Return the first normalizer that can handle the given provider/host/path."""
    for n in _normalizers:
        if n.can_handle(provider, host, path):
            return n
    return None


def _auto_register():
    """Import and register all built-in normalizers."""
    from .openai import OpenAIChatNormalizer
    from .anthropic import AnthropicMessagesNormalizer

    register_normalizer(OpenAIChatNormalizer())
    register_normalizer(AnthropicMessagesNormalizer())


# Auto-register on import
_auto_register()
