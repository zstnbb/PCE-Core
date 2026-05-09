# SPDX-License-Identifier: Apache-2.0
"""pce_mcp_proxy.install – one-shot helper that wraps every MCP server
registered in a supported host's config file with ``pce_mcp_proxy``
(capture posture B / UCS L3f) so the user gets transparent capture of
host ↔ upstream JSON-RPC frames without hand-editing JSON.

Design invariants:

- **Never wrap pce_mcp itself.** ``pce_mcp`` (posture A) is a PCE-owned
  MCP server; wrapping it would recursively capture our own frames.
- **Idempotent.** Running install twice is a no-op; already-wrapped
  entries are detected via their ``args`` prefix (``["-m",
  "pce_mcp_proxy", ...]``).
- **Preserve everything else.** ``env``, ``cwd``, and any vendor-
  specific extra fields on the server entry are kept verbatim.
- **Backup before write.** Every mutation writes
  ``<config>.pce-backup-<ISO>.json`` alongside the original so
  ``uninstall`` can revert. Backups are additive; the user can prune
  old ones at will.
- **Dry-run prints a diff.** ``--dry-run`` returns the proposed diff
  without touching disk.

See ADR-016 §3.3 (M-plane posture C architecture).

Not in scope:

- Creating MCP host config files from scratch. Users are expected to
  have at least one MCP server already registered in the host. If not,
  the detector reports ``exists=False`` for that host.
- Editing host-specific extra state (e.g. Windsurf UI selection). We
  only touch ``mcpServers.*``.

CLI entry::

    python -m pce_mcp_proxy.install detect
    python -m pce_mcp_proxy.install install --host=claude_desktop [--dry-run]
    python -m pce_mcp_proxy.install install --all                 [--dry-run]
    python -m pce_mcp_proxy.install uninstall --host=claude_desktop
    python -m pce_mcp_proxy.install status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger("pce.mcp_proxy.install")


# ---------------------------------------------------------------------------
# Host catalog
# ---------------------------------------------------------------------------


def _home() -> Path:
    return Path.home()


def _appdata_roaming() -> Optional[Path]:
    """Windows %APPDATA% (Roaming). None on non-Windows."""
    value = os.environ.get("APPDATA")
    return Path(value) if value else None


def _xdg_config_home() -> Path:
    """Follow the XDG Base Directory spec; fall back to ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else (_home() / ".config")


@dataclass(frozen=True)
class McpHost:
    """Static descriptor for a known MCP host config location."""

    key: str
    display_name: str
    tier: str  # D0 = must-work, D1 = best-effort
    #: Ordered list of candidate config paths (first existing wins).
    path_candidates: tuple[Callable[[], Optional[Path]], ...] = field(default_factory=tuple)

    def candidates(self) -> list[Path]:
        """Resolve all candidate paths, skipping resolvers that return None."""
        out: list[Path] = []
        for resolver in self.path_candidates:
            try:
                p = resolver()
            except Exception:
                p = None
            if p is not None:
                out.append(p)
        return out

    def existing_path(self) -> Optional[Path]:
        """Return the first candidate that actually exists on disk."""
        for p in self.candidates():
            if p.is_file():
                return p
        return None


# Each host's candidate list covers every platform it's documented to run on.
# Entries marked 'unlikely' are listed for completeness; the detector filters
# to only those that actually exist on disk.

HOSTS: tuple[McpHost, ...] = (
    McpHost(
        key="claude_desktop",
        display_name="Claude Desktop",
        tier="D0",
        path_candidates=(
            # macOS
            lambda: _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            # Windows (Roaming AppData)
            lambda: (_appdata_roaming() / "Claude" / "claude_desktop_config.json") if _appdata_roaming() else None,
            # Linux (unofficial but community-documented)
            lambda: _xdg_config_home() / "Claude" / "claude_desktop_config.json",
        ),
    ),
    McpHost(
        key="cursor",
        display_name="Cursor",
        tier="D0",
        path_candidates=(
            lambda: _home() / ".cursor" / "mcp.json",
        ),
    ),
    McpHost(
        key="windsurf",
        display_name="Windsurf",
        tier="D1",
        path_candidates=(
            lambda: _home() / ".codeium" / "windsurf" / "mcp_config.json",
        ),
    ),
    McpHost(
        key="claude_code",
        display_name="Claude Code",
        tier="D0",
        path_candidates=(
            lambda: _home() / ".claude" / "mcp_config.json",
            # Claude Code also supports ~/.claude.json with a different shape;
            # we only touch files whose top level has "mcpServers".
            lambda: _home() / ".claude.json",
        ),
    ),
    McpHost(
        key="codex_cli",
        display_name="Codex CLI",
        tier="D1",
        path_candidates=(
            lambda: _home() / ".openai" / "codex" / "mcp_config.json",
            lambda: _xdg_config_home() / "openai-codex" / "mcp.json",
        ),
    ),
    McpHost(
        key="gemini_cli",
        display_name="Gemini CLI",
        tier="D1",
        path_candidates=(
            lambda: _xdg_config_home() / "gemini" / "mcp_config.json",
            lambda: _home() / ".gemini" / "mcp_config.json",
        ),
    ),
    McpHost(
        key="cascade_windsurf",
        display_name="Cascade in Windsurf (self-test)",
        tier="self-test",
        path_candidates=(
            # Same path as windsurf — listed separately so users can target
            # the self-test install from Docs/install/PCE_MCP_INSTALL.md §7
            # by name.
            lambda: _home() / ".codeium" / "windsurf" / "mcp_config.json",
        ),
    ),
)


HOSTS_BY_KEY: dict[str, McpHost] = {h.key: h for h in HOSTS}


# ---------------------------------------------------------------------------
# Wrap / unwrap logic
# ---------------------------------------------------------------------------


PROXY_MODULE = "pce_mcp_proxy"
# pce_mcp itself must never be wrapped — it's our own posture-A server.
# We detect it by the args signature, not the user-chosen map key.
_PCE_MCP_ARGS = ("-m", "pce_mcp")


def _is_already_wrapped(entry: dict) -> bool:
    """Return True if this server entry already routes through pce_mcp_proxy."""
    args = entry.get("args") or []
    return (
        len(args) >= 2
        and args[0] == "-m"
        and args[1] == PROXY_MODULE
    )


def _is_pce_mcp_self(entry: dict) -> bool:
    """Return True if this entry IS pce_mcp (posture A). Must not be wrapped."""
    args = entry.get("args") or []
    return (
        len(args) >= 2
        and tuple(args[:2]) == _PCE_MCP_ARGS
    )


def wrap_server_entry(
    name: str,
    entry: dict,
    *,
    python_exe: Optional[str] = None,
) -> tuple[dict, str]:
    """Return (new_entry, action) where action is one of:

    - ``"wrapped"``     — freshly transformed to route through the proxy
    - ``"already"``     — already proxy-wrapped, no change
    - ``"skip_self"``   — it's pce_mcp itself, never wrap
    - ``"skip_shape"``  — entry has no ``command`` (e.g. SSE/HTTP-only
      servers use ``url`` field; we don't wrap transport-layer hosts
      because stdio is the only thing pce_mcp_proxy handles).
    """
    if not isinstance(entry, dict):
        return entry, "skip_shape"

    if _is_pce_mcp_self(entry):
        return entry, "skip_self"

    if _is_already_wrapped(entry):
        return entry, "already"

    # Some hosts (Windsurf, Cursor) let users register a remote SSE/HTTP
    # MCP server without a local command. We can't wrap those — stdio is
    # the only transport pce_mcp_proxy handles.
    if "command" not in entry or not entry.get("command"):
        return entry, "skip_shape"

    upstream_cmd = entry["command"]
    upstream_args = list(entry.get("args") or [])

    new_entry: dict = dict(entry)  # preserve env / cwd / extras
    new_entry["command"] = python_exe or sys.executable
    new_entry["args"] = [
        "-m",
        PROXY_MODULE,
        "--upstream-name",
        name,
        "--",
        upstream_cmd,
        *upstream_args,
    ]
    return new_entry, "wrapped"


def unwrap_server_entry(entry: dict) -> tuple[dict, str]:
    """Reverse ``wrap_server_entry``. Used by ``uninstall --no-backup`` fallback.

    Returns (new_entry, action). Prefers restore-from-backup over this
    pure-string unwrap when a backup is available.
    """
    if not _is_already_wrapped(entry):
        return entry, "noop"

    args = list(entry.get("args") or [])
    # args layout: ["-m", "pce_mcp_proxy", ["--upstream-name", <name>]?, "--", <cmd>, ...]
    try:
        sep = args.index("--")
    except ValueError:
        return entry, "malformed"
    tail = args[sep + 1:]
    if not tail:
        return entry, "malformed"

    new_entry = dict(entry)
    new_entry["command"] = tail[0]
    new_entry["args"] = tail[1:]
    return new_entry, "unwrapped"


# ---------------------------------------------------------------------------
# Config read / write / backup
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict:
    """Load a host's MCP config. Missing file → empty dict."""
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        text = fh.read()
    if not text.strip():
        return {}
    return json.loads(text)


def write_config(path: Path, cfg: dict) -> None:
    """Serialise config with a stable two-space indent + trailing newline.

    Deterministic output matters so re-running install on an unchanged
    config produces no git-diff-visible churn for users versioning their
    MCP configs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cfg, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _backup_suffix() -> str:
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    return f".pce-backup-{ts}.json"


def backup_path_for(config_path: Path) -> Path:
    """Compute a fresh backup path (timestamp-unique). Does not touch disk."""
    return config_path.with_name(config_path.name + _backup_suffix())


def list_backups(config_path: Path) -> list[Path]:
    """Find existing pce-backup-* siblings of config_path, oldest first."""
    parent = config_path.parent
    if not parent.is_dir():
        return []
    prefix = config_path.name + ".pce-backup-"
    out = [p for p in parent.iterdir() if p.name.startswith(prefix)]
    out.sort(key=lambda p: p.name)
    return out


def write_backup(config_path: Path) -> Optional[Path]:
    """Copy current config to a fresh ``.pce-backup-<ts>.json``.

    Returns the backup path, or None if the source doesn't exist.
    """
    if not config_path.is_file():
        return None
    backup = backup_path_for(config_path)
    # Guarantee no collision even if two backups happen in the same second.
    counter = 0
    while backup.exists():
        counter += 1
        backup = config_path.with_name(
            config_path.name + _backup_suffix().rstrip(".json") + f".{counter}.json"
        )
    backup.write_bytes(config_path.read_bytes())
    return backup


# ---------------------------------------------------------------------------
# Install / uninstall / diff at the config-object level
# ---------------------------------------------------------------------------


@dataclass
class PlannedChange:
    """What an install/uninstall run proposes to do for one host."""

    host: McpHost
    config_path: Path
    config_exists: bool
    original_cfg: dict
    new_cfg: dict
    actions: dict[str, str]  # server_name → action label from wrap_server_entry

    @property
    def wrapped_count(self) -> int:
        return sum(1 for a in self.actions.values() if a == "wrapped")

    @property
    def already_count(self) -> int:
        return sum(1 for a in self.actions.values() if a == "already")

    @property
    def skipped_count(self) -> int:
        return sum(1 for a in self.actions.values() if a.startswith("skip_"))

    @property
    def has_changes(self) -> bool:
        return self.wrapped_count > 0

    def diff_summary(self) -> str:
        lines = [
            f"[{self.host.key}] {self.config_path}",
            f"  wrapped={self.wrapped_count} already={self.already_count} "
            f"skipped={self.skipped_count}",
        ]
        for name, action in sorted(self.actions.items()):
            lines.append(f"    {name}: {action}")
        return "\n".join(lines)


def plan_install(
    host: McpHost,
    *,
    python_exe: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> PlannedChange:
    """Compute the install delta for one host without touching disk."""
    cfg_path = config_path or host.existing_path() or (host.candidates()[0] if host.candidates() else Path("(none)"))
    exists = cfg_path.is_file()
    original = load_config(cfg_path) if exists else {}
    new_cfg = json.loads(json.dumps(original))  # deep copy

    servers = new_cfg.get("mcpServers") or {}
    actions: dict[str, str] = {}
    if not isinstance(servers, dict):
        # Unknown shape → leave as-is; report a single skip
        actions["<config>"] = "skip_shape"
    else:
        for name, entry in list(servers.items()):
            new_entry, action = wrap_server_entry(name, entry, python_exe=python_exe)
            servers[name] = new_entry
            actions[name] = action
        if servers:
            new_cfg["mcpServers"] = servers

    return PlannedChange(
        host=host,
        config_path=cfg_path,
        config_exists=exists,
        original_cfg=original,
        new_cfg=new_cfg,
        actions=actions,
    )


def apply_install(plan: PlannedChange, *, dry_run: bool = False) -> Optional[Path]:
    """Write the planned change to disk after backing up. Returns backup path."""
    if not plan.config_exists:
        raise FileNotFoundError(
            f"Config for host '{plan.host.key}' does not exist at {plan.config_path}. "
            f"Register at least one MCP server in the host first, then re-run install."
        )
    if not plan.has_changes:
        return None  # no-op
    if dry_run:
        return None
    backup = write_backup(plan.config_path)
    write_config(plan.config_path, plan.new_cfg)
    return backup


def restore_latest_backup(config_path: Path) -> Optional[Path]:
    """Restore the newest pce-backup-* sibling over config_path.

    Returns the backup path that was used, or None if no backup exists.
    """
    backups = list_backups(config_path)
    if not backups:
        return None
    latest = backups[-1]
    config_path.write_bytes(latest.read_bytes())
    return latest


def plan_uninstall(
    host: McpHost,
    *,
    config_path: Optional[Path] = None,
) -> tuple[Path, Optional[Path]]:
    """Compute what uninstall would do. Returns (config_path, latest_backup_or_None)."""
    cfg_path = config_path or host.existing_path()
    if cfg_path is None:
        raise FileNotFoundError(f"No config file found for host '{host.key}'")
    backups = list_backups(cfg_path)
    latest = backups[-1] if backups else None
    return cfg_path, latest


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class DetectedHost:
    host: McpHost
    config_path: Optional[Path]
    exists: bool
    server_count: int

    def as_row(self) -> str:
        mark = "[*]" if self.exists else "[ ]"
        label = f"{self.host.display_name} [{self.host.tier}]"
        path = str(self.config_path) if self.config_path else "(none)"
        servers = f"{self.server_count} server(s)" if self.exists else "not installed"
        return f"  {mark} {label:40s} {servers:15s} {path}"


def detect_hosts() -> list[DetectedHost]:
    """Scan every known host; report whether its config exists + server count."""
    out: list[DetectedHost] = []
    seen: set[Path] = set()
    for host in HOSTS:
        existing = host.existing_path()
        if existing is not None and existing in seen:
            # Windsurf + cascade_windsurf share the same file — include both
            # rows (for CLI targeting by key) but don't double-count servers.
            pass
        candidate = existing or (host.candidates()[0] if host.candidates() else None)
        server_count = 0
        if existing:
            try:
                cfg = load_config(existing)
                servers = cfg.get("mcpServers") or {}
                server_count = len(servers) if isinstance(servers, dict) else 0
            except Exception:
                server_count = 0
            seen.add(existing)
        out.append(
            DetectedHost(
                host=host,
                config_path=candidate,
                exists=existing is not None,
                server_count=server_count,
            )
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_detect(_args: argparse.Namespace) -> int:
    rows = detect_hosts()
    print("Detected MCP hosts:")
    for r in rows:
        print(r.as_row())
    return 0


def _iter_target_hosts(args: argparse.Namespace) -> Iterable[McpHost]:
    if args.all:
        # Only yield hosts whose config actually exists — avoids spurious
        # errors for hosts the user doesn't have installed.
        for dh in detect_hosts():
            if dh.exists:
                yield dh.host
        return
    if not args.host:
        print(
            "error: must specify --host=<key> or --all",
            file=sys.stderr,
        )
        raise SystemExit(2)
    host = HOSTS_BY_KEY.get(args.host)
    if host is None:
        print(
            f"error: unknown host '{args.host}'. Known: "
            + ", ".join(HOSTS_BY_KEY),
            file=sys.stderr,
        )
        raise SystemExit(2)
    yield host


def _cmd_install(args: argparse.Namespace) -> int:
    any_ran = False
    for host in _iter_target_hosts(args):
        any_ran = True
        try:
            plan = plan_install(host, python_exe=args.python_exe)
        except Exception as exc:
            print(f"[{host.key}] plan failed: {exc}", file=sys.stderr)
            continue
        print(plan.diff_summary())
        if not plan.config_exists:
            print(f"  → config missing; skipping {host.key}")
            continue
        if not plan.has_changes:
            print(f"  → no changes needed for {host.key}")
            continue
        if args.dry_run:
            print(f"  → --dry-run: would wrap {plan.wrapped_count} server(s), skipped writing")
            continue
        backup = apply_install(plan)
        if backup:
            print(f"  → wrote {plan.config_path}; backup at {backup.name}")
    return 0 if any_ran else 2


def _cmd_uninstall(args: argparse.Namespace) -> int:
    any_ran = False
    for host in _iter_target_hosts(args):
        any_ran = True
        try:
            cfg_path, latest_backup = plan_uninstall(host)
        except FileNotFoundError as exc:
            print(f"[{host.key}] {exc}")
            continue
        if latest_backup is None:
            # No backup → try pure-string unwrap
            cfg = load_config(cfg_path)
            servers = cfg.get("mcpServers") or {}
            changed = 0
            for name, entry in list(servers.items()):
                new_entry, action = unwrap_server_entry(entry)
                if action == "unwrapped":
                    servers[name] = new_entry
                    changed += 1
            if changed == 0:
                print(f"[{host.key}] nothing to uninstall (no backup + no wrapped entries)")
                continue
            if args.dry_run:
                print(f"[{host.key}] --dry-run: would unwrap {changed} server(s) in {cfg_path}")
                continue
            write_backup(cfg_path)
            write_config(cfg_path, cfg)
            print(f"[{host.key}] unwrapped {changed} server(s) in {cfg_path}")
            continue
        if args.dry_run:
            print(f"[{host.key}] --dry-run: would restore {latest_backup.name} → {cfg_path}")
            continue
        # Keep a pre-restore snapshot so an accidental uninstall is recoverable.
        write_backup(cfg_path)
        cfg_path.write_bytes(latest_backup.read_bytes())
        print(f"[{host.key}] restored {latest_backup.name} → {cfg_path}")
    return 0 if any_ran else 2


def _cmd_status(_args: argparse.Namespace) -> int:
    rows = detect_hosts()
    print("PCE MCP proxy install status:")
    for dh in rows:
        if not dh.exists:
            print(dh.as_row())
            continue
        cfg = load_config(dh.config_path) if dh.config_path else {}
        servers = cfg.get("mcpServers") or {}
        wrapped = 0
        pce = 0
        other = 0
        for entry in servers.values():
            if not isinstance(entry, dict):
                continue
            if _is_pce_mcp_self(entry):
                pce += 1
            elif _is_already_wrapped(entry):
                wrapped += 1
            else:
                other += 1
        backups = list_backups(dh.config_path) if dh.config_path else []
        print(
            f"  [*] {dh.host.display_name:40s} "
            f"pce={pce} wrapped={wrapped} other={other} "
            f"backups={len(backups)}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pce_mcp_proxy.install",
        description=(
            "Wrap every MCP server in a supported host's config with "
            "pce_mcp_proxy so PCE captures every host ↔ upstream frame. "
            "See ADR-016 §3.3."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_detect = sub.add_parser("detect", help="List known hosts and whether their config exists.")
    p_detect.set_defaults(func=_cmd_detect)

    p_install = sub.add_parser("install", help="Wrap every non-pce MCP server in a host.")
    p_install.add_argument("--host", help="Host key (e.g. claude_desktop).")
    p_install.add_argument("--all", action="store_true", help="Apply to every detected host.")
    p_install.add_argument(
        "--python-exe",
        default=None,
        help="Absolute path to the Python interpreter used in the wrapped command. "
             "Defaults to sys.executable.",
    )
    p_install.add_argument("--dry-run", action="store_true", help="Show diff without writing.")
    p_install.set_defaults(func=_cmd_install)

    p_uninstall = sub.add_parser(
        "uninstall",
        help="Restore the most recent pre-install backup, or unwrap in-place if no backup.",
    )
    p_uninstall.add_argument("--host", help="Host key (e.g. claude_desktop).")
    p_uninstall.add_argument("--all", action="store_true", help="Apply to every detected host.")
    p_uninstall.add_argument("--dry-run", action="store_true", help="Show diff without writing.")
    p_uninstall.set_defaults(func=_cmd_uninstall)

    p_status = sub.add_parser(
        "status",
        help="One-line summary per host: pce count / wrapped count / other count / backup count.",
    )
    p_status.set_defaults(func=_cmd_status)

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
