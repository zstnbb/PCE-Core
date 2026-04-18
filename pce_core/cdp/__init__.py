# SPDX-License-Identifier: Apache-2.0
"""Embedded-browser capture via the Chrome DevTools Protocol (P4.4).

PCE already supports three capture sources — the browser extension
(front-of-the-DOM), the mitmproxy-based proxy (on-the-wire), and the
local hook (single-endpoint client). This module adds a fourth: a
Playwright-driven embedded Chrome that lets PCE itself own the browser
session and capture traffic without any user-visible extension or
system-level proxy.

When to use which source
------------------------

- **Extension**      : user runs their normal Chrome; best UX.
- **Proxy**          : power users who already have mitmproxy set up.
- **Local hook**     : developers pointing their own LLM app at PCE.
- **CDP (this one)** : kiosk / headless / CI / demo modes where PCE
                       launches the browser itself and doesn't need
                       persistent user profile changes.

Dependency posture
==================

Playwright is an **optional** dep (``pip install playwright`` + one-
time ``playwright install chromium``). When it isn't importable every
entry point in this module degrades to
``{"ok": False, "error": "playwright_not_installed", ...}`` — same
graceful pattern as the DuckDB analytics layer.

Public surface
--------------

- :func:`status`           – is Playwright importable?
- :class:`CDPDriver`       – lifecycle wrapper around a Playwright browser
- :func:`start_default`    – module-level singleton + sane defaults, used
                             by the FastAPI ``/api/v1/cdp/start`` endpoint
- :func:`stop_default`     – symmetric stop
- :func:`current_status`   – snapshot of the singleton for the dashboard

See :mod:`pce_core.cdp.driver` for the captured-response handling logic
and :mod:`pce_core.cdp.__main__` for the standalone CLI wizard.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger("pce.cdp")


# ---------------------------------------------------------------------------
# Feature probe — never raises on import.
# ---------------------------------------------------------------------------

try:
    import playwright  # noqa: F401
    PLAYWRIGHT_AVAILABLE = True
    try:
        import importlib.metadata as _metadata
        PLAYWRIGHT_VERSION: Optional[str] = _metadata.version("playwright")
    except Exception:
        PLAYWRIGHT_VERSION = "unknown"
except Exception:
    PLAYWRIGHT_AVAILABLE = False
    PLAYWRIGHT_VERSION = None


# ---------------------------------------------------------------------------
# Module-level singleton used by HTTP endpoints.
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_singleton: "Optional[object]" = None   # CDPDriver; import lazily to avoid
                                        # pulling Playwright at module load
                                        # when it isn't available.


def status() -> dict:
    """Is Playwright installed? What's the current driver doing?"""
    snap = current_status()
    return {
        "available": PLAYWRIGHT_AVAILABLE,
        "playwright_version": PLAYWRIGHT_VERSION,
        "hint": (
            None if PLAYWRIGHT_AVAILABLE
            else "pip install playwright && python -m playwright install chromium"
        ),
        "running": snap["running"],
        "captures_written": snap["captures_written"],
        "last_error": snap["last_error"],
        "start_url": snap["start_url"],
    }


def current_status() -> dict:
    """Snapshot of the singleton driver state — always returns a dict."""
    with _lock:
        drv = _singleton
    if drv is None:
        return {
            "running": False,
            "captures_written": 0,
            "last_error": None,
            "start_url": None,
        }
    # The driver exposes the same keys.
    return drv.snapshot()  # type: ignore[attr-defined]


def start_default(
    *,
    start_url: str = "https://chat.openai.com",
    url_patterns: Optional[list[str]] = None,
    headless: bool = False,
) -> dict:
    """Spin up the singleton :class:`CDPDriver` and return its status.

    Idempotent: re-calling with a running driver returns the existing
    status dict without launching a second browser.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {"ok": False, "error": "playwright_not_installed"}

    global _singleton
    with _lock:
        if _singleton is not None and _singleton.is_running():  # type: ignore[attr-defined]
            return {
                "ok": True,
                "already_running": True,
                **_singleton.snapshot(),       # type: ignore[attr-defined]
            }
        from .driver import CDPDriver
        drv = CDPDriver(
            start_url=start_url,
            url_patterns=url_patterns,
            headless=headless,
        )
        _singleton = drv
    try:
        drv.start()
    except Exception as exc:
        logger.exception("cdp driver failed to start")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"ok": True, **drv.snapshot()}


def stop_default() -> dict:
    """Stop and unregister the singleton driver."""
    global _singleton
    with _lock:
        drv = _singleton
        _singleton = None
    if drv is None:
        return {"ok": True, "running": False, "already_stopped": True}
    try:
        drv.stop()   # type: ignore[attr-defined]
    except Exception as exc:
        logger.exception("cdp driver failed to stop cleanly")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "running": False, **drv.snapshot()}  # type: ignore[attr-defined]


def _reset_singleton_for_tests() -> None:
    """Purge the module singleton. Only for unit tests."""
    global _singleton
    with _lock:
        _singleton = None


__all__ = [
    "PLAYWRIGHT_AVAILABLE",
    "PLAYWRIGHT_VERSION",
    "current_status",
    "start_default",
    "status",
    "stop_default",
]
