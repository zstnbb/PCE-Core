# SPDX-License-Identifier: Apache-2.0
"""DeepSeek (chat.deepseek.com) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/deepseek.yaml``.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class DeepSeekAdapter(BaseProbeSiteAdapter):
    """DeepSeek probe adapter. Configured via ``adapters/deepseek.yaml``."""


apply_to_class(DeepSeekAdapter, load_adapter("deepseek"))
