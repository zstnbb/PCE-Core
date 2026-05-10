# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for desktop UI automation cases.

Three concerns:

1. **Win32 foreground forcing** — Windows' SetForegroundWindow has a
   30-year-old anti-stealing restriction that fails silently when the
   calling process isn't already foreground. The AttachThreadInput trick
   is the standard workaround. ``force_foreground(hwnd)`` does it.

2. **Mouse click at absolute screen coordinates** — Chromium-based apps
   render their UI inside a child window managed by the renderer
   process. The outer (parent) HWND can be foregrounded but the inner
   child only takes Win32 focus when it receives a mouse event over its
   coordinates. ``click_at(x, y)`` is a thin wrapper.

3. **DB poll for "/completion" request/response pairs** — the canonical
   "did Claude actually send the message" / "is generation done" signal
   is the appearance of a request row + its matching response row in
   ``raw_captures``. ``baseline_ts()`` and ``wait_completion_response()``
   encapsulate the polling.

These helpers are deliberately stateless so cases can compose them.
"""
from __future__ import annotations

import ctypes
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import win32api
import win32con
import win32gui
import win32process
from pywinauto.mouse import click as _mouse_click


def configure_utf8_stdout() -> None:
    """Force ``sys.stdout`` / ``sys.stderr`` to UTF-8 with replace-on-error.

    Default Windows console codec on a Chinese-locale machine is GBK,
    which fails on emojis, mathematical symbols, CJK glyphs in unusual
    combinations, and various Unicode control characters that show up
    routinely in captured assistant replies. This is a no-op on
    machines that already have UTF-8 stdout.

    Idempotent. Safe to call from every case ``main()``.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            # Stream already detached or older Python; ignore — the
            # case will at least show partial output.
            pass


# ---------- Foreground / focus ----------

def force_foreground(hwnd: int) -> None:
    """Force ``hwnd`` to be the foreground window, bypassing
    SetForegroundWindow's anti-stealing restriction.

    Safe to call when the window is minimized (calls ShowWindow first).
    No-op-equivalent when the target is already foreground.
    """
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
    my_thread = win32api.GetCurrentThreadId()
    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
    try:
        if fg_thread != my_thread:
            ctypes.windll.user32.AttachThreadInput(my_thread, fg_thread, True)
        if target_thread != my_thread:
            ctypes.windll.user32.AttachThreadInput(my_thread, target_thread, True)
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetActiveWindow(hwnd)
    finally:
        if fg_thread != my_thread:
            ctypes.windll.user32.AttachThreadInput(my_thread, fg_thread, False)
        if target_thread != my_thread:
            ctypes.windll.user32.AttachThreadInput(my_thread, target_thread, False)


def click_at(x: int, y: int, *, button: str = "left") -> None:
    """Send a left-click at absolute screen coordinates ``(x, y)``."""
    _mouse_click(button=button, coords=(x, y))


def copy_files_to_clipboard(file_paths) -> None:
    """Put one or more files on the Windows clipboard in CF_HDROP format.

    Equivalent to selecting files in Explorer and pressing Ctrl+C —
    pasting (Ctrl+V) into Claude Desktop / ChatGPT Desktop / most
    Electron apps will then attach the files. This avoids needing to
    drive the native Win32 file-open dialog.

    ``file_paths`` may be a single string/Path or an iterable of them.
    """
    import struct
    import win32clipboard
    import win32con

    if isinstance(file_paths, (str, Path)):
        paths = [str(file_paths)]
    else:
        paths = [str(p) for p in file_paths]

    # CF_HDROP wire format:
    #   DROPFILES struct (20 bytes): pFiles offset, pt.x, pt.y, fNC, fWide
    #   followed by a double-null-terminated UTF-16LE string list.
    files_str = "\0".join(paths) + "\0\0"
    files_data = files_str.encode("utf-16-le")
    drop_files = struct.pack("Iiiii", 20, 0, 0, 0, 1) + files_data

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, drop_files)
    finally:
        win32clipboard.CloseClipboard()


# ---------- DB-side polling ----------

def default_db_path() -> Path:
    """Return the canonical PCE DB path (``~/.pce/data/pce.db``)."""
    return Path.home() / ".pce" / "data" / "pce.db"


def baseline_ts() -> float:
    """Return ``time.time()`` (real UTC seconds since epoch).

    Use this — never PowerShell ``Get-Date -UFormat %s``, which on
    Windows PowerShell 5.x returns local-time-as-seconds-since-epoch
    and produces a baseline 8h ahead in UTC+8 timezones, breaking
    every ``WHERE created_at >= ?`` query downstream.
    """
    return time.time()


def count_completions(
    since_ts: float,
    host: str = "claude.ai",
    *,
    db_path: Optional[Path] = None,
) -> int:
    """Count distinct ``/completion`` request pairs since ``since_ts``."""
    db = db_path or default_db_path()
    con = sqlite3.connect(str(db))
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT COUNT(*) AS c FROM raw_captures "
            "WHERE created_at >= ? AND host=? "
            "  AND path LIKE '%/completion' AND direction='request'",
            (since_ts, host),
        ).fetchone()
        return int(row["c"]) if row else 0
    finally:
        con.close()


def latest_completion_pair_id(
    since_ts: float,
    host: str = "claude.ai",
    *,
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Return the most recent ``/completion`` request pair_id since
    ``since_ts``, or None if no such request exists yet."""
    db = db_path or default_db_path()
    con = sqlite3.connect(str(db))
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT pair_id, created_at FROM raw_captures "
            "WHERE created_at >= ? AND host=? "
            "  AND path LIKE '%/completion' AND direction='request' "
            "ORDER BY created_at DESC LIMIT 1",
            (since_ts, host),
        ).fetchone()
        return row["pair_id"] if row else None
    finally:
        con.close()


def wait_completion_response(
    pair_id: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """Wait until the response row for ``pair_id`` appears in
    raw_captures, then return a small dict with ``status_code``,
    ``body_len``, ``body_format``. Returns None on timeout.

    A response row that has any non-zero body length is considered
    "done" — mitmproxy waits for the full SSE stream to terminate
    before persisting (under ``stream_large_bodies=1m`` for bodies
    smaller than 1 MB, which is every Claude completion seen so far).
    """
    db = db_path or default_db_path()
    deadline = time.time() + timeout
    while time.time() < deadline:
        con = sqlite3.connect(str(db))
        try:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT status_code, body_format, length(body_text_or_json) AS L "
                "FROM raw_captures "
                "WHERE pair_id=? AND direction='response'",
                (pair_id,),
            ).fetchone()
            if row:
                return {
                    "status_code": row["status_code"],
                    "body_format": row["body_format"],
                    "body_len": int(row["L"]),
                }
        finally:
            con.close()
        time.sleep(poll_interval)
    return None


def wait_for_new_completion(
    since_ts: float,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    host: str = "claude.ai",
    db_path: Optional[Path] = None,
) -> Optional[str]:
    """Wait for a NEW ``/completion`` request to appear since
    ``since_ts``. Returns its pair_id, or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = latest_completion_pair_id(since_ts, host=host, db_path=db_path)
        if pid:
            return pid
        time.sleep(poll_interval)
    return None
