# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper – CLI entry point.

Usage::

    python -m pce_cli_wrapper status [--json]
    python -m pce_cli_wrapper install [--bin-dir <path>] [--targets <id>]
    python -m pce_cli_wrapper uninstall [--bin-dir <path>] [--targets <id>]
    python -m pce_cli_wrapper relay --target <path> [--label <id>] -- <args...>

Exit codes:

- ``0``    success (mode-specific success criteria — see below)
- ``2``    usage error (handled by argparse)
- ``3``    install / uninstall: no targets actionable on this host
- ``127``  relay: ``--target`` missing or unreadable
- ``<n>``  relay: child's own exit code (passed through verbatim)
- ``130``  Ctrl+C interrupt during status / install
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from pce_core.db import init_db

from . import __version__
from .config import WrapperConfig, default_bin_dir, parse_argv
from .discovery import discover, summarise
from .install import InstallReport, install as do_install, uninstall as do_uninstall
from .relay import relay as do_relay

logger = logging.getLogger("pce.cli_wrapper")


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    cfg = parse_argv(argv)
    _configure_logging(cfg)

    if cfg.mode == "status":
        return _cmd_status(cfg)
    if cfg.mode == "install":
        return _cmd_install(cfg)
    if cfg.mode == "uninstall":
        return _cmd_uninstall(cfg)
    if cfg.mode == "relay":
        return _cmd_relay(cfg)
    sys.stderr.write(f"pce-cli-wrapper: unknown mode {cfg.mode}\n")
    return 2


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _cmd_status(cfg: WrapperConfig) -> int:
    targets = discover(target_filter=cfg.targets or None)
    bin_dir = cfg.bin_dir or default_bin_dir()
    summary = summarise(targets)
    summary["bin_dir"] = str(bin_dir)
    summary["bin_dir_exists"] = bin_dir.exists()
    summary["wrapper_version"] = __version__

    if cfg.json_output:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(
        f"pce-cli-wrapper v{__version__}\n"
        f"  bin_dir: {bin_dir} (exists={bin_dir.exists()})\n"
        f"  platform: {summary['platform']}\n"
        f"  installed targets: {summary['count']}\n"
    )
    for t in summary["targets"]:
        flag = "✓" if t["installed"] else "✗"
        sys.stdout.write(
            f"    {flag} {t['target_id']} [{t['command_name']}] "
            f"v{t['version'] or '?'}\n"
        )
        if t["shim_path"]:
            sys.stdout.write(f"        shim: {t['shim_path']}\n")
    return 0


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------


def _cmd_install(cfg: WrapperConfig) -> int:
    bin_dir = cfg.bin_dir or default_bin_dir()
    report = do_install(
        bin_dir=bin_dir,
        target_filter=cfg.targets or None,
        dry_run=cfg.dry_run,
    )
    return _print_install_report(cfg, report, "install")


def _cmd_uninstall(cfg: WrapperConfig) -> int:
    bin_dir = cfg.bin_dir or default_bin_dir()
    report = do_uninstall(
        bin_dir=bin_dir,
        target_filter=cfg.targets or None,
        dry_run=cfg.dry_run,
    )
    return _print_install_report(cfg, report, "uninstall")


def _print_install_report(
    cfg: WrapperConfig,
    report: InstallReport,
    verb: str,
) -> int:
    if cfg.json_output:
        sys.stdout.write(json.dumps({
            "mode": verb,
            "bin_dir": str(report.bin_dir),
            "on_path": report.on_path,
            "actions": [
                {
                    "target_id": a.target_id,
                    "action": a.action,
                    "path": str(a.path),
                    "reason": a.reason,
                }
                for a in report.actions
            ],
            "path_guidance": report.path_guidance,
            "dry_run": cfg.dry_run,
        }, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(f"pce-cli-wrapper {verb} → {report.bin_dir}\n")
        for a in report.actions:
            note = f" ({a.reason})" if a.reason else ""
            sys.stdout.write(f"  [{a.action}] {a.path}{note}\n")
        if not report.on_path and verb == "install":
            sys.stdout.write("\nPATH guidance — prepend the bin_dir for this shell:\n")
            for line in report.path_guidance:
                sys.stdout.write(f"  {line}\n")

    actionable = [a for a in report.actions if a.action in ("create", "remove")]
    return 0 if actionable else 3


# ---------------------------------------------------------------------------
# relay
# ---------------------------------------------------------------------------


def _cmd_relay(cfg: WrapperConfig) -> int:
    if cfg.target is None:
        sys.stderr.write("pce-cli-wrapper relay: --target is required\n")
        return 127
    target_path = cfg.target.expanduser()
    if not target_path.exists():
        sys.stderr.write(f"pce-cli-wrapper relay: target missing: {target_path}\n")
        return 127

    # Initialise the DB lazily so a stale capture row never blocks the
    # child's exit code.
    if not cfg.dry_run:
        try:
            init_db(cfg.db_path)
        except Exception as exc:
            sys.stderr.write(
                f"pce-cli-wrapper relay: db init failed ({exc}); "
                "captures will NOT persist for this run\n"
            )

    return do_relay(
        target_path=target_path,
        child_args=list(cfg.child_args),
        target_id=None,                # let relay derive via discovery
        capture_label=cfg.capture_label,
        max_body_bytes=cfg.max_body_bytes,
        timeout_s=cfg.timeout_s,
        db_path=cfg.db_path,
        dry_run=cfg.dry_run,
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _configure_logging(cfg: WrapperConfig) -> None:
    root = logging.getLogger("pce.cli_wrapper")
    root.setLevel(logging.DEBUG if cfg.verbose else logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")
    )
    if not any(isinstance(x, logging.StreamHandler) for x in root.handlers):
        root.addHandler(h)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
