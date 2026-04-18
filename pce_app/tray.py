# SPDX-License-Identifier: Apache-2.0
"""PCE System Tray Application.

Provides a system tray icon with:
- Service status indicators
- Start/stop controls for each service
- Quick access to Dashboard
- Setup Wizard / Collect Diagnostics / Phoenix toggle / Check for updates (P3)
- Quit option

All user-facing actions are delegated to :mod:`pce_app.tray_actions` so the
same logic can back a future Tauri tray without duplication.
"""

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont

from . import tray_actions
from .service_manager import ServiceManager, ServiceStatus

logger = logging.getLogger("pce.tray")

DASHBOARD_URL = "http://127.0.0.1:9800"
ONBOARDING_URL = "http://127.0.0.1:9800/onboarding"


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
                pystray.MenuItem("Run Setup Wizard", self._open_onboarding),
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
                pystray.MenuItem("Phoenix", pystray.Menu(
                    pystray.MenuItem(
                        lambda item: self._phoenix_label(),
                        None,
                        enabled=False,
                    ),
                    pystray.MenuItem(
                        "  Start / Stop Phoenix",
                        self._toggle_phoenix,
                    ),
                    pystray.MenuItem(
                        "  Open Phoenix UI",
                        self._open_phoenix_ui,
                    ),
                )),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Collect Diagnostics…", self._collect_diagnostics),
                pystray.MenuItem("Check for Updates…", self._check_for_updates),
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

    # ── P3 additions ────────────────────────────────────────────────

    def _open_onboarding(self, icon=None, item=None):
        """Open the first-run wizard in the user's browser."""
        threading.Thread(
            target=self._run_action,
            args=(tray_actions.open_onboarding,),
            daemon=True,
        ).start()

    def _open_phoenix_ui(self, icon=None, item=None):
        """Open the embedded Phoenix UI (assumes default port)."""
        webbrowser.open("http://127.0.0.1:6006/")

    def _collect_diagnostics(self, icon=None, item=None):
        threading.Thread(
            target=self._run_action,
            args=(tray_actions.collect_diagnostics,),
            daemon=True,
        ).start()

    def _toggle_phoenix(self, icon=None, item=None):
        threading.Thread(
            target=self._run_action,
            args=(tray_actions.phoenix_toggle,),
            daemon=True,
        ).start()

    def _check_for_updates(self, icon=None, item=None):
        threading.Thread(
            target=self._run_action,
            args=(tray_actions.check_for_updates,),
            daemon=True,
        ).start()

    def _phoenix_label(self) -> str:
        """Best-effort status label shown in the Phoenix submenu."""
        try:
            status, body = tray_actions._api_call(
                "GET", "/api/v1/phoenix", timeout=0.5,
            )
        except Exception:
            return "○ Phoenix (unknown)"
        if status != 200 or not isinstance(body, dict):
            return "○ Phoenix (unavailable)"
        if body.get("running"):
            return f"● Phoenix on :{body.get('port')}"
        if not body.get("phoenix_available"):
            return "○ Phoenix (not installed)"
        return "○ Phoenix (stopped)"

    def _run_action(self, fn, *args, **kwargs):
        """Run a tray action and surface its ``ActionResult`` as a tray notification."""
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("tray action %s failed", getattr(fn, "__name__", fn))
            self._notify("PCE", f"Action failed: {exc}")
            return
        if result is None:
            return
        title = getattr(result, "title", "PCE") or "PCE"
        message = getattr(result, "message", "") or ""
        self._notify(title, message)

    def _notify(self, title: str, message: str) -> None:
        """Best-effort desktop notification — logs on platforms without support."""
        try:
            if self.icon is not None and hasattr(self.icon, "notify"):
                self.icon.notify(message or " ", title=title)
                return
        except Exception:
            logger.debug("tray notify failed", exc_info=True)
        logger.info("tray: %s — %s", title, message)

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
