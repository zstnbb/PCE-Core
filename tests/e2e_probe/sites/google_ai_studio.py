# SPDX-License-Identifier: Apache-2.0
"""Google AI Studio (aistudio.google.com) — probe site adapter.

P5.C.5.2 refactor: all data lives in ``pce_core/adapters/googleaistudio.yaml``.
This file keeps only the methods that encode AI Studio's non-data
quirks (the longest method-override surface of the 14 sites):

  * ``send_prompt`` — ensure preferred model before every prompt (opt-in
    via ``PCE_PROBE_GAS_ENSURE_MODEL`` env var).
  * ``ensure_preferred_model`` — open the run-settings panel, then
    dispatch ``_ensure_model_js`` in MAIN world to pick a stable Gemini
    variant (default = the YAML's ``preferred_model_labels`` list).
  * ``upload_file_via_paste`` — paste path is preferred; after dispatch,
    poll for an ``ms-image-chunk`` / ``ms-file-chunk`` chip with a 8s
    deadline. Returning False forces fallback to the input path.
  * ``upload_file_via_input`` — the <input type=file> is NOT in the DOM
    at page load. Click ``add-media-button`` first to lazy-mount it.
  * ``_uploaded_chip_present`` — helper polled by both upload paths.
  * ``_selector_exists`` — helper used by ``ensure_preferred_model``.

Plus a module-level ``_ensure_model_js`` JavaScript builder — that's
the page-side setInterval logic which picks a non-preview Gemini
variant from the Angular Material menu.
"""
from __future__ import annotations

import json
import os

from pce_core.adapter_loader import apply_to_class, load_adapter

from .base import BaseProbeSiteAdapter


class GoogleAIStudioAdapter(BaseProbeSiteAdapter):
    """Google AI Studio probe adapter. Configured via ``adapters/googleaistudio.yaml``."""

    def send_prompt(self, probe, tab_id, prompt):  # type: ignore[override]
        self.ensure_preferred_model(probe, tab_id)
        return super().send_prompt(probe, tab_id, prompt)

    def ensure_preferred_model(self, probe, tab_id) -> None:
        from pce_probe import ProbeError as _PE

        flag = os.environ.get("PCE_PROBE_GAS_ENSURE_MODEL", "0").lower()
        if flag in {"0", "false", "no", "off"}:
            return
        labels_env = os.environ.get("PCE_PROBE_GAS_MODEL_LABELS", "")
        labels = [
            part.strip()
            for part in labels_env.split(",")
            if part.strip()
        ] or list(self.preferred_model_labels)
        if not self._selector_exists(probe, tab_id, "ms-model-selector button"):
            for sel in (
                'button[aria-label="Toggle run settings panel"]',
                "button.runsettings-toggle-button",
                'button[aria-label*="run settings" i]',
            ):
                try:
                    probe.dom.click(tab_id, sel)
                    break
                except _PE:
                    continue
            import time as _time

            _time.sleep(1.2)
        try:
            probe.dom.execute_js(tab_id, _ensure_model_js(labels))
        except _PE:
            return
        # The menu is Angular-rendered; selection happens from a short
        # setInterval in page context because execute_js return values
        # are not dependable under MV3 isolated worlds.
        import time as _time

        _time.sleep(3.0)

    def upload_file_via_paste(self, probe, tab_id, **kwargs):  # type: ignore[override]
        # AI Studio's <input type="file"> is NOT in the DOM at page load;
        # it's lazy-mounted only after clicking the add-media-button.
        # Prefer the paste path because it's the least intrusive route
        # and matches a real user pasting a file into textarea[msfilecopypaste].
        # If the dispatch reaches MAIN but no chip mounts within 8s,
        # return False so the case falls back to the input path.
        import time as _time

        result = super().upload_file_via_paste(probe, tab_id, **kwargs)
        if not result:
            return False
        file_name = str(kwargs.get("file_name") or "")
        image = bool(kwargs.get("image"))
        deadline = _time.time() + 8.0
        while _time.time() < deadline:
            if self._uploaded_chip_present(
                probe,
                tab_id,
                file_name=file_name,
                image=image,
            ):
                return True
            _time.sleep(0.5)
        return False

    def upload_file_via_input(self, probe, tab_id, **kwargs):  # type: ignore[override]
        import time as _time
        from pce_probe import ProbeError as _PE

        # Click the +/add-media-button to mount the file input.
        for sel in (
            'button[data-test-id="add-media-button"]',
            'button[aria-label*="Insert images" i]',
            'button[data-test="selectMediaMenu"]',
        ):
            try:
                probe.dom.click(tab_id, sel)
                break
            except _PE:
                continue
        else:
            return False
        _time.sleep(1.0)  # let Angular Material menu mount
        # Now input[type="file"] should be in the DOM. Hand off to the
        # base implementation which uses dom.set_file_input_main.
        result = super().upload_file_via_input(probe, tab_id, **kwargs)
        if result:
            # AI Studio uploads to its asset store after the change
            # event; the chip mounts asynchronously. Poll for the actual
            # chip so the T-case doesn't advance while OCR/preview is
            # still settling.
            file_name = str(kwargs.get("file_name") or "")
            image = bool(kwargs.get("image"))
            deadline = _time.time() + 8.0
            while _time.time() < deadline:
                if self._uploaded_chip_present(
                    probe,
                    tab_id,
                    file_name=file_name,
                    image=image,
                ):
                    break
                _time.sleep(0.5)
        return result

    def _uploaded_chip_present(
        self,
        probe,
        tab_id,
        *,
        file_name: str,
        image: bool,
    ) -> bool:
        from pce_probe import ProbeError as _PE

        selectors = (
            ("ms-image-chunk", ".image-container img", "img[alt*='thumbnail' i]")
            if image
            else ("ms-file-chunk", ".file-chunk-container", "[class*='file-chunk']")
        )
        for sel in selectors:
            try:
                res = probe.dom.query(tab_id, sel, all=True)
            except _PE:
                continue
            matches = res.get("matches", []) or []
            if not matches:
                continue
            if image:
                return True
            if not file_name:
                return True
            for match in matches:
                text = str(match.get("text_excerpt") or "")
                outer = str(match.get("outer_html_excerpt") or "")
                if file_name in text or file_name in outer:
                    return True
            # AI Studio may truncate long tokenized filenames in the
            # query excerpt; the presence of a single fresh file chip
            # is still enough on a newly-opened prompt tab.
            return True
        return False

    def _selector_exists(self, probe, tab_id, selector: str) -> bool:
        from pce_probe import ProbeError as _PE

        try:
            res = probe.dom.query(tab_id, selector)
        except _PE:
            return False
        return bool(res.get("matches"))


apply_to_class(GoogleAIStudioAdapter, load_adapter("googleaistudio"))


def _ensure_model_js(labels: list[str]) -> str:
    """Build the page-side setInterval JS that picks a stable Gemini variant.

    AI Studio's model picker is Angular-rendered and surfaces asynchronously
    via a ``setInterval``. We can't rely on ``execute_js`` return values
    under MV3 isolated worlds, so we write progress to
    ``document.body.dataset.pceGasModelEnsure`` for caller-side observation.
    """
    labels_json = json.dumps(labels)
    return f"""
(function () {{
  var labels = {labels_json};
  function norm(value) {{
    return String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
  }}
  function write(state) {{
    try {{ document.body.dataset.pceGasModelEnsure = JSON.stringify(state); }} catch (e) {{}}
  }}
  function currentModelText() {{
    var selectors = [
      "[data-test-id='model-name']",
      "ms-model-selector .subtitle",
      "ms-model-selector .title",
      ".model-selector-card"
    ];
    for (var i = 0; i < selectors.length; i++) {{
      var el = document.querySelector(selectors[i]);
      var text = norm(el && (el.innerText || el.textContent));
      if (text) return text;
    }}
    return "";
  }}
  var current = currentModelText();
  if (current && !/preview/.test(current)) {{
    write({{ phase: "already_stable", current: current }});
    return;
  }}
  var opener = document.querySelector(
    "ms-model-selector button, .model-selector-card, ms-model-selector"
  );
  if (!opener || typeof opener.click !== "function") {{
    write({{ phase: "no_opener", current: current }});
    return;
  }}
  try {{ opener.click(); }} catch (e) {{
    write({{ phase: "open_failed", current: current, err: String(e && e.message || e) }});
    return;
  }}
  write({{ phase: "opened", current: current }});
  var attempts = 0;
  var timer = setInterval(function () {{
    attempts += 1;
    var candidates = Array.from(document.querySelectorAll(
      "[role='option'], mat-option, mat-list-option, button, " +
      ".model-selector-card, [data-test-id='model-name'], [data-test-id*='model']"
    ));
    for (var i = 0; i < candidates.length; i++) {{
      var el = candidates[i];
      var text = norm(el.innerText || el.textContent);
      if (!text || /preview/.test(text)) continue;
      var matched = labels.find(function (label) {{
        var n = norm(label);
        return n && text.indexOf(n) !== -1;
      }});
      if (!matched) continue;
      var target = el.closest(
        "button,[role='option'],mat-option,mat-list-option,.model-selector-card"
      ) || el;
      try {{
        target.click();
        write({{ phase: "selected", label: matched, text: text, attempts: attempts }});
      }} catch (e) {{
        write({{ phase: "select_failed", label: matched, text: text, err: String(e && e.message || e) }});
      }}
      clearInterval(timer);
      return;
    }}
    if (attempts >= 24) {{
      write({{ phase: "no_stable_match", current: current, labels: labels }});
      clearInterval(timer);
    }}
  }}, 250);
}})();
"""
