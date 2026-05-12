# SPDX-License-Identifier: Apache-2.0
"""Restore Claude Desktop to a fresh Cowork chat composer.

After C09/C10 click sidebar entries (Live artifacts / Dispatch), the
right pane navigates away from the Cowork chat composer. Top-tab click
alone doesn't return to chat — also need to click "New task".
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from tests.e2e_desktop_ui.drivers.claude_desktop import ClaudeDesktopDriver

    driver = ClaudeDesktopDriver()
    driver.focus()
    time.sleep(0.5)

    # Step 1: click the top-level Cowork TAB
    print("[1/3] Clicking top-level 'Cowork' tab ...")
    ok = driver.open_cowork_tab()
    print(f"      open_cowork_tab returned: {ok}")
    time.sleep(1.0)

    # Step 2: click "New task" button in the sidebar
    print("[2/3] Clicking 'New task' sidebar button ...")
    new_task = driver._find_uia_by_name_substr(  # noqa: SLF001
        ("New task",),
        control_types=("Button",),
        timeout=3.0,
    )
    if new_task is None:
        print("      [WARN] 'New task' button not found")
    else:
        try:
            new_task.click_input()
            print("      OK — clicked")
        except Exception as exc:
            print(f"      [FAIL] click raised: {exc}")
    time.sleep(1.5)

    # Step 3: verify composer is focusable
    print("[3/3] Verifying composer focusable ...")
    composer = driver._find_composer_uia()  # noqa: SLF001
    if composer is None:
        print("      [WARN] composer UIA element not found")
        return 1
    try:
        rect = composer.rectangle()
        print(f"      OK — composer at rect=({rect.left}, {rect.top}, "
              f"{rect.right}, {rect.bottom})")
    except Exception as exc:
        print(f"      [WARN] composer.rectangle raised: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
