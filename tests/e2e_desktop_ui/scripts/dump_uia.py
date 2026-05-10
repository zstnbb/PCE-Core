# SPDX-License-Identifier: Apache-2.0
"""UIA tree dumper for Claude Desktop.

Run modes:

    python -m tests.e2e_desktop_ui.scripts.dump_uia idle
    python -m tests.e2e_desktop_ui.scripts.dump_uia idle --kw paperclip,attach,upload,file,thinking,extended
    python -m tests.e2e_desktop_ui.scripts.dump_uia idle --kw send,model,sonnet,haiku,opus,style,picker
    python -m tests.e2e_desktop_ui.scripts.dump_uia hover-last     # hovers last message bubble first
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-attach    # clicks paperclip then dumps the file dialog
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-style     # clicks the style picker then dumps menu
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-model     # clicks the model picker then dumps menu

Use ``--kw`` to filter the output by case-insensitive substring against
``name``, ``automation_id``, ``control_type``, or ``value``. Comma-
separated.

Use ``--ct`` to filter by control_type (Button / MenuItem / Edit /
ListItem / ...). Comma-separated.

Output is written to stdout and to ``_uia_dump_<mode>.txt`` in the cwd.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pywinauto.keyboard import send_keys

from ..drivers.claude_desktop import ClaudeDesktopDriver
from ..utils import configure_utf8_stdout


def main() -> int:
    configure_utf8_stdout()
    p = argparse.ArgumentParser()
    p.add_argument(
        "mode",
        choices=("idle", "hover-last", "open-attach", "open-style", "open-model"),
    )
    p.add_argument(
        "--kw",
        default="",
        help="comma-separated case-insensitive substring filters",
    )
    p.add_argument(
        "--ct",
        default="",
        help="comma-separated control_type filters (Button,MenuItem,...)",
    )
    p.add_argument(
        "--max",
        type=int,
        default=400,
        help="max lines to print (after filter)",
    )
    args = p.parse_args()
    keywords = [k.strip() for k in args.kw.split(",") if k.strip()] or None
    control_types = [c.strip() for c in args.ct.split(",") if c.strip()] or None

    drv = ClaudeDesktopDriver()
    drv.focus()
    time.sleep(0.5)

    if args.mode == "hover-last":
        drv._hover_message(0)
        time.sleep(0.6)
    elif args.mode == "open-attach":
        # click any button whose name suggests file attach
        btn = drv._find_uia_by_name_substr(
            ("attach", "upload", "paperclip", "file", "image"),
            control_types=("Button",),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(0.6)
        else:
            print("[dump_uia] open-attach: no attach-shaped button found; "
                  "falling back to plain idle dump", file=sys.stderr)
    elif args.mode == "open-style":
        btn = drv._find_uia_by_name_substr(
            ("style", "concise", "explanatory", "formal", "writing style"),
            control_types=("Button", "MenuItem"),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(0.6)
        else:
            print("[dump_uia] open-style: no style trigger found; "
                  "falling back to plain idle dump", file=sys.stderr)
    elif args.mode == "open-model":
        btn = drv._find_uia_by_name_substr(
            ("sonnet", "haiku", "opus", "claude ", "model"),
            control_types=("Button",),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(0.6)
        else:
            print("[dump_uia] open-model: no model trigger found; "
                  "falling back to plain idle dump", file=sys.stderr)

    rows = drv.dump_tree(keywords=keywords, control_types=control_types)
    print(f"[dump_uia] mode={args.mode} kw={keywords} ct={control_types} "
          f"-> {len(rows)} elements")
    out_path = Path(f"_uia_dump_{args.mode}.txt")
    with out_path.open("w", encoding="utf-8") as f:
        for ct, nm, aid, rect, val in rows[: args.max]:
            line = f"  {ct:18s} | name={nm!r:80s} | aid={aid!r:30s} | rect={rect:32s} | val={val!r}"
            print(line)
            f.write(line + "\n")
    if len(rows) > args.max:
        more = f"  ... ({len(rows) - args.max} more rows; raise --max to see them)"
        print(more)
        with out_path.open("a", encoding="utf-8") as f:
            f.write(more + "\n")
    print(f"[dump_uia] wrote {out_path.resolve()}")

    # If we opened a menu, dismiss it
    if args.mode in ("open-attach", "open-style", "open-model"):
        send_keys("{ESC}", pause=0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
