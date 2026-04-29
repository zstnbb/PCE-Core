# SPDX-License-Identifier: Apache-2.0
"""Per-site probe adapters for the PCE Probe-driven E2E matrix.

Each module in this package contributes one ``BaseProbeSiteAdapter``
subclass, registered automatically via ``ALL_SITES``. The matrix runner
in ``test_matrix.py`` consumes ``ALL_SITES`` to parametrize every case
across every site.

Adding a new site:
    1. Drop a new ``mysite.py`` next to the existing ones.
    2. Define ``MySiteAdapter(BaseProbeSiteAdapter)`` with at minimum
       ``name`` / ``provider`` / ``url`` / ``input_selectors``.
    3. Append ``MySiteAdapter`` to ``ALL_SITES`` below.
    4. (Optional) Add tracking entries in ``_inventory.md``.

The order of ``ALL_SITES`` controls the pytest collection order, which
is also the order ``-v --collect-only`` prints. Highest-traffic sites
first so flaky-suite triage starts with the most-impactful failures.
"""
from __future__ import annotations

from .base import BaseProbeSiteAdapter, LoginCheckResult
from .chatgpt import ChatGPTAdapter
from .claude import ClaudeAdapter
from .gemini import GeminiAdapter
from .perplexity import PerplexityAdapter
from .google_ai_studio import GoogleAIStudioAdapter
from .copilot import CopilotAdapter
from .deepseek import DeepSeekAdapter
from .kimi import KimiAdapter
from .grok import GrokAdapter
from .manus import ManusAdapter
from .mistral import MistralAdapter
from .huggingface import HuggingFaceAdapter
from .poe import PoeAdapter
from .zhipu import ZhiPuAdapter

# Order matters: the matrix iterates this list and pytest reports in
# the same order. High-traffic / well-validated sites first.
ALL_SITES: list[type[BaseProbeSiteAdapter]] = [
    ChatGPTAdapter,
    ClaudeAdapter,
    GeminiAdapter,
    PerplexityAdapter,
    GoogleAIStudioAdapter,
    CopilotAdapter,
    DeepSeekAdapter,
    KimiAdapter,
    GrokAdapter,
    ManusAdapter,
    MistralAdapter,
    HuggingFaceAdapter,
    PoeAdapter,
    ZhiPuAdapter,
]


def site_by_name(name: str) -> type[BaseProbeSiteAdapter]:
    """Return the adapter class whose ``name`` attribute matches.

    Raises ``KeyError`` if no match — caller should ``pytest.skip`` or
    surface it as a config error.
    """
    for cls in ALL_SITES:
        if cls.name == name:
            return cls
    raise KeyError(f"no site adapter registered with name={name!r}")


__all__ = [
    "ALL_SITES",
    "BaseProbeSiteAdapter",
    "LoginCheckResult",
    "site_by_name",
]
