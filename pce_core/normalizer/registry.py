# SPDX-License-Identifier: Apache-2.0
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


def get_all_normalizers(provider: str, host: str, path: str) -> list[BaseNormalizer]:
    """Return all normalizers that can handle the given provider/host/path.

    Used by the try-fallback chain in normalize_pair() to attempt multiple
    normalizers and pick the highest-confidence result.
    """
    return [n for n in _normalizers if n.can_handle(provider, host, path)]


def _auto_register():
    """Import and register all built-in normalizers."""
    from .openai import OpenAIChatNormalizer
    from .anthropic import AnthropicMessagesNormalizer
    from .conversation import ConversationNormalizer
    from .mcp_jsonrpc import MCPJsonRpcNormalizer
    from .local_persistence import LocalPersistenceNormalizer
    from .copilot_chat import CopilotChatNormalizer
    from .cursor_chat import CursorChatNormalizer
    from .windsurf_management import WindsurfManagementNormalizer
    from .windsurf_cascade import WindsurfCascadeNormalizer
    from .codex_cli import CodexCliNormalizer
    from .gemini_cli import GeminiCliNormalizer

    # L3g host-specific normalizers registered BEFORE broad provider
    # matchers (OpenAI/Anthropic) so they win on host discrimination.
    register_normalizer(CopilotChatNormalizer())
    register_normalizer(CursorChatNormalizer())
    register_normalizer(WindsurfCascadeNormalizer())
    register_normalizer(WindsurfManagementNormalizer())
    register_normalizer(CodexCliNormalizer())
    register_normalizer(GeminiCliNormalizer())
    # Network normalizers (broad provider match).
    register_normalizer(OpenAIChatNormalizer())
    register_normalizer(AnthropicMessagesNormalizer())
    # MCP middleware proxy (UCS L3f, posture B). Specific to
    # provider="mcp:*" + host="stdio", so ordering is moot — but kept
    # before Conversation (the catch-all) so it stays explicit.
    register_normalizer(MCPJsonRpcNormalizer())
    # L3g Cowork agent-mode JSONL transcript (P5.B.5.3, 2026-05-11).
    # Specific to host="local-agent-mode" + path matching
    # ``/<app_id>/agent-transcript/...``, so ordering is moot. Kept
    # before Conversation (catch-all) for clarity.
    register_normalizer(LocalPersistenceNormalizer())
    # Conversation normalizer is registered last as a catch-all for
    # browser extension DOM-extracted captures (DeepSeek, Gemini, etc.)
    register_normalizer(ConversationNormalizer())


# Auto-register on import
_auto_register()
