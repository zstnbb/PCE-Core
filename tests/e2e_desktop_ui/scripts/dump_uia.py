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

Cowork-region modes (P5.B.5 RECON; closes §5.B.2 questions Q1/Q3):

    python -m tests.e2e_desktop_ui.scripts.dump_uia open-cowork    # click sidebar 'Cowork' tab, idle dump
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-skills    # assumes Cowork tab is open; types '/' in composer, dump Skills picker
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-dispatch  # click 'Dispatch' sidebar entry, dump
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-scheduled # click 'Scheduled' sidebar entry, dump
    python -m tests.e2e_desktop_ui.scripts.dump_uia open-customize # click 'Customize' entry, dump cowork settings panel

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
        choices=(
            "idle",
            "hover-last",
            "open-attach",
            "open-style",
            "open-model",
            # Cowork-region (P5.B.5 RECON):
            "open-cowork",
            "open-skills",
            "open-dispatch",
            "open-scheduled",
            "open-customize",
        ),
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
    elif args.mode == "open-cowork":
        # P5.B.5 RECON Q1 supporting dump: locate the sidebar 'Cowork'
        # tab and click it. Dump the resulting top-level tree so we can
        # see (a) the Cowork tab's inner navigation (Live Artifacts /
        # Dispatch / Scheduled / Customize entries) and (b) any
        # automation_id pinning we can use for `open_cowork_tab()`.
        btn = drv._find_uia_by_name_substr(
            ("cowork", "Cowork"),
            control_types=("Button", "TabItem", "ListItem", "Hyperlink",
                           "TreeItem", "Custom"),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(1.0)  # cowork tab can lazy-load its sub-pane
        else:
            print("[dump_uia] open-cowork: no 'Cowork' entry found in "
                  "sidebar; falling back to plain idle dump (manually "
                  "switch tab first if your build hides it under a menu)",
                  file=sys.stderr)
    elif args.mode == "open-skills":
        # P5.B.5 RECON Q1 primary dump: with Cowork tab already open,
        # focus the composer and type '/' to trigger the Skills picker.
        # The picker may render as a UIA descendant of the main window
        # OR as a separate top-level Win32 popup — this dump tells us
        # which (which determines whether `pick_skill()` reuses
        # `_find_uia_by_name_substr` or `_find_uia_by_name_substr_all`
        # cross-window mode).
        focused = drv.ensure_composer_focus()
        if not focused:
            print("[dump_uia] open-skills: composer focus failed; '/' will "
                  "not trigger Skills picker — dump may be empty",
                  file=sys.stderr)
        send_keys("/", pause=0.05)
        time.sleep(0.8)  # let the picker render
    elif args.mode == "open-dispatch":
        # P5.B.5 RECON Q3 dump: locate 'Dispatch' (Beta) entry. May be
        # in the sidebar or on the cowork tab's inner pane. Click and
        # dump — the resulting tree tells us if Dispatch is a separate
        # top-level Win32 popup window or an in-app sidebar pane.
        btn = drv._find_uia_by_name_substr(
            ("dispatch", "Dispatch"),
            control_types=("Button", "ListItem", "Hyperlink", "TreeItem",
                           "TabItem", "Custom"),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(1.0)
        else:
            print("[dump_uia] open-dispatch: no 'Dispatch' entry found; "
                  "falling back to plain idle dump (Dispatch may be a "
                  "Beta-gated feature unavailable on this account)",
                  file=sys.stderr)
    elif args.mode == "open-scheduled":
        btn = drv._find_uia_by_name_substr(
            ("scheduled", "Scheduled", "schedule", "Schedule"),
            control_types=("Button", "ListItem", "Hyperlink", "TreeItem",
                           "TabItem", "Custom"),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(1.0)
        else:
            print("[dump_uia] open-scheduled: no 'Scheduled' entry found; "
                  "falling back to plain idle dump",
                  file=sys.stderr)
    elif args.mode == "open-customize":
        # Customize panel is where cowork-settings toggles live (e.g.
        # 'Web search enabled' coworkWebSearchEnabled). Used by C13.
        btn = drv._find_uia_by_name_substr(
            ("customize", "Customize", "settings", "Settings"),
            control_types=("Button", "MenuItem", "ListItem", "Hyperlink",
                           "TabItem", "Custom"),
            timeout=2.0,
        )
        if btn is not None:
            try:
                btn.click_input()
            except Exception:
                pass
            time.sleep(1.0)
        else:
            print("[dump_uia] open-customize: no 'Customize' / 'Settings' "
                  "entry found; falling back to plain idle dump",
                  file=sys.stderr)

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

    # If we opened a popup-style menu, dismiss it. Cowork navigation
    # modes (open-cowork / open-dispatch / open-scheduled) leave the
    # user on the new pane intentionally — RECON usually wants to
    # follow up with another dump from there. open-customize is
    # popup-shaped on this build but harmless to ESC.
    if args.mode in (
        "open-attach", "open-style", "open-model",
        "open-skills", "open-customize",
    ):
        send_keys("{ESC}", pause=0.05)
    return 0


if __name__ == "__main__":
    sys.exit(main())
