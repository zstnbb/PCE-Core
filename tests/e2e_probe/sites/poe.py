# SPDX-License-Identifier: Apache-2.0
"""Poe (poe.com) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/poe.yaml``.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class PoeAdapter(BaseProbeSiteAdapter):
    """Poe probe adapter. Configured via ``adapters/poe.yaml``."""


apply_to_class(PoeAdapter, load_adapter("poe"))
