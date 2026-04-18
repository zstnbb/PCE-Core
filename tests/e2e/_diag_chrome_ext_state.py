# SPDX-License-Identifier: Apache-2.0
"""Diagnostic: launch the managed Chrome profile via Selenium (as the
probe does) and enumerate which extensions Chrome actually loaded.

Also screenshots chrome://extensions for visual confirmation.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# path wiring
_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent.parent))

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.chrome.service import Service  # noqa: E402
from tests.e2e import conftest as _cf  # noqa: E402

PROFILE = Path(
    os.environ.get(
        "PCE_CHROME_MANAGED_PROFILE",
        str(Path.home() / ".pce" / "chrome_profile"),
    )
)
EXT = Path(__file__).resolve().parent.parent.parent / \
    "pce_browser_extension_wxt" / ".output" / "chrome-mv3"
SHOTS = Path(__file__).resolve().parent / "screenshots"


def main() -> int:
    print(f"Profile:   {PROFILE}")
    print(f"Extension: {EXT}")
    print(f"Profile exists: {PROFILE.is_dir()}")
    print(f"  Default/ exists: {(PROFILE / 'Default').is_dir()}")
    print()

    opts = Options()
    opts.add_argument(f"--user-data-dir={PROFILE}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1400,900")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # Important: do NOT add --load-extension (Chrome 147 rejects it
    # anyway). The managed profile should have the extension pre-
    # installed via chrome://extensions UI.

    print("Launching Chrome (selenium, no --load-extension)...")
    driver_path = _cf._get_chromedriver_path()
    service = Service(driver_path) if driver_path else None
    print(f"  chromedriver: {driver_path or '<selenium-manager>'}")
    drv = webdriver.Chrome(service=service, options=opts)
    try:
        print("Chrome version:", drv.capabilities.get("browserVersion"))
        # Enumerate all windows/targets (chromedriver exposes only
        # standard pages, but we can use CDP to list ALL targets
        # including background pages / service workers / extensions).
        try:
            targets = drv.execute_cdp_cmd("Target.getTargets", {})
            infos = targets.get("targetInfos", [])
            print(f"\nCDP targets ({len(infos)}):")
            for t in infos:
                ttype = t.get("type", "?")
                url = t.get("url", "")
                title = t.get("title", "")
                print(f"  [{ttype:<16}] {url[:90]}  :: {title[:40]}")
        except Exception as e:
            print(f"CDP targets query failed: {e}")

        # Navigate to chrome://extensions and screenshot
        print("\nNavigating to chrome://extensions ...")
        drv.get("chrome://extensions/")
        time.sleep(3)
        SHOTS.mkdir(parents=True, exist_ok=True)
        shot = SHOTS / "diag_chrome_extensions.png"
        drv.save_screenshot(str(shot))
        print(f"Screenshot: {shot}")

        # Extract extension IDs/names from the Polymer shadow DOM
        # via JS. chrome://extensions exposes the list as custom
        # elements.
        js = """
        const mgr = document.querySelector('extensions-manager');
        if (!mgr) return {error: 'no extensions-manager'};
        const itemList = mgr.shadowRoot.querySelector('extensions-item-list');
        if (!itemList) return {error: 'no extensions-item-list'};
        const items = itemList.shadowRoot.querySelectorAll('extensions-item');
        const out = [];
        for (const it of items) {
            const id = it.getAttribute('id');
            const name = it.shadowRoot?.querySelector('#name')?.textContent?.trim();
            const enabled = it.shadowRoot?.querySelector('#enableToggle')?.checked;
            out.push({id, name, enabled});
        }
        const devMode = mgr.shadowRoot.querySelector('#devMode');
        return {
            count: out.length,
            items: out,
            dev_mode_on: devMode ? devMode.checked : null,
        };
        """
        try:
            state = drv.execute_script(js)
            print("\nchrome://extensions state:")
            print(json.dumps(state, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"extensions-manager query failed: {e}")

        # Navigate to a simple AI page and check __PCE_* flags after
        # bridge.js should have had time to inject.
        print("\nNavigating to https://gemini.google.com/app and waiting 6s ...")
        drv.get("https://gemini.google.com/app")
        time.sleep(6)
        keys = ["__PCE_AI_PAGE_CONFIRMED", "__PCE_GEMINI_ACTIVE",
                "__PCE_BRIDGE_ACTIVE", "__PCE_UNIVERSAL_EXTRACTOR_LOADED"]
        flags = drv.execute_script(
            "const o={}; for (const k of arguments[0]) o[k] = !!window[k]; "
            "o.__attr = document.documentElement.getAttribute('data-pce-ai-confirmed'); "
            "return o;",
            keys,
        )
        print(f"PCE flags on gemini: {flags}")

        shot2 = SHOTS / "diag_chrome_gemini_loaded.png"
        drv.save_screenshot(str(shot2))
        print(f"Screenshot: {shot2}")

        return 0
    finally:
        try:
            drv.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
