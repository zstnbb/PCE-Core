# SPDX-License-Identifier: Apache-2.0
"""pce_app_launcher — L3d CDP launcher for desktop Electron AI apps.

Phase 3 of P5.B.2 (ADR-016). Launches Claude Desktop / Cursor /
Windsurf with ``--remote-debugging-port=<port>``, attaches a
Playwright CDP client, and forwards every matching network response
to ``pce_core`` over local HTTP.

Modules:

- :mod:`pce_app_launcher.claude_desktop.detector` — locate the app
  binary on Windows / macOS / Linux.
- :mod:`pce_app_launcher.claude_desktop.launcher` — spawn the app with
  the CDP debugging port flag, poll until ready.
- :mod:`pce_app_launcher.claude_desktop.capture_bridge` — Playwright
  ``connect_over_cdp(...)`` + response listener + HTTP forward.
- :mod:`pce_app_launcher.claude_desktop.shortcut` — install a desktop
  shortcut that launches the app via ``pce_app_launcher`` so users
  double-click the same icon they always did.

OSS classification: Apache-2.0, per ADR-013 + ADR-016 §3.9.
"""

__version__ = "0.1.0"
