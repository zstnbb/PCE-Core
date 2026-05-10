# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop (Windows MSIX) UIA + SendInput driver.

Empirically validated 2026-05-10 against Claude Desktop v1.6608.2.0
MSIX. Strategy:

- Connect to the top-level window with title="Claude" via pywinauto
  UIA backend. (Window class is ``Chrome_WidgetWin_1`` — Chromium's
  standard Win32 widget class.)
- ``focus()`` uses Win32 ``AttachThreadInput`` trick to bypass
  SetForegroundWindow's anti-stealing restriction.
- ``click_composer()`` clicks at ``(window.center_x, window.bottom -
  120)``. Empirical: this hits the prompt input area on the typical
  desktop layout (window heights 900–1700px). The click is necessary
  because Chromium's renderer process holds focus on its inner child
  window — outer-window foregrounding alone routes keys to nowhere.
- ``send_message()`` uses ``send_keys(..., vk_packet=True)`` to bypass
  IME (the user's default IME may be Chinese on this locale, which
  intercepts ASCII letters). Then ``{ENTER}`` to submit.
- ``wait_done()`` polls ``raw_captures`` for the response row matching
  the request pair_id — mitmproxy persists the body when the SSE
  stream terminates (or hits ``stream_large_bodies=1m`` threshold).
- ``cancel_current()`` sends Esc; Claude Desktop honours Esc as the
  "stop generating" shortcut on this build.
- ``new_chat()`` sends Ctrl+N **after** a ``click_composer()`` to make
  sure the renderer has Win32 focus (otherwise Ctrl+N is consumed by
  the outer Win32 caption frame).

Known caveats:

- If the user has manually navigated to a non-chat view (Settings,
  Projects sidebar) the driver doesn't detect it — caller responsible
  for putting Claude Desktop into a chat-like view first time.
- Window rect-based click coords assume default UI layout; if Claude
  Desktop is resized to <500px tall, the input area may not be at
  bottom-120.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from pywinauto import Desktop
from pywinauto.keyboard import send_keys

from ..utils import (
    click_at,
    force_foreground,
    latest_completion_pair_id,
    wait_completion_response,
    wait_for_new_completion,
)
from .base import DesktopDriver

logger = logging.getLogger("pce.e2e_desktop_ui.claude")


class ClaudeDesktopDriver(DesktopDriver):
    """Drives an installed Claude Desktop (Win MSIX) app via UIA + SendInput."""

    PRODUCT_NAME = "Claude Desktop"
    PRODUCT_HOST = "claude.ai"

    def __init__(self) -> None:
        self._desktop = Desktop(backend="uia")
        self._window = None  # lazily resolved
        self._hwnd: Optional[int] = None

    # ---------- internal helpers ----------

    def _ensure_window(self):
        if self._window is None:
            w = self._desktop.window(title="Claude")
            w.wait("exists", timeout=5)
            self._window = w
            self._hwnd = w.handle
        return self._window

    def _composer_click_point(self) -> tuple[int, int]:
        w = self._ensure_window()
        rect = w.rectangle()
        cx = (rect.left + rect.right) // 2
        cy = rect.bottom - 120
        return cx, cy

    # ---------- DesktopDriver impl ----------

    def focus(self) -> None:
        w = self._ensure_window()
        force_foreground(self._hwnd)
        time.sleep(0.6)

    def click_composer(self) -> None:
        cx, cy = self._composer_click_point()
        click_at(cx, cy)
        time.sleep(0.4)

    def send_message(
        self,
        text: str,
        *,
        wait_done: bool = True,
        wait_timeout: float = 60.0,
    ) -> Optional[str]:
        # Use a since_ts that's guaranteed to be earlier than any new
        # request triggered by this call (subtract 2s for clock skew
        # between Python's time.time() and mitm's internal clock).
        since_ts = time.time() - 2.0

        self.focus()
        self.click_composer()

        logger.info("send_message: typing %r", text)
        send_keys(text, with_spaces=True, vk_packet=True, pause=0.02)
        time.sleep(0.3)
        send_keys("{ENTER}", pause=0.05)

        # Wait for the /completion request to materialize in raw_captures
        pair_id = wait_for_new_completion(
            since_ts, timeout=15.0, host=self.PRODUCT_HOST,
        )
        if not pair_id:
            logger.error("send_message: no /completion request observed within 15s")
            return None
        logger.info("send_message: request pair_id=%s", pair_id[:10])

        if wait_done:
            done = self.wait_done(pair_id, timeout=wait_timeout)
            if done is None:
                logger.warning(
                    "send_message: response did not arrive within %.1fs "
                    "(request was sent though, pair_id=%s)",
                    wait_timeout, pair_id[:10],
                )
            else:
                logger.info(
                    "send_message: response status=%s body=%dB",
                    done["status_code"], done["body_len"],
                )

        return pair_id

    def wait_done(
        self,
        request_pair_id: str,
        *,
        timeout: float = 60.0,
    ) -> Optional[dict]:
        return wait_completion_response(request_pair_id, timeout=timeout)

    def cancel_current(self) -> bool:
        # Esc key — Claude Desktop's "stop generating" shortcut on the
        # current build. Caller is responsible for calling this while
        # the assistant is actually generating; otherwise it's a no-op.
        self.focus()
        send_keys("{ESC}", pause=0.05)
        time.sleep(0.3)
        return True

    def new_chat(self) -> bool:
        # Ctrl+N must follow a click that gives the renderer focus.
        self.focus()
        self.click_composer()
        send_keys("^n", pause=0.1)
        time.sleep(1.0)
        return True
