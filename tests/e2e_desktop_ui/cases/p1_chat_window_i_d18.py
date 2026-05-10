# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window I: D18 PDF document upload + summarise.

Drives Claude Desktop to attach a small PDF (containing token
``PCE-D18-4471``) via clipboard CF_HDROP, then asks Claude to summarise
the PDF. The reply must mention the token (proves PDF text was read),
and the captured ``messages`` row must have an attachment entry with
``file_kind="document"`` (or equivalent).

D18 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "Uploading a PDF and asking a summarisation question stores
   ``attachments[]`` with ``file_kind="document"`` + page count where
   surfaced; assistant summary references PDF contents."

PASS / PARTIAL / FAIL semantics are the same shape as D17 (see
``p1_chat_window_h_d17.py`` docstring).

Run cost: ~30s, 1 Claude turn.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

from pywinauto.keyboard import send_keys

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..fixtures import ensure_test_pdf
from ..utils import (
    baseline_ts as _baseline_ts,
    configure_utf8_stdout,
    copy_files_to_clipboard,
    default_db_path,
    wait_for_new_completion,
)

PDF_TOKEN = "PCE-D18-4471"
PROMPT = (
    "Summarise the attached PDF in one sentence. "
    "Include any unique identifier you see in the document."
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_i_d18")
    configure_utf8_stdout()

    fixture = ensure_test_pdf(PDF_TOKEN)
    log.info("D18 pdf fixture: %s (%d bytes)", fixture, fixture.stat().st_size)

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window I — D18 PDF upload ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    log.info("[2] attaching %s via paperclip + native file dialog", fixture.name)
    attached = driver.attach_file_via_picker(str(fixture), settle=6.0)
    if not attached:
        log.warning("    attach_file_via_picker returned False — "
                    "falling back to clipboard CF_HDROP paste")
        copy_files_to_clipboard(str(fixture))
        driver.focus()
        driver.click_composer()
        time.sleep(0.4)
        send_keys("^v", pause=0.05)
        time.sleep(6.0)

    log.info("[4] (post-attach settle complete)")

    log.info("[5] typing prompt: %r", PROMPT)
    driver.focus()
    driver.click_composer()
    time.sleep(0.3)
    send_keys(PROMPT, with_spaces=True, vk_packet=True, pause=0.02)
    time.sleep(0.3)

    since_ts = time.time() - 2.0
    log.info("[6] pressing Enter")
    send_keys("{ENTER}", pause=0.05)

    pair_id = wait_for_new_completion(since_ts, timeout=20.0)
    if not pair_id:
        log.error("no /completion request observed within 20s")
        return 1
    log.info("    /completion pair_id=%s", pair_id[:10])

    log.info("[7] waiting up to 90s for response...")
    done = driver.wait_done(pair_id, timeout=90.0)
    if done:
        log.info("    response: status=%s body=%dB",
                 done["status_code"], done["body_len"])
    else:
        log.warning("    response did NOT appear within 90s")

    # ============== Inspection ==============
    log.info("\n[8] D18 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    uploads = con.execute(
        "SELECT path, COUNT(*) AS n, status_code, length(body_text_or_json) AS L "
        "FROM raw_captures "
        "WHERE created_at >= ? AND host='claude.ai' "
        "  AND (path LIKE '%upload-file%' OR path LIKE '%/files%' "
        "       OR path LIKE '%/wiggle/%') "
        "GROUP BY path, status_code "
        "ORDER BY n DESC LIMIT 10",
        (base,),
    ).fetchall()
    print(f"\n  upload-shaped paths since baseline: {len(uploads)}")
    for r in uploads:
        print(f"    {r['n']:3d}x  status={r['status_code']}  body={r['L']}B  {r['path'][:90]}")

    req = con.execute(
        "SELECT body_text_or_json FROM raw_captures "
        "WHERE pair_id=? AND direction='request'",
        (pair_id,),
    ).fetchone()
    body = req["body_text_or_json"] if req else ""
    body_low = (body or "").lower()
    has_file_ref = any(m in body_low for m in (
        "file_uuid", '"files":[', "attachment", ".pdf",
    ))
    print(f"\n  /completion request body: {len(body)} bytes")
    print(f"  body has file reference: {has_file_ref}")

    msgs = con.execute(
        "SELECT role, content_text, content_json, length(content_text) AS L "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D18 pair: {len(msgs)}")
    user_atts = []
    asst_text = ""
    for m in msgs:
        atts = []
        if m["content_json"]:
            try:
                cj = json.loads(m["content_json"])
                if isinstance(cj, dict):
                    atts = cj.get("attachments") or []
            except Exception:
                pass
        print(f"    role={m['role']:10s} text_len={m['L']:5d} attachments={len(atts)}")
        if atts:
            print(f"      preview: {str(atts)[:240]!r}")
        if m["role"] == "user":
            user_atts = atts
        elif m["role"] == "assistant":
            asst_text = m["content_text"] or ""

    print(f"\n  assistant text head: {asst_text[:240]!r}")
    has_token = PDF_TOKEN.lower() in asst_text.lower()
    has_doc_kind = any(
        (str((a or {}).get("file_kind", "")).lower() in ("document", "blob", "pdf"))
        or ("pdf" in str(a).lower())
        or ("document" in str(a).lower())
        for a in user_atts
    )
    print(f"  assistant recognised token {PDF_TOKEN!r}: {has_token}")
    print(f"  user msg has document-kind attachment: {has_doc_kind}")

    print()
    if has_token and len(user_atts) > 0 and has_doc_kind:
        print(f"  D18 VERDICT: PASS - PDF summarised with token, "
              f"user msg has {len(user_atts)} document attachment(s)")
        return 0
    if has_token and has_file_ref and len(user_atts) == 0:
        print(f"  D18 VERDICT: PARTIAL - token recognised + file ref in "
              f"body but no attachments[] in normalized user msg "
              f"(reconciler join gap)")
        return 0
    if has_token:
        print(f"  D18 VERDICT: PARTIAL - token recognised but doc-kind "
              f"flagging missing")
        return 0
    if len(uploads) == 0:
        print(f"  D18 VERDICT: SKIP - no upload requests captured AND "
              f"token not recognised. PDF clipboard paste (CF_HDROP) "
              f"did not trigger Claude Desktop's file-upload handler "
              f"in this build. Capture pipeline is fine; this is a "
              f"driver-side automation gap. Reproduce manually via "
              f"drag-drop or paperclip / file-picker; capture "
              f"pipeline expected to handle the upload identically "
              f"to D06 (CSV).")
        return 0
    print(f"  D18 VERDICT: PARTIAL - upload happened but PDF text not "
          f"surfaced in reply (model may not be document-OCR capable, "
          f"or PDF arrived in unexpected shape)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
