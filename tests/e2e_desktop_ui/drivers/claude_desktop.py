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
from typing import Iterable, Optional

from pywinauto import Desktop
from pywinauto.keyboard import send_keys
from pywinauto.mouse import move as _mouse_move

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

    # ---------- D13–D22 extensions ----------

    def paste_clipboard(self, *, settle: float = 0.6) -> None:
        """Press Ctrl+V to paste the current Windows clipboard contents
        into the focused composer. Caller is responsible for putting
        whatever (text, CF_HDROP file list, image bitmap) on the
        clipboard first."""
        self.focus()
        self.click_composer()
        send_keys("^v", pause=0.05)
        time.sleep(settle)

    def _find_uia_by_name_substr(
        self,
        substrings: Iterable[str],
        *,
        control_types: Iterable[str] = ("Button",),
        timeout: float = 4.0,
    ):
        """Walk the Claude window's UIA descendants and return the first
        element whose ``Name`` contains any of ``substrings`` and whose
        ``ControlType`` is in ``control_types``.

        Substring match is case-insensitive. Returns None on timeout.
        """
        w = self._ensure_window()
        deadline = time.time() + timeout
        wanted = tuple(s.lower() for s in substrings)
        while time.time() < deadline:
            try:
                for desc in w.descendants():
                    try:
                        info = desc.element_info
                        ct = (info.control_type or "")
                        if ct not in control_types:
                            continue
                        nm = (info.name or "").lower()
                        if not nm:
                            continue
                        if any(s in nm for s in wanted):
                            return desc
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.4)
        return None

    def _hover_message(self, message_index_from_end: int = 0) -> bool:
        """Move the mouse over the Nth-most-recent message bubble to
        trigger the hover-only action toolbar (edit / retry / branch
        arrows).

        ``message_index_from_end=0`` is the latest bubble, ``1`` is the
        previous, etc. Returns True on success.
        """
        w = self._ensure_window()
        # Strategy: walk descendants, collect anything that looks like a
        # message bubble (has a non-trivial text length and is positioned
        # in the middle of the window vertically). Sort by Y, pick from
        # the end.
        rect = w.rectangle()
        candidates = []
        try:
            for desc in w.descendants():
                try:
                    info = desc.element_info
                    rl = info.rectangle
                    if rl is None:
                        continue
                    h = rl.bottom - rl.top
                    if h < 30 or h > 800:
                        continue
                    if rl.left < rect.left or rl.right > rect.right:
                        continue
                    nm = (info.name or "")
                    if len(nm) < 8:
                        continue
                    candidates.append((rl.top, rl.left, rl.right, rl.bottom, desc))
                except Exception:
                    continue
        except Exception:
            return False
        candidates.sort(key=lambda t: t[0])
        if not candidates:
            return False
        target = candidates[max(0, len(candidates) - 1 - message_index_from_end)]
        cx = (target[1] + target[2]) // 2
        cy = (target[0] + target[3]) // 2
        _mouse_move(coords=(cx, cy))
        time.sleep(0.6)  # let the action toolbar fade in
        return True

    def regenerate_last(self) -> bool:
        """Find and click the retry / regenerate button on the last
        assistant message. Returns True if a click was issued.

        Strategy: hover over the last visible message, then UIA-search
        for a Button whose Name contains "retry" or "regenerate".
        """
        if not self._hover_message(message_index_from_end=0):
            logger.warning("regenerate_last: could not hover last message")
            return False
        btn = self._find_uia_by_name_substr(
            ("retry", "regenerate"), control_types=("Button",), timeout=3.0,
        )
        if btn is None:
            logger.warning("regenerate_last: no retry/regenerate button found in UIA tree")
            return False
        try:
            btn.click_input()
        except Exception as exc:
            logger.warning("regenerate_last: click failed: %s", exc)
            return False
        time.sleep(0.6)
        return True

    def edit_last_user(self, new_text: str) -> bool:
        """Find and click the edit (pencil) button on the most recent
        USER message, clear its current text, type ``new_text``, then
        submit. Returns True on apparent success.

        Note: the ``Name`` substring "edit" is generic; we narrow by
        also requiring the button to be near the LAST user-side bubble.
        On modern Claude Desktop the action toolbar shows underneath
        the user bubble after a 300ms hover.
        """
        # The user's last message is two bubbles up from the latest
        # (last bubble is assistant, before that is user) — but if the
        # assistant bubble wasn't created yet (interrupted), it's just
        # the last bubble. Try index_from_end=1 first, then 0.
        if not self._hover_message(message_index_from_end=1):
            self._hover_message(message_index_from_end=0)
        btn = self._find_uia_by_name_substr(
            ("edit",), control_types=("Button",), timeout=3.0,
        )
        if btn is None:
            logger.warning("edit_last_user: no edit button found")
            return False
        try:
            btn.click_input()
        except Exception as exc:
            logger.warning("edit_last_user: click failed: %s", exc)
            return False
        time.sleep(0.6)
        # Select all in the now-focused edit area, type replacement, send
        send_keys("^a", pause=0.05)
        time.sleep(0.2)
        send_keys(new_text, with_spaces=True, vk_packet=True, pause=0.02)
        time.sleep(0.3)
        # Submit via the "Save & submit" button if present, otherwise Enter
        save_btn = self._find_uia_by_name_substr(
            ("save", "submit"), control_types=("Button",), timeout=2.0,
        )
        if save_btn is not None:
            try:
                save_btn.click_input()
                time.sleep(0.4)
                return True
            except Exception:
                pass
        send_keys("{ENTER}", pause=0.05)
        time.sleep(0.4)
        return True

    def flip_branch(self, direction: str = "right") -> bool:
        """Click the left or right branch arrow on the most recent
        assistant message that has a branch indicator. Returns True if
        a click landed.

        ``direction`` is ``"right"`` (next branch / >) or ``"left"`` (prev
        / <).
        """
        if not self._hover_message(message_index_from_end=0):
            return False
        # Branch arrows are usually labelled "Previous response" /
        # "Next response" or similar; some builds use just "<" / ">"
        if direction == "right":
            patterns = ("next", "next response", "next branch", "next variant")
        else:
            patterns = ("previous", "previous response", "previous branch", "prev variant")
        btn = self._find_uia_by_name_substr(
            patterns, control_types=("Button",), timeout=3.0,
        )
        if btn is None:
            logger.warning("flip_branch(%s): no branch arrow found", direction)
            return False
        try:
            btn.click_input()
        except Exception as exc:
            logger.warning("flip_branch: click failed: %s", exc)
            return False
        time.sleep(0.4)
        return True

    def select_model(self, name_substring: str) -> bool:
        """Open the model picker and click the entry whose label
        contains ``name_substring`` (case-insensitive).

        Strategy: find the model-picker trigger (a button near the top
        of the chat composer area whose name typically contains the
        currently-selected model family — "Sonnet" / "Haiku" / "Opus"),
        click to open the picker, then click the target.
        """
        self.focus()
        self.click_composer()
        # The composer usually exposes a model-picker button by name
        # containing the current model family. Try a broad set.
        trigger = self._find_uia_by_name_substr(
            ("sonnet", "haiku", "opus", "claude ", "model"),
            control_types=("Button",),
            timeout=3.0,
        )
        if trigger is None:
            logger.warning("select_model: model picker trigger not found")
            return False
        try:
            trigger.click_input()
        except Exception as exc:
            logger.warning("select_model: trigger click failed: %s", exc)
            return False
        time.sleep(0.5)
        # Now find the menu item matching name_substring
        target = self._find_uia_by_name_substr(
            (name_substring,),
            control_types=("MenuItem", "Button", "ListItem"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("select_model: no model item matching %r", name_substring)
            # Try to dismiss the open menu by pressing Esc
            send_keys("{ESC}", pause=0.05)
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("select_model: target click failed: %s", exc)
            return False
        time.sleep(0.5)
        return True

    def select_style(self, name_substring: str) -> bool:
        """Open the Writing Style picker and select the entry whose
        label contains ``name_substring`` (case-insensitive).

        Like ``select_model``: find the trigger, open menu, click item.
        Trigger labels observed: "Default style", "Concise", "Explanatory",
        "Formal", or just "Style".
        """
        self.focus()
        self.click_composer()
        trigger = self._find_uia_by_name_substr(
            ("style", "concise", "explanatory", "formal", "default style"),
            control_types=("Button",),
            timeout=3.0,
        )
        if trigger is None:
            logger.warning("select_style: style picker trigger not found")
            return False
        try:
            trigger.click_input()
        except Exception as exc:
            logger.warning("select_style: trigger click failed: %s", exc)
            return False
        time.sleep(0.5)
        target = self._find_uia_by_name_substr(
            (name_substring,),
            control_types=("MenuItem", "Button", "ListItem"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("select_style: no style item matching %r", name_substring)
            send_keys("{ESC}", pause=0.05)
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("select_style: target click failed: %s", exc)
            return False
        time.sleep(0.5)
        return True

    def open_project(self, name_substring: str) -> bool:
        """Locate a project in the sidebar by name and click into it.

        Strategy: ensure sidebar is visible (Ctrl+\\ on Claude Desktop
        toggles), then UIA-find a list item whose name contains
        ``name_substring`` and click it.
        """
        self.focus()
        # Some Claude Desktop builds have the sidebar collapsed by
        # default. Toggle with Ctrl+\\ to be safe (idempotent if already
        # open — Claude treats double-toggle as no-op within 200ms).
        send_keys("^{VK_OEM_5}", pause=0.05)  # Ctrl+\
        time.sleep(0.4)
        target = self._find_uia_by_name_substr(
            (name_substring,),
            control_types=("ListItem", "Button", "Hyperlink", "TreeItem"),
            timeout=4.0,
        )
        if target is None:
            logger.warning("open_project: no sidebar item matching %r", name_substring)
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("open_project: click failed: %s", exc)
            return False
        time.sleep(1.0)
        return True
