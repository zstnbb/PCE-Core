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
        upload_needles = (
            "upload from computer",
            "upload from device",
            "from this device",
            "from your computer",
            "upload a file",
            "browse files",
            "添加文件",
            "上传文件",
            "upload",
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

        if upload_item is None:
            logger.warning(
                "attach_file_via_picker: 'Upload from computer' menu item "
                "not found across desktop windows; submenu may use a "
                "different label on this build"
            )
            send_keys("{ESC}", pause=0.05)
            return False
        try:
            upload_item.click_input()
        except Exception as exc:
            logger.warning("attach_file_via_picker: upload-item click failed: %s", exc)
            send_keys("{ESC}", pause=0.05)
            return False

        # 3. Wait for native file dialog (class #32770) and drive it
        try:
            wait_until(
                timeout=6.0,
                retry_interval=0.3,
                func=lambda: any(
                    (w.class_name() or "") == "#32770"
                    and "open" in (w.window_text() or "").lower()
                    for w in self._desktop.windows()
                ),
            )
        except Exception:
            logger.warning(
                "attach_file_via_picker: native file dialog (class=#32770) "
                "did not appear within 6s"
            )
            return False

        dialog = None
        for w in self._desktop.windows():
            try:
                if (w.class_name() or "") == "#32770" \
                        and "open" in (w.window_text() or "").lower():
                    dialog = w
                    break
            except Exception:
                continue
        if dialog is None:
            logger.warning("attach_file_via_picker: lost dialog handle")
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
