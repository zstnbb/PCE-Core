# SPDX-License-Identifier: Apache-2.0
"""pce_mcp_proxy – CLI argument parsing and runtime configuration.

The proxy is invoked as::

    python -m pce_mcp_proxy [proxy-flags] -- <upstream-cmd> [upstream-args...]

The literal ``--`` separator is required when the upstream command has
flags of its own (which is almost always — ``npx -y @scope/server``,
``python -m mcp_server``, etc.). Anything before ``--`` is parsed as
proxy options; anything after is passed verbatim to ``subprocess.Popen``.

Examples::

    # Wrap a filesystem server
    python -m pce_mcp_proxy -- npx -y @modelcontextprotocol/server-filesystem /tmp

    # Wrap a Python MCP server with a friendly name
    python -m pce_mcp_proxy --upstream-name git -- python -m mcp_git

    # Use a non-default DB path
    python -m pce_mcp_proxy --data-dir D:/pce/data -- npx -y @scope/server
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RelayConfig:
    """Resolved configuration for one proxy invocation."""

    upstream_argv: list[str] = field(default_factory=list)
    upstream_name: str = "unknown"
    db_path: Optional[Path] = None
    print_stats: bool = False
    log_file: Optional[Path] = None
    quiet: bool = False


def split_at_dashdash(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first literal ``--`` token.

    Returns (before_dashdash, after_dashdash). If ``--`` is absent, the
    whole argv is treated as proxy flags and upstream is empty (caller
    will surface a usage error).
    """
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1:]
    return list(argv), []


def derive_upstream_name(upstream_argv: list[str]) -> str:
    """Infer a human-readable label for the upstream server.

    Heuristic: pick the most distinctive token from the upstream argv,
    preferring a package-like specifier (``@scope/name``) over a
    runner (``npx`` / ``python``). Falls back to the first arg's
    basename, falls back to ``"unknown"`` if the argv is empty.
    """
    if not upstream_argv:
        return "unknown"

    # Look for a token that looks like an npm package, a Python
    # module-style path, or a script path.
    for tok in upstream_argv[1:]:
        if tok.startswith("@") and "/" in tok:
            # @scope/name → name
            return tok.split("/", 1)[1].split("@", 1)[0]
        if tok.startswith("-"):
            continue  # skip flags like -y / --foo
        if tok in ("-y", "-q", "--yes"):
            continue
        # Looks like a substantive token
        base = os.path.basename(tok)
        if base and not base.startswith("-"):
            return base.replace(".js", "").replace(".py", "")

    # Fall back to first arg's basename
    base = os.path.basename(upstream_argv[0])
    return base or "unknown"


def parse_argv(argv: list[str]) -> RelayConfig:
    """Parse a CLI invocation into a :class:`RelayConfig`.

    Raises :class:`SystemExit` via argparse on bad input or when the
    user requests ``--help``.
    """
    proxy_args, upstream_argv = split_at_dashdash(argv)

    parser = argparse.ArgumentParser(
        prog="python -m pce_mcp_proxy",
        description=(
            "Transparent MCP middleware proxy: forwards host ↔ upstream "
            "JSON-RPC stdio frames while side-channelling each frame into "
            "the PCE capture pipeline (UCS L3f, posture B)."
        ),
        epilog=(
            "Use a literal '--' before the upstream command, e.g.:\n"
            "  python -m pce_mcp_proxy -- npx -y @modelcontextprotocol/server-filesystem /tmp\n"
            "  python -m pce_mcp_proxy --upstream-name git -- python -m mcp_git\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--upstream-name",
        default=None,
        help=(
            "Friendly label used as the 'provider' tag (mcp:<name>) on "
            "captures from this proxy. Defaults to a heuristic over the "
            "upstream argv."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Override PCE data directory (writes pce.db inside it). "
            "Defaults to the PCE_DATA_DIR env var or the standard "
            "platform-specific user data path used by pce_core.db."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Path for the proxy's own diagnostic log. Defaults to "
            "<data-dir>/mcp_proxy.log when --data-dir is given, else "
            "stderr only."
        ),
    )
    parser.add_argument(
        "--print-stats",
        action="store_true",
        help=(
            "On exit, print a one-line summary to stderr: total frames "
            "observed, requests, responses, notifications, errors, "
            "orphans. Useful for verifying capture coverage against an "
            "upstream session."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "Suppress the proxy's own startup banner on stderr. Capture "
            "still happens; only diagnostics are silenced."
        ),
    )

    ns = parser.parse_args(proxy_args)

    cfg = RelayConfig(
        upstream_argv=upstream_argv,
        upstream_name=ns.upstream_name or derive_upstream_name(upstream_argv),
        db_path=_resolve_db_path(ns.data_dir),
        print_stats=ns.print_stats,
        log_file=Path(ns.log_file) if ns.log_file else None,
        quiet=ns.quiet,
    )
    return cfg


def _resolve_db_path(data_dir_opt: Optional[str]) -> Optional[Path]:
    """Resolve the SQLite path from --data-dir or environment.

    Returns ``None`` when neither is set, in which case ``pce_core.db``
    will fall back to its own default (``DATA_DIR / 'pce.db'``).
    """
    if data_dir_opt:
        return Path(data_dir_opt) / "pce.db"
    env = os.environ.get("PCE_DATA_DIR")
    if env:
        return Path(env) / "pce.db"
    return None
