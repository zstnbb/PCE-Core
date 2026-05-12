# SPDX-License-Identifier: Apache-2.0
"""ChatGPT (chatgpt.com) — probe site adapter.

P5.C.4.2 refactor: all selectors / timeouts / labels / trigger prompts /
behavioural flags live in ``pce_core/adapters/chatgpt.yaml``. The class
body is intentionally empty; ``apply_to_class`` mutates the class at
import time with the YAML-loaded values.

The probe's ``dom.type`` already handles ChatGPT's ProseMirror
contenteditable input transparently, so this adapter is just selector
configuration (now external).

Maintenance: when ChatGPT drift breaks a case, edit the YAML — NOT
this file — and re-run the failing target. The conductor's
``propose_patch`` (P5.C.4.3) emits YAML diffs that drop straight into
the manifest.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class ChatGPTAdapter(BaseProbeSiteAdapter):
    """ChatGPT probe adapter. Configured via ``adapters/chatgpt.yaml``."""


apply_to_class(ChatGPTAdapter, load_adapter("chatgpt"))
