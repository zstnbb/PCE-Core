"""PCE Desktop Application entry point.

Usage:
    python -m pce_app                  # Start with system tray + auto-start core
    python -m pce_app --no-tray        # Start core server only (headless)
    python -m pce_app --all            # Start all services (core + proxy + local hook)
"""

import argparse
import logging
import multiprocessing
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.app")


def main():
    parser = argparse.ArgumentParser(description="PCE Desktop Application")
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray (headless)")
    parser.add_argument("--all", action="store_true", help="Start all services")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open dashboard")
    parser.add_argument("--proxy", action="store_true", help="Also start the network proxy")
    parser.add_argument("--local-hook", action="store_true", help="Also start the local model hook")
    args = parser.parse_args()

    from .service_manager import ServiceManager

    manager = ServiceManager()

    if args.no_tray:
        # Headless mode – just start services and block
        logger.info("PCE starting in headless mode...")
        manager.start_core()

        if args.all or args.proxy:
            manager.start_proxy()
        if args.all or args.local_hook:
            manager.start_local_hook()

        if not args.no_browser:
            import time
            import webbrowser
            time.sleep(2)
            webbrowser.open("http://127.0.0.1:9800")

        logger.info("PCE running. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            manager.stop_all()
    else:
        # System tray mode
        from .tray import PCETray

        tray = PCETray(manager)

        # Auto-start additional services if requested
        if args.all or args.proxy:
            manager.start_proxy()
        if args.all or args.local_hook:
            manager.start_local_hook()

        tray.run(
            auto_start_core=True,
            open_dashboard=not args.no_browser,
        )


if __name__ == "__main__":
    multiprocessing.freeze_support()  # Required for PyInstaller
    main()
