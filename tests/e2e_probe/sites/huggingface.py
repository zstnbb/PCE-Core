# SPDX-License-Identifier: Apache-2.0
"""HuggingChat (huggingface.co/chat) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/huggingface.yaml``.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class HuggingFaceAdapter(BaseProbeSiteAdapter):
    """HuggingChat probe adapter. Configured via ``adapters/huggingface.yaml``."""


apply_to_class(HuggingFaceAdapter, load_adapter("huggingface"))
