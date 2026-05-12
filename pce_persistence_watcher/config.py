# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.config – CLI argument parsing + runtime config.

The watcher has two run modes:

- ``scan``   — one-shot: discover installs, parse all currently-visible
               sources, emit new (not-yet-seen) captures to PCE, exit.
- ``watch``  — continuous: same as ``scan`` but re-runs on a polling
               interval and deduplicates against the state file.
- ``discover`` — print what's found, do NOT emit captures, exit.

All three modes share the same ``WatcherConfig`` dataclass so the
observer and pipeline code stay mode-agnostic.

Why hand-rolled argparse instead of ``click``: matches the rest of
``pce_*`` packages (``pce_mcp_proxy.config``, ``pce_proxy.config``) —
no extra runtime dep, shorter stack traces when parsing fails.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------


@dataclass
class WatcherConfig:
    """Parsed CLI flags for the persistence watcher."""

    # Run mode: one of {"scan", "watch", "discover"}.
    mode: str = "scan"

    # Optional override for the data directory PCE writes to. When None,
    # the package falls back to ``pce_core.config.DATA_DIR``.
    db_path: Optional[Path] = None

    # Sidecar state file used by the dedup layer. Defaults to
    # ``<pce-data>/persistence_watcher_state.json``.
    state_path: Optional[Path] = None

    # Polling interval for ``watch`` mode, seconds.
    poll_interval_s: float = 5.0

    # Optional list of explicit target applications to scan. Empty ⇒
    # scan everything ``discovery.py`` finds installed.
    apps: list[str] = field(default_factory=list)

    # Optional filter: restrict scanning to a single source category
    # (one of: "agent_sessions", "skills", "leveldb", "config",
    # "transcripts", "code_tab"). Useful for debugging a single parser
    # without the noise of the rest. P5.B.5.3 added "transcripts"
    # (cowork + code-tab JSONL lines); P5.B.7 added "code_tab"
    # (code-tab-only: JSONL + pointer).
    only: Optional[str] = None

    # When True, show detailed per-source tracing on stderr.
    verbose: bool = False

    # When True, do not write any captures — useful with ``watch`` to
    # measure the watcher's overhead without populating the DB.
    dry_run: bool = False

    # When True, include the raw body text of each record in the capture
    # payload. Default True for v0; may be gated by privacy policy later.
    include_bodies: bool = True

    # When True, print a JSON summary of the run to stdout on exit.
    # stdout is free for this package (unlike pce_mcp_proxy which has
    # to keep it clear for MCP framing).
    json_output: bool = False


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


_MODES = ("scan", "watch", "discover")
_SOURCE_CATEGORIES = (
    "agent_sessions",
    "skills",
    "leveldb",
    "config",
    # P5.B.5.3 (2026-05-11): cowork + code-tab JSONL transcript lines.
    "transcripts",
    # P5.B.7 (2026-05-11): code-tab only (JSONL + pointer).
    "code_tab",
    # P5.B.7.P2 (2026-05-12): Code-tab sub-agent JSONLs (Task tool
    # spawn products) — composite session_id with parent link.
    "code_subagents",
    # P5.B.7.P2 (2026-05-12): user-home Claude Code state surfaces
    # (~/.claude.json + ~/.claude/{settings*.json,todos/*.json,
    # history.jsonl}). Secret-scrubbed at walker boundary.
    "user_state",
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pce_persistence_watcher",
        description=(
            "PCE Local Persistence Watcher (UCS L3g, ADR-018). "
            "Extracts AI interaction records from files that closed-source "
            "Electron AI apps persist under the user profile, where "
            "wire-level capture (L3b / L3d / L1) is blocked by the MSIX "
            "distribution channel."
        ),
        epilog=(
            "Examples:\n"
            "  python -m pce_persistence_watcher discover\n"
            "  python -m pce_persistence_watcher scan --verbose\n"
            "  python -m pce_persistence_watcher watch --poll-interval 10\n"
            "  python -m pce_persistence_watcher scan --only agent_sessions --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "mode",
        choices=_MODES,
        help="Run mode. `discover` is read-only (no captures emitted).",
    )
    p.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override PCE sqlite DB path (default: pce_core.config.DB_PATH).",
    )
    p.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help=(
            "Path to the dedup state JSON "
            "(default: <pce-data>/persistence_watcher_state.json)."
        ),
    )
    p.add_argument(
        "--poll-interval",
        dest="poll_interval_s",
        type=float,
        default=5.0,
        help="Polling interval in seconds for `watch` mode (default: 5.0).",
    )
    p.add_argument(
        "--app",
        dest="apps",
        action="append",
        default=[],
        help=(
            "Restrict to a specific target app. Repeatable. "
            "Known names: 'claude-desktop', 'chatgpt-desktop'. "
            "If omitted, all discovered installs are scanned."
        ),
    )
    p.add_argument(
        "--only",
        choices=_SOURCE_CATEGORIES,
        default=None,
        help=(
            "Restrict scanning to one source category for debugging."
        ),
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Emit per-record trace on stderr.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse sources but do NOT emit captures to PCE.",
    )
    p.add_argument(
        "--no-bodies",
        dest="include_bodies",
        action="store_false",
        help=(
            "Drop record bodies from emitted captures (metadata only). "
            "Useful for small-storage environments."
        ),
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print a JSON summary of the run to stdout on exit.",
    )
    return p


def parse_argv(argv: list[str]) -> WatcherConfig:
    """Parse a list of CLI tokens into a ``WatcherConfig``.

    Environment overrides:

    - ``PCE_WATCHER_DB_PATH`` overrides ``--db-path`` (flag wins if both set).
    - ``PCE_WATCHER_STATE_PATH`` overrides ``--state-path``.
    """
    parser = _build_parser()
    ns = parser.parse_args(argv)

    db_path = ns.db_path
    if db_path is None:
        env = os.environ.get("PCE_WATCHER_DB_PATH")
        if env:
            db_path = Path(env)

    state_path = ns.state_path
    if state_path is None:
        env = os.environ.get("PCE_WATCHER_STATE_PATH")
        if env:
            state_path = Path(env)

    return WatcherConfig(
        mode=ns.mode,
        db_path=db_path,
        state_path=state_path,
        poll_interval_s=float(ns.poll_interval_s),
        apps=list(ns.apps or []),
        only=ns.only,
        verbose=bool(ns.verbose),
        dry_run=bool(ns.dry_run),
        include_bodies=bool(ns.include_bodies),
        json_output=bool(ns.json_output),
    )
