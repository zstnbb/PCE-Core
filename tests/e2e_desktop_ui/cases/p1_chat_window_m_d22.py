# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window M: D22 Writing Style.

Switches Claude Desktop's Writing Style to "Concise" (or another
non-default option), sends a single prompt, and verifies the captured
request body carries the ``personalized_styles`` payload AND the
persisted assistant ``content_text`` is the answer (not polluted by
the style system prompt).

D22 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "Switching the in-product Writing Style (Concise / Explanatory /
   custom) BEFORE sending a turn captures the style metadata on the
   session (``layer_meta.style`` or equivalent); the assistant's
   ``content_text`` stays clean of style-prompt boilerplate."

PASS:
  - request body has a non-empty ``personalized_styles`` array with
    name / type / prompt fields populated
  - persisted user message body OR session row carries the style key
    in ``content_json`` / ``layer_meta`` (or equivalent)
  - assistant content_text does NOT contain the verbatim style prompt
    (just the answer)

PARTIAL:
  - request body has personalized_styles BUT session schema does not
    surface it (= web N11 / `fu_recon_join` item 5 — same gap)

SKIP:
  - select_style returned False (UIA picker not actionable)

Run cost: ~30s, 1 Claude turn.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import baseline_ts as _baseline_ts, configure_utf8_stdout, default_db_path

STYLE_NAME = "Concise"
PROMPT = "Explain photosynthesis. Reply briefly."


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_m_d22")
    configure_utf8_stdout()

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window M — D22 Writing Style ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    log.info("[2] selecting style %r", STYLE_NAME)
    style_ok = driver.select_style(STYLE_NAME)
    if not style_ok:
        log.warning("    select_style returned False — D22 will be SKIP")

    time.sleep(1.0)

    log.info("[3] sending prompt: %r", PROMPT)
    pair_id = driver.send_message(PROMPT, wait_done=True, wait_timeout=60.0)
    if not pair_id:
        log.error("no /completion request observed")
        return 1
    log.info("    pair_id=%s", pair_id[:10])

    # ============== Inspection ==============
    log.info("\n[4] D22 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    req = con.execute(
        "SELECT body_text_or_json FROM raw_captures "
        "WHERE pair_id=? AND direction='request'",
        (pair_id,),
    ).fetchone()
    body = req["body_text_or_json"] if req else ""
    body_low = (body or "").lower()
    has_personalized_styles = '"personalized_styles"' in body_low
    has_concise = STYLE_NAME.lower() in body_low
    print(f"\n  /completion request body length: {len(body)}")
    print(f"  body has 'personalized_styles' field: {has_personalized_styles}")
    print(f"  body mentions style {STYLE_NAME!r}: {has_concise}")

    # Try to extract the styles payload for a preview
    if has_personalized_styles:
        try:
            payload = json.loads(body)
            styles = payload.get("personalized_styles") or []
            print(f"  personalized_styles entries: {len(styles)}")
            for s in styles[:3]:
                if isinstance(s, dict):
                    keys = sorted(s.keys())
                    print(f"    keys={keys}  name={s.get('name')!r}  "
                          f"type={s.get('type')!r}  "
                          f"prompt_len={len(str(s.get('prompt', '')))}")
        except Exception as exc:
            print(f"  body JSON parse error: {exc}")

    # Persisted messages — assistant text + session metadata
    msgs = con.execute(
        "SELECT role, content_text, content_json, length(content_text) AS L "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D22 pair: {len(msgs)}")
    asst_text = ""
    user_cj_has_style = False
    for m in msgs:
        cjs = m["content_json"] or ""
        has_style_in_msg = (
            "personalized_styles" in cjs.lower()
            or "layer_meta" in cjs.lower()
            or "style" in cjs.lower()
        )
        if m["role"] == "user" and has_style_in_msg:
            user_cj_has_style = True
        if m["role"] == "assistant":
            asst_text = m["content_text"] or ""
        print(f"    role={m['role']:10s} text_len={m['L']:5d} "
              f"has_style_marker={has_style_in_msg}")

    # Session-level style — sessions table doesn't have a dedicated
    # ``layer_meta`` column on the current schema (see migrations under
    # ``pce_core/migrations/``). The closest surface is
    # ``oi_attributes_json`` (OpenInference span attributes); style
    # would land there if the normaliser surfaced it. Today (2026-05-10)
    # it doesn't — that's the same gap as web's N11 / fu_recon_join
    # item 5.
    sess = con.execute(
        "SELECT s.id, s.oi_attributes_json FROM sessions s "
        "JOIN messages m ON m.session_id = s.id "
        "WHERE m.capture_pair_id=? GROUP BY s.id LIMIT 1",
        (pair_id,),
    ).fetchone()
    sess_layer_meta_has_style = False
    if sess and sess["oi_attributes_json"]:
        attrs_low = (sess["oi_attributes_json"] or "").lower()
        sess_layer_meta_has_style = (
            "style" in attrs_low
            and (STYLE_NAME.lower() in attrs_low or "concise" in attrs_low)
        )
        print(f"\n  session oi_attributes_json head: "
              f"{(sess['oi_attributes_json'] or '')[:200]!r}")
        print(f"  session.oi_attributes_json references style: "
              f"{sess_layer_meta_has_style}")

    # Check assistant text isn't polluted
    asst_has_style_boilerplate = any(
        m in asst_text.lower() for m in (
            "be concise", "respond concisely", "you are concise",
            "[style:", "<style>",
        )
    )
    print(f"\n  assistant text head: {asst_text[:240]!r}")
    print(f"  assistant text contains style boilerplate: {asst_has_style_boilerplate}")

    print()
    if not style_ok:
        print(f"  D22 VERDICT: SKIP - select_style could not actuate the "
              f"style picker via UIA (Claude Desktop UI variation)")
        return 0
    if has_personalized_styles and (user_cj_has_style or sess_layer_meta_has_style) and not asst_has_style_boilerplate:
        print(f"  D22 VERDICT: PASS - personalized_styles in request, "
              f"style marker in normalized session/message, assistant "
              f"text clean")
        return 0
    if has_personalized_styles and not asst_has_style_boilerplate:
        print(f"  D22 VERDICT: PARTIAL - personalized_styles present in "
              f"request body but session schema does not yet surface "
              f"it (= web N11 / fu_recon_join item 5)")
        return 0
    if has_personalized_styles and asst_has_style_boilerplate:
        print(f"  D22 VERDICT: PARTIAL - styles captured but assistant "
              f"text appears polluted by style boilerplate")
        return 0
    print(f"  D22 VERDICT: FAIL - no personalized_styles field in "
          f"request body (style picker may not have actually applied)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
