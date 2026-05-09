# SPDX-License-Identifier: Apache-2.0
"""pce_app_launcher CLI entry point.

Subcommands::

    python -m pce_app_launcher detect   [--app=claude-desktop]
    python -m pce_app_launcher run      [--app=claude-desktop] [--port=9222] [--no-bridge]
    python -m pce_app_launcher status   [--app=claude-desktop]
    python -m pce_app_launcher install-shortcut   [--target=desktop|start_menu]
    python -m pce_app_launcher uninstall-shortcut [--target=desktop|start_menu]

Only ``claude-desktop`` is supported in v1.1 (P5.B.2 Phase 3, ADR-016
§3.7); ``cursor`` / ``windsurf`` will be added in P5.B.3.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.app_launcher.cli")


SUPPORTED_APPS = ("claude-desktop",)


def _cmd_detect(args: argparse.Namespace) -> int:
    if args.app != "claude-desktop":
        print(f"error: unsupported app {args.app!r}", file=sys.stderr)
        return 2
    from .claude_desktop.detector import detect_claude_desktop

    install = detect_claude_desktop()
    if install is None:
        print(json.dumps({
            "app": args.app, "found": False,
            "hint": "Install Claude Desktop from https://claude.ai/download",
        }, indent=2))
        return 1
    print(json.dumps({
        "app": args.app,
        "found": True,
        "exe_path": str(install.exe_path),
        "version": install.version,
        "platform": install.platform,
        "install_root": str(install.install_root) if install.install_root else None,
    }, indent=2))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if args.app != "claude-desktop":
        print(f"error: unsupported app {args.app!r}", file=sys.stderr)
        return 2

    from .claude_desktop.detector import detect_claude_desktop
    from .claude_desktop.launcher import launch_claude_desktop

    install = detect_claude_desktop()
    if install is None:
        print("error: Claude Desktop is not installed", file=sys.stderr)
        return 1

    handle = launch_claude_desktop(
        install,
        debug_port=args.port,
        auto_pick_port=not args.no_auto_port,
    )
    print(f"Claude Desktop launched (pid={handle.process.pid}); CDP {handle.cdp_endpoint}")

    bridge = None
    if not args.no_bridge:
        from .claude_desktop.capture_bridge import CaptureBridge

        bridge = CaptureBridge(
            handle.cdp_endpoint,
            pce_core_url=args.pce_core_url,
        )
        try:
            bridge.start()
        except Exception as exc:
            print(f"warning: capture bridge failed to start: {exc}", file=sys.stderr)
            bridge = None
        else:
            print(f"capture bridge attached → {args.pce_core_url}")

    print("Press Ctrl+C or close Claude Desktop to exit.")
    try:
        while handle.is_running():
            time.sleep(0.5)
            if bridge is not None and args.print_stats_interval > 0:
                # Print stats every N seconds
                pass  # left as future-only knob
    except KeyboardInterrupt:
        print("\nshutdown signal received")
    finally:
        if bridge is not None:
            print("stats:", json.dumps(bridge.snapshot(), default=str))
            bridge.stop()
        handle.terminate()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    if args.app != "claude-desktop":
        print(f"error: unsupported app {args.app!r}", file=sys.stderr)
        return 2

    from .claude_desktop.detector import detect_claude_desktop
    from .claude_desktop.shortcut import _windows_shortcut_dir
    import platform

    install = detect_claude_desktop()
    out: dict = {"app": args.app, "installed": install is not None}
    if install is not None:
        out["exe_path"] = str(install.exe_path)
        out["version"] = install.version
        out["install_root"] = str(install.install_root) if install.install_root else None

    sys_name = platform.system()
    out["platform"] = sys_name
    if sys_name == "Windows":
        sd = _windows_shortcut_dir("desktop")
        if sd is not None:
            out["desktop_shortcut_dir"] = str(sd)
            wrapped = sd / "Claude (via PCE).lnk"
            out["pce_shortcut_present"] = wrapped.is_file()

    print(json.dumps(out, indent=2))
    return 0 if install is not None else 1


def _cmd_install_shortcut(args: argparse.Namespace) -> int:
    from .claude_desktop.shortcut import install_shortcut

    result = install_shortcut(target=args.target)
    payload = {
        "ok": result.ok,
        "action": result.action,
        "shortcut_path": str(result.shortcut_path) if result.shortcut_path else None,
        "backup_path": str(result.backup_path) if result.backup_path else None,
        "fallback_instructions": result.fallback_instructions,
        "error": result.error,
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


def _cmd_uninstall_shortcut(args: argparse.Namespace) -> int:
    from .claude_desktop.shortcut import uninstall_shortcut

    result = uninstall_shortcut(target=args.target)
    payload = {
        "ok": result.ok,
        "action": result.action,
        "shortcut_path": str(result.shortcut_path) if result.shortcut_path else None,
        "backup_path": str(result.backup_path) if result.backup_path else None,
        "error": result.error,
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pce_app_launcher",
        description=(
            "L3d CDP launcher for desktop Electron AI apps. v1.1 supports "
            "Claude Desktop (ADR-016)."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_detect = sub.add_parser("detect", help="Locate the app's install path on this host.")
    p_detect.add_argument("--app", default="claude-desktop", choices=SUPPORTED_APPS)
    p_detect.set_defaults(func=_cmd_detect)

    p_run = sub.add_parser("run", help="Launch the app and attach a capture bridge.")
    p_run.add_argument("--app", default="claude-desktop", choices=SUPPORTED_APPS)
    p_run.add_argument("--port", type=int, default=9222, help="Preferred CDP debug port.")
    p_run.add_argument(
        "--no-auto-port", action="store_true",
        help="Fail (instead of falling back to ephemeral) if --port is busy.",
    )
    p_run.add_argument("--no-bridge", action="store_true", help="Skip capture; just launch.")
    p_run.add_argument(
        "--pce-core-url", default="http://127.0.0.1:9800",
        help="pce_core daemon URL the bridge POSTs captures to.",
    )
    p_run.add_argument(
        "--print-stats-interval", type=int, default=0,
        help="Print bridge stats every N seconds (0 = only on exit).",
    )
    p_run.set_defaults(func=_cmd_run)

    p_status = sub.add_parser(
        "status",
        help="Print install + shortcut status as JSON.",
    )
    p_status.add_argument("--app", default="claude-desktop", choices=SUPPORTED_APPS)
    p_status.set_defaults(func=_cmd_status)

    p_inst = sub.add_parser(
        "install-shortcut",
        help="Install a desktop shortcut that launches the app via PCE.",
    )
    p_inst.add_argument(
        "--target", default="desktop", choices=("desktop", "start_menu"),
        help="Where to put the shortcut (Windows only; ignored elsewhere).",
    )
    p_inst.set_defaults(func=_cmd_install_shortcut)

    p_uninst = sub.add_parser(
        "uninstall-shortcut",
        help="Remove the PCE-wrapped shortcut and restore the most recent backup.",
    )
    p_uninst.add_argument(
        "--target", default="desktop", choices=("desktop", "start_menu"),
    )
    p_uninst.set_defaults(func=_cmd_uninstall_shortcut)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
