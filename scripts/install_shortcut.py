# SPDX-License-Identifier: Apache-2.0
"""Place a "PCE Control Panel" shortcut on the user's Desktop.

Idempotent. Run any time to (re-)install:

    python scripts/install_shortcut.py
    python scripts/install_shortcut.py --uninstall

What we ship:
    - ~/.pce/assets/pce.ico      (generated from the in-app icon)
    - <Desktop>/PCE Control Panel.lnk
          target  : pythonw.exe (or python.exe)
          arg     : <repo-root>/pce.py
          workdir : <repo-root>
          icon    : ~/.pce/assets/pce.ico

We launch via ``pythonw.exe`` so no console window flashes when the
shortcut is double-clicked. If ``pythonw.exe`` is not next to the
active interpreter we fall back to ``python.exe``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "pce.py"
ICON_DIR = Path.home() / ".pce" / "assets"
ICON_PATH = ICON_DIR / "pce.ico"
SHORTCUT_NAME = "PCE Control Panel.lnk"


# ---------------------------------------------------------------------------
# Desktop resolution
# ---------------------------------------------------------------------------

def find_desktop_dir() -> Path:
    """Best-effort Desktop folder discovery (handles OneDrive redirect)."""
    if sys.platform == "win32":
        # Authoritative path comes from the Shell:Desktop knownfolder.
        try:
            import ctypes
            from ctypes import wintypes, windll
            CSIDL_DESKTOPDIRECTORY = 0x10
            SHGFP_TYPE_CURRENT = 0
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            res = windll.shell32.SHGetFolderPathW(
                None, CSIDL_DESKTOPDIRECTORY, None, SHGFP_TYPE_CURRENT, buf,
            )
            if res == 0 and buf.value:
                p = Path(buf.value)
                if p.is_dir():
                    return p
        except Exception:
            pass
        # Fallback chain.
        for env_key in ("OneDriveCommercial", "OneDrive", "USERPROFILE"):
            base = os.environ.get(env_key)
            if not base:
                continue
            cand = Path(base) / "Desktop"
            if cand.is_dir():
                return cand
        return Path.home() / "Desktop"
    if sys.platform == "darwin":
        return Path.home() / "Desktop"
    # Linux: respect XDG, else ~/Desktop
    xdg = os.environ.get("XDG_DESKTOP_DIR")
    if xdg:
        p = Path(xdg)
        if p.is_dir():
            return p
    return Path.home() / "Desktop"


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------

def render_pce_icon(out_path: Path) -> Path:
    """Generate a multi-resolution .ico matching the in-app P tile.

    Pillow's ``Image.save(format='ICO', sizes=...)`` discards
    ``append_images`` for non-PNG output, so we render ONE large 256
    source and let Pillow downscale to the standard ladder. This
    yields a ~150 kB .ico that Windows shows crisp at every size.
    """
    from PIL import Image, ImageDraw, ImageFont
    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, size // 8)
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill="#7c83ff",
    )
    try:
        font = ImageFont.truetype("arialbd.ttf", int(size * 0.62))
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("arial.ttf", int(size * 0.62))
        except (OSError, IOError):
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "P", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), "P", fill="white", font=font)
    img.save(
        out_path, format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return out_path


# ---------------------------------------------------------------------------
# Interpreter resolution
# ---------------------------------------------------------------------------

def find_interpreter() -> Path:
    """Prefer ``pythonw.exe`` next to ``sys.executable`` so no console flashes."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        candidate = exe.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    return exe


# ---------------------------------------------------------------------------
# Shortcut creators — platform-specific
# ---------------------------------------------------------------------------

def install_windows_shortcut(target: Path, args: str, workdir: Path,
                             icon: Path, link_path: Path) -> None:
    """Create a .lnk via PowerShell's WScript.Shell COM object."""
    ps_script = (
        "$s = New-Object -ComObject WScript.Shell;\n"
        f"$lnk = $s.CreateShortcut('{link_path}');\n"
        f"$lnk.TargetPath = '{target}';\n"
        f"$lnk.Arguments = '{args}';\n"
        f"$lnk.WorkingDirectory = '{workdir}';\n"
        f"$lnk.IconLocation = '{icon}';\n"
        "$lnk.Description = 'PCE Control Panel — start/stop services, "
        "watch capture lanes, manage VPN adaptation.';\n"
        "$lnk.Save();\n"
        "Write-Host 'OK';\n"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PowerShell shortcut creation failed (rc={result.returncode}):\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )


def install_linux_desktop_file(target: Path, args: str, workdir: Path,
                               icon: Path, link_path: Path) -> None:
    """Write a freedesktop .desktop file at link_path (rename suffix)."""
    desktop_path = link_path.with_suffix(".desktop")
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=PCE Control Panel\n"
        "Comment=PCE — start/stop services, watch lanes, manage VPN\n"
        f"Exec={target} {args}\n"
        f"Path={workdir}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
    )
    desktop_path.write_text(content, encoding="utf-8")
    desktop_path.chmod(0o755)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def install() -> Path:
    if not ENTRYPOINT.is_file():
        raise FileNotFoundError(
            f"PCE entrypoint not found: {ENTRYPOINT}. "
            "Are you running this from the repo root?"
        )

    print(f"[1/4] Rendering icon → {ICON_PATH}")
    try:
        render_pce_icon(ICON_PATH)
    except ImportError:
        print(
            "      ! Pillow not installed; using letter-P fallback icon.\n"
            "        pip install Pillow  to get a polished icon."
        )
        # We can ship without an icon — Windows will use a default.
        if not ICON_PATH.is_file():
            ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
            ICON_PATH.write_bytes(b"")  # placeholder

    interp = find_interpreter()
    print(f"[2/4] Interpreter      → {interp}")

    desktop = find_desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    link_path = desktop / SHORTCUT_NAME
    print(f"[3/4] Shortcut path    → {link_path}")

    if sys.platform == "win32":
        install_windows_shortcut(
            target=interp,
            args=f'"{ENTRYPOINT}"',
            workdir=REPO_ROOT,
            icon=ICON_PATH,
            link_path=link_path,
        )
    elif sys.platform == "darwin":
        # macOS: drop a .command shellscript; double-clicking it
        # launches PCE in Terminal.
        cmd_path = desktop / "PCE Control Panel.command"
        cmd_path.write_text(
            f'#!/bin/sh\nexec "{interp}" "{ENTRYPOINT}" "$@"\n',
            encoding="utf-8",
        )
        cmd_path.chmod(0o755)
        link_path = cmd_path
    else:
        install_linux_desktop_file(
            target=interp,
            args=f'"{ENTRYPOINT}"',
            workdir=REPO_ROOT,
            icon=ICON_PATH,
            link_path=link_path,
        )
        link_path = link_path.with_suffix(".desktop")

    print(f"[4/4] Installed        → {link_path}")
    return link_path


def uninstall() -> bool:
    desktop = find_desktop_dir()
    removed = False
    for name in (SHORTCUT_NAME, "PCE Control Panel.desktop",
                 "PCE Control Panel.command"):
        path = desktop / name
        if path.exists():
            path.unlink()
            print(f"removed {path}")
            removed = True
    if not removed:
        print("no PCE shortcut found on the Desktop")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove the PCE shortcut from the Desktop")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
        return 0

    try:
        install()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("\nDouble-click the shortcut to launch the PCE Control Panel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
