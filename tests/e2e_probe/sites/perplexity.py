# SPDX-License-Identifier: Apache-2.0
"""Perplexity (www.perplexity.ai) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/perplexity.yaml``.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class PerplexityAdapter(BaseProbeSiteAdapter):
    """Perplexity probe adapter. Configured via ``adapters/perplexity.yaml``."""


apply_to_class(PerplexityAdapter, load_adapter("perplexity"))
