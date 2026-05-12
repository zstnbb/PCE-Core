# SPDX-License-Identifier: Apache-2.0
"""Lightweight git context extraction for capture metadata.

All functions are fail-safe: they return None on any error (not a git
repo, git not installed, timeout, permission denied, etc.). They never
raise and never block for more than 2 seconds.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.git_context")

_TIMEOUT_S = 2


def get_head_commit(cwd: Optional[Path] = None) -> Optional[str]:
    """Return the full SHA-1 of HEAD in the repo at *cwd*, or None."""
    return _git(["rev-parse", "HEAD"], cwd)


def get_branch(cwd: Optional[Path] = None) -> Optional[str]:
    """Return the current branch name, or None if detached/unavailable."""
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def get_short_commit(cwd: Optional[Path] = None) -> Optional[str]:
    """Return the short (7-char) SHA of HEAD, or None."""
    return _git(["rev-parse", "--short", "HEAD"], cwd)


def get_git_context(cwd: Optional[Path] = None) -> dict:
    """Return a dict with git_commit_sha, git_branch, git_short_sha.

    Only includes keys whose values are non-None. Returns {} if not
    in a git repo or git is unavailable.
    """
    ctx: dict = {}
    sha = get_head_commit(cwd)
    if sha:
        ctx["git_commit_sha"] = sha
    branch = get_branch(cwd)
    if branch and branch != "HEAD":
        ctx["git_branch"] = branch
    short = get_short_commit(cwd)
    if short:
        ctx["git_short_sha"] = short
    return ctx


def _git(args: list[str], cwd: Optional[Path]) -> Optional[str]:
    """Run a git command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        return out if out else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
