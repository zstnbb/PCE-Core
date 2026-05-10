# SPDX-License-Identifier: Apache-2.0
"""Abstract base class for desktop AI app drivers.

Each concrete driver wraps one product (Claude Desktop, ChatGPT
Desktop, ...) and exposes a uniform set of high-level actions that
desktop D-cases need:

- ``focus()`` — bring the app's main window to foreground
- ``click_composer()`` — focus the composer (text input area)
- ``send_message(text, ...)`` — type ``text`` and submit
- ``wait_done(timeout)`` — wait for assistant response to finish
- ``cancel_current()`` — stop generation mid-stream
- ``new_chat()`` — start a fresh conversation (best-effort; not all
  apps expose a reliable shortcut)

Concrete drivers may add app-specific helpers (e.g. attachment upload).

The abstract layer defines no UI specifics — those vary per app — but
ensures cases can be written against an interface, not against
``pywinauto`` / ``win32gui`` directly.
"""
from __future__ import annotations

import abc
from typing import Optional


class DesktopDriver(abc.ABC):
    """Abstract base for an installed AI desktop app's UI driver."""

    #: Human-readable product label, e.g. "Claude Desktop", for log lines.
    PRODUCT_NAME: str = "Unknown"

    #: Host string the product talks to (used by DB-poll helpers to
    #: filter raw_captures rows).
    PRODUCT_HOST: str = ""

    @abc.abstractmethod
    def focus(self) -> None:
        """Bring the product's main window to foreground.

        Must work even when the window is minimized. Caller can rely on
        a 0.5–1.0s settle delay being applied by the implementation.
        """

    @abc.abstractmethod
    def click_composer(self) -> None:
        """Place focus on the composer (text input area).

        For Chromium-based apps this typically means a mouse click at a
        known offset within the renderer area. Idempotent: calling
        twice is safe.
        """

    @abc.abstractmethod
    def send_message(
        self,
        text: str,
        *,
        wait_done: bool = True,
        wait_timeout: float = 60.0,
    ) -> Optional[str]:
        """Type ``text`` into the composer and submit.

        Returns the ``pair_id`` of the resulting ``/completion`` request
        row in raw_captures, if one appears within ``wait_timeout``.
        Returns None if no request was observed (caller should treat as
        a hard failure — UI didn't accept the message).

        If ``wait_done`` is True, also waits for the response row (still
        returns the request pair_id either way).
        """

    @abc.abstractmethod
    def wait_done(
        self,
        request_pair_id: str,
        *,
        timeout: float = 60.0,
    ) -> Optional[dict]:
        """Wait for ``request_pair_id``'s response row to appear in
        raw_captures. Returns ``{status_code, body_format, body_len}``
        or None on timeout."""

    @abc.abstractmethod
    def cancel_current(self) -> bool:
        """Cancel the in-flight generation (D04). Returns True if a
        cancel signal was sent, False if no signal could be delivered
        (e.g. nothing was generating)."""

    @abc.abstractmethod
    def new_chat(self) -> bool:
        """Start a new conversation (best-effort). Returns True on
        success, False if the app didn't provide a reliable shortcut."""
