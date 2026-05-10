# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for tests/e2e_l3g/.

Each test gets:

- a fresh PCE sqlite DB (``tmp_pce_db``) with all migrations applied,
  including ``0011`` which registers the ``l3g-local-persistence-default``
  source row;
- an ``isolated_data_dir`` env so any default-path lookups land under
  ``tmp_path`` instead of the developer's real ``~/.pce/data``;
- a ``fake_app_profile`` factory that builds a synthetic Claude-Desktop-
  style profile under tmp_path, with one agent-session manifest and one
  skills-catalogue manifest;
- a matching ``fake_app_install`` returning a ready-to-scan ``AppInstall``.

The fixtures are intentionally small and composable so individual test
files can build only what they need.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pce_core.db import init_db  # noqa: E402  (sys.path tweaked above)
from pce_persistence_watcher.discovery import AppInstall  # noqa: E402


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``PCE_DATA_DIR`` at a fresh tmp dir for the test's lifetime."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def tmp_pce_db(tmp_path: Path) -> Path:
    """Create an empty PCE sqlite DB at ``<tmp>/pce.db`` with migrations applied.

    The migration runner is the same one production uses, so every test
    that asserts on the schema (notably the ``sources`` row registered by
    migration 0011) sees an identical view of the DB.
    """
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Synthetic app profile builder
# ---------------------------------------------------------------------------


def _make_session_manifest(plugins: list[dict] | None = None, last_updated_ms: int = 1_700_000_000_000) -> dict:
    return {
        "lastUpdated": last_updated_ms,
        "plugins": plugins or [],
    }


def _make_skills_manifest(skills: list[dict] | None = None, last_updated_ms: int = 1_700_000_000_000) -> dict:
    return {
        "lastUpdated": last_updated_ms,
        "skills": skills or [
            {
                "skillId": "skill.test.echo",
                "name": "Echo",
                "description": "Repeats user input back",
                "enabled": True,
            },
            {
                "skillId": "skill.test.fortune",
                "name": "Fortune",
                "description": "Returns a fortune cookie",
                "enabled": False,
            },
        ],
    }


@pytest.fixture
def fake_app_profile(tmp_path: Path) -> Callable[..., dict]:
    """Factory that builds a Claude-Desktop-style profile dir.

    Returns a dict::

        {
          "profile":          <Path>,
          "agent_sessions":   <Path>,
          "session_uuid":     <str>,
          "session_manifest": <Path>,
          "skills_manifest":  <Path>,
        }

    Callers pass keyword overrides: ``num_sessions=N`` to spawn N session
    UUIDs (default 1), or ``include_skills=False`` to skip the catalogue.
    """

    def _build(
        *,
        profile_dirname: str = "Claude",
        num_sessions: int = 1,
        include_skills: bool = True,
        session_plugins: list[dict] | None = None,
        skills_payload: list[dict] | None = None,
    ) -> dict:
        profile = tmp_path / "fake_profile" / profile_dirname
        agent_root = profile / "local-agent-mode-sessions"
        agent_root.mkdir(parents=True, exist_ok=True)

        session_uuids: list[str] = []
        session_manifests: list[Path] = []
        for _ in range(num_sessions):
            sid = str(uuid.uuid4())
            session_uuids.append(sid)
            sdir = agent_root / sid
            sdir.mkdir()
            mf = sdir / "manifest.json"
            mf.write_text(
                json.dumps(_make_session_manifest(plugins=session_plugins)),
                encoding="utf-8",
            )
            session_manifests.append(mf)

        skills_manifest: Path | None = None
        if include_skills:
            outer = uuid.uuid4().hex
            inner = uuid.uuid4().hex
            skills_dir = agent_root / "skills-plugin" / outer / inner
            skills_dir.mkdir(parents=True)
            skills_manifest = skills_dir / "manifest.json"
            skills_manifest.write_text(
                json.dumps(_make_skills_manifest(skills=skills_payload)),
                encoding="utf-8",
            )

        return {
            "profile": profile,
            "agent_sessions": agent_root,
            "session_uuids": session_uuids,
            "session_manifests": session_manifests,
            "skills_manifest": skills_manifest,
        }

    return _build


@pytest.fixture
def fake_app_install(fake_app_profile: Callable[..., dict]) -> Callable[..., AppInstall]:
    """Factory wrapping a fake profile in a populated ``AppInstall``.

    Returns ``(install, layout_dict)`` so tests can assert against the
    exact paths created by the profile builder.
    """

    def _build(
        *,
        app_id: str = "claude-desktop",
        channel: str = "msix",
        version: str = "1.0.0-test",
        **profile_kwargs,
    ) -> tuple[AppInstall, dict]:
        layout = fake_app_profile(**profile_kwargs)
        profile = layout["profile"]
        roots = {
            "app_profile": profile,
            "local_storage_leveldb": profile / "Local Storage" / "leveldb",
            "indexeddb": profile / "IndexedDB",
            "agent_sessions": layout["agent_sessions"],
            "logs": profile / "logs",
        }
        install = AppInstall(
            app_id=app_id,
            channel=channel,
            version=version,
            install_location=None,
            roots=roots,
        )
        return install, layout

    return _build
