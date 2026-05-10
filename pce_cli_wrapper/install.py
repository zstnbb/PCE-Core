# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper.install – generate / remove wrapper shim files.

The install path takes a discovered ``ShimTarget`` and writes a small
wrapper shim into the PCE-owned ``bin/`` directory. Each shim
re-execs::

    <python> -m pce_cli_wrapper relay --target <real-shim> -- <args>

When ``bin_dir`` precedes the real shim's directory on ``PATH``, every
``claude`` invocation flows through the wrapper, gets stdio-mirrored
into PCE, and exits with the child's exit code.

Why a separate ``bin_dir`` instead of overwriting the npm shim in
place? Three reasons:

1. **Reversible** — uninstall is a single ``rmdir`` of our own dir.
2. **Upgrade-safe** — ``npm install -g @anthropic-ai/claude-code``
   blows away its own ``%APPDATA%\\npm\\claude.cmd`` regularly. If we
   had patched that file, every npm upgrade would silently undo us.
3. **Clear provenance** — looking at ``where.exe claude`` immediately
   tells the user whether they're hitting the wrapper or the real
   shim, with no ambiguity.

The install does NOT modify the user's PATH — we print guidance for
the user to do it themselves (one line for cmd / pwsh / bash). Auto-
editing shell rc files is too invasive for v0.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .discovery import ShimTarget, discover

logger = logging.getLogger("pce.cli_wrapper.install")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class InstallAction:
    """One filesystem mutation the install / uninstall pass would perform."""

    target_id: str
    action: str         # "create" | "skip" | "remove"
    path: Path
    reason: str = ""    # human note, e.g. "target not installed"


@dataclass
class InstallReport:
    """The full outcome of an install / uninstall pass."""

    bin_dir: Path
    actions: list[InstallAction]
    on_path: bool                 # is bin_dir already on the user's PATH?
    path_guidance: list[str]      # shell-specific commands to prepend bin_dir


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------


def install(
    *,
    bin_dir: Path,
    targets: Optional[list[ShimTarget]] = None,
    target_filter: Optional[list[str]] = None,
    python_exe: Optional[Path] = None,
    dry_run: bool = False,
) -> InstallReport:
    """Generate wrapper shim files for every discovered target.

    ``targets`` can be passed in explicitly (tests do this); when None
    we fall back to ``discovery.discover(target_filter=...)``.

    A target with ``shim_path is None`` (= not installed on this host)
    is recorded as a ``skip`` action, never written.
    """
    if targets is None:
        targets = discover(target_filter=target_filter)

    actions: list[InstallAction] = []
    bin_dir = Path(bin_dir).expanduser()

    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)

    py = python_exe or _detect_python_exe()

    for tgt in targets:
        if tgt.shim_path is None:
            actions.append(InstallAction(
                target_id=tgt.target_id,
                action="skip",
                path=bin_dir / tgt.command_name,
                reason="target shim not present on host",
            ))
            continue
        for variant in _wrapper_variants_for(bin_dir, tgt.command_name):
            content = _render_wrapper(
                wrapper_path=variant,
                target_path=tgt.shim_path,
                target_id=tgt.target_id,
                command_name=tgt.command_name,
                python_exe=py,
            )
            if content is None:
                continue
            if dry_run:
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="create",
                    path=variant,
                    reason="dry-run; not written",
                ))
                continue
            try:
                variant.write_text(content, encoding="utf-8", newline="\n")
                if not _is_windows() and variant.suffix == "":
                    # POSIX bare-name shim must be executable.
                    variant.chmod(0o755)
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="create",
                    path=variant,
                ))
            except OSError as exc:
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="skip",
                    path=variant,
                    reason=f"write failed: {exc}",
                ))

    return InstallReport(
        bin_dir=bin_dir,
        actions=actions,
        on_path=_bin_dir_on_path(bin_dir),
        path_guidance=_path_guidance(bin_dir),
    )


def uninstall(
    *,
    bin_dir: Path,
    targets: Optional[list[ShimTarget]] = None,
    target_filter: Optional[list[str]] = None,
    dry_run: bool = False,
) -> InstallReport:
    """Remove every wrapper shim file from ``bin_dir`` we recognise.

    Files we did not create are left untouched. The ``bin_dir`` itself
    is removed only when it ends up empty (so a user that cohabits
    other tools in ``~/.pce/bin`` does not lose them).
    """
    if targets is None:
        targets = discover(target_filter=target_filter)

    actions: list[InstallAction] = []
    bin_dir = Path(bin_dir).expanduser()

    for tgt in targets:
        for variant in _wrapper_variants_for(bin_dir, tgt.command_name):
            if not variant.exists():
                continue
            if not _looks_like_our_wrapper(variant):
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="skip",
                    path=variant,
                    reason="not a PCE-generated wrapper; left in place",
                ))
                continue
            if dry_run:
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="remove",
                    path=variant,
                    reason="dry-run; not deleted",
                ))
                continue
            try:
                variant.unlink()
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="remove",
                    path=variant,
                ))
            except OSError as exc:
                actions.append(InstallAction(
                    target_id=tgt.target_id,
                    action="skip",
                    path=variant,
                    reason=f"unlink failed: {exc}",
                ))

    # Sweep the dir if nothing of ours remains.
    if not dry_run and bin_dir.exists():
        try:
            if not any(bin_dir.iterdir()):
                bin_dir.rmdir()
        except OSError:
            pass

    return InstallReport(
        bin_dir=bin_dir,
        actions=actions,
        on_path=_bin_dir_on_path(bin_dir),
        path_guidance=_path_guidance(bin_dir),
    )


# ---------------------------------------------------------------------------
# Wrapper rendering
# ---------------------------------------------------------------------------


# Marker line embedded in every PCE-generated wrapper so ``uninstall``
# can distinguish our files from anything else the user might have
# dropped into the same ``bin/`` directory.
_WRAPPER_MARKER = "# PCE-CLI-WRAPPER-GENERATED"


def _wrapper_variants_for(bin_dir: Path, command_name: str) -> list[Path]:
    """Return the file paths we'd create for one target on this OS."""
    if _is_windows():
        # Mirror the npm convention: drop both the cmd shim and a ps1
        # for PowerShell hosts that prefer scripts over .cmd. We do
        # NOT drop a bare-name file — Windows resolves PATHEXT order
        # automatically and writing an extensionless file confuses
        # MSYS / Git Bash users.
        return [
            bin_dir / f"{command_name}.cmd",
            bin_dir / f"{command_name}.ps1",
        ]
    # POSIX: a single bare-name script with a shebang.
    return [bin_dir / command_name]


def _render_wrapper(
    *,
    wrapper_path: Path,
    target_path: Path,
    target_id: str,
    command_name: str,
    python_exe: Path,
) -> Optional[str]:
    """Build the wrapper file body for one variant."""
    suffix = wrapper_path.suffix.lower()
    if suffix == ".cmd":
        return _render_cmd(wrapper_path, target_path, target_id, command_name, python_exe)
    if suffix == ".ps1":
        return _render_ps1(wrapper_path, target_path, target_id, command_name, python_exe)
    if suffix == "":
        return _render_posix(wrapper_path, target_path, target_id, command_name, python_exe)
    logger.debug("unknown wrapper extension %s for %s", suffix, wrapper_path)
    return None


def _render_cmd(
    wrapper_path: Path,
    target_path: Path,
    target_id: str,
    command_name: str,
    python_exe: Path,
) -> str:
    return (
        "@echo off\r\n"
        f"rem {_WRAPPER_MARKER}\r\n"
        f"rem target_id={target_id}\r\n"
        f"rem target_path={target_path}\r\n"
        "setlocal\r\n"
        f'"{python_exe}" -m pce_cli_wrapper relay '
        f'--target "{target_path}" --label "{target_id}" -- %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )


def _render_ps1(
    wrapper_path: Path,
    target_path: Path,
    target_id: str,
    command_name: str,
    python_exe: Path,
) -> str:
    return (
        f"# {_WRAPPER_MARKER}\n"
        f"# target_id={target_id}\n"
        f"# target_path={target_path}\n"
        "$ErrorActionPreference = 'Continue'\n"
        f"& '{python_exe}' -m pce_cli_wrapper relay "
        f"--target '{target_path}' --label '{target_id}' -- @args\n"
        "exit $LASTEXITCODE\n"
    )


def _render_posix(
    wrapper_path: Path,
    target_path: Path,
    target_id: str,
    command_name: str,
    python_exe: Path,
) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"# {_WRAPPER_MARKER}\n"
        f"# target_id={target_id}\n"
        f"# target_path={target_path}\n"
        f'exec "{python_exe}" -m pce_cli_wrapper relay '
        f'--target "{target_path}" --label "{target_id}" -- "$@"\n'
    )


def _looks_like_our_wrapper(path: Path) -> bool:
    """True iff ``path`` carries the PCE wrapper marker line."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        return False
    return _WRAPPER_MARKER in head


# ---------------------------------------------------------------------------
# Environment introspection
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform == "win32"


def _detect_python_exe() -> Path:
    """Resolve the python interpreter the wrapper should re-invoke.

    On Windows we strongly prefer the launcher (``pythonw.exe`` would
    swallow stderr; the regular ``python.exe`` does not). We use
    ``sys.executable`` because that is the interpreter the user
    invoked ``python -m pce_cli_wrapper install`` with — meaning
    ``pce_cli_wrapper`` is already importable from it.
    """
    return Path(sys.executable).resolve()


def _bin_dir_on_path(bin_dir: Path) -> bool:
    """True iff ``bin_dir`` precedes any other ``claude`` shim on PATH.

    We walk the user's PATH in order and check whether ``bin_dir`` is
    the FIRST directory containing any of our wrapper command names —
    being merely present somewhere on PATH is not enough; the wrapper
    has to win the search.
    """
    raw = os.environ.get("PATH", "")
    parts: list[Path] = []
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        try:
            parts.append(Path(entry).expanduser().resolve())
        except OSError:
            continue
    try:
        wanted = bin_dir.resolve()
    except OSError:
        return False
    for p in parts:
        if p == wanted:
            return True
        # If we hit any directory containing the same command name
        # before our bin_dir, we lose the search.
        for cmd in _known_command_names():
            for ext in _exec_extensions():
                if (p / f"{cmd}{ext}").exists():
                    return False
    return False


def _known_command_names() -> list[str]:
    return [t.command_name for t in discover()]


def _exec_extensions() -> list[str]:
    if _is_windows():
        raw = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.PS1")
        return [e.lower() for e in raw.split(";") if e]
    return [""]


def _path_guidance(bin_dir: Path) -> list[str]:
    """Return shell-specific one-liners that prepend ``bin_dir`` to PATH."""
    bin_str = str(bin_dir)
    lines: list[str] = []
    if _is_windows():
        lines.append(f'cmd  : set "PATH={bin_str};%PATH%"')
        lines.append(f"pwsh : $env:PATH = '{bin_str}' + [IO.Path]::PathSeparator + $env:PATH")
        lines.append(
            f'pwsh (persistent) : [Environment]::SetEnvironmentVariable('
            f"'PATH', '{bin_str}' + ';' + "
            f"[Environment]::GetEnvironmentVariable('PATH','User'), 'User')"
        )
    else:
        lines.append(f'bash/zsh : export PATH="{bin_str}:$PATH"')
        lines.append(f'fish     : fish_add_path -p "{bin_str}"')
    return lines
