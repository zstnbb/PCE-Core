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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# Post-P2.5 Phase 4: load the WXT build output, not the removed
# legacy source directory.
EXTENSION_DIR = (
    PROJECT_ROOT / "pce_browser_extension_wxt" / ".output" / "chrome-mv3"
)
MANAGED_PROFILE = Path.home() / ".pce" / "chrome_profile"

SITES = [
    ("chatgpt",        "https://chatgpt.com/"),
    ("claude",         "https://claude.ai/new"),
    ("deepseek",       "https://chat.deepseek.com/"),
    ("gemini",         "https://gemini.google.com/app"),
    ("googleaistudio", "https://aistudio.google.com/"),
    ("grok",           "https://grok.com/"),
    ("kimi",           "https://www.kimi.com/"),
    ("manus",          "https://manus.im/app"),
    ("perplexity",     "https://www.perplexity.ai/"),
    ("poe",            "https://poe.com/"),
    ("zhipu",          "https://chatglm.cn/"),
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
        # Open first site directly
        SITES[0][1],
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

        opts = Options()
        opts.debugger_address = f"127.0.0.1:{port}"
        driver = webdriver.Chrome(options=opts)

        for name, url in SITES[1:]:
            driver.execute_script(f'window.open("{url}", "_blank");')
            time.sleep(0.3)
            print(f"  Opened: {name:<16} {url}")

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
