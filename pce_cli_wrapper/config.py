# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper.config – CLI argument parsing and runtime config.

This module is the single source of truth for what knobs the wrapper
exposes. Every other module reads from a ``WrapperConfig`` instance
rather than re-parsing argv.

Modes::

    install     Generate / refresh the wrapper shim files in the PCE
                bin directory and print PATH-prepend guidance.
    uninstall   Remove the wrapper shim files from the PCE bin
                directory.
    status      Diagnose: which CLI agents are discoverable, whether
                the wrapper is on PATH, where rows would land.
    relay       Spawn a child CLI invocation under stdio tee and
                write one capture row to PCE on exit. This is the
                mode the wrapper shim re-execs into.

For ``relay`` the argv layout is::

    python -m pce_cli_wrapper relay --target <abs-path-to-shim> -- <child args ...>

The ``--`` separator is mandatory: arguments after it are forwarded
verbatim to the child and never interpreted by argparse.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Public config dataclass
# ---------------------------------------------------------------------------


@dataclass
class WrapperConfig:
    """Resolved CLI configuration. Treat as immutable after parse."""

    mode: str  # "install" | "uninstall" | "status" | "relay"

    # Common
    db_path: Optional[Path] = None
    bin_dir: Optional[Path] = None
    targets: list[str] = field(default_factory=list)  # which shims to wrap; empty = all known
    verbose: bool = False
    json_output: bool = False
    dry_run: bool = False

    # Relay-only
    target: Optional[Path] = None        # absolute path of the real shim to invoke
    child_args: list[str] = field(default_factory=list)
    capture_label: Optional[str] = None  # human label for the row, e.g. "claude-code"
    max_body_bytes: int = 1_000_000      # cap per-stream transcript at 1 MiB by default
    timeout_s: Optional[float] = None    # if set, force-kill child after this many seconds


# ---------------------------------------------------------------------------
# argparse front-end
# ---------------------------------------------------------------------------


def parse_argv(argv: list[str]) -> WrapperConfig:
    """Parse a CLI argv into a ``WrapperConfig``.

    Tolerates the ``relay --target <p> -- <child args>`` layout: any
    argv after ``--`` is captured verbatim into ``child_args``.
    """
    # Pre-split argv on ``--`` so argparse never sees the child's flags.
    if "--" in argv:
        idx = argv.index("--")
        head, tail = argv[:idx], argv[idx + 1:]
    else:
        head, tail = list(argv), []

    parser = argparse.ArgumentParser(
        prog="pce-cli-wrapper",
        description="ADR-018 Phase 4 — H1 CLI wrapper for AI agents.",
    )
    parser.add_argument(
        "mode",
        choices=("install", "uninstall", "status", "relay"),
        help="What to do.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Override PCE sqlite DB path (defaults to PCE_DATA_DIR/pce.db).",
    )
    parser.add_argument(
        "--bin-dir",
        type=Path,
        help=(
            "Directory where the wrapper shim files are placed. Defaults "
            "to %%LOCALAPPDATA%%\\PCE\\bin on Windows or ~/.pce/bin on POSIX."
        ),
    )
    parser.add_argument(
        "--target",
        type=Path,
        help="(relay) Absolute path of the real CLI shim to invoke.",
    )
    parser.add_argument(
        "--targets",
        action="append",
        default=[],
        help=(
            "(install/status) Restrict to these target ids "
            "(repeatable, e.g. --targets claude-code). Empty = all known."
        ),
    )
    parser.add_argument(
        "--label",
        dest="capture_label",
        help="(relay) Human label stored in meta (default: derived from target).",
    )
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=1_000_000,
        help="(relay) Hard cap on per-stream captured bytes (default 1 MiB).",
    )
    parser.add_argument(
        "--timeout",
        dest="timeout_s",
        type=float,
        help="(relay) Force-kill the child after N seconds (default: no timeout).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose stderr logging.",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="(status) Emit JSON instead of human-readable output.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "(install/uninstall) Print what would happen without "
            "touching the filesystem."
        ),
    )

    ns = parser.parse_args(head)

    return WrapperConfig(
        mode=ns.mode,
        db_path=ns.db_path,
        bin_dir=ns.bin_dir,
        targets=list(ns.targets),
        target=ns.target,
        child_args=tail,
        capture_label=ns.capture_label,
        max_body_bytes=int(ns.max_body_bytes),
        timeout_s=ns.timeout_s,
        verbose=bool(ns.verbose),
        json_output=bool(ns.json_output),
        dry_run=bool(ns.dry_run),
    )


# ---------------------------------------------------------------------------
# Default bin directory
# ---------------------------------------------------------------------------


def default_bin_dir() -> Path:
    """Return the OS-appropriate default ``bin/`` directory.

    Honours ``PCE_CLI_WRAPPER_BIN_DIR`` env var if set so CI / tests can
    redirect; otherwise:

    - Windows: ``%LOCALAPPDATA%\\PCE\\bin``
    - POSIX:   ``~/.pce/bin``
    """
    import os
    import sys

    override = os.environ.get("PCE_CLI_WRAPPER_BIN_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "PCE" / "bin"
    return Path.home() / ".pce" / "bin"
