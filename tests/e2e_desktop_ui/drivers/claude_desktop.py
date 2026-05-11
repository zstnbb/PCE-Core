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

import json
import logging
import os
import time
from pathlib import Path
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

    # Composer (text input) discovery hints. The composer in Claude
    # Desktop is a Chromium-rendered Edit/Document UIA element whose
    # name varies by build / locale ("Reply to Claude", "How can I
    # help you today?", "Type your message...", etc.). After Ctrl+N
    # new chat the composer is centered on screen, NOT pinned to the
    # bottom — so the previous fixed (cx, bottom-120) heuristic
    # missed it and clicked into blank space, dropping focus and
    # silently breaking subsequent Ctrl+V paste.
    _COMPOSER_CONTROL_TYPES = ("Edit", "Document", "Custom")
    _COMPOSER_NAME_HINTS = (
        "reply to claude", "reply to anthropic", "how can i help",
        "what can i help", "how are you", "start a new chat",
        "message claude", "send a message", "type a message",
        "type your message", "write a message", "start typing",
        "发送消息", "输入消息", "输入你的问题", "给 claude 发消息",
    )

    def _composer_click_point(self) -> tuple[int, int]:
        """Return ``(cx, cy)`` that should land inside the composer.

        Strategy (in order):
        1. UIA: find the largest Edit/Document/Custom element whose
           name matches a composer hint. Click its centre.
        2. UIA: find the bottom-most reasonably wide Edit/Document
           that is inside the Claude window's chat area.
        3. Fallback: the legacy ``bottom-120`` heuristic.
        """
        el = self._find_composer_uia()
        if el is not None:
            try:
                r = el.element_info.rectangle
                if r and r.right > r.left and r.bottom > r.top:
                    cx = (r.left + r.right) // 2
                    cy = (r.top + r.bottom) // 2
                    logger.debug("composer (UIA): rect=(%d,%d)-(%d,%d) -> click (%d,%d)",
                                 r.left, r.top, r.right, r.bottom, cx, cy)
                    return cx, cy
            except Exception:
                pass
        w = self._ensure_window()
        rect = w.rectangle()
        cx = (rect.left + rect.right) // 2
        cy = rect.bottom - 120
        logger.debug("composer (fallback bottom-120): click (%d,%d)", cx, cy)
        return cx, cy

    def _find_composer_uia(self):
        """Walk the Claude window UIA tree and return the wrapper that
        looks most like the composer text input. None if nothing
        plausible is found.
        """
        w = self._ensure_window()
        try:
            win_rect = w.rectangle()
            descendants = list(w.descendants())
        except Exception as exc:
            logger.debug("_find_composer_uia: descendants() failed: %s", exc)
            return None
        win_w = max(1, win_rect.right - win_rect.left)
        win_h = max(1, win_rect.bottom - win_rect.top)
        candidates: list[tuple[int, object]] = []
        for d in descendants:
            try:
                info = d.element_info
                ct = info.control_type or ""
                if ct not in self._COMPOSER_CONTROL_TYPES:
                    continue
                r = info.rectangle
                if r is None:
                    continue
                el_w = r.right - r.left
                el_h = r.bottom - r.top
                if el_w < 200 or el_h < 24:
                    continue
                # Must be inside the window
                if (r.left < win_rect.left - 50 or r.right > win_rect.right + 50
                        or r.top < win_rect.top - 50 or r.bottom > win_rect.bottom + 50):
                    continue
                # Reject things bigger than 90% of the window (likely the
                # whole document area)
                if el_w > 0.95 * win_w and el_h > 0.85 * win_h:
                    continue
                name = (info.name or "").lower()
                score = 0
                if any(h in name for h in self._COMPOSER_NAME_HINTS):
                    score += 100_000
                # Prefer Edit > Document > Custom
                if ct == "Edit":
                    score += 5_000
                elif ct == "Document":
                    score += 2_500
                # Prefer wider elements (composer is wide, not a tiny search box)
                score += el_w
                # Prefer elements in the bottom 70% of the window (avoid header search)
                if r.top > win_rect.top + 0.30 * win_h:
                    score += 3_000
                # Slight preference for ones that aren't at the very top
                candidates.append((score, d))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda t: t[0], reverse=True)
        return candidates[0][1]

    def _is_composer_focused(self) -> bool:
        """Check whether the system-wide UIA focused element looks like
        the composer (Edit / Document, reasonably wide). Used to verify
        a click_at actually landed in the right place before pasting.
        """
        try:
            from pywinauto.uia_defines import IUIA  # type: ignore
            iuia = IUIA().iuia
            focused = iuia.GetFocusedElement()
            if focused is None:
                return False
            # 50004 = UIA_EditControlTypeId
            # 50030 = UIA_DocumentControlTypeId
            # 50025 = UIA_CustomControlTypeId
            ct_id = int(focused.CurrentControlType)
            if ct_id not in (50004, 50030, 50025):
                return False
            br = focused.CurrentBoundingRectangle
            width = br.right - br.left
            height = br.bottom - br.top
            if width < 200 or height < 24:
                return False
            return True
        except Exception as exc:
            logger.debug("_is_composer_focused: failed: %s", exc)
            return False

    # ---------- DesktopDriver impl ----------

    def focus(self) -> None:
        w = self._ensure_window()
        force_foreground(self._hwnd)
        time.sleep(0.6)

    def click_composer(self) -> None:
        """Click into the composer. Verifies focus afterwards and
        retries up to 3 times if the click missed (e.g. the composer
        moved due to layout reflow between dump and click).
        """
        for attempt in range(3):
            cx, cy = self._composer_click_point()
            click_at(cx, cy)
            time.sleep(0.4)
            if self._is_composer_focused():
                if attempt > 0:
                    logger.info("click_composer: focused on retry attempt %d", attempt + 1)
                return
            logger.warning(
                "click_composer: focus check FAILED after click_at(%d,%d) "
                "on attempt %d/3 \u2014 composer may have moved; re-discovering",
                cx, cy, attempt + 1,
            )
            # Re-foreground in case another window stole focus
            self.focus()
        logger.warning(
            "click_composer: focus verification failed after 3 attempts \u2014 "
            "proceeding anyway (downstream paste may silently no-op)"
        )

    def ensure_composer_focus(self, *, max_attempts: int = 4) -> bool:
        """Public helper: bring Claude to foreground and click into the
        composer, verifying focus actually landed. Returns True on
        success. Use this before any Ctrl+V paste or send_keys-typed
        prompt to guarantee the keys hit the right input element.
        """
        for attempt in range(max_attempts):
            self.focus()
            cx, cy = self._composer_click_point()
            click_at(cx, cy)
            time.sleep(0.4)
            if self._is_composer_focused():
                if attempt > 0:
                    logger.info(
                        "ensure_composer_focus: succeeded on attempt %d/%d",
                        attempt + 1, max_attempts,
                    )
                return True
            logger.warning(
                "ensure_composer_focus: focus NOT on composer after click_at(%d,%d) "
                "on attempt %d/%d",
                cx, cy, attempt + 1, max_attempts,
            )
            time.sleep(0.6)
        return False

    def send_message(
        self,
        text: str,
        *,
        wait_done: bool = True,
        wait_timeout: float = 60.0,
        wait_request: bool = True,
    ) -> Optional[str]:
        """Type ``text`` into the composer and press Enter.

        For chat-region cases the default behaviour is to wait for the
        ``/completion`` POST to materialise in ``raw_captures`` and
        return that ``pair_id``. For cowork-region cases the underlying
        traffic is WebSocket-over-HTTP/2 (Q2 architectural finding) and
        ``/completion`` is never observed by L1; callers there should
        pass ``wait_request=False`` to skip the 15 s HTTP probe and
        verify the turn via UI cues + L3g axis instead.
        """
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

        if not wait_request:
            # Cowork-region path: caller will verify the turn via UI
            # cues + L3g. Return a synthetic placeholder so the caller
            # can still tell "send completed" from "send raised".
            logger.info("send_message: wait_request=False — returning without probing /completion")
            return "cowork-no-probe"

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
        # After Ctrl+N the composer reflows to a centered position on a
        # blank chat. Wait until the composer Edit element is
        # discoverable AND focusable again \u2014 otherwise the next
        # click_composer() will race with the layout transition and
        # land in blank space.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._find_composer_uia() is not None:
                break
            time.sleep(0.2)
        # Force-focus the new composer (the post-reflow rect)
        self.ensure_composer_focus()
        time.sleep(0.4)
        return True

    # ---------- D13–D22 extensions ----------

    def paste_clipboard(self, *, settle: float = 0.6) -> None:
        """Press Ctrl+V to paste the current Windows clipboard contents
        into the focused composer. Caller is responsible for putting
        whatever (text, CF_HDROP file list, image bitmap) on the
        clipboard first.

        This calls ``ensure_composer_focus()`` before pressing Ctrl+V
        and logs a warning (but still presses Ctrl+V) if focus could
        not be verified \u2014 historically the silent-paste-no-op was
        what made D17/D18 SKIP on this build.
        """
        ok = self.ensure_composer_focus()
        if not ok:
            logger.warning(
                "paste_clipboard: composer focus could not be verified; "
                "pressing Ctrl+V anyway but the paste may silently no-op"
            )
        send_keys("^v", pause=0.05)
        time.sleep(settle)

    def dump_tree(
        self,
        *,
        keywords: Optional[Iterable[str]] = None,
        control_types: Optional[Iterable[str]] = None,
        max_name_len: int = 120,
    ) -> list:
        """Walk the Claude window's UIA descendants and return a list of
        ``(control_type, name, automation_id, rect, value)`` tuples.

        ``keywords`` filters by case-insensitive substring match against
        ``name``, ``automation_id``, or ``control_type``. Empty / None
        keywords means "include all".

        ``control_types`` filters to a specific set of UIA control types
        (Button / MenuItem / Edit / etc.). None means "include all".

        Diagnostic helper for figuring out actual button names on a
        given Claude Desktop build.
        """
        w = self._ensure_window()
        out: list = []
        kw = tuple((s or "").lower() for s in (keywords or ())) or None
        cts = tuple(control_types) if control_types else None
        try:
            for desc in w.descendants():
                try:
                    info = desc.element_info
                    ct = info.control_type or ""
                    if cts and ct not in cts:
                        continue
                    nm = (info.name or "")[:max_name_len]
                    aid = info.automation_id or ""
                    rl = info.rectangle
                    rect_str = (
                        f"({rl.left},{rl.top})-({rl.right},{rl.bottom})"
                        if rl is not None else ""
                    )
                    val = ""
                    try:
                        # element_info.runtime_id is hashable, but value is
                        # in the Pattern. Skip if not exposed cheaply.
                        if hasattr(desc, "get_value"):
                            v = desc.get_value()
                            if v:
                                val = str(v)[:max_name_len]
                    except Exception:
                        pass
                    if kw is not None:
                        hay = f"{ct} {nm} {aid} {val}".lower()
                        if not any(k in hay for k in kw if k):
                            continue
                    out.append((ct, nm, aid, rect_str, val))
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("dump_tree: walk failed: %s", exc)
        return out

    def _find_uia_by_name_substr(
        self,
        substrings: Iterable[str],
        *,
        control_types: Iterable[str] = ("Button",),
        timeout: float = 4.0,
        prefer: str = "first",
        prefer_y_min: Optional[int] = None,
        prefer_y_max: Optional[int] = None,
    ):
        """Walk the Claude window's UIA descendants and return the
        element whose ``Name`` contains any of ``substrings`` and whose
        ``ControlType`` is in ``control_types``.

        Substring match is case-insensitive. Returns None on timeout.

        ``prefer`` controls which match wins when multiple match:

        - ``"first"`` (default): tree-order first (legacy behaviour).
        - ``"last"``: tree-order last — useful when there are multiple
          matching action-toolbar buttons (one per assistant message)
          and we want the one for the most recent message.
        - ``"max_y"``: the match with the largest top-Y — typically the
          newest item in the chat scroll.
        - ``"min_y"``: the match with the smallest top-Y.

        Optional ``prefer_y_min`` / ``prefer_y_max`` filter candidates
        to a vertical band (useful for separating composer-area
        controls Y > 1400 from chat-content controls).
        """
        all_matches = self._find_uia_by_name_substr_all(
            substrings,
            control_types=control_types,
            timeout=timeout,
            min_count=1,
            prefer_y_min=prefer_y_min,
            prefer_y_max=prefer_y_max,
        )
        if not all_matches:
            return None
        if prefer == "first":
            return all_matches[0][1]
        if prefer == "last":
            return all_matches[-1][1]
        if prefer == "max_y":
            return max(all_matches, key=lambda t: t[0])[1]
        if prefer == "min_y":
            return min(all_matches, key=lambda t: t[0])[1]
        return all_matches[0][1]

    def _find_uia_by_name_substr_all(
        self,
        substrings: Iterable[str],
        *,
        control_types: Iterable[str] = ("Button",),
        timeout: float = 4.0,
        min_count: int = 1,
        prefer_y_min: Optional[int] = None,
        prefer_y_max: Optional[int] = None,
    ) -> list:
        """Same as ``_find_uia_by_name_substr`` but returns a list of
        ``(top_y, element)`` tuples in tree order, filtered by optional
        Y-band. Polls the window until at least ``min_count`` matches
        are found OR ``timeout`` elapses.
        """
        w = self._ensure_window()
        deadline = time.time() + timeout
        wanted = tuple(s.lower() for s in substrings)
        cts = tuple(control_types)
        out: list = []
        while time.time() < deadline:
            try:
                out = []
                for desc in w.descendants():
                    try:
                        info = desc.element_info
                        ct = (info.control_type or "")
                        if ct not in cts:
                            continue
                        nm = (info.name or "").lower()
                        if not nm or not any(s in nm for s in wanted):
                            continue
                        rl = info.rectangle
                        top_y = rl.top if rl is not None else 0
                        if prefer_y_min is not None and top_y < prefer_y_min:
                            continue
                        if prefer_y_max is not None and top_y > prefer_y_max:
                            continue
                        out.append((top_y, desc))
                    except Exception:
                        continue
                if len(out) >= min_count:
                    return out
            except Exception:
                pass
            time.sleep(0.4)
        return out

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
        for ALL Buttons whose Name contains "retry" / "regenerate" /
        "重新生成", and pick the one with the largest top-Y (= bottom-most
        on screen = most recent assistant message). Empirically the
        chat is laid out top-to-bottom, so multiple turns produce
        multiple "Retry" buttons; we want the LAST one.

        Empirically validated 2026-05-10 against Claude Desktop v1.6608+:
        the action-toolbar button is plain ``Name="Retry"``.
        """
        if not self._hover_message(message_index_from_end=0):
            logger.warning("regenerate_last: could not hover last message")
            return False
        btn = self._find_uia_by_name_substr(
            ("retry", "regenerate", "重新生成"),
            control_types=("Button",),
            timeout=3.0,
            prefer="max_y",
            # Stay below the conversation header (Y>200) and above the
            # composer (Y<1400) — the action toolbars live in this band.
            prefer_y_min=200,
            prefer_y_max=1400,
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

    def attach_file_via_picker(self, file_path: str, *, settle: float = 4.0) -> bool:
        """Attach a file by clicking the paperclip button and driving
        the resulting submenu / native file dialog.

        On Claude Desktop v1.6608+ the paperclip is labelled
        ``Add files, connectors, and more``. Clicking it opens a
        Chromium menu (a separate top-level Win32 popup window, NOT
        a descendant of the main window). The first item is typically
        ``Upload from computer``, which then opens the standard
        Windows file-open dialog. Strategy:

        1. Click the paperclip button.
        2. Poll all top-level desktop windows for one whose tree
           contains a menu item named ``Upload from computer`` /
           ``Upload`` / ``From this device`` / ``浏览本地文件``;
           click it.
        3. Wait for the native ``Open`` file dialog (matched by class
           ``#32770`` or title containing ``Open``).
        4. Type the absolute file path into its filename ``Edit``
           field and press Enter.
        5. Sleep ``settle`` seconds for Claude Desktop to upload.

        Returns True if the file picker chain was driven through to
        the submit step. Caller still needs to verify the upload
        actually completed (e.g., by checking ``raw_captures`` for an
        upload-shaped path).
        """
        from pywinauto.timings import wait_until

        # 0. Make sure Claude is foreground so its UIA descendants are
        #    fully enumerable.
        self.focus()
        time.sleep(0.7)

        # 1. Click paperclip — same finder strategy as select_style;
        #    no Y-band because the composer position varies between
        #    fresh-chat (centered Y~600) and chat-with-content
        #    (bottom Y~1446) layouts.
        clip_btn = self._find_uia_by_name_substr(
            ("add files, connectors", "add files,", "attach"),
            control_types=("Button",),
            timeout=6.0,
        )
        if clip_btn is None:
            logger.warning("attach_file_via_picker: paperclip button not found")
            return False
        try:
            clip_btn.click_input()
        except Exception as exc:
            logger.warning("attach_file_via_picker: paperclip click failed: %s", exc)
            return False
        time.sleep(0.7)

        # 2. Click "Upload from computer" — search ALL top-level windows
        #    INCLUDING ones that report is_visible=False (Chromium popups
        #    transition through invisible state) and broad control types.
        upload_item = None
        # Specific phrases only — bare ``"upload"`` matches too many
        # unrelated chrome elements (settings, history, etc.) and the
        # first-match-wins loop would click the wrong thing.
        upload_needles = (
            "upload from computer",
            "upload from device",
            "from this device",
            "from your computer",
            "upload a file",
            "browse files",
            "browse file",
            "添加文件",
            "上传文件",
            "从电脑",
            "从计算机",
            "从此设备",
        )
        upload_types = ("MenuItem", "Button", "ListItem", "Hyperlink",
                        "TreeItem", "Custom")
        deadline = time.time() + 4.0
        while time.time() < deadline and upload_item is None:
            try:
                for win in self._desktop.windows():
                    try:
                        for desc in win.descendants():
                            try:
                                info = desc.element_info
                                ct = info.control_type or ""
                                if ct not in upload_types:
                                    continue
                                nm = (info.name or "").lower()
                                if not nm:
                                    continue
                                if any(s in nm for s in upload_needles):
                                    upload_item = desc
                                    break
                            except Exception:
                                continue
                        if upload_item is not None:
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.3)

        # Helper to detect the native file-open dialog. Class names
        # vary across Windows versions — accept the legacy ``#32770``
        # AND modern ``CabinetWClass`` / Windows 11 picker variants.
        def _find_open_dialog():
            for w in self._desktop.windows():
                try:
                    cls = (w.class_name() or "")
                    title = (w.window_text() or "").lower()
                    if cls in ("#32770", "CabinetWClass") and (
                        "open" in title or "打开" in title or title == ""
                    ):
                        return w
                except Exception:
                    continue
            return None

        if upload_item is not None:
            try:
                upload_item.click_input()
            except Exception as exc:
                logger.warning("attach_file_via_picker: upload-item "
                               "click failed: %s — falling through to "
                               "keyboard fallback", exc)

        # 3. Wait briefly for native file dialog. If it didn't appear
        #    after the named-item click (or no item was found), try
        #    keyboard navigation: Down arrow + Enter activates the
        #    FIRST menu item, which is typically "Upload from
        #    computer" / "Browse files" on this build.
        deadline = time.time() + 3.0
        dialog = None
        while time.time() < deadline and dialog is None:
            dialog = _find_open_dialog()
            if dialog is not None:
                break
            time.sleep(0.3)

        if dialog is None:
            # Keyboard fallback. If we're not sure the menu is open,
            # we may have already dismissed it via ESC — re-click the
            # paperclip first.
            logger.info("attach_file_via_picker: named click did not "
                        "yield dialog; trying keyboard Down+Enter "
                        "on the paperclip menu")
            try:
                clip_btn.click_input()
                time.sleep(0.7)
                send_keys("{DOWN}", pause=0.1)
                time.sleep(0.2)
                send_keys("{ENTER}", pause=0.1)
            except Exception as exc:
                logger.warning("attach_file_via_picker: keyboard "
                               "fallback failed: %s", exc)
                send_keys("{ESC}", pause=0.05)
                return False

            # Wait again for the dialog
            deadline = time.time() + 6.0
            while time.time() < deadline and dialog is None:
                dialog = _find_open_dialog()
                if dialog is not None:
                    break
                time.sleep(0.3)

        if dialog is None:
            logger.warning(
                "attach_file_via_picker: native file dialog did not "
                "appear within 9s (tried named item + keyboard "
                "Down+Enter)"
            )
            send_keys("{ESC}", pause=0.05)
            return False

        # 4. Type path into filename Edit; press Enter
        try:
            dialog.set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        # Most Open dialogs accept paste / typing into the active edit
        # control. Use the standard Win32 trick: Alt+N goes to "File name".
        send_keys("%n", pause=0.1)  # Alt+N
        time.sleep(0.2)
        send_keys("^a", pause=0.05)
        time.sleep(0.1)
        send_keys(file_path, with_spaces=True, vk_packet=True, pause=0.01)
        time.sleep(0.3)
        send_keys("{ENTER}", pause=0.1)
        time.sleep(settle)
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
            patterns = ("next version", "next response", "next branch", "next variant")
        else:
            patterns = ("previous version", "previous response", "previous branch", "prev variant")
        btn = self._find_uia_by_name_substr(
            patterns,
            control_types=("Button",),
            timeout=3.0,
            prefer="max_y",
            prefer_y_min=200,
            prefer_y_max=1400,
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

        Strategy: find the model-picker trigger (a button at the
        composer area whose name starts with ``"Model: "`` — empirical
        on Claude Desktop v1.6608+), click to open the picker, then
        click the target across all top-level desktop windows
        (Chromium menus open in a separate Win32 popup window, NOT as
        a descendant of the main Claude window).

        Returns True if the model item was found and clicked.
        """
        self.focus()
        time.sleep(0.7)
        # Empirical name: ``Model: Haiku 4.5 Extended`` etc. The
        # ``Model:`` prefix is the most reliable substring. No Y-band
        # filter: in a brand-new chat the composer is centered
        # (Y~600); in a chat with content it's at the bottom (Y~1446).
        trigger = self._find_uia_by_name_substr(
            ("model:",),
            control_types=("Button",),
            timeout=6.0,
        )
        if trigger is None:
            logger.warning("select_model: model picker trigger 'Model:' not found")
            return False
        try:
            trigger.click_input()
        except Exception as exc:
            logger.warning("select_model: trigger click failed: %s", exc)
            return False
        time.sleep(0.7)

        # The picker menu lives in a separate top-level Win32 popup
        # window — search ALL visible desktop windows for the item.
        wanted = name_substring.lower()
        target = None
        deadline = time.time() + 4.0
        while time.time() < deadline and target is None:
            try:
                for win in self._desktop.windows():
                    try:
                        if not win.is_visible():
                            continue
                        for desc in win.descendants():
                            try:
                                info = desc.element_info
                                ct = info.control_type or ""
                                if ct not in ("MenuItem", "Button", "ListItem"):
                                    continue
                                nm = (info.name or "").lower()
                                if not nm or wanted not in nm:
                                    continue
                                target = desc
                                break
                            except Exception:
                                continue
                        if target is not None:
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.3)

        if target is None:
            logger.warning("select_model: no model item matching %r found in any popup",
                           name_substring)
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

        On Claude Desktop v1.6608+ the Style picker isn't surfaced as
        a standalone composer-area button — it's inside the paperclip
        ``Add files, connectors, and more`` submenu OR the model-
        picker submenu. Strategy:

        1. Click the paperclip / "Add files" button to open its menu
           (which surfaces options like ``Style``, ``Connectors``,
           etc.).
        2. Across all visible top-level desktop windows, search for a
           menu item matching ``style`` / ``writing style`` and click it.
        3. From the resulting style submenu, click the item matching
           ``name_substring``.

        Returns True if the style item was found and clicked.
        """
        self.focus()
        time.sleep(0.7)

        # Step 1 — open the "Add files, connectors, and more" menu
        # (the user-visible Plus / Paperclip button). Style picker is
        # one of the submenu items on this build. The button name is
        # unique across the tree so we don't need a Y-band filter; in
        # a brand-new chat the composer is centered (Y~600) rather
        # than at the bottom (Y~1446) so a Y-min filter would skip it.
        clip_btn = self._find_uia_by_name_substr(
            ("add files, connectors", "add files,", "attach"),
            control_types=("Button",),
            timeout=6.0,
        )
        if clip_btn is None:
            logger.warning("select_style: paperclip button not found "
                           "(searched all Y across the tree)")
            return False
        try:
            clip_btn.click_input()
        except Exception as exc:
            logger.warning("select_style: paperclip click failed: %s", exc)
            return False
        time.sleep(0.7)

        # Step 2 — find a menu item matching the style name DIRECTLY
        # across all top-level windows (visible + hidden — Chromium
        # popups sometimes report is_visible=False before fully
        # rendering). On v1.6608+ the paperclip menu may surface style
        # items directly OR through a "Use a style" submenu — try the
        # direct path first.
        def _find_in_popups(needles, types=("MenuItem", "Button", "ListItem",
                                            "Hyperlink", "TreeItem", "Custom",
                                            "RadioButton", "CheckBox")):
            deadline = time.time() + 4.0
            while time.time() < deadline:
                try:
                    for win in self._desktop.windows():
                        try:
                            for desc in win.descendants():
                                try:
                                    info = desc.element_info
                                    ct = info.control_type or ""
                                    if ct not in types:
                                        continue
                                    nm = (info.name or "").lower()
                                    if not nm:
                                        continue
                                    if any(s in nm for s in needles):
                                        return desc
                                except Exception:
                                    continue
                        except Exception:
                            continue
                except Exception:
                    pass
                time.sleep(0.3)
            return None

        # Try direct style-item match first (paperclip menu may expose
        # ``Concise`` / ``Explanatory`` etc. as flat items)
        target = _find_in_popups((name_substring.lower(),))
        if target is None:
            # Fall back: try the two-step path (click "Use a style" /
            # "Writing Style" submenu trigger first, then look for
            # the actual item).
            style_trigger = _find_in_popups((
                "use a style", "writing style", "use style",
            ))
            if style_trigger is not None:
                try:
                    style_trigger.click_input()
                    time.sleep(0.6)
                    target = _find_in_popups((name_substring.lower(),))
                except Exception:
                    pass

        if target is None:
            logger.warning("select_style: no style item matching %r found "
                           "in popup window tree (direct or via submenu)",
                           name_substring)
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
        # ``VK_OEM_5`` (the actual backslash VK code) isn't recognised
        # by pywinauto's parser on this version; use literal ``\\``.
        try:
            send_keys("^\\", pause=0.05)  # Ctrl+\
        except Exception:
            # Some pywinauto versions still need an alternate spelling
            send_keys("{VK_CONTROL down}\\{VK_CONTROL up}", pause=0.05)
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

    # ────────────────────────────────────────────────────────────────────
    # P5.B.5.2 — Cowork-region driver helpers (added 2026-05-11)
    #
    # All shapes empirically locked during the 3-pass RECON on
    # 2026-05-11. See
    # ``Docs/research/2026-05-11-cowork-recon-findings.md`` for the
    # per-question evidence trail. Each helper is a thin wrapper over
    # ``_find_uia_by_name_substr`` + ``click_input`` modelled on the
    # existing ``select_model`` / ``open_project`` patterns.
    # ────────────────────────────────────────────────────────────────────

    def open_cowork_tab(self) -> bool:
        """Click the top-level "Cowork" tab.

        Claude Desktop's top of the main window has three tabs:
        Chat / Cowork / Code. RECON 2 baseline UIA dump and the user's
        clean Cowork screenshot (11:38) confirm these render as
        ``TabItem`` or ``Button`` controls in the y<120 band of the
        2560×1344 main window.

        Returns True if a click landed.
        """
        self.focus()
        target = self._find_uia_by_name_substr(
            ("Cowork",),
            control_types=("TabItem", "Button", "Hyperlink"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("open_cowork_tab: 'Cowork' tab not found in UIA tree")
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("open_cowork_tab: click failed: %s", exc)
            return False
        time.sleep(1.2)
        return True

    def ensure_cowork_chat(self) -> bool:
        """Return Claude to a fresh Cowork chat composer state.

        Top-tab "Cowork" alone does NOT bring back the chat composer
        if the right pane is currently showing Live artifacts /
        Dispatch / Projects / etc. (a sidebar-driven subpane).
        This helper:

        1. Clicks the top-level "Cowork" tab.
        2. Clicks the "New task" button in the sidebar — this resets
           the right pane to a fresh Cowork chat composer.
        3. Verifies a composer UIA element exists.

        Returns True if the composer is reachable afterwards.
        """
        self.focus()
        # 1. Cowork tab
        if not self.open_cowork_tab():
            logger.warning("ensure_cowork_chat: open_cowork_tab failed")
            # don't bail — composer may still be reachable
        time.sleep(0.4)

        # 2. New task button
        new_task = self._find_uia_by_name_substr(
            ("New task",),
            control_types=("Button",),
            timeout=2.0,
        )
        if new_task is None:
            logger.warning("ensure_cowork_chat: 'New task' sidebar button not found")
        else:
            try:
                new_task.click_input()
                time.sleep(1.2)
            except Exception as exc:
                logger.warning(
                    "ensure_cowork_chat: 'New task' click failed: %s", exc,
                )

        # 3. Composer reachability check
        composer = self._find_composer_uia()
        if composer is None:
            logger.warning("ensure_cowork_chat: composer still not reachable")
            return False
        return True

    def open_chat_tab(self) -> bool:
        """Click the top-level "Chat" tab (return-to-chat utility)."""
        self.focus()
        target = self._find_uia_by_name_substr(
            ("Chat",),
            control_types=("TabItem", "Button", "Hyperlink"),
            timeout=3.0,
        )
        if target is None:
            return False
        try:
            target.click_input()
        except Exception:
            return False
        time.sleep(0.8)
        return True

    def pick_skill(self, name_substring: str, *, timeout: float = 6.0) -> bool:
        """Open the Cowork slash-picker and click a matching command/skill.

        Empirical shape (2026-05-11 sweep recon, build N+1):
        1. ``/`` in the Cowork composer opens a flat menu
           (NO "Directory" header, NO Skills/Connectors/Plugins tabs).
        2. The search field is an ``Edit`` element with the typed text
           (initially "/"). Auto-focused — keystrokes go straight in.
        3. Each command/skill row is a ``MenuItem`` whose name is the
           command identifier (e.g. ``"skill-creator"``,
           ``"add-files Open file picker"``).

        Strategy:
        1. ``click_composer()`` + send ``/`` to open the picker.
        2. Wait for any ``MenuItem`` to appear (proves picker is open).
        3. Type ``name_substring`` to filter the list.
        4. Wait for a ``MenuItem`` whose name contains ``name_substring``
           and click it.
        5. If no row matches within ``timeout``, send ``Esc`` + clean
           up the lingering ``/`` characters in the composer and
           return False.

        Returns True on apparent success.
        """
        self.focus()
        self.click_composer()
        time.sleep(0.3)
        try:
            send_keys("/", pause=0.1)
        except Exception as exc:
            logger.warning("pick_skill: '/' keystroke failed: %s", exc)
            return False

        # Wait for the picker to render — any MenuItem appearing is
        # the canonical signal. Old builds had a "Directory" Text
        # marker; the new build has none, so we probe MenuItems.
        deadline = time.time() + 3.0
        picker_open = False
        while time.time() < deadline:
            probe = self._find_uia_by_name_substr(
                ("",),  # match any MenuItem
                control_types=("MenuItem",),
                timeout=0.5,
            )
            if probe is not None:
                picker_open = True
                break
            time.sleep(0.25)
        if not picker_open:
            logger.warning(
                "pick_skill: slash picker did not render any MenuItem within 3s"
            )
            try:
                send_keys("{BACKSPACE}", pause=0.05)
            except Exception:
                pass
            return False

        # Type the search query — the Edit field is auto-focused inside
        # the picker so keystrokes filter the list directly.
        try:
            send_keys(name_substring, pause=0.03, vk_packet=True)
        except Exception as exc:
            logger.warning("pick_skill: search text input failed: %s", exc)
            try:
                send_keys("{ESC}", pause=0.05)
            except Exception:
                pass
            return False
        time.sleep(0.6)

        # Click the matching MenuItem.
        deadline = time.time() + timeout
        clicked = False
        while time.time() < deadline and not clicked:
            row = self._find_uia_by_name_substr(
                (name_substring,),
                control_types=("MenuItem",),
                timeout=1.0,
            )
            if row is not None:
                try:
                    row.click_input()
                    clicked = True
                except Exception:
                    pass
            else:
                time.sleep(0.3)
        if not clicked:
            logger.warning(
                "pick_skill: no MenuItem matching %r within %.1fs",
                name_substring, timeout,
            )
            # Dismiss + clean up lingering '/' + typed query in composer
            try:
                send_keys("{ESC}", pause=0.05)
                # Erase the '/' + query string left in the composer
                # (one BACKSPACE per char + the leading '/')
                for _ in range(len(name_substring) + 1):
                    send_keys("{BACKSPACE}", pause=0.02)
            except Exception:
                pass
            return False
        time.sleep(0.8)
        return True

    def select_ask_mode(self, mode_substring: str) -> bool:
        """Click the composer's "Ask ▼" mode picker and pick a mode.

        The Cowork composer has a mode picker labeled "Ask" (default)
        with a dropdown that exposes modes such as "Ask", "Plan",
        "Plan-and-execute" etc. RECON 2 baseline UIA dump shows it
        renders next to the model picker and "Work in a project"
        button in the composer footer.
        """
        self.focus()
        # Locate the "Ask" mode button (Y near composer footer)
        mode_btn = self._find_uia_by_name_substr(
            ("Ask",),
            control_types=("Button", "MenuItem", "ComboBox"),
            timeout=3.0,
        )
        if mode_btn is None:
            logger.warning("select_ask_mode: 'Ask' mode picker not found")
            return False
        try:
            mode_btn.click_input()
        except Exception as exc:
            logger.warning("select_ask_mode: click failed: %s", exc)
            return False
        time.sleep(0.5)

        # Pick the requested mode from the dropdown
        target = self._find_uia_by_name_substr(
            (mode_substring,),
            control_types=("MenuItem", "Button", "ListItem", "RadioButton"),
            timeout=2.0,
        )
        if target is None:
            logger.warning(
                "select_ask_mode: no mode matching %r in dropdown",
                mode_substring,
            )
            # Press Esc to close the dropdown
            try:
                send_keys("{ESC}", pause=0.05)
            except Exception:
                pass
            return False
        try:
            target.click_input()
        except Exception:
            return False
        time.sleep(0.4)
        return True

    def view_live_artifacts(self) -> bool:
        """Click the Cowork sidebar entry "Live artifacts".

        RECON 2 confirmed this is a ``df-pill`` styled Button in the
        left sidebar (visible alongside "New task", "Projects",
        "Scheduled", "Dispatch (Beta)", "Customize"). Clicking
        navigates to the Live Artifacts pane (in-app, no Win32 popup).
        """
        self.focus()
        target = self._find_uia_by_name_substr(
            ("Live artifacts", "Live Artifacts", "Live Artifact"),
            control_types=("Button", "ListItem", "Hyperlink"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("view_live_artifacts: sidebar entry not found")
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("view_live_artifacts: click failed: %s", exc)
            return False
        time.sleep(1.2)
        return True

    def open_dispatch(self) -> bool:
        """Click the Cowork sidebar entry "Dispatch (Beta)".

        RECON 2 Q3 closure: shape is an in-app pane, descendant of the
        main Claude window. Button class ``df-pill hide-focus-ring``.
        """
        self.focus()
        target = self._find_uia_by_name_substr(
            ("Dispatch",),
            control_types=("Button", "ListItem", "Hyperlink"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("open_dispatch: 'Dispatch' sidebar entry not found")
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("open_dispatch: click failed: %s", exc)
            return False
        time.sleep(1.2)
        return True

    def open_scheduled(self) -> bool:
        """Click the Cowork sidebar entry "Scheduled"."""
        self.focus()
        target = self._find_uia_by_name_substr(
            ("Scheduled",),
            control_types=("Button", "ListItem", "Hyperlink"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("open_scheduled: 'Scheduled' sidebar entry not found")
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("open_scheduled: click failed: %s", exc)
            return False
        time.sleep(1.0)
        return True

    def wait_for_cowork_step(
        self,
        *,
        timeout: float = 180.0,
        poll_interval: float = 1.5,
        idle_settle: float = 3.0,
    ) -> dict:
        """Wait for a Cowork turn to complete using UI-side cues.

        Cowork chat traffic does NOT appear in PCE's network captures
        (Q2 WS-over-HTTP/2 gap), so the standard ``wait_done`` which
        counts ``/completion`` rows can't tell us when a Cowork turn
        finished. This helper polls UIA instead:

        1. While a "Stop" / "Cancel" button is visible in the composer
           area, the assistant is still generating.
        2. Once that button disappears AND stays missing for
           ``idle_settle`` seconds, the turn is considered done.
        3. Aborts after ``timeout`` seconds with ``outcome="timeout"``.

        Returns a dict with keys::

            outcome:        "done" | "timeout"
            elapsed_s:      float
            polls:          int
            saw_stop_button: bool   (True if stop was observed at least once)
        """
        start = time.time()
        polls = 0
        saw_stop = False
        last_seen_stop = start  # last time we saw the stop button
        # Search names — Claude Desktop on different builds uses any of
        # these labels for the in-flight stop control. Empty match
        # means "stop button not visible right now".
        stop_names = ("Stop response", "Stop generating", "Stop", "Cancel")

        while True:
            polls += 1
            elapsed = time.time() - start
            if elapsed > timeout:
                return {
                    "outcome": "timeout",
                    "elapsed_s": round(elapsed, 1),
                    "polls": polls,
                    "saw_stop_button": saw_stop,
                }

            stop_btn = self._find_uia_by_name_substr(
                stop_names,
                control_types=("Button",),
                timeout=0.5,
            )
            if stop_btn is not None:
                saw_stop = True
                last_seen_stop = time.time()
            else:
                # Stop button absent. If we previously saw it OR
                # ``idle_settle`` seconds have passed without ever
                # seeing it (e.g. a very short turn), the turn is done.
                idle_for = time.time() - last_seen_stop
                if saw_stop and idle_for >= idle_settle:
                    return {
                        "outcome": "done",
                        "elapsed_s": round(time.time() - start, 1),
                        "polls": polls,
                        "saw_stop_button": True,
                    }
                if (not saw_stop) and elapsed >= idle_settle:
                    # Turn likely produced no streaming (e.g. an
                    # extremely fast text-only reply that completed
                    # between polls). Return done conservatively.
                    return {
                        "outcome": "done",
                        "elapsed_s": round(time.time() - start, 1),
                        "polls": polls,
                        "saw_stop_button": False,
                    }
            time.sleep(poll_interval)

    # ──────────────────────────────────────────────────────────────────────
    # Code-region (inline) helpers — P5.B.7 (2026-05-11)
    #
    # Unlike chat-region's /completion SSE pairing, the inline Code-
    # tab agent writes every turn directly to a local JSONL
    # transcript at ~/.claude/projects/<encoded-cwd>/<cliSessId>.jsonl.
    # Wait semantics therefore key off the JSONL (not PCE's DB).
    #
    # The permission-dialog helper targets permissionMode=default
    # flows where tool_use blocks surface an Allow/Deny modal.
    # 2026-05-11 RECON did NOT close the dialog UIA control names
    # (MATRIX §5.C.2 Q2); we try several well-known substrings.
    # ──────────────────────────────────────────────────────────────────────

    def open_code_tab(self) -> bool:
        """Click the top-level "Code" tab.

        Mirrors :meth:`open_cowork_tab` / :meth:`open_chat_tab`.
        Claude Desktop's top strip has Chat / Cowork / Code as
        ``TabItem`` / ``Button`` / ``Hyperlink`` controls in the y<120
        band of the main window.
        """
        self.focus()
        target = self._find_uia_by_name_substr(
            ("Code",),
            control_types=("TabItem", "Button", "Hyperlink"),
            timeout=3.0,
        )
        if target is None:
            logger.warning("open_code_tab: 'Code' tab not found in UIA tree")
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("open_code_tab: click failed: %s", exc)
            return False
        time.sleep(1.2)
        return True

    def ensure_code_session(self) -> bool:
        """Land on a Code-tab composer ready to send a prompt.

        1. Click the Code top-level tab.
        2. If a "New session" / "New" button is visible in the left
           panel, click it to force a fresh composer state (no
           previously-open session resumed).
        3. Verify a composer UIA element is reachable.

        Returns True if the composer is reachable afterwards.
        """
        self.focus()
        if not self.open_code_tab():
            logger.warning("ensure_code_session: open_code_tab failed")
            # don't bail — composer may still be reachable
        time.sleep(0.5)

        new_btn = self._find_uia_by_name_substr(
            ("New session", "New chat", "New"),
            control_types=("Button", "Hyperlink", "ListItem"),
            timeout=2.0,
        )
        if new_btn is not None:
            try:
                new_btn.click_input()
                time.sleep(1.0)
            except Exception as exc:
                logger.debug("ensure_code_session: 'New' click failed: %s", exc)

        composer = self._find_composer_uia()
        if composer is None:
            logger.warning("ensure_code_session: composer not reachable")
            return False
        return True

    def find_active_code_session(
        self,
        *,
        encoded_cwd: Optional[str] = None,
        max_age_s: float = 300.0,
    ) -> Optional[dict]:
        """Scan ``~/.claude/projects/`` for the most recent Code-tab session.

        Returns ``None`` if no JSONL file modified within ``max_age_s``
        seconds was found. Otherwise a dict::

            {
              "cli_session_id":  str,   # JSONL filename stem (UUID)
              "jsonl_path":      Path,
              "encoded_cwd":     str,   # parent dir name, e.g. "F--test"
              "mtime_ns":        int,
              "line_count":      int,   # current line count
              "pointer_path":    Optional[Path],
              "pointer_body":    Optional[dict],
            }

        ``encoded_cwd``, when provided, restricts the scan to a single
        subdir so parallel sessions in other cwds don't interfere.

        The pointer join walks MSIX-style and Squirrel-style
        ``claude-code-sessions/<user>/<org>/local_<sess>.json`` and
        matches on ``cliSessionId``; pointer_path/body are None if no
        pointer has been written yet (possible on very-first prompt).
        """
        projects_root = Path.home() / ".claude" / "projects"
        if not projects_root.is_dir():
            return None

        now = time.time()
        best: Optional[tuple[int, Path]] = None
        if encoded_cwd:
            cwd_dirs = [projects_root / encoded_cwd]
        else:
            try:
                cwd_dirs = [p for p in projects_root.iterdir() if p.is_dir()]
            except OSError:
                return None

        for cwd_dir in cwd_dirs:
            if not cwd_dir.is_dir():
                continue
            try:
                children = list(cwd_dir.iterdir())
            except OSError:
                continue
            for f in children:
                if not f.is_file() or f.suffix != ".jsonl":
                    continue
                try:
                    mtime_ns = f.stat().st_mtime_ns
                except OSError:
                    continue
                age_s = now - (mtime_ns / 1e9)
                if max_age_s and age_s > max_age_s:
                    continue
                if best is None or mtime_ns > best[0]:
                    best = (mtime_ns, f)

        if best is None:
            return None
        mtime_ns, jsonl_path = best
        cli_session_id = jsonl_path.stem

        try:
            with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
                line_count = sum(1 for _ in fh)
        except OSError:
            line_count = 0

        pointer_path, pointer_body = self._find_code_pointer_for(cli_session_id)

        return {
            "cli_session_id": cli_session_id,
            "jsonl_path": jsonl_path,
            "encoded_cwd": jsonl_path.parent.name,
            "mtime_ns": mtime_ns,
            "line_count": line_count,
            "pointer_path": pointer_path,
            "pointer_body": pointer_body,
        }

    def _find_code_pointer_for(
        self, cli_session_id: str,
    ) -> tuple[Optional[Path], Optional[dict]]:
        """Locate the claude-code-sessions pointer JSON for a cliSessionId.

        Walks the MSIX virtual-store paths and the legacy Squirrel
        state dir. Returns (None, None) if no pointer yet matches.
        """
        candidates: list[Path] = []
        local_appdata = os.environ.get("LOCALAPPDATA")
        appdata = os.environ.get("APPDATA")
        if local_appdata:
            try:
                for pkg in (Path(local_appdata) / "Packages").glob("*Claude*"):
                    p = pkg / "LocalCache" / "Roaming" / "Claude" / "claude-code-sessions"
                    if p.is_dir():
                        candidates.append(p)
            except OSError:
                pass
        if appdata:
            p = Path(appdata) / "Claude" / "claude-code-sessions"
            if p.is_dir():
                candidates.append(p)

        for root in candidates:
            try:
                users = list(root.iterdir())
            except OSError:
                continue
            for user in users:
                if not user.is_dir():
                    continue
                try:
                    orgs = list(user.iterdir())
                except OSError:
                    continue
                for org in orgs:
                    if not org.is_dir():
                        continue
                    try:
                        pointers = list(org.iterdir())
                    except OSError:
                        continue
                    for pointer in pointers:
                        if not pointer.is_file() or pointer.suffix != ".json":
                            continue
                        try:
                            body = json.loads(pointer.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            continue
                        if (
                            isinstance(body, dict)
                            and body.get("cliSessionId") == cli_session_id
                        ):
                            return pointer, body
        return None, None

    def wait_for_code_response(
        self,
        *,
        jsonl_path: Path,
        prior_line_count: int,
        timeout: float = 90.0,
        poll_interval: float = 1.0,
        idle_settle: float = 3.0,
    ) -> dict:
        """Wait for the Code-tab JSONL to grow and stabilize.

        Code-tab writes every turn directly to the local JSONL
        transcript. This helper polls for file growth; when a line
        with ``type='assistant'`` is present AND no new lines arrive
        for ``idle_settle`` seconds, the turn is considered complete.

        Returns::

            {
              "outcome":             "done" | "timeout" | "no_growth",
              "elapsed_s":           float,
              "polls":               int,
              "final_line_count":    int,
              "new_lines":           int,
              "last_assistant_uuid": Optional[str],
            }

        ``no_growth`` is returned when the JSONL did not grow at all
        after ``max(idle_settle, timeout/3)`` seconds — a strong hint
        that the prompt didn't actually submit (focus missed the
        composer, modal blocking, etc.).
        """
        start = time.time()
        polls = 0
        last_growth = start
        last_count = prior_line_count
        last_assistant_uuid: Optional[str] = None

        while True:
            polls += 1
            elapsed = time.time() - start
            if elapsed > timeout:
                return {
                    "outcome": "timeout",
                    "elapsed_s": round(elapsed, 1),
                    "polls": polls,
                    "final_line_count": last_count,
                    "new_lines": max(0, last_count - prior_line_count),
                    "last_assistant_uuid": last_assistant_uuid,
                }
            try:
                with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
            except OSError:
                lines = []
            count = len(lines)
            if count > last_count:
                last_count = count
                last_growth = time.time()
                # Scan back for the most recent assistant-line uuid
                for raw in reversed(lines):
                    try:
                        body = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(body, dict) and body.get("type") == "assistant":
                        u = body.get("uuid")
                        if isinstance(u, str):
                            last_assistant_uuid = u
                        break
            else:
                idle_for = time.time() - last_growth
                if last_assistant_uuid and idle_for >= idle_settle:
                    return {
                        "outcome": "done",
                        "elapsed_s": round(time.time() - start, 1),
                        "polls": polls,
                        "final_line_count": last_count,
                        "new_lines": max(0, last_count - prior_line_count),
                        "last_assistant_uuid": last_assistant_uuid,
                    }
                # Hard no-growth exit after max(idle_settle, timeout/3)
                if (
                    count == prior_line_count
                    and elapsed >= max(idle_settle, timeout / 3)
                ):
                    return {
                        "outcome": "no_growth",
                        "elapsed_s": round(elapsed, 1),
                        "polls": polls,
                        "final_line_count": last_count,
                        "new_lines": 0,
                        "last_assistant_uuid": None,
                    }
            time.sleep(poll_interval)

    def accept_permission_dialog(
        self,
        *,
        which: str = "once",
        timeout: float = 5.0,
    ) -> bool:
        """Click the Code-tab permission dialog's accept (or deny) button.

        ``which`` selects the button kind::

            "once"   — "Allow once" (one-time accept)
            "always" — "Allow always" / "Always allow"
            "deny"   — "Deny" / "Reject"

        The 2026-05-11 RECON did not close the exact UIA control Name
        for these buttons (MATRIX §5.C.2 Q2), so this helper tries
        several well-known substrings per ``which`` value. Returns
        True if a click landed, False otherwise.

        Only meaningful under ``permissionMode=default``;
        ``permissionMode=acceptEdits`` sessions auto-approve without
        ever surfacing this dialog.
        """
        self.focus()
        candidates = {
            "once": (
                "Allow once", "Accept once", "Approve once",
                "Allow", "Accept", "Approve", "Yes",
            ),
            "always": (
                "Allow always", "Always allow", "Accept always",
                "Allow this and future", "Always",
            ),
            "deny": (
                "Deny", "Reject", "Cancel", "No",
            ),
        }.get(which, ("Allow once", "Accept", "Yes"))

        target = self._find_uia_by_name_substr(
            candidates,
            control_types=("Button",),
            timeout=timeout,
        )
        if target is None:
            logger.warning(
                "accept_permission_dialog: no button matching %r found (which=%s)",
                candidates, which,
            )
            return False
        try:
            target.click_input()
        except Exception as exc:
            logger.warning("accept_permission_dialog: click failed: %s", exc)
            return False
        time.sleep(0.4)
        return True
