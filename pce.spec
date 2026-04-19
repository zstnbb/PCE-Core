# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""PyInstaller spec file for PCE Desktop Application (cross-platform).

Build with:
    pyinstaller pce.spec

Output (per platform):
    Windows:  dist/PCE/PCE.exe         (console=False tray app)
    macOS:    dist/PCE.app             (.app bundle)
    Linux:    dist/PCE/PCE             (executable + folder layout)

P5.A-11 cross-platform overhaul:

- ``sys.platform`` gates the Windows-specific flags (``win_no_prefer_*``,
  the pystray Win32 backend, the ``.ico`` lookup) and the macOS
  ``BUNDLE`` step. Linux falls out naturally.
- **mitmproxy is now INCLUDED** — v1.0 L1 network capture depends on
  it, so shipping without it would defeat the product. The legacy
  exclusion only made sense during the ``pce_app`` tray-only iteration
  before the proxy became part of the one-click default. ``mitmproxy``
  adds ~90 MB to the bundle but saves every user a separate pip install.
- Still-excluded: ``tkinter`` / ``matplotlib`` / ``numpy`` / ``scipy`` /
  ``pandas`` / ``IPython`` / ``jupyter`` / ``notebook`` / ``pytest``.
  These are dev-only deps pulled in by transitive imports; excluding
  them trims ~120 MB of wheel data we never load at runtime.
- Icons: optional ``assets/icons/pce.ico`` / ``assets/icons/pce.icns``
  — if present, bundled into the binary; otherwise a no-icon build
  proceeds without failing the release pipeline.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# PyInstaller walks static imports but misses: (a) late-imports inside
# lazily-loaded functions, (b) submodules that the parent package
# discovers via ``pkgutil.iter_modules`` at runtime, (c) entry-point
# plugin targets (mitmproxy addons). ``collect_submodules`` handles the
# tree walk; the explicit tail-list covers modules whose *parent*
# package is imported statically but which would otherwise be pruned.

_hidden = (
    collect_submodules("pce_core")
    + collect_submodules("pce_app")
    + collect_submodules("pce_proxy")
    + collect_submodules("pce_mcp")
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    + collect_submodules("anyio")
    + collect_submodules("mitmproxy")
    + [
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "multiprocessing",
        "multiprocessing.resource_tracker",
        "multiprocessing.sharedctypes",
        # Tray backends — pystray picks the right one at import time.
        "pystray",
    ]
)
if _IS_WIN:
    _hidden += ["pystray._win32"]
elif _IS_MAC:
    _hidden += ["pystray._darwin", "AppKit", "Foundation", "objc"]
elif _IS_LINUX:
    _hidden += ["pystray._gtk", "pystray._appindicator"]

# ---------------------------------------------------------------------------
# Bundled data (dashboard HTML/JS/CSS, migrations, etc.)
# ---------------------------------------------------------------------------
_datas = [
    ("pce_core/dashboard", "pce_core/dashboard"),
    ("pce_core/migrations", "pce_core/migrations"),
]

# ---------------------------------------------------------------------------
# Icon resolution — optional so the build doesn't hard-fail if icons
# haven't been authored yet.
# ---------------------------------------------------------------------------
_icon_path: str | None = None
_assets_dir = Path("assets") / "icons"
if _IS_WIN:
    candidate = _assets_dir / "pce.ico"
    if candidate.is_file():
        _icon_path = str(candidate)
elif _IS_MAC:
    candidate = _assets_dir / "pce.icns"
    if candidate.is_file():
        _icon_path = str(candidate)

# ---------------------------------------------------------------------------
# Analysis — shared across platforms
# ---------------------------------------------------------------------------
a = Analysis(
    ["pce.py"],
    pathex=["."],
    binaries=[],
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev-only transitively-imported heavyweights.
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# EXE — headless/tray on Windows, plain executable on Linux, .app on macOS
# ---------------------------------------------------------------------------
# ``console=False`` on Windows keeps a double-click from spawning a cmd
# window behind the tray icon. Linux keeps the console so journalctl /
# stdout logging stays visible when a user runs the binary from a
# terminal. macOS wraps the binary in a .app bundle below, so the
# console flag is moot there.
_console = False if _IS_WIN else (_IS_LINUX or False)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PCE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX compression shrinks the bundle ~30% but occasionally confuses
    # Windows Defender. Keep it on by default; release.yml can flip it
    # off via env if first-run AV scans start flagging the binary.
    upx=True,
    console=_console,
    icon=_icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PCE",
)

# ---------------------------------------------------------------------------
# macOS app-bundle wrapper
# ---------------------------------------------------------------------------
# The COLLECT step above produces a ``dist/PCE/`` folder; on macOS we
# additionally wrap it in ``dist/PCE.app`` so users can drag it into
# /Applications. ``LSUIElement=True`` hides the Dock icon (tray app).
if _IS_MAC:
    app = BUNDLE(  # noqa: F841 — BUNDLE is a PyInstaller directive
        coll,
        name="PCE.app",
        icon=_icon_path,
        bundle_identifier="dev.zstnbb.pce",
        info_plist={
            "CFBundleName": "PCE",
            "CFBundleDisplayName": "PCE – AI Capture",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "LSUIElement": True,  # Tray-only, no Dock icon
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": (
                "PCE uses Apple Events to toggle the system proxy."
            ),
        },
    )
