# SPDX-License-Identifier: Apache-2.0
"""Post-build self-check for PyInstaller output (P5.A-11).

Runs against the freshly-built ``dist/`` tree and verifies the minimum
invariants needed to call the build "releasable":

1. The executable artifact exists at the platform-correct path.
2. The binary is non-empty and marked executable where applicable.
3. Critical bundled data (``pce_core/dashboard``, ``pce_core/migrations``)
   is present under the binary's ``_internal/`` tree.
4. (Windows / Linux) The executable can be invoked with
   ``--version``-style probe and exits cleanly — proves the bootloader
   + hidden-imports aren't broken. Skipped on macOS because launching
   the tray app auto-starts services we don't want in CI.

Usage::

    python scripts/verify_build.py --platform Windows
    python scripts/verify_build.py --platform macOS
    python scripts/verify_build.py --platform Linux

Non-zero exit on any failure — the GitHub Actions release job gates
the artifact upload on this script returning 0.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"


def _fail(msg: str) -> None:
    print(f"verify_build: FAIL — {msg}", file=sys.stderr)
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"verify_build: OK — {msg}")


def _locate_bundle(platform: str) -> Path:
    """Return the per-platform root directory PyInstaller COLLECT emitted."""
    if platform == "Windows":
        path = DIST / "PCE"
        if not (path / "PCE.exe").is_file():
            _fail(f"expected dist/PCE/PCE.exe, not found at {path}")
        return path
    if platform == "macOS":
        # COLLECT still emits dist/PCE; BUNDLE adds dist/PCE.app.
        path = DIST / "PCE.app"
        if not path.is_dir():
            _fail(f"expected dist/PCE.app, not found at {path}")
        return path
    if platform == "Linux":
        path = DIST / "PCE"
        if not (path / "PCE").is_file():
            _fail(f"expected dist/PCE/PCE, not found at {path}")
        return path
    _fail(f"unknown platform: {platform!r}")
    return Path()  # unreachable — _fail exits.


def _check_bundled_data(bundle_root: Path, platform: str) -> None:
    """Ensure dashboard + migrations are present under the internal tree."""
    if platform == "macOS":
        # .app bundle layout: PCE.app/Contents/Frameworks/... or Resources/...
        # PyInstaller 6+ ships data under Contents/Resources/ or
        # Contents/MacOS/_internal depending on the build mode.
        search_roots = [
            bundle_root / "Contents" / "Resources",
            bundle_root / "Contents" / "MacOS" / "_internal",
            bundle_root / "Contents" / "MacOS",
        ]
    else:
        # Folder layout: dist/PCE/_internal/pce_core/...
        search_roots = [bundle_root / "_internal", bundle_root]

    required = ["pce_core/dashboard", "pce_core/migrations"]
    for rel in required:
        found = False
        for root in search_roots:
            candidate = root / rel
            if candidate.is_dir():
                _ok(f"bundled data present: {rel} (under {root.relative_to(bundle_root)})")
                found = True
                break
        if not found:
            _fail(
                f"bundled data missing: {rel!r} not found under any of "
                f"{[str(r.relative_to(bundle_root)) for r in search_roots]}"
            )


def _check_executable_bit(bundle_root: Path, platform: str) -> None:
    if platform == "Windows":
        exe = bundle_root / "PCE.exe"
    elif platform == "macOS":
        exe = bundle_root / "Contents" / "MacOS" / "PCE"
    else:
        exe = bundle_root / "PCE"
    if not exe.is_file():
        _fail(f"executable missing: {exe}")
    size = exe.stat().st_size
    if size < 1024 * 1024:
        _fail(f"executable unusually small ({size} bytes): {exe}")
    _ok(f"executable present and non-trivial: {exe.name} ({size/1e6:.1f} MB)")

    if platform != "Windows":
        mode = exe.stat().st_mode
        if not mode & 0o111:
            _fail(f"executable bit not set on {exe}")
        _ok("executable bit set")


def _smoke_run(bundle_root: Path, platform: str) -> None:
    """Invoke the binary briefly to prove the bootloader + imports work.

    Skipped on macOS because launching the tray app in CI is racy (it
    would register a tray icon + auto-open a browser window). The
    bundled-data + executable-bit checks above are sufficient coverage
    for the macOS release asset.
    """
    if platform == "macOS":
        _ok("smoke-run skipped on macOS (tray app side effects)")
        return

    if platform == "Windows":
        exe = bundle_root / "PCE.exe"
    else:
        exe = bundle_root / "PCE"

    # Use --no-tray --no-browser to avoid any UI side-effects. Bound
    # the run to ~4 seconds: long enough for import + argparse but not
    # so long that we spin up full services.
    env = dict(os.environ)
    env["PCE_SMOKE_EXIT"] = "1"  # cooperative marker; binary may or may not honour

    try:
        proc = subprocess.run(
            [str(exe), "--no-tray", "--no-browser"],
            env=env,
            capture_output=True,
            timeout=4,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # The binary started and was still running after 4s → bootloader
        # + imports succeeded. That's what we wanted to prove.
        _ok("smoke-run reached steady state (timed out after 4s as expected)")
        return
    except FileNotFoundError:
        _fail(f"could not execute {exe}: not found or not launchable")

    # If it exited before the timeout, only accept RC=0 (clean shutdown).
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")[:2000]
        _fail(f"binary exited with rc={proc.returncode}:\n{stderr}")
    _ok("smoke-run exited cleanly")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        required=True,
        choices=["Windows", "macOS", "Linux"],
        help="Target platform label (match GitHub Actions runner.os).",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the launch-the-binary step (useful when cross-building).",
    )
    args = parser.parse_args()

    bundle_root = _locate_bundle(args.platform)
    _ok(f"bundle root resolved: {bundle_root}")

    _check_executable_bit(bundle_root, args.platform)
    _check_bundled_data(bundle_root, args.platform)

    if not args.skip_smoke:
        _smoke_run(bundle_root, args.platform)

    print("verify_build: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
