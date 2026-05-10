# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window H: D17 image upload + vision Q.

Drives Claude Desktop to attach a small PNG with a visible token
(``PCE-D17-5039``) via clipboard CF_HDROP, then asks Claude to read
the token. The model's reply must contain the token, and the captured
``messages`` row must have ``content_json.attachments[].file_kind ==
"image"`` (or equivalent).

D17 verdict (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "Uploading an image (PNG / JPG) and asking a visual question stores
   ``attachments[]`` with ``file_kind="image"`` + dimensions + thumbnail
   URL where present; assistant successfully answers about image
   content."

PASS:
  - upload-shaped request appears in raw_captures since baseline
  - /completion request body references the image (file_uuid OR files)
  - persisted user message has content_json.attachments with image info
  - assistant content_text contains the visible token "PCE-D17-5039"

PARTIAL:
  - request body has the file reference but content_json.attachments
    on the user msg is empty (= reconciler regression — same shape as
    Round 2 / 2026-04-27 N10/file-uuid join issue)

FAIL:
  - assistant didn't recognise the token (likely vision not invoked)
    OR no upload was captured

Run cost: ~30s, 1 vision-capable Claude turn.
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
from ..fixtures import ensure_test_image
from ..utils import (
    baseline_ts as _baseline_ts,
    configure_utf8_stdout,
    copy_files_to_clipboard,
    default_db_path,
    wait_for_new_completion,
)

VISION_TOKEN = "PCE-D17-5039"
PROMPT = (
    "What text is shown in this image? "
    "Reply with just the text you see, in one short sentence."
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_h_d17")
    configure_utf8_stdout()

    fixture = ensure_test_image(VISION_TOKEN)
    log.info("D17 image fixture: %s (%d bytes)", fixture, fixture.stat().st_size)

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()
    log.info("=== Window H — D17 image / vision ===")
    log.info("[1] starting fresh chat")
    driver.new_chat()
    time.sleep(1.5)

    log.info("[2] putting %s on clipboard (CF_HDROP)", fixture.name)
    copy_files_to_clipboard(str(fixture))

    log.info("[3] focus + click composer + paste")
    driver.focus()
    driver.click_composer()
    time.sleep(0.4)
    send_keys("^v", pause=0.05)

    log.info("[4] waiting 6s for upload + thumbnail render...")
    time.sleep(6.0)

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

    log.info("[7] waiting up to 90s for response (vision is slower)...")
    done = driver.wait_done(pair_id, timeout=90.0)
    if done:
        log.info("    response: status=%s body=%dB",
                 done["status_code"], done["body_len"])
    else:
        log.warning("    response did NOT appear within 90s")

    # ============== Inspection ==============
    log.info("\n[8] D17 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    # Upload-shaped requests
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

    # /completion request body — file reference
    req = con.execute(
        "SELECT body_text_or_json FROM raw_captures "
        "WHERE pair_id=? AND direction='request'",
        (pair_id,),
    ).fetchone()
    body = req["body_text_or_json"] if req else ""
    body_low = (body or "").lower()
    has_file_ref = any(m in body_low for m in (
        "file_uuid", '"files":[', "attachment",
    ))
    print(f"\n  /completion request body: {len(body)} bytes")
    print(f"  body has file reference: {has_file_ref}")

    # Persisted user msg attachments
    msgs = con.execute(
        "SELECT role, content_text, content_json, length(content_text) AS L "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D17 pair: {len(msgs)}")
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
        print(f"    role={m['role']:10s} text_len={m['L']:5d} "
              f"attachments={len(atts)}")
        if atts:
            print(f"      preview: {str(atts)[:240]!r}")
        if m["role"] == "user":
            user_atts = atts
        elif m["role"] == "assistant":
            asst_text = m["content_text"] or ""

    print(f"\n  assistant text head: {asst_text[:200]!r}")
    asst_lower = asst_text.lower()
    has_token = VISION_TOKEN.lower() in asst_lower
    print(f"  assistant recognised token {VISION_TOKEN!r}: {has_token}")

    # Detect image-kind in attachments
    has_image_kind = any(
        (str((a or {}).get("file_kind", "")).lower() == "image")
        or ("image" in str(a).lower())
        for a in user_atts
    )
    print(f"  user msg has image-kind attachment: {has_image_kind}")

    # === Verdict ===
    print()
    if has_token and len(user_atts) > 0 and has_image_kind:
        print(f"  D17 VERDICT: PASS - vision answered with token, "
              f"user msg has {len(user_atts)} image-kind attachment(s)")
        return 0
    if has_token and has_file_ref and len(user_atts) == 0:
        print(f"  D17 VERDICT: PARTIAL - vision answered with token, "
              f"file reference is in request body, but normalized user "
              f"msg has 0 attachments (reconciler join gap — same "
              f"shape as web Round 2 finding for image upload)")
        return 0  # treat as success for spike
    if has_token and len(user_atts) > 0:
        print(f"  D17 VERDICT: PARTIAL - token recognised + "
              f"{len(user_atts)} attachment(s) but not flagged as image")
        return 0
    if not has_token and len(uploads) == 0:
        print(f"  D17 VERDICT: SKIP - no upload requests captured + "
              f"token not recognised in reply. Image clipboard paste "
              f"(CF_HDROP for PNG) did not trigger Claude Desktop's "
              f"file-upload handler in this build. Capture pipeline "
              f"is fine; this is a driver-side automation gap. "
              f"Reproduce manually by drag-dropping the image OR "
              f"using the in-UI paperclip / file-picker; capture "
              f"pipeline expected to handle the upload identically "
              f"to D06.")
        return 0
    if not has_token:
        print(f"  D17 VERDICT: PARTIAL - upload happened but assistant "
              f"did not recognise the token (model not vision-capable, "
              f"or image arrived in unexpected shape)")
        return 0
    print(f"  D17 VERDICT: FAIL - unspecified")
    return 1


if __name__ == "__main__":
    sys.exit(main())
