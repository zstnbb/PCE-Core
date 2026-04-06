# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for PCE Desktop Application.

Build with:
    pyinstaller pce.spec

This produces a single-folder distribution in dist/PCE/
containing the PCE executable and all dependencies.
"""

import os
from pathlib import Path

block_cipher = None

# Collect dashboard static files
dashboard_dir = Path("pce_core/dashboard")
dashboard_datas = []
if dashboard_dir.is_dir():
    for f in dashboard_dir.rglob("*"):
        if f.is_file():
            dashboard_datas.append(
                (str(f), str(f.parent.relative_to(".")))
            )

# Collect browser extension (for reference/install)
ext_dir = Path("pce_browser_extension")
ext_datas = []
if ext_dir.is_dir():
    for f in ext_dir.rglob("*"):
        if f.is_file():
            ext_datas.append(
                (str(f), str(f.parent.relative_to(".")))
            )

a = Analysis(
    ["pce.py"],
    pathex=["."],
    binaries=[],
    datas=dashboard_datas + ext_datas,
    hiddenimports=[
        "pce_core",
        "pce_core.server",
        "pce_core.config",
        "pce_core.db",
        "pce_core.models",
        "pce_core.redact",
        "pce_core.normalizer",
        "pce_core.normalizer.base",
        "pce_core.normalizer.registry",
        "pce_core.normalizer.pipeline",
        "pce_core.normalizer.openai",
        "pce_core.normalizer.anthropic",
        "pce_core.local_hook",
        "pce_core.local_hook.hook",
        "pce_app",
        "pce_app.service_manager",
        "pce_app.tray",
        "pce_mcp",
        "pce_mcp.server",
        "pce_proxy",
        "pce_proxy.addon",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "pydantic",
        "starlette",
        "httpx",
        "pystray",
        "PIL",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    console=False,  # No console window – runs as tray app
    icon=None,  # TODO: add .ico file
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
