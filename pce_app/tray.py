"""PCE System Tray Application.

Provides a system tray icon with:
- Service status indicators
- Start/stop controls for each service
- Quick access to Dashboard
- Quit option
"""

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

from .service_manager import ServiceManager, ServiceStatus

logger = logging.getLogger("pce.tray")

DASHBOARD_URL = "http://127.0.0.1:9800"


def create_icon_image(color: str = "#7c83ff", size: int = 64) -> Image.Image:
    """Generate a simple 'P' icon for the system tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background rounded rectangle
    r = size // 8
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=color)

    # Letter 'P'
    try:
        font = ImageFont.truetype("arial.ttf", int(size * 0.6))
    except (OSError, IOError):
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "P", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), "P", fill="white", font=font)

    return img


def _status_symbol(status: ServiceStatus) -> str:
    if status == ServiceStatus.RUNNING:
        return "●"
    elif status == ServiceStatus.ERROR:
        return "✖"
    elif status == ServiceStatus.STARTING:
        return "◌"
    return "○"


class PCETray:
    """System tray application for PCE."""

    def __init__(self, manager: ServiceManager):
        self.manager = manager
        self.icon: pystray.Icon | None = None
        self._build_icon()

    def _build_icon(self):
        self.icon = pystray.Icon(
            name="PCE",
            icon=create_icon_image(),
            title="PCE - Personal Cognitive Engine",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "Open Dashboard",
                    self._open_dashboard,
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda item: self._service_label("core"),
                    None,
                    enabled=False,
                ),
                pystray.MenuItem(
                    lambda item: "  Start Core Server" if not self.manager.is_running("core") else "  Stop Core Server",
                    self._toggle_core,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda item: self._service_label("proxy"),
                    None,
                    enabled=False,
                ),
                pystray.MenuItem(
                    lambda item: "  Start Proxy" if not self.manager.is_running("proxy") else "  Stop Proxy",
                    self._toggle_proxy,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda item: self._service_label("local_hook"),
                    None,
                    enabled=False,
                ),
                pystray.MenuItem(
                    lambda item: "  Start Local Hook" if not self.manager.is_running("local_hook") else "  Stop Local Hook",
                    self._toggle_local_hook,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit PCE", self._quit),
            ),
        )

    def _service_label(self, key: str) -> str:
        status = self.manager.get_status().get(key, {})
        name = status.get("name", key)
        st = ServiceStatus(status.get("status", "stopped"))
        port = status.get("port", "")
        sym = _status_symbol(st)
        return f"{sym} {name} (:{port})"

    def _open_dashboard(self, icon=None, item=None):
        webbrowser.open(DASHBOARD_URL)

    def _toggle_core(self, icon=None, item=None):
        if self.manager.is_running("core"):
            self.manager.stop_service("core")
        else:
            self.manager.start_core()

    def _toggle_proxy(self, icon=None, item=None):
        if self.manager.is_running("proxy"):
            self.manager.stop_service("proxy")
        else:
            self.manager.start_proxy()

    def _toggle_local_hook(self, icon=None, item=None):
        if self.manager.is_running("local_hook"):
            self.manager.stop_service("local_hook")
        else:
            self.manager.start_local_hook()

    def _quit(self, icon=None, item=None):
        logger.info("Shutting down PCE...")
        self.manager.stop_all()
        if self.icon:
            self.icon.stop()

    def run(self, auto_start_core: bool = True, open_dashboard: bool = True):
        """Start the tray icon and optionally auto-start the core server."""
        if auto_start_core:
            self.manager.start_core()

        if open_dashboard:
            # Give the server a moment to start, then open browser
            def _delayed_open():
                import time
                time.sleep(2)
                webbrowser.open(DASHBOARD_URL)

            threading.Thread(target=_delayed_open, daemon=True).start()

        logger.info("PCE tray started")
        self.icon.run()
