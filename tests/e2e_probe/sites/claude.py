# SPDX-License-Identifier: Apache-2.0
"""Claude (claude.ai) — probe site adapter.

P5.C.4.2 refactor: all selectors / timeouts / labels / trigger prompts /
behavioural flags live in ``pce_core/adapters/claude.yaml``. The class
body retains only the ``upload_file_via_paste`` method override — Claude
TipTap rejects synthetic ``paste`` events (verified 2026-05-03) so the
case must fall through to ``upload_file_via_input`` (which actually
works because the file input has a real React change handler observed
via ``set_file_input_main``'s native setter).
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class ClaudeAdapter(BaseProbeSiteAdapter):
    """Claude probe adapter. Configured via ``adapters/claude.yaml``."""

    def upload_file_via_paste(self, *args, **kwargs) -> bool:  # type: ignore[override]
        """Claude TipTap rejects synthetic ``paste`` events — verified
        2026-05-03: ``dispatch_paste_main`` reports
        ``ok=true, dt_files_len=1, paste_dispatched`` but no chip
        mounts and the upload chain never fires. Force the case to
        fall through to ``upload_file_via_input`` (the file input
        path actually works on Claude because the input has a real
        change handler that React's value tracker observes via
        ``set_file_input_main``'s native setter).
        """
        return False


apply_to_class(ClaudeAdapter, load_adapter("claude"))
