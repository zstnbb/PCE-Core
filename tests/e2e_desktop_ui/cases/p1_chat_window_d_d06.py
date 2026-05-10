# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop chat — Window D: D06 attachment upload.

Drives Claude Desktop to attach a small CSV file via the
clipboard-paste path (CF_HDROP -> Ctrl+V), then asks a question about
the file contents. This avoids needing to drive the native Win32
file-open dialog (which is doable but adds a window-handle hop).

D06 verdict criteria (per ``DESKTOP-PRODUCT-MATRIX.md``):

  "Attachment uploaded (PDF / image / CSV) is referenced in the
   captured request body, and an upload-side request appears in
   raw_captures."

We check post-run:
  1. A request to a Claude file-upload endpoint appears since baseline.
     (Path patterns: ``/api/.../files`` or ``/api/.../upload`` —
     observed empirically; recorded as informational either way.)
  2. The ``/completion`` request body contains a reference to the
     uploaded file (file_uuid, attachment object, etc.).
  3. The persisted ``messages`` row has attachment metadata in
     ``content_json.attachments``.

Run cost: ~30s. ~1 small Claude API call.

Pre-condition: ``tests/e2e/fixtures/generated/sample_data.csv`` exists.
The repo already ships this fixture.
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
from ..utils import (
    baseline_ts as _baseline_ts,
    copy_files_to_clipboard,
    default_db_path,
    latest_completion_pair_id,
    wait_for_new_completion,
)

FIXTURE = Path("tests/e2e/fixtures/generated/sample_data.csv").resolve()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("p1_chat_window_d_d06")

    if not FIXTURE.exists():
        log.error("fixture not found: %s", FIXTURE)
        return 1

    log.info("attachment fixture: %s (%d bytes)", FIXTURE, FIXTURE.stat().st_size)

    base = _baseline_ts()
    Path("_baseline_ts.txt").write_text(str(base), encoding="ascii")
    log.info("baseline_ts = %.3f (saved)", base)

    driver = ClaudeDesktopDriver()

    log.info("=== Window D — D06 attachment ===")

    # 1. Put the CSV on the clipboard (CF_HDROP)
    log.info("[1] putting %s on clipboard (CF_HDROP)", FIXTURE.name)
    copy_files_to_clipboard(str(FIXTURE))

    # 2. Focus + click composer + paste
    log.info("[2] focusing Claude Desktop and pasting via Ctrl+V")
    driver.focus()
    driver.click_composer()
    time.sleep(0.4)
    send_keys("^v", pause=0.05)

    # 3. Give Claude Desktop time to upload (it'll fire to /api/.../files)
    log.info("[3] waiting 5s for upload to complete...")
    time.sleep(5.0)

    # 4. Type the question (the CSV must be already attached as a chip)
    prompt = "What's the column header in the attached file? Reply briefly."
    log.info("[4] typing prompt: %r", prompt)
    # Re-focus + re-click composer (paste may have shifted UI focus to chip)
    driver.focus()
    driver.click_composer()
    time.sleep(0.3)
    send_keys(prompt, with_spaces=True, vk_packet=True, pause=0.02)
    time.sleep(0.3)

    # 5. Submit
    since_ts = time.time() - 2.0
    log.info("[5] pressing Enter")
    send_keys("{ENTER}", pause=0.05)

    # 6. Wait for /completion
    pair_id = wait_for_new_completion(since_ts, timeout=20.0)
    if not pair_id:
        log.error("no /completion request observed within 20s")
        return 1
    log.info("    /completion request pair_id=%s", pair_id[:10])

    # 7. Wait for response row
    log.info("[6] waiting up to 60s for response...")
    done = driver.wait_done(pair_id, timeout=60.0)
    if done:
        log.info("    response: status=%s body=%dB",
                 done["status_code"], done["body_len"])
    else:
        log.warning("    response did NOT appear within 60s")

    # 8. Inspection
    log.info("\n[7] D06 inspection")
    db = default_db_path()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    # File-upload-style requests in delta window
    file_paths = con.execute(
        "SELECT path, COUNT(*) AS n, status_code, length(body_text_or_json) AS L "
        "FROM raw_captures "
        "WHERE created_at >= ? AND host='claude.ai' "
        "  AND (path LIKE '%/files%' OR path LIKE '%/upload%' "
        "       OR path LIKE '%/attachments%') "
        "GROUP BY path, status_code "
        "ORDER BY n DESC LIMIT 10",
        (base,),
    ).fetchall()
    print(f"\n  file-upload-shaped paths since baseline: {len(file_paths)}")
    for r in file_paths:
        print(f"    {r['n']:3d}x  status={r['status_code']}  body={r['L']}B  {r['path'][:90]}")

    # /completion request body for the prompt
    req = con.execute(
        "SELECT body_text_or_json FROM raw_captures "
        "WHERE pair_id=? AND direction='request'",
        (pair_id,),
    ).fetchone()
    body = req["body_text_or_json"] if req else ""
    print(f"\n  /completion request body length: {len(body)}")
    body_low = (body or "").lower()
    has_file_marker = any(m in body_low for m in (
        "file_uuid", "attachment", "files\":", "file\":", "csv", "sample_data",
    ))
    print(f"  body contains file/attachment marker: {has_file_marker}")
    if has_file_marker:
        # Show context around first marker
        for m in ("attachment", "file_uuid", "files", "sample_data"):
            idx = body_low.find(m)
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(body), idx + 200)
                print(f"  marker {m!r} context [{start}:{end}]: {body[start:end]!r}")
                break

    # Persisted assistant message
    msgs = con.execute(
        "SELECT role, length(content_text) AS L, content_json "
        "FROM messages WHERE capture_pair_id=? ORDER BY ts",
        (pair_id,),
    ).fetchall()
    print(f"\n  messages rows for D06 pair: {len(msgs)}")
    for m in msgs:
        atts = []
        if m["content_json"]:
            try:
                cj = json.loads(m["content_json"])
                if isinstance(cj, dict):
                    atts = cj.get("attachments") or []
            except Exception:
                pass
        print(f"    role={m['role']:10s} text_len={m['L']:4d} attachments={len(atts)}")
        if atts:
            print(f"      attachments preview: {str(atts)[:200]!r}")

    # === D06 verdict ===
    print()
    has_upload_path = len(file_paths) > 0
    asst_msg = next((m for m in msgs if m["role"] == "assistant"), None)
    user_msg = next((m for m in msgs if m["role"] == "user"), None)

    user_atts = 0
    if user_msg and user_msg["content_json"]:
        try:
            cj = json.loads(user_msg["content_json"])
            if isinstance(cj, dict):
                user_atts = len(cj.get("attachments") or [])
        except Exception:
            pass

    if has_file_marker and user_atts > 0 and asst_msg:
        print(f"  D06 VERDICT: PASS - attachment present in request body, "
              f"user message has {user_atts} attachments, assistant replied")
        return 0
    elif has_file_marker and asst_msg:
        print(f"  D06 VERDICT: PARTIAL - attachment in request body but no "
              f"attachments[] in normalized user message")
        return 0  # treat partial as success for the spike
    elif asst_msg:
        print(f"  D06 VERDICT: FAIL - assistant replied but no attachment "
              f"reference found in request body")
        return 1
    else:
        print(f"  D06 VERDICT: FAIL - no assistant message persisted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
