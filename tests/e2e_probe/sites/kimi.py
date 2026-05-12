# SPDX-License-Identifier: Apache-2.0
"""Kimi (kimi.com) — probe site adapter.

P5.C.5.2 refactor: configuration in ``pce_core/adapters/kimi.yaml``.
Kimi uses a div.chat-input-editor contenteditable with no standard
send button — empty send_button_selectors in YAML forces fall-through
to Enter via dom.type submit=True.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class KimiAdapter(BaseProbeSiteAdapter):
    """Kimi probe adapter. Configured via ``adapters/kimi.yaml``."""


apply_to_class(KimiAdapter, load_adapter("kimi"))
