# SPDX-License-Identifier: Apache-2.0
"""Tests for pce_core.git_context — fail-safe git metadata extraction."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pce_core.git_context import (
    get_branch,
    get_git_context,
    get_head_commit,
    get_short_commit,
)


class TestGetHeadCommit:
    def test_returns_sha_in_git_repo(self, tmp_git_repo: Path) -> None:
        sha = get_head_commit(tmp_git_repo)
        assert sha is not None
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_returns_none_outside_git_repo(self, tmp_path: Path) -> None:
        assert get_head_commit(tmp_path) is None

    def test_returns_none_when_git_missing(self, tmp_path: Path) -> None:
        with patch("pce_core.git_context.subprocess.run", side_effect=OSError):
            assert get_head_commit(tmp_path) is None

    def test_returns_none_on_timeout(self, tmp_path: Path) -> None:
        with patch(
            "pce_core.git_context.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 2),
        ):
            assert get_head_commit(tmp_path) is None


class TestGetBranch:
    def test_returns_branch_name(self, tmp_git_repo: Path) -> None:
        branch = get_branch(tmp_git_repo)
        assert branch is not None
        assert branch in ("master", "main")

    def test_returns_none_outside_git_repo(self, tmp_path: Path) -> None:
        assert get_branch(tmp_path) is None


class TestGetShortCommit:
    def test_returns_short_sha(self, tmp_git_repo: Path) -> None:
        short = get_short_commit(tmp_git_repo)
        assert short is not None
        assert 7 <= len(short) <= 12

    def test_returns_none_outside_git_repo(self, tmp_path: Path) -> None:
        assert get_short_commit(tmp_path) is None


class TestGetGitContext:
    def test_returns_full_context_in_repo(self, tmp_git_repo: Path) -> None:
        ctx = get_git_context(tmp_git_repo)
        assert "git_commit_sha" in ctx
        assert len(ctx["git_commit_sha"]) == 40
        assert "git_branch" in ctx
        assert "git_short_sha" in ctx

    def test_returns_empty_dict_outside_repo(self, tmp_path: Path) -> None:
        ctx = get_git_context(tmp_path)
        assert ctx == {}

    def test_returns_empty_dict_when_git_unavailable(self, tmp_path: Path) -> None:
        with patch("pce_core.git_context.subprocess.run", side_effect=OSError):
            ctx = get_git_context(tmp_path)
            assert ctx == {}

    def test_none_cwd_uses_current_dir(self) -> None:
        ctx = get_git_context(None)
        # We're running inside the PCE repo, so this should work
        assert "git_commit_sha" in ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit."""
    subprocess.run(
        ["git", "init"], cwd=str(tmp_path),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    (tmp_path / "README.md").write_text("init")
    subprocess.run(
        ["git", "add", "."], cwd=str(tmp_path),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    return tmp_path
