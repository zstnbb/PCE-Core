# SPDX-License-Identifier: Apache-2.0
"""pce_mcp_proxy – CLI entry point.

Usage::

    python -m pce_mcp_proxy [proxy-flags] -- <upstream-cmd> [upstream-args...]

See ``pce_mcp_proxy.config`` for the full flag reference.

Exit codes:

- ``0..255``      forwarded from the upstream MCP server
- ``2``           usage error (missing upstream command)
- ``126``         upstream binary found but failed to launch
- ``127``         upstream binary not found on PATH
- ``130``         interrupted by SIGINT before upstream started

Per the package boundary contract (see ``__init__.py``), this entry
point and the relay it spawns MUST keep the host's MCP session
working even if PCE itself misbehaves: any DB / config / logging
failure is downgraded to a stderr warning and the relay carries on.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from pce_core.db import init_db

from .capture import JsonRpcObserver
from .config import RelayConfig, parse_argv
from .relay import Relay

logger = logging.getLogger("pce.mcp_proxy")


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry. Returns the proxy exit code."""
    if argv is None:
        argv = sys.argv[1:]

    cfg = parse_argv(argv)

    if not cfg.upstream_argv:
        sys.stderr.write(
            "pce-mcp-proxy: no upstream command provided.\n"
            "Usage: python -m pce_mcp_proxy [opts] -- <upstream> [args...]\n"
            "Run 'python -m pce_mcp_proxy --help' for the full flag list.\n"
        )
        return 2

    _configure_logging(cfg)

    if not cfg.quiet:
        sys.stderr.write(
            f"pce-mcp-proxy: relaying upstream={cfg.upstream_name!r} "
            f"argv={cfg.upstream_argv!r}\n"
        )
        sys.stderr.flush()

    # Initialise PCE DB. If it fails we keep going with a no-op
    # observer so the host's MCP session stays alive.
    db_ok = _init_db_safe(cfg.db_path)
    observer = JsonRpcObserver(
        upstream_name=cfg.upstream_name,
        db_path=cfg.db_path,
    )
    if not db_ok and not cfg.quiet:
        sys.stderr.write(
            "pce-mcp-proxy: WARNING — DB init failed; capture disabled "
            "for this session, relay continues.\n"
        )
        sys.stderr.flush()

    relay = Relay(cfg.upstream_argv, observer)
    rc = relay.run()

    if cfg.print_stats:
        sys.stderr.write(f"pce-mcp-proxy: stats {observer.stats}\n")
        sys.stderr.flush()

    return rc


def _init_db_safe(db_path: Optional[Path]) -> bool:
    try:
        init_db(db_path)
        return True
    except Exception as exc:  # pragma: no cover — defensive guard
        logger.warning(
            "mcp_proxy.db_init_failed",
            extra={
                "event": "mcp_proxy.db_init_failed",
                "pce_fields": {"error": repr(exc)},
            },
        )
        return False


def _configure_logging(cfg: RelayConfig) -> None:
    """Wire up the proxy's own structured-log file if requested.

    The relay deliberately does NOT call into ``pce_core.logging`` so
    we don't accidentally pull stdout-targeting handlers into a process
    where stdout is the protocol channel. We attach a single FileHandler
    when the user asks for one and otherwise let stderr do.
    """
    log_path = cfg.log_file
    if log_path is None and cfg.db_path is not None:
        log_path = cfg.db_path.parent / "mcp_proxy.log"

    root = logging.getLogger("pce.mcp_proxy")
    root.setLevel(logging.INFO)

    # Always attach a stderr handler — it makes errors visible in the
    # host's log surface without polluting stdout.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")
    )
    root.addHandler(stderr_handler)

    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(name)s] %(levelname)s %(message)s"
                )
            )
            root.addHandler(file_handler)
        except OSError:
            # Permission / path issues should not break the relay.
            pass


if __name__ == "__main__":
    sys.exit(main())
