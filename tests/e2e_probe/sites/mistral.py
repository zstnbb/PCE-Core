# SPDX-License-Identifier: Apache-2.0
"""Mistral (chat.mistral.ai) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/mistral.yaml``.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class MistralAdapter(BaseProbeSiteAdapter):
    """Mistral probe adapter. Configured via ``adapters/mistral.yaml``."""


apply_to_class(MistralAdapter, load_adapter("mistral"))
