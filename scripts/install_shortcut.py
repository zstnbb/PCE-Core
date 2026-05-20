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
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "pce_restore.cmd"
ICON_DIR = Path.home() / ".pce" / "assets"
ICON_PATH = ICON_DIR / "pce.ico"
RESTORE_ICON_PATH = ICON_DIR / "pce_restore.ico"
SHORTCUT_NAME = "PCE Control Panel.lnk"
RESTORE_SHORTCUT_NAME = "PCE Emergency Restore.lnk"


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

def render_emergency_icon(out_path: Path) -> Path:
    """Generate a red ⚠ tile for the emergency-restore shortcut."""
    from PIL import Image, ImageDraw, ImageFont
    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = max(2, size // 8)
    # Apple system red — same hex used by the panel's status palette.
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill="#FF3B30",
    )
    try:
        font = ImageFont.truetype("seguisym.ttf", int(size * 0.55))
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("arialbd.ttf", int(size * 0.55))
        except (OSError, IOError):
            font = ImageFont.load_default()
    # An exclamation mark — reads as warning at any size, no symbol
    # font shenanigans.
    txt = "!"
    bbox = draw.textbbox((0, 0), txt, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), txt, fill="white", font=font)
    img.save(out_path, format="ICO",
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                    (128, 128), (256, 256)])
    return out_path


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
                             icon: Path, link_path: Path,
                             description: str = "") -> None:
    """Create a .lnk via PowerShell's WScript.Shell COM object."""
    if not description:
        description = (
            "PCE Control Panel - start/stop services, "
            "watch capture lanes, manage VPN adaptation."
        )
    # Escape single quotes in description for the inline PS string.
    desc_escaped = description.replace("'", "''")
    ps_script = (
        "$s = New-Object -ComObject WScript.Shell;\n"
        f"$lnk = $s.CreateShortcut('{link_path}');\n"
        f"$lnk.TargetPath = '{target}';\n"
        f"$lnk.Arguments = '{args}';\n"
        f"$lnk.WorkingDirectory = '{workdir}';\n"
        f"$lnk.IconLocation = '{icon}';\n"
        f"$lnk.Description = '{desc_escaped}';\n"
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

    print(f"[1/5] Rendering icons → {ICON_DIR}")
    try:
        render_pce_icon(ICON_PATH)
    except ImportError:
        print(
            "      ! Pillow not installed; using letter-P fallback icon.\n"
            "        pip install Pillow  to get a polished icon."
        )
        if not ICON_PATH.is_file():
            ICON_PATH.parent.mkdir(parents=True, exist_ok=True)
            ICON_PATH.write_bytes(b"")

    # Emergency restore icon — separate red ⚠ tile so users can tell
    # them apart on the desktop at a glance.
    try:
        render_emergency_icon(RESTORE_ICON_PATH)
    except ImportError:
        if not RESTORE_ICON_PATH.is_file():
            RESTORE_ICON_PATH.write_bytes(b"")

    interp = find_interpreter()
    print(f"[2/5] Interpreter      → {interp}")

    desktop = find_desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    link_path = desktop / SHORTCUT_NAME
    restore_link_path = desktop / RESTORE_SHORTCUT_NAME
    print(f"[3/5] Shortcut path    → {link_path}")
    print(f"[4/5] Emergency restore → {restore_link_path}")

    if sys.platform == "win32":
        install_windows_shortcut(
            target=interp,
            args=f'"{ENTRYPOINT}"',
            workdir=REPO_ROOT,
            icon=ICON_PATH,
            link_path=link_path,
        )
        # The emergency restore shortcut points at the .cmd wrapper —
        # NOT at python or the .py file — because:
        #  (1) the .cmd works even if Python is uninstalled
        #  (2) the .cmd shows a console window with the result, which
        #      is exactly what we want for a recovery tool the user
        #      runs in a panic
        if RESTORE_SCRIPT.is_file():
            install_windows_shortcut(
                target=RESTORE_SCRIPT,
                args="",
                workdir=REPO_ROOT,
                icon=RESTORE_ICON_PATH if RESTORE_ICON_PATH.is_file() else ICON_PATH,
                link_path=restore_link_path,
                description=(
                    "PCE Emergency Restore - undo PCE's system-proxy "
                    "takeover. Use if your computer can't reach the "
                    "network after a PCE crash."
                ),
            )
            print(f"      [OK] emergency restore installed")
        else:
            print(f"      ! restore .cmd not found at {RESTORE_SCRIPT}")
    elif sys.platform == "darwin":
        cmd_path = desktop / "PCE Control Panel.command"
        cmd_path.write_text(
            f'#!/bin/sh\nexec "{interp}" "{ENTRYPOINT}" "$@"\n',
            encoding="utf-8",
        )
        cmd_path.chmod(0o755)
        link_path = cmd_path
        # macOS emergency restore: invoke python script via shell
        restore_path = desktop / "PCE Emergency Restore.command"
        restore_path.write_text(
            f'#!/bin/sh\nexec "{interp}" '
            f'"{REPO_ROOT / "scripts" / "pce_restore.py"}" "$@"\n',
            encoding="utf-8",
        )
        restore_path.chmod(0o755)
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
    candidates = (
        SHORTCUT_NAME,
        "PCE Control Panel.desktop",
        "PCE Control Panel.command",
        RESTORE_SHORTCUT_NAME,
        "PCE Emergency Restore.desktop",
        "PCE Emergency Restore.command",
    )
    for name in candidates:
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
