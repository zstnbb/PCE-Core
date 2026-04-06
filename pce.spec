# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for PCE Desktop Application.

Build with:
    pyinstaller pce.spec

Output: dist/PCE/PCE.exe  (double-click to launch)

NOTE: mitmproxy is excluded from the build (too large).
The network proxy service will show as unavailable in the packaged app.
All other services (Core API, Local Hook, Dashboard) work normally.
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Collect all submodules for key packages
hiddens = (
    collect_submodules("pce_core")
    + collect_submodules("pce_app")
    + collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    + collect_submodules("anyio")
    + [
        "pce_mcp",
        "pce_mcp.server",
        "pce_proxy",
        "pystray",
        "pystray._win32",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "multiprocessing",
        "multiprocessing.resource_tracker",
        "multiprocessing.sharedctypes",
    ]
)

a = Analysis(
    ["pce.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("pce_core/dashboard", "pce_core/dashboard"),
    ],
    hiddenimports=hiddens,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "mitmproxy",
        "mitmproxy_rs",
        "mitmproxy_macos",
        "mitmproxy_windows",
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PCE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window – tray app
    icon=None,      # TODO: add .ico file
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
