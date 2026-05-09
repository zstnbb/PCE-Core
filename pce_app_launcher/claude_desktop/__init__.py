# SPDX-License-Identifier: Apache-2.0
"""Claude Desktop variant of the pce_app_launcher.

Public surface (re-exported for convenience):

- ``detect_claude_desktop()``   — locate Claude.exe / Claude.app
- ``launch_claude_desktop(...)`` — spawn with ``--remote-debugging-port``
- ``CaptureBridge``             — Playwright-attached CDP listener
- ``install_shortcut(...)``     — install/uninstall PCE-wrapped launcher

See :mod:`pce_app_launcher` for context and ADR-016 §3.2 for design.
"""

from .detector import detect_claude_desktop, ClaudeDesktopInstall  # noqa: F401
from .launcher import launch_claude_desktop, LauncherHandle  # noqa: F401
from .capture_bridge import CaptureBridge, build_capture_event  # noqa: F401
from .shortcut import install_shortcut, uninstall_shortcut  # noqa: F401

__all__ = [
    "detect_claude_desktop",
    "ClaudeDesktopInstall",
    "launch_claude_desktop",
    "LauncherHandle",
    "CaptureBridge",
    "build_capture_event",
    "install_shortcut",
    "uninstall_shortcut",
]
