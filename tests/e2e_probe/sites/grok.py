# SPDX-License-Identifier: Apache-2.0
"""Grok (grok.com) — probe site adapter.

P5.C.5.2 refactor: all data lives in ``pce_core/adapters/grok.yaml``.
This file keeps only the 3 method overrides that encode Grok's
non-data quirks:
  * ``_submit_via`` — Grok's submit-by-Enter sometimes mis-fires; click
    the real send button first if the selector resolves.
  * ``upload_file_via_paste`` — Grok's TipTap composer reaches MAIN
    with the dispatched paste, but the React/Tailwind upload pipeline
    never mounts a chip from the paste path on the live UI. Return
    False so the T-case falls back to ``upload_file_via_input`` which
    invokes React's native ``HTMLInputElement.files`` setter directly.
  * ``upload_file_via_input`` — Grok's image upload pipeline takes
    ~6-8s end-to-end on the live UI before the send button re-enables.
    Sleep an extra 5s on top of the base class so T11 doesn't time out
    waiting for the chip + send to re-enable.
"""
from __future__ import annotations

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class GrokAdapter(BaseProbeSiteAdapter):
    """Grok (xAI) probe adapter. Configured via ``adapters/grok.yaml``.

    See module-level docstring for the 3 method overrides Grok needs.
    """

    def _submit_via(self, probe, tab_id: int, selector: str, mode: str) -> None:
        if mode == "button":
            result = self._click_action_main(
                probe,
                tab_id,
                action="send",
                scope="document",
                selectors=self.send_button_selectors,
                prefer_last=False,
            )
            if result and result.get("ok"):
                return
        super()._submit_via(probe, tab_id, selector, mode)

    def upload_file_via_paste(self, *args, **kwargs):  # type: ignore[override]
        # Grok's TipTap composer accepts paste events with populated
        # clipboardData.files (dispatch reaches MAIN per .diag_grok_chip.py),
        # but the React/Tailwind upload pipeline never mounts a chip from
        # the paste path on the live UI — it only reacts to <input type=file>
        # change events. Returning False forces fallback to
        # upload_file_via_input which invokes the privileged
        # dom.set_file_input_main verb the upload pipeline actually observes.
        return False

    def upload_file_via_input(self, *args, **kwargs):  # type: ignore[override]
        # Grok's image upload pipeline (image → preview-image URL via
        # assets.grok.com) takes ~6-8s end-to-end on the live UI before
        # the send button re-enables. Base class waits 1.5s + T11 waits
        # 2s pre-send (~3.5s total) which is enough for PDF (T10) but
        # tight for image (T11): Send stays disabled and send_prompt
        # times out reporting "submit did not fire". Wait an extra 5s
        # so the chip + send re-enable cycle completes.
        import time as _time
        result = super().upload_file_via_input(*args, **kwargs)
        if result:
            _time.sleep(5.0)
        return result


apply_to_class(GrokAdapter, load_adapter("grok"))
