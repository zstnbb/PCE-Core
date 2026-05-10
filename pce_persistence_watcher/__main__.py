# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher – CLI entry point.

Usage::

    # One-shot scan: emit all newly-seen records to PCE, exit.
    python -m pce_persistence_watcher scan

    # Continuous watch: same scan, repeated every --poll-interval seconds.
    python -m pce_persistence_watcher watch --poll-interval 5

    # Discover only: print what's installed + what we'd scan, no writes.
    python -m pce_persistence_watcher discover --json

Exit codes:

- ``0``    success (always for ``discover``; for ``scan``/``watch`` when
           at least one discovered install was scanned without fatal errors)
- ``2``    usage error (handled by argparse)
- ``3``    no target applications discovered on this machine
- ``130``  interrupted by Ctrl+C

Per ADR-018 §3.4 this package is read-only and tolerant of missing
state — an exit code of 0 + "0 records" is a valid outcome.
"""

from __future__ import annotations

import json
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

from pce_core.db import init_db

from . import __version__
from .agent_sessions import iter_records as iter_agent_session_records
from .capture import ChromiumStateObserver
from .config import WatcherConfig, parse_argv
from .discovery import AppInstall, discover, summarise
from .leveldb_reader import LevelDbUnavailable, backend_info, iter_records as iter_ldb_records

logger = logging.getLogger("pce.persistence_watcher")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    cfg = parse_argv(argv)
    _configure_logging(cfg)

    sys.stderr.write(
        f"pce-persistence-watcher v{__version__} — mode={cfg.mode} "
        f"apps_filter={cfg.apps or '(all)'} only={cfg.only or '(all)'}\n"
    )

    installs = discover(app_filter=cfg.apps or None)
    if not installs:
        sys.stderr.write(
            "pce-persistence-watcher: no target applications discovered. "
            "If you expected one, re-run with --verbose to see what was checked.\n"
        )
        if cfg.mode == "discover":
            _print_discover_summary(cfg, installs)
            return 0
        return 3

    if cfg.mode == "discover":
        _print_discover_summary(cfg, installs)
        return 0

    # From here on we need a PCE DB connection.
    _init_db_safe(cfg.db_path)

    rc = 0
    if cfg.mode == "scan":
        rc = _run_scan_pass(cfg, installs)
    elif cfg.mode == "watch":
        rc = _run_watch_loop(cfg, installs)
    else:  # pragma: no cover — argparse already guards
        sys.stderr.write(f"pce-persistence-watcher: unknown mode {cfg.mode}\n")
        return 2

    return rc


# ---------------------------------------------------------------------------
# Scan pass
# ---------------------------------------------------------------------------


def _run_scan_pass(cfg: WatcherConfig, installs: list[AppInstall]) -> int:
    """Run one full scan across all discovered installs.

    Returns 0 on success, non-zero only if every install failed
    catastrophically.
    """
    overall: dict[str, dict[str, int]] = {}
    any_success = False

    for inst in installs:
        sys.stderr.write(
            f"  → scanning {inst.app_id} ({inst.channel} v{inst.version or '?'})\n"
        )
        state_path = cfg.state_path or _default_state_path(cfg)
        observer = ChromiumStateObserver(
            app_id=inst.app_id,
            app_version=inst.version,
            app_channel=inst.channel,
            db_path=cfg.db_path,
            state_path=state_path,
            dry_run=cfg.dry_run,
            include_bodies=cfg.include_bodies,
        )

        try:
            _scan_install(inst, observer, cfg)
            any_success = True
        except Exception as exc:
            logger.exception("fatal scan error for %s: %s", inst.app_id, exc)
        finally:
            observer.flush_state()
            overall[inst.app_id] = dict(observer.stats)

    _print_scan_summary(cfg, overall)
    return 0 if any_success else 4


def _scan_install(
    inst: AppInstall,
    observer: ChromiumStateObserver,
    cfg: WatcherConfig,
) -> None:
    """Run each source parser for one install against one observer."""
    only = cfg.only

    # ── agent_sessions + skills (free, no binary deps) ──
    if only in (None, "agent_sessions", "skills"):
        ag_root = inst.root("agent_sessions")
        if ag_root is None:
            if cfg.verbose:
                sys.stderr.write("    (no agent_sessions root for this install)\n")
        else:
            n = 0
            for rec in iter_agent_session_records(ag_root):
                if only == "skills" and rec.kind != "skills_catalogue":
                    continue
                if only == "agent_sessions" and rec.kind != "session":
                    continue
                observer.observe_agent_session(rec)
                n += 1
            if cfg.verbose:
                sys.stderr.write(f"    agent_sessions records parsed: {n}\n")

    # ── LevelDB (Local Storage + IndexedDB) ── optional binary backend
    if only in (None, "leveldb"):
        for storage_kind, root_name in (
            ("local_storage", "local_storage_leveldb"),
            ("indexeddb", "indexeddb"),
        ):
            ldb_root = inst.root(root_name)
            if ldb_root is None:
                if cfg.verbose:
                    sys.stderr.write(f"    ({root_name} not present)\n")
                continue

            # ``indexeddb`` directory contains PER-ORIGIN subdirs like
            # ``https_claude.ai_0.indexeddb.leveldb``. Enumerate and
            # scan each one.
            targets: list[tuple[Optional[str], Path]] = []
            if storage_kind == "indexeddb":
                for child in ldb_root.iterdir():
                    if child.is_dir() and child.name.endswith(".leveldb"):
                        origin = _extract_indexeddb_origin(child.name)
                        targets.append((origin, child))
            else:
                targets.append((None, ldb_root))

            for origin, target in targets:
                try:
                    n = 0
                    for rec in iter_ldb_records(target):
                        observer.observe_leveldb(
                            rec, storage_kind=storage_kind, origin=origin,
                        )
                        n += 1
                    if cfg.verbose:
                        sys.stderr.write(
                            f"    leveldb {storage_kind} {origin or '<default>'}: {n} records\n"
                        )
                except LevelDbUnavailable:
                    if cfg.verbose:
                        sys.stderr.write(
                            "    leveldb backend unavailable; install 'plyvel-ci' "
                            "to enable Local Storage / IndexedDB capture "
                            "(ADR-018 §3.4 still delivers via JSON sources)\n"
                        )
                    break  # same backend absence holds for all targets
                except Exception as exc:
                    logger.warning(
                        "leveldb scan failed for %s (%s): %s",
                        target, storage_kind, exc,
                    )


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------


_SHUTDOWN = False


def _on_signal(signum, frame) -> None:  # pragma: no cover — signal handler
    global _SHUTDOWN
    _SHUTDOWN = True


def _run_watch_loop(cfg: WatcherConfig, installs: list[AppInstall]) -> int:
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    sys.stderr.write(
        f"  watch mode: polling every {cfg.poll_interval_s}s. Ctrl+C to stop.\n"
    )

    pass_ix = 0
    while not _SHUTDOWN:
        pass_ix += 1
        if cfg.verbose:
            sys.stderr.write(f"  pass {pass_ix} starting ...\n")
        _run_scan_pass(cfg, installs)

        # Sleep in small slices so Ctrl+C is responsive.
        deadline = time.time() + cfg.poll_interval_s
        while not _SHUTDOWN and time.time() < deadline:
            time.sleep(min(0.5, max(0.0, deadline - time.time())))

    sys.stderr.write("  watch loop exiting cleanly.\n")
    return 0 if pass_ix > 0 else 130


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_discover_summary(cfg: WatcherConfig, installs: list[AppInstall]) -> None:
    summary = summarise(installs)
    summary["leveldb_backend"] = backend_info()
    if cfg.json_output:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
    else:
        if not installs:
            sys.stdout.write("no target applications found on this machine.\n")
            return
        sys.stdout.write(f"discovered {len(installs)} install(s):\n")
        for i in installs:
            sys.stdout.write(
                f"  - {i.app_id} [{i.channel}] v{i.version or '?'}\n"
                f"      install_location: {i.install_location}\n"
            )
            for name, p in i.roots.items():
                flag = "✓" if p.exists() else "✗"
                sys.stdout.write(f"      {flag} {name}: {p}\n")
        ldb = backend_info()
        sys.stdout.write(
            f"\nleveldb backend: {ldb['backend'] or '(none; pip install plyvel-ci)'}\n"
        )


def _print_scan_summary(cfg: WatcherConfig, overall: dict[str, dict[str, int]]) -> None:
    if cfg.json_output:
        sys.stdout.write(json.dumps({"installs": overall}, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return
    for app_id, stats in overall.items():
        sys.stderr.write(
            f"  [{app_id}] seen={stats.get('records_seen', 0)} "
            f"emitted={stats.get('records_emitted', 0)} "
            f"deduped={stats.get('records_deduped', 0)} "
            f"failures={stats.get('capture_failures', 0)}\n"
        )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _default_state_path(cfg: WatcherConfig) -> Path:
    """Resolve the dedup state file location.

    Priority:
    1. explicit ``--state-path``
    2. ``<cfg.db_path.parent>/persistence_watcher_state.json`` if db_path set
    3. ``<pce_core.config.DATA_DIR>/persistence_watcher_state.json``
    """
    if cfg.state_path:
        return cfg.state_path
    if cfg.db_path:
        return cfg.db_path.parent / "persistence_watcher_state.json"
    try:
        from pce_core.config import DATA_DIR
        return DATA_DIR / "persistence_watcher_state.json"
    except Exception:  # pragma: no cover — defensive
        return Path.home() / ".pce" / "persistence_watcher_state.json"


def _configure_logging(cfg: WatcherConfig) -> None:
    root = logging.getLogger("pce.persistence_watcher")
    root.setLevel(logging.DEBUG if cfg.verbose else logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")
    )
    # Avoid duplicate handlers when main() is called multiple times in tests.
    if not any(isinstance(x, logging.StreamHandler) for x in root.handlers):
        root.addHandler(h)


def _init_db_safe(db_path: Optional[Path]) -> bool:
    try:
        init_db(db_path)
        return True
    except Exception as exc:
        sys.stderr.write(f"  WARNING: DB init failed ({exc}); captures will NOT persist.\n")
        return False


def _extract_indexeddb_origin(dir_name: str) -> Optional[str]:
    """Best-effort decode Chromium's per-origin IndexedDB dir name.

    Format is typically ``<scheme>_<host>_<port>.indexeddb.leveldb`` —
    e.g. ``https_claude.ai_0.indexeddb.leveldb`` → ``https://claude.ai``.
    Returns None for shapes we don't recognise.
    """
    core = dir_name
    if core.endswith(".indexeddb.leveldb"):
        core = core[: -len(".indexeddb.leveldb")]
    if "_" not in core:
        return None
    parts = core.split("_")
    if len(parts) < 3:
        return None
    scheme = parts[0]
    host = parts[1]
    port = parts[2]
    port_suffix = "" if port in ("0", "") else f":{port}"
    return f"{scheme}://{host}{port_suffix}"


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
