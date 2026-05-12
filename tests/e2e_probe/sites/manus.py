# SPDX-License-Identifier: Apache-2.0
"""Manus (manus.im) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/manus.yaml``.
Manus runs long-form tasks; response_timeout_ms bumped to 120s in YAML.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class ManusAdapter(BaseProbeSiteAdapter):
    """Manus probe adapter. Configured via ``adapters/manus.yaml``."""


apply_to_class(ManusAdapter, load_adapter("manus"))
