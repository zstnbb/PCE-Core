# SPDX-License-Identifier: Apache-2.0
"""Gemini (gemini.google.com) — probe site adapter.

P5.C.4.2 refactor: all selectors / timeouts / labels / trigger prompts /
behavioural flags live in ``pce_core/adapters/gemini.yaml``. The class
body is intentionally empty.

Gemini uses Quill (``.ql-editor``) inside an Angular ``rich-textarea``.
The probe's ``dom.type`` handles contenteditable correctly. Send is
either via ``button[aria-label*="Send"]`` or the matTooltip fallback
when the page localizes to Chinese — both captured in the YAML's
``selectors.send_button`` list.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class GeminiAdapter(BaseProbeSiteAdapter):
    """Gemini probe adapter. Configured via ``adapters/gemini.yaml``."""


apply_to_class(GeminiAdapter, load_adapter("gemini"))
