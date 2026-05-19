# SPDX-License-Identifier: Apache-2.0
"""PCE Desktop Application entry point.

Default mode is the **PySide6 Control Panel** (`pce_app.control_panel`),
an independent native window + system tray. Use ``--legacy-tray`` to
fall back to the pystray menu, or ``--no-tray`` for a headless server.

Usage::

    python -m pce_app                 # Control Panel + tray (default)
    python -m pce_app --all           # + auto-start proxy & local hook
    python -m pce_app --no-tray       # Headless (core only, Ctrl+C to stop)
    python -m pce_app --legacy-tray   # Old pystray menu
    python -m pce_app --no-browser    # Don't auto-open the dashboard
"""

import argparse
import logging
import multiprocessing
import os
import sys


def _patch_stdio():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_patch_stdio()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.app")


def main():
    parser = argparse.ArgumentParser(description="PCE Desktop Application")
    parser.add_argument("--no-tray", action="store_true",
                        help="Headless mode (Ctrl+C to stop)")
    parser.add_argument("--legacy-tray", action="store_true",
                        help="Use the old pystray menu")
    parser.add_argument("--all", action="store_true",
                        help="Auto-start all services")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't auto-open the dashboard")
    parser.add_argument("--proxy", action="store_true",
                        help="Also start the network proxy")
    parser.add_argument("--local-hook", action="store_true",
                        help="Also start the local model hook")
    args = parser.parse_args()

    if args.no_tray:
        _run_headless(args)
        return
    if args.legacy_tray:
        _run_legacy_tray(args)
        return
    _run_control_panel(args)


def _run_control_panel(args) -> None:
    try:
        from .control_panel import run as run_panel
    except ImportError as exc:
        logger.error(
            "PySide6 is required for the Control Panel (%s). "
            "Either `pip install PySide6` or use --legacy-tray.", exc,
        )
        sys.exit(2)

    extras: list[str] = []
    if args.all or args.proxy:
        extras.append("proxy")
    if args.all or args.local_hook:
        extras.append("local_hook")

    sys.exit(run_panel(
        auto_start_core=True,
        open_dashboard=False,  # Panel IS the UI; users click "Open Dashboard"
        extra_services=tuple(extras),
    ))


def _run_legacy_tray(args) -> None:
    from .service_manager import ServiceManager
    from .tray import PCETray

    manager = ServiceManager()
    if args.all or args.proxy:
        manager.start_proxy()
    if args.all or args.local_hook:
        manager.start_local_hook()
    PCETray(manager).run(
        auto_start_core=True,
        open_dashboard=not args.no_browser,
    )


def _run_headless(args) -> None:
    from .service_manager import ServiceManager
    from .system_state_guard import get_guard

    guard = get_guard()
    guard.recover_from_crash()
    guard.install_signal_handlers()

    manager = ServiceManager()
    logger.info("PCE starting in headless mode...")
    manager.start_core()
    if args.all or args.proxy:
        manager.start_proxy()
    if args.all or args.local_hook:
        manager.start_local_hook()

    if not args.no_browser:
        import time, webbrowser
        time.sleep(2)
        webbrowser.open("http://127.0.0.1:9800")

    logger.info("PCE running. Press Ctrl+C to stop.")
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        try:
            manager.stop_all()
        finally:
            guard.restore(reason="headless-ctrl-c")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
