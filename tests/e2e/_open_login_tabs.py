# SPDX-License-Identifier: Apache-2.0
"""Launch the E2E test browser (managed profile + PCE extension) and open all sites for login.

Usage:
    python tests/e2e/_open_login_tabs.py
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Make absolute imports work whether invoked as
# ``python tests/e2e/_open_login_tabs.py`` or as a module. Mirrors the
# pattern used in ``pce_site_probe.py``.
if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent.parent))
    __package__ = "tests.e2e"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Post-P2.5 Phase 4: load the WXT build output, not the removed
# legacy source directory.
EXTENSION_DIR = (
    PROJECT_ROOT / "pce_browser_extension_wxt" / ".output" / "chrome-mv3"
)
MANAGED_PROFILE = Path.home() / ".pce" / "chrome_profile"

# Site list is sorted by ``SITE-TIER-MATRIX.md`` tier so the user
# logs in to the high-value tabs first (tab 0 == ChatGPT). Adding /
# removing entries here is the canonical way to extend login coverage;
# keep the per-line comment in sync with the matrix.
SITES = [
    # --- S0 (indispensable daily) ---------------------------------
    ("chatgpt",        "https://chatgpt.com/"),
    ("claude",         "https://claude.ai/new"),
    # --- S1 (high-value frequent) ---------------------------------
    ("gemini",         "https://gemini.google.com/app"),
    ("googleaistudio", "https://aistudio.google.com/"),
    ("perplexity",     "https://www.perplexity.ai/"),
    # --- S2 (workflow-critical per segment) -----------------------
    ("copilot",        "https://copilot.microsoft.com/"),
    ("grok",           "https://grok.com/"),
    ("deepseek",       "https://chat.deepseek.com/"),
    # --- S3 (breadth coverage) ------------------------------------
    ("huggingface",    "https://huggingface.co/chat/"),
    ("poe",            "https://poe.com/"),
    ("kimi",           "https://www.kimi.com/"),
    ("zhipu",          "https://chatglm.cn/"),
    ("mistral",        "https://chat.mistral.ai/chat"),
    ("manus",          "https://manus.im/app"),
    # --- SX (scaffolding, account-gated, NOT READY) ---------------
    # Open these so the user can land on the login page even though
    # the per-site content scripts are still DOM-unverified. Drop a
    # site if its login URL turns out to break the rest of the run.
    ("m365-copilot",   "https://m365.cloud.microsoft/chat"),
    ("notion",         "https://www.notion.so/login"),
    ("gmail",          "https://mail.google.com/"),
    ("figma",          "https://www.figma.com/login"),
]


def _find_chrome() -> str:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    raise FileNotFoundError("Chrome not found")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    MANAGED_PROFILE.mkdir(parents=True, exist_ok=True)
    chrome = _find_chrome()
    port = _find_free_port()

    args = [
        chrome,
        f"--user-data-dir={MANAGED_PROFILE}",
        f"--remote-debugging-port={port}",
        "--enable-extensions",
        f"--load-extension={EXTENSION_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--remote-allow-origins=*",
        "--window-size=1280,900",
        # Start on about:blank so Selenium can register the stealth payload
        # BEFORE the first real navigation. Loading SITES[0][1] here would
        # mean tab 0 finishes Cloudflare's JS challenge with the un-patched
        # navigator.webdriver/cdc_/WebGL fingerprint -- the exact bug we
        # are fixing.
        "about:blank",
    ]

    print(f"Launching Chrome (managed profile) on port {port} ...")
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    # Wait for debugger endpoint
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            urllib.request.urlopen(endpoint, timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("ERROR: Chrome debugger did not start. Is another instance using this profile?")
        sys.exit(1)

    print(f"Chrome ready at 127.0.0.1:{port}")

    # Connect Selenium to open remaining tabs
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        # Stealth + chromedriver helpers live in the same package; the
        # ``__package__`` setup at the top of this file ensures absolute
        # imports resolve whether this script runs directly or as a
        # module. Use absolute paths because ``conftest`` itself imports
        # ``tests.e2e._stealth`` and would fail if the package context
        # weren't established.
        from tests.e2e._stealth import apply_stealth, apply_stealth_all_tabs
        from tests.e2e.conftest import _get_chromedriver_path

        opts = Options()
        opts.debugger_address = f"127.0.0.1:{port}"
        # Selenium Manager often fails to download chromedriver in CN
        # network environments. ``_get_chromedriver_path()`` first
        # checks ``~/.wdm/drivers/chromedriver`` (populated by
        # webdriver_manager during normal pytest runs) and only falls
        # back to a fresh download if no cache exists. This makes the
        # script work offline-after-first-success.
        driver_path = _get_chromedriver_path()
        service = Service(driver_path) if driver_path else None
        driver = webdriver.Chrome(service=service, options=opts)

        # Tab 0 is currently about:blank. Register stealth on it BEFORE
        # navigating, then re-run AFTER navigation so the loaded page
        # also has the patches in its live window (the
        # ``addScriptToEvaluateOnNewDocument`` CDP path is unreliable in
        # Selenium 4 because chromedriver binds CDP commands to the
        # original target; the post-navigation ``execute_script`` is the
        # path that actually patches what Cloudflare's challenge sees).
        apply_stealth(driver, label="open_login_tabs:tab0:pre")
        print(f"  Opened: {SITES[0][0]:<16} {SITES[0][1]}")
        driver.get(SITES[0][1])
        apply_stealth(driver, label="open_login_tabs:tab0:post")

        # For each subsequent site: open a NEW tab via the W3C
        # standard ``switch_to.new_window`` (NOT ``window.open``, which
        # Chrome 137+ throttles when invoked without a user gesture --
        # the result is multiple loop iterations re-using the same tab
        # and only 2-3 tabs end up created out of 11). Pre- AND post-
        # navigation ``apply_stealth`` ensures the page Cloudflare
        # samples has all fingerprint surfaces patched.
        for name, url in SITES[1:]:
            driver.switch_to.new_window("tab")  # creates + auto-switches
            apply_stealth(driver, label=f"open_login_tabs:{name}:pre")
            driver.get(url)
            apply_stealth(driver, label=f"open_login_tabs:{name}:post")
            print(f"  Opened: {name:<16} {url}")

        # Belt-and-braces: re-register on every tab in case any of the
        # window.open calls created a target we didn't switch to. Idempotent.
        applied = apply_stealth_all_tabs(driver, label="open_login_tabs:final")
        print(f"  Stealth registered on {applied}/{len(driver.window_handles)} tabs")

        # Switch back to the first tab so the user sees ChatGPT first.
        try:
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass

        print(f"\n{'='*60}")
        print(f"  Opened {len(SITES)} sites. Please log in to each tab.")
        print(f"  Debug address: 127.0.0.1:{port}")
        print(f"  Profile: {MANAGED_PROFILE}")
        print(f"{'='*60}")
        print(f"\nWhen done, set this env var before running tests:")
        print(f"  $env:PCE_CHROME_DEBUG_ADDRESS = \"127.0.0.1:{port}\"")
        print(f"\nKeep this browser open. Press Ctrl+C here when finished.")

        # Keep script alive so the browser stays open
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nDone. Browser left open for testing.")

    except Exception as e:
        print(f"Selenium attach failed: {e}")
        print("Browser is open — manually navigate to sites and log in.")
        print(f"Debug address: 127.0.0.1:{port}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
