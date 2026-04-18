# SPDX-License-Identifier: Apache-2.0
"""CLI wizard for the CDP embedded-browser capture source (P4.4).

Invoke with ``python -m pce_core.cdp`` to launch a managed Chromium
window that ships every matching response straight into the PCE SQLite
database. Useful for:

- Demos where PCE has to work out-of-the-box without installing the
  browser extension.
- Headless CI runs that programmatically exercise an LLM UI.
- Kiosk deployments (single-purpose workstations) that shouldn't let
  the user disable / uninstall the capture layer.

The wizard deliberately keeps the UX minimal — the CDP driver itself
does the heavy lifting. This is just a friendly argparse front-end
with a ``Ctrl-C``-to-stop loop.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from . import PLAYWRIGHT_AVAILABLE, PLAYWRIGHT_VERSION


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pce_core.cdp",
        description=(
            "Launch a managed Chromium session and capture matching "
            "LLM traffic into PCE's SQLite database."
        ),
    )
    p.add_argument(
        "--start-url", default="https://chat.openai.com",
        help="Page the browser opens first. Default: chat.openai.com.",
    )
    p.add_argument(
        "--headless", action="store_true",
        help="Run Chromium headless. Useful for CI.",
    )
    p.add_argument(
        "--pattern", action="append", default=None, metavar="REGEX",
        help=(
            "Extra URL regex to capture. Repeat to add multiple. If "
            "omitted the driver's default patterns (OpenAI/Anthropic/"
            "Google/Mistral/Groq/Perplexity) apply."
        ),
    )
    p.add_argument(
        "--duration", type=int, default=0, metavar="SECONDS",
        help=(
            "Stop after this many seconds. 0 (default) runs until "
            "Ctrl-C or the browser is closed."
        ),
    )
    p.add_argument(
        "--probe-only", action="store_true",
        help=(
            "Report backend availability and exit — handy for smoke-"
            "testing environments without actually launching a browser."
        ),
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.probe_only or not PLAYWRIGHT_AVAILABLE:
        print(f"playwright_available={PLAYWRIGHT_AVAILABLE}")
        print(f"playwright_version={PLAYWRIGHT_VERSION}")
        if not PLAYWRIGHT_AVAILABLE:
            print(
                "[!] playwright is not installed. To enable the CDP "
                "capture source run:\n"
                "    pip install playwright\n"
                "    python -m playwright install chromium"
            )
            return 1 if args.probe_only else 2
        return 0

    from .driver import CDPDriver

    driver = CDPDriver(
        start_url=args.start_url,
        url_patterns=args.pattern,
        headless=args.headless,
    )

    stop_requested = False

    def _signal_handler(signum, frame):  # noqa: ARG001
        nonlocal stop_requested
        stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            # Windows doesn't always accept SIGTERM handlers.
            pass

    print(f"[PCE CDP] launching Chromium → {args.start_url}")
    try:
        driver.start(timeout_s=45.0)
    except Exception as exc:
        print(f"[PCE CDP] failed to start: {exc}", file=sys.stderr)
        return 2

    print("[PCE CDP] running. Press Ctrl-C to stop.")
    started_at = time.time()
    try:
        while not stop_requested:
            snap = driver.snapshot()
            if not snap["running"]:
                print(f"[PCE CDP] driver exited: {snap['last_error']}")
                break
            if args.duration and (time.time() - started_at) >= args.duration:
                print(f"[PCE CDP] --duration={args.duration}s elapsed; stopping")
                break
            time.sleep(0.5)
    finally:
        driver.stop()
        snap = driver.snapshot()
        print(
            "[PCE CDP] stopped. "
            f"captures_written={snap['captures_written']} "
            f"last_error={snap['last_error']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
