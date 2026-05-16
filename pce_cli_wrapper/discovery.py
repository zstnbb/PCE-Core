# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper.discovery – locate CLI AI-agent shims on this machine.

Each ``ShimTarget`` is a single wrap-able CLI binary on the user's
PATH. v0 only knows about ``claude-code`` (the
``@anthropic-ai/claude-code`` npm package). Adding more targets is a
matter of registering another ``_TargetSpec`` below — the rest of the
package treats targets opaquely.

Discovery sources (Windows + POSIX):

1. ``which`` / ``where.exe`` of the canonical command name (fast path).
2. ``%APPDATA%\\npm\\<cmd>.cmd`` / ``%APPDATA%\\npm\\<cmd>.ps1`` /
   ``%APPDATA%\\npm\\<cmd>`` enumeration (Windows).
3. ``$(npm prefix -g)/bin/<cmd>`` (POSIX) or
   ``$(npm prefix -g)/<cmd>.cmd`` (Windows).
4. The Desktop-embedded ``%LOCALAPPDATA%\\Packages\\Claude_*\\
   LocalCache\\Roaming\\Claude\\claude-code\\<ver>\\`` is recognised
   for diagnostic purposes but NOT marked installable — Desktop
   spawns it with an absolute path so a PATH-priority wrapper cannot
   intercept (see ADR-018 §3.5 H1 "v1 follow-up").

The discovery functions are pure and read-only; they never spawn the
target or modify the filesystem.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.cli_wrapper.discovery")


# ---------------------------------------------------------------------------
# Target catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TargetSpec:
    """Static description of one wrap-able CLI agent."""

    target_id: str           # e.g. "claude-code"
    command_name: str        # the CLI command users type, e.g. "claude"
    npm_package: Optional[str]  # canonical npm package, e.g. "@anthropic-ai/claude-code"
    provider: str            # PCE provider tag for the capture row
    description: str


_TARGETS: list[_TargetSpec] = [
    _TargetSpec(
        target_id="claude-code",
        command_name="claude",
        npm_package="@anthropic-ai/claude-code",
        provider="anthropic",
        description="Anthropic Claude Code CLI (npm-installed)",
    ),
    _TargetSpec(
        target_id="codex-cli",
        command_name="codex",
        npm_package="@openai/codex",
        provider="openai",
        description="OpenAI Codex CLI (npm-installed; subscription-backed via chatgpt.com/backend-api/codex)",
    ),
    _TargetSpec(
        target_id="gemini-cli",
        command_name="gemini",
        npm_package="@google/gemini-cli",
        provider="google",
        description="Google Gemini CLI (npm-installed)",
    ),
]


def known_targets() -> list[_TargetSpec]:
    """Return the static target catalogue (defensive copy)."""
    return list(_TARGETS)


# ---------------------------------------------------------------------------
# Discovery result
# ---------------------------------------------------------------------------


@dataclass
class ShimTarget:
    """A discovered, wrap-able CLI shim on this host."""

    target_id: str
    command_name: str
    provider: str
    description: str

    # Resolved canonical shim. None when the command is not installed.
    shim_path: Optional[Path]

    # Variants of the same shim (npm drops .cmd + .ps1 + bare on Windows).
    shim_variants: list[Path] = field(default_factory=list)

    # Best-effort version string (e.g. ``2.1.59 (Claude Code)``).
    version: Optional[str] = None

    # The npm "prefix -g" directory the shim lives under, when known.
    npm_prefix: Optional[Path] = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover(*, target_filter: Optional[list[str]] = None) -> list[ShimTarget]:
    """Walk the target catalogue and probe each candidate's shim path.

    ``target_filter`` is a list of ``target_id`` strings; empty / None
    means "everything in the catalogue". Unknown ids are silently
    skipped.
    """
    wanted = {t.lower() for t in (target_filter or []) if t}
    results: list[ShimTarget] = []
    for spec in _TARGETS:
        if wanted and spec.target_id.lower() not in wanted:
            continue
        try:
            results.append(_probe_target(spec))
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "discovery probe failed for %s: %s", spec.target_id, exc,
            )
            results.append(ShimTarget(
                target_id=spec.target_id,
                command_name=spec.command_name,
                provider=spec.provider,
                description=spec.description,
                shim_path=None,
            ))
    return results


def _probe_target(spec: _TargetSpec) -> ShimTarget:
    """Return a populated ``ShimTarget`` for one catalogue entry."""
    shim_path: Optional[Path] = None
    variants: list[Path] = []
    npm_prefix: Optional[Path] = None

    # Strategy 1 — shutil.which (PATH walk, respects PATHEXT on Windows).
    found = shutil.which(spec.command_name)
    if found:
        shim_path = Path(found).resolve()

    # Strategy 2 — direct enumeration of %APPDATA%/npm or POSIX npm prefix.
    candidates = list(_npm_candidates(spec))
    if shim_path is None and candidates:
        shim_path = candidates[0].resolve()

    # Collect every variant (.cmd / .ps1 / bare) under the same dir for
    # the install / uninstall logic — npm always drops all three on
    # Windows.
    if shim_path is not None:
        npm_prefix = shim_path.parent
        for cand in _expand_variants(shim_path, spec.command_name):
            if cand.exists() and cand.resolve() not in {v.resolve() for v in variants}:
                variants.append(cand)
        # If shim_path itself isn't in variants (e.g. resolved through a
        # symlink) add it explicitly.
        if shim_path not in variants:
            variants.insert(0, shim_path)

    version = _try_read_version(spec, shim_path)

    return ShimTarget(
        target_id=spec.target_id,
        command_name=spec.command_name,
        provider=spec.provider,
        description=spec.description,
        shim_path=shim_path,
        shim_variants=variants,
        version=version,
        npm_prefix=npm_prefix,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _npm_candidates(spec: _TargetSpec) -> list[Path]:
    """Enumerate likely shim file paths under the user's npm prefix dir."""
    out: list[Path] = []

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata) / "npm"
            for name in (
                f"{spec.command_name}.cmd",
                f"{spec.command_name}.ps1",
                spec.command_name,
            ):
                p = base / name
                if p.exists():
                    out.append(p)
    else:
        # POSIX: try common prefix paths without invoking node.
        for base in (Path.home() / ".npm-global" / "bin", Path("/usr/local/bin"), Path("/usr/bin")):
            p = base / spec.command_name
            if p.exists():
                out.append(p)
    return out


def _expand_variants(shim_path: Path, command_name: str) -> list[Path]:
    """For a discovered shim, list every npm variant in the same directory.

    npm-on-Windows drops three files for every global bin: ``foo``,
    ``foo.cmd``, ``foo.ps1``. We need all of them so install can
    front-run every invocation channel (cmd.exe, PowerShell, MSYS bash).
    """
    parent = shim_path.parent
    return [
        parent / command_name,
        parent / f"{command_name}.cmd",
        parent / f"{command_name}.ps1",
    ]


def _try_read_version(spec: _TargetSpec, shim_path: Optional[Path]) -> Optional[str]:
    """Best-effort ``<cmd> --version`` read. Never raises.

    We deliberately do not call out to the shim during ``status`` /
    ``install`` because some agents have heavy startup cost; instead
    we look for a sibling ``package.json`` under ``node_modules/<pkg>``
    and pull the ``version`` field from there.
    """
    if shim_path is None or spec.npm_package is None:
        return None
    pkg_root = shim_path.parent / "node_modules"
    pkg_dir = pkg_root.joinpath(*spec.npm_package.split("/"))
    pkg_json = pkg_dir / "package.json"
    try:
        import json
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = data.get("version")
    return str(v) if isinstance(v, str) else None


# ---------------------------------------------------------------------------
# Convenience for the status subcommand
# ---------------------------------------------------------------------------


def summarise(targets: list[ShimTarget]) -> dict:
    """Return a JSON-friendly summary of a discovery pass."""
    out = {
        "count": sum(1 for t in targets if t.shim_path is not None),
        "platform": sys.platform,
        "targets": [],
    }
    for t in targets:
        out["targets"].append({
            "target_id": t.target_id,
            "command_name": t.command_name,
            "provider": t.provider,
            "description": t.description,
            "installed": t.shim_path is not None,
            "shim_path": str(t.shim_path) if t.shim_path else None,
            "shim_variants": [str(v) for v in t.shim_variants],
            "version": t.version,
            "npm_prefix": str(t.npm_prefix) if t.npm_prefix else None,
        })
    return out
