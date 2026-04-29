# SPDX-License-Identifier: Apache-2.0
"""Attach the PCE test runner to your everyday Chrome instead of
spawning a managed test profile.

Why this exists
---------------
The clone-cookies-into-managed-profile workflow
(``_clone_login_state.py``) hits two walls in practice:

1. **Cloudflare fingerprint binding.** ``cf_clearance`` cookies are
   bound to the browser fingerprint that earned them; copying the
   cookie alone does not pass the re-challenge if the destination
   browser's WebGL / Canvas / locale fingerprint differs even
   slightly. Sites like Grok and Perplexity sit behind this gate.

2. **Client-side auth in LocalStorage / IndexedDB.** Modern AI sites
   (Claude, ChatGPT) keep client-side bearer tokens and auth state
   in LevelDB-backed storage; merging just the Cookies SQLite leaves
   the front-end JS believing the user is logged out. The dest
   profile then redirects to ``/login?from=logout`` even with valid
   server-side session cookies present.

Running tests directly against your everyday Chrome sidesteps both
problems -- no clone, no fingerprint mismatch, no missing storage,
no Cloudflare re-challenge. The tradeoff is your daily browsing
session becomes the test host: PCE content scripts run on the 18
covered AI sites, the network interceptor samples requests, and a
debug port is exposed on localhost.

How to use
----------
1. Quit ALL Chrome windows (Ctrl+Shift+Q saves the session).
2. Re-launch Chrome with the debug flag + the PCE extension. This
   script prints the exact PowerShell command to copy-paste; pass
   ``--print-launch`` to print it without attaching.
3. Run this script:
       python -m tests.e2e._attach_to_daily_chrome --open-tabs
   It polls ``127.0.0.1:9222`` until Chrome is up, attaches Selenium
   via ``debugger_address``, optionally opens any missing AI site
   tabs, and babysits until Ctrl+C.

Flags
-----
    --port <int>          Debug port (default: 9222)
    --open-tabs           Open the 18 PCE-tracked AI sites in tabs
                          (skips sites already open in Chrome)
    --wait <int>          Seconds to wait for Chrome to come up after
                          printing the launch command (default: 180)
    --print-launch        Print the launch command and exit (no attach)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent.parent))
    __package__ = "tests.e2e"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXTENSION_DIR = (
    PROJECT_ROOT / "pce_browser_extension_wxt" / ".output" / "chrome-mv3"
)

# Risk-sorted site list for the daily-Chrome attach flow. We diverge
# from ``_open_login_tabs.SITES`` (which is sorted by tier / business
# value) on purpose: bot-scoring engines weight the **session
# trajectory** of opened domains heavily, so we want the cluster of
# easy / domestic / Google-suite sites to be visited *first* (build
# trust, harvest organic mousemove + scroll telemetry on the daily
# Chrome session) and the high-risk Cloudflare-gated ones LAST (Grok,
# Perplexity, Claude). When the high-risk sites' Turnstile fires, the
# session has 5-10 minutes of normal browsing context behind it and
# scores much better than a cold open.
SITES_BY_RISK: list[tuple[str, str]] = [
    # --- Tier 0: Google / Microsoft / domestic -- low CF presence ----
    ("gmail",          "https://mail.google.com/"),
    ("gemini",         "https://gemini.google.com/app"),
    ("googleaistudio", "https://aistudio.google.com/"),
    ("notion",         "https://www.notion.so/login"),
    ("figma",          "https://www.figma.com/login"),
    ("kimi",           "https://www.kimi.com/"),
    ("zhipu",          "https://chatglm.cn/"),
    ("deepseek",       "https://chat.deepseek.com/"),
    # --- Tier 1: cookie-auth, mild bot scoring -----------------------
    ("copilot",        "https://copilot.microsoft.com/"),
    ("m365-copilot",   "https://m365.cloud.microsoft/chat"),
    ("huggingface",    "https://huggingface.co/chat/"),
    ("manus",          "https://manus.im/app"),
    ("mistral",        "https://chat.mistral.ai/chat"),
    ("poe",            "https://poe.com/"),
    # --- Tier 2: heavy bot scoring (OpenAI Cloudflare + Auth0) -------
    ("chatgpt",        "https://chatgpt.com/"),
    # --- Tier 3: Cloudflare Super-Bot-Fight + Turnstile ALWAYS -------
    ("claude",         "https://claude.ai/new"),
    ("perplexity",     "https://www.perplexity.ai/"),
    ("grok",           "https://grok.com/"),
]

# Backwards-compat alias so older scripts importing ``SITES`` from this
# module still work; new code should use ``SITES_BY_RISK``.
SITES = SITES_BY_RISK


def _find_chrome() -> str:
    """Locate the system Chrome.exe; falls back to the canonical
    Program Files path so the printed launch command is still
    copy-pasteable even if our auto-detect misses (rare).
    """
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def _print_launch_command(port: int) -> None:
    chrome = _find_chrome()
    # Single-line PowerShell command, easy to copy-paste. ``Start-Process``
    # detaches Chrome from the current PowerShell so closing the
    # terminal doesn't kill the browser.
    cmd = (
        f'Start-Process "{chrome}" -ArgumentList '
        f"'--remote-debugging-port={port}',"
        f"'--remote-allow-origins=*',"
        f"'--disable-blink-features=AutomationControlled',"
        f'\'--load-extension="{EXTENSION_DIR}"\''
    )
    print()
    print("=" * 74)
    print("  Quit Chrome completely (Ctrl+Shift+Q saves the session), then")
    print("  paste this into a NEW PowerShell window:")
    print()
    print(f"    {cmd}")
    print()
    print("  Notes:")
    print(f"    - --remote-debugging-port={port}: lets Selenium attach via CDP")
    print("    - --disable-blink-features=AutomationControlled: keeps")
    print("      navigator.webdriver=false so Cloudflare doesn't flag the tab")
    print("    - --load-extension: side-loads the PCE WXT extension build")
    print("    - Your daily session restores on next launch.")
    print("=" * 74)
    print()


def _wait_for_debug_port(port: int, timeout: float) -> dict | None:
    """Poll ``http://127.0.0.1:<port>/json/version`` until it responds
    or ``timeout`` elapses. Returns the version dict on success.

    DevTools' ``/json/version`` is the canonical liveness probe -- it
    responds to GET only, doesn't require any auth, and returns a
    short JSON like ``{"Browser":"Chrome/137.0.0.0", "User-Agent":..}``.
    """
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            last_err = exc
            time.sleep(1.0)
    if last_err is not None:
        print(f"  last probe error: {last_err}", file=sys.stderr)
    return None


def _list_extensions(port: int) -> list[dict]:
    """Return DevTools targets that look like loaded extensions
    (``chrome-extension://<id>/...`` URLs). Used as a soft check that
    PCE is loaded; we never abort on this -- the user might have
    forgotten ``--load-extension`` and just wants to attach.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/list", timeout=2.0,
        ) as resp:
            targets = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    return [
        t for t in targets
        if (t.get("url") or "").startswith("chrome-extension://")
    ]


def _normalize(url: str) -> str:
    """Strip query and trailing slash for tab-dedup comparisons."""
    base = url.split("?", 1)[0].split("#", 1)[0]
    return base.rstrip("/").lower()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Attach to your daily Chrome via --remote-debugging-port",
    )
    p.add_argument("--port", type=int, default=9222)
    p.add_argument(
        "--open-tabs", action="store_true",
        help="Open all PCE-tracked AI site tabs (skips sites already open)",
    )
    p.add_argument(
        "--wait", type=int, default=180,
        help="Seconds to wait for Chrome to come up after printing the "
             "launch command (default: 180)",
    )
    p.add_argument(
        "--print-launch", action="store_true",
        help="Print the launch command and exit; do not attach",
    )
    p.add_argument(
        "--warmup", action="store_true",
        help="Before opening AI sites, visit a few benign sites "
             "(Wikipedia / GitHub / HN) and dwell + scroll on each, "
             "so the bot-scoring engine sees organic browsing context "
             "before any high-risk endpoint is hit. Recommended for "
             "the first run after switching VPN nodes.",
    )
    p.add_argument(
        "--no-humanize", action="store_true",
        help="Disable mouse-jiggler + bezier paths + read pauses. "
             "(Equivalent to PCE_E2E_HUMANIZE=0.)",
    )
    args = p.parse_args(argv)

    if args.print_launch:
        _print_launch_command(args.port)
        return 0

    # Quick probe -- if Chrome is already up we skip the launch dance.
    info = _wait_for_debug_port(args.port, timeout=2.0)
    if info is None:
        print(
            f"Chrome is NOT listening on 127.0.0.1:{args.port} yet."
        )
        _print_launch_command(args.port)
        print(f"Polling every 1s for up to {args.wait}s ...")
        info = _wait_for_debug_port(args.port, timeout=float(args.wait))
        if info is None:
            print(
                f"\nERROR: timed out waiting for Chrome on "
                f"127.0.0.1:{args.port}. Did you run the launch command?",
                file=sys.stderr,
            )
            return 1

    print(
        f"\nAttached. Browser: {info.get('Browser', '?')}  "
        f"WebKit: {info.get('WebKit-Version', '?')}"
    )

    # Soft sanity check on PCE extension presence.
    exts = _list_extensions(args.port)
    if exts:
        ext_ids = sorted({
            (t.get("url") or "").split("/")[2]
            for t in exts
            if (t.get("url") or "").startswith("chrome-extension://")
        })
        print(f"  Extensions present: {len(ext_ids)} ids -> {ext_ids[:6]}")
    else:
        print(
            "  WARN: no chrome-extension:// targets visible. The PCE "
            "extension may not be loaded -- if so, captures won't fire. "
            "Re-launch Chrome with --load-extension if needed."
        )

    # Selenium attach (only if we need to drive tabs or warm up). The
    # bare CDP /json/list + /json/new endpoints would also work but
    # Selenium gives us session-aware tab management for free.
    jiggler = None
    if args.open_tabs or args.warmup:
        if args.no_humanize:
            os.environ["PCE_E2E_HUMANIZE"] = "0"

        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        from tests.e2e._humanizer import (
            MouseJiggler,
            gentle_scroll,
            pace_between_sites,
            read_pause,
            warmup_browse,
        )

        # ``_get_chromedriver_path`` prefers the cached chromedriver
        # under ~/.wdm so we don't trigger Selenium Manager downloads
        # on every run.
        try:
            from tests.e2e.conftest import _get_chromedriver_path
            driver_path = _get_chromedriver_path()
        except Exception:
            driver_path = None

        opts = Options()
        opts.debugger_address = f"127.0.0.1:{args.port}"
        service = Service(driver_path) if driver_path else None
        driver = webdriver.Chrome(service=service, options=opts)

        # Start the ambient mouse jiggler immediately so the bot
        # scoring engine sees continuous interaction telemetry from
        # the moment we attach -- not just during the brief moments
        # we're driving Selenium commands. Idempotent if humanize=0.
        jiggler = MouseJiggler(driver).start()

        # Snapshot tabs that are already open (case-insensitive,
        # query/fragment stripped) so re-running this script doesn't
        # duplicate tabs in the user's daily browser.
        existing: set[str] = set()
        for h in driver.window_handles:
            try:
                driver.switch_to.window(h)
                existing.add(_normalize(driver.current_url))
            except Exception:
                pass

        # Warmup phase: visit a few benign domains, dwell, scroll. Two
        # bot-scoring effects: (1) ``document.referrer`` on the eventual
        # AI-site request becomes a real URL instead of empty;
        # (2) the per-session DNS / TCP / TLS-keepalive state and the
        # browsing-history vector both pick up neutral domains, diluting
        # the "this session has only ever seen grok.com" outlier signal.
        if args.warmup:
            print("\n  Warmup phase (visit benign sites, dwell + scroll)...")
            n = warmup_browse(driver)
            print(f"  Warmup: {n} sites visited")
            # Track them so we don't reopen them or dedup against them.
            for h in driver.window_handles:
                try:
                    driver.switch_to.window(h)
                    existing.add(_normalize(driver.current_url))
                except Exception:
                    pass

        opened = 0
        # We will open tabs in *risk-ascending* order so by the time
        # we reach the high-risk endpoints (Grok / Perplexity / Claude),
        # the session has many minutes of organic browsing telemetry
        # behind it.
        if args.open_tabs:
            print("\n  Opening AI sites (risk-sorted, paced)...")
            total = len(SITES_BY_RISK)
            for idx, (name, url) in enumerate(SITES_BY_RISK):
                target = _normalize(url)
                # Open if neither equal nor contained in any existing tab's
                # URL; site URLs differ enough that this is unambiguous.
                if any(target == ex or target in ex or ex in target for ex in existing):
                    print(f"  [{idx+1:>2}/{total}] skip (already open): {name:<14} {url}")
                    continue
                driver.switch_to.new_window("tab")
                driver.get(url)
                existing.add(target)
                opened += 1
                print(f"  [{idx+1:>2}/{total}] opened: {name:<14} {url}")

                # Brief read pause + gentle scroll on the just-loaded tab so
                # the page emits realistic first-paint -> first-input timing
                # and a wheel-event signature.
                read_pause(2.0, 5.0)
                try:
                    gentle_scroll(driver, random.randint(200, 700))
                except Exception:
                    pass

                # Inter-site pacing grows as we progress. Skip on the very
                # last site -- the user is about to log in there manually.
                if idx < total - 1:
                    secs = pace_between_sites(idx, total)
                    print(f"      paced {secs:5.1f}s before next tab")

        # Switch back to the first window so the user lands on a known
        # tab when they look at the browser.
        try:
            driver.switch_to.window(driver.window_handles[0])
        except Exception:
            pass

        print(f"\n  Tabs opened this run: {opened} / {len(SITES_BY_RISK)} sites total")

    print()
    print("=" * 74)
    print(f"  Daily Chrome attached. Debug address: 127.0.0.1:{args.port}")
    print("  Set the env var so your tests target this Chrome:")
    print(f'    $env:PCE_CHROME_DEBUG_ADDRESS = "127.0.0.1:{args.port}"')
    if jiggler is not None:
        print("  MouseJiggler is running in the background -- ambient mouse")
        print("  telemetry will keep firing while you log in / test.")
    print("=" * 74)
    print()
    print("Keep this script running (it just babysits). Press Ctrl+C to detach.")
    print("Detaching does NOT close Chrome -- it stays open with your tabs.")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        # Cleanly stop the jiggler so its daemon thread doesn't try to
        # use a closed CDP socket on the way out. The browser itself
        # is owned by the user and stays untouched.
        if jiggler is not None:
            jiggler.stop()
        print("\nDetached. Daily Chrome stays open.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
