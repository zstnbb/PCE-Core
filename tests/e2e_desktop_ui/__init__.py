# SPDX-License-Identifier: Apache-2.0
"""End-to-end desktop UI automation against installed AI desktop apps.

Distinct from ``tests/e2e_desktop/`` (which holds hermetic unit tests for
``pce_app/``'s Tauri launcher / CLI / detector / capture_bridge / shortcut).

This package drives **the actual installed AI desktop application's UI**
(Claude Desktop, ChatGPT Desktop, ...) via Windows UI Automation +
SendInput, then verifies the resulting capture/normalize/persist
pipeline (raw_captures -> sessions -> messages tables) end-to-end.

Why a separate package: Electron Fuses (ADR-018 H4 LOCKED) closed CDP /
NODE_OPTIONS / --inspect on MSIX-packaged Electron AI apps, so the
existing browser-extension probe framework (tests/e2e_probe/) cannot be
reused. This package implements a UIA + SendInput driver that works
against MSIX Electron apps that have accessibility tree exposed via
Chromium NativeViewAccessibility.

Layout::

    tests/e2e_desktop_ui/
        drivers/
            base.py                  Abstract DesktopDriver
            claude_desktop.py        Claude Desktop concrete impl
            chatgpt_desktop.py       (future)
        cases/
            p1_chat_window_a.py      D03 multi-turn + D07 code block + D04 cancel
            p1_chat_window_b_d11.py  D11 long-context (50 turns, >=8K tokens)
            p1_chat_window_c_d12.py  D12 silent on idle (5 min + 10 s)
            p1_chat_window_d_d06.py  D06 attachment (CSV via clipboard CF_HDROP)
            p1_chat_window_e_d10.py  D10 mid-stream proxy kill + restart
        utils.py                     Shared focus / click / clipboard / DB-poll helpers

Each case is a standalone Python module runnable via::

    python -m tests.e2e_desktop_ui.cases.p1_chat_window_a
    python -m tests.e2e_desktop_ui.cases.p1_chat_window_b_d11
    python -m tests.e2e_desktop_ui.cases.p1_chat_window_c_d12
    python -m tests.e2e_desktop_ui.cases.p1_chat_window_d_d06
    python -m tests.e2e_desktop_ui.cases.p1_chat_window_e_d10

Companion inspector scripts at the repo root
(``_inspect_window_<a..e>.py``) directly query ``~/.pce/data/pce.db`` and
emit per-case PASS/FAIL verdicts. They are scratch-grade and live at
repo root rather than under tests/ to keep them easy to delete /
regenerate as the schema evolves.

Cases write progress to stdout and verdict + counters to stdout/stderr.
They do NOT use pytest fixtures (the AI desktop app's state is shared
mutable global which doesn't fit pytest's per-test isolation model).
"""
