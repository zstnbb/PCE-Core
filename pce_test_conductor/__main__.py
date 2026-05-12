# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor entry point.

Three usage modes:

    python -m pce_test_conductor                   # stdio MCP server (default)
    python -m pce_test_conductor --sse             # SSE MCP server (dev / browser smoke)
    python -m pce_test_conductor --list-targets    # CLI introspection (no MCP)
    python -m pce_test_conductor --tool <name> --args '<json>'   # direct tool call

The first two are the canonical agent surfaces (Cascade / Claude Desktop /
Claude Code mount the conductor via MCP). The CLI flags are operator
ergonomics — useful for nightly CI smoke checks (P5.C.3) and for human
debugging without spinning up an MCP client.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .server import ALL_TOOL_NAMES, TOOL_DISPATCH


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )


def _cli_list_targets() -> int:
    """Print a small human-readable target list. Exit 0 always."""
    result = TOOL_DISPATCH["list_targets"](include_health=True)
    targets = result.get("targets", [])
    if not targets:
        print("(no targets registered)")
        return 0
    print(f"{len(targets)} target(s) registered:\n")
    for t in targets:
        color = t.get("health_color", "grey")
        plane = "+".join(t.get("plane", []))
        print(
            f"  [{color:^6}] {t['target_id']:<28} "
            f"lane={t['lane']:<8} tier={t['tier']:<3} "
            f"plane={plane:<6} layer={t['primary_layer']}"
        )
    return 0


def _cli_run_tool(name: str, args_json: str) -> int:
    """Direct tool call. Prints JSON result to stdout, exits 0/1 on error key."""
    if name not in TOOL_DISPATCH:
        print(
            f"unknown tool {name!r}; available: {', '.join(ALL_TOOL_NAMES)}",
            file=sys.stderr,
        )
        return 2
    try:
        kwargs: dict[str, Any] = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as exc:
        print(f"--args must be JSON object: {exc!r}", file=sys.stderr)
        return 2
    result = TOOL_DISPATCH[name](**kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if not isinstance(result, dict) or "error" not in result else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pce_test_conductor")
    p.add_argument("--sse", action="store_true", help="run MCP over SSE (dev only)")
    p.add_argument("--stdio", action="store_true", help="run MCP over stdio (default)")
    p.add_argument("--list-targets", action="store_true",
                   help="print registered targets and exit")
    p.add_argument("--tool", help=f"directly invoke one tool: {', '.join(ALL_TOOL_NAMES)}")
    p.add_argument("--args", default="",
                   help="JSON-object kwargs for --tool")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    # CLI introspection paths — exit before importing FastMCP.
    if args.list_targets:
        return _cli_list_targets()
    if args.tool:
        return _cli_run_tool(args.tool, args.args)

    # MCP server modes — import FastMCP only here so CLI-only flows
    # don't pay the import cost (and tests don't need the mcp dep).
    from .server import get_mcp
    mcp = get_mcp()
    transport = "sse" if args.sse else "stdio"
    logging.getLogger("pce.conductor").info("starting MCP server (transport=%s)", transport)
    mcp.run(transport=transport)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
