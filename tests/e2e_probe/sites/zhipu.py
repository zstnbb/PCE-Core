# SPDX-License-Identifier: Apache-2.0
"""Zhipu / Z.AI (chat.z.ai) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/zhipu.yaml``.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class ZhiPuAdapter(BaseProbeSiteAdapter):
    """Zhipu Z.AI probe adapter. Configured via ``adapters/zhipu.yaml``."""


apply_to_class(ZhiPuAdapter, load_adapter("zhipu"))
