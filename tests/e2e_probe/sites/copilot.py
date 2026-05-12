# SPDX-License-Identifier: Apache-2.0
"""Microsoft Copilot (copilot.microsoft.com) — probe site adapter.

P5.C.5.2 refactor: all configuration lives in
``pce_core/adapters/copilot.yaml``. The WXT extractor emits
``provider="microsoft"`` — the YAML mirrors that.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class CopilotAdapter(BaseProbeSiteAdapter):
    """Microsoft Copilot probe adapter. Configured via ``adapters/copilot.yaml``."""


apply_to_class(CopilotAdapter, load_adapter("copilot"))
