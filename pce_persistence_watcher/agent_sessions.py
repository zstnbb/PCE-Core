# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.agent_sessions – parse local-agent-mode-sessions/.

Claude Desktop's Cowork feature (local agent mode) writes session-scoped
state under::

    <app_profile>/local-agent-mode-sessions/
    ├── <session-uuid>/
    │   ├── manifest.json                 # {"lastUpdated": <ms>, "plugins": [...]}
    │   └── <sub-session-uuid>/           # optional nesting
    │       └── rpm/
    │           └── manifest.json         # {"lastUpdated": <ms>, "plugins": [...]}
    └── skills-plugin/
        └── <uuid>/
            └── <uuid>/
                └── manifest.json         # {"skills": [ {skillId, name, description, ...}, ... ]}

Plus each skill is a self-contained directory with a ``SKILL.md`` and
optional scripts. For v0 we capture two semantic record types:

1. **Agent session**: one record per ``<session-uuid>/manifest.json``
   found. Body = the manifest JSON; ``session_hint = <session-uuid>``;
   ``provider = "anthropic"``; ``host = "local-agent-mode"``.

2. **Skills catalogue snapshot**: one record per ``skills-plugin/
   <uuid>/<uuid>/manifest.json`` found. Body = manifest JSON
   (contains the full ``skills[]`` array with skillId/name/description
   /enabled per entry). This captures WHICH skills the user has
   installed — valuable context for understanding future Cowork
   conversations.

Dedup: the capture observer hashes ``(source_path, content_hash)`` and
only re-emits a session / skills record when the file's mtime+size+
content changes. The parser here is stateless and returns the full
record every time; dedup is ``capture.py``'s responsibility.

Parse failures are logged at WARNING and skipped; a single malformed
session directory must not block the rest.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

logger = logging.getLogger("pce.persistence_watcher.agent_sessions")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentSessionRecord:
    """A parsed record from a Claude Desktop persistence surface.

    ``kind`` is one of ``"session"`` / ``"skills_catalogue"`` /
    ``"local_config"`` — the observer uses it to route the record into
    the right PCE capture envelope shape.

    ``surface`` is set only for ``kind == "local_config"`` and names
    which top-level config file the record came from (per ADR-018 §6
    C4 supplementary findings, 2026-05-10): one of ``"preferences"``,
    ``"cowork_owner"``, ``"git_worktrees"``, ``"device_id"``.
    """

    kind: str  # "session" | "skills_catalogue" | "local_config"
    session_id: Optional[str]  # uuid if kind == "session", else None
    source_path: Path
    mtime_ns: int
    size_bytes: int
    body_json: dict  # parsed manifest content
    last_updated_ms: Optional[int]  # from manifest when present
    surface: Optional[str] = None  # set when kind == "local_config"


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


def _safe_read_json(path: Path) -> Optional[dict]:
    """Read a JSON file, return dict on success or None on any failure."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("cannot read %s: %s", path, exc)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("malformed json %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("unexpected json shape at %s: %s", path, type(data).__name__)
        return None
    return data


def _stat_safe(path: Path) -> Optional[tuple[int, int]]:
    """Return (mtime_ns, size) or None if stat fails (transient disk error, etc.)."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (int(st.st_mtime_ns), int(st.st_size))


def _looks_like_uuid(name: str) -> bool:
    """Cheap UUID-shape check: 8-4-4-4-12 hex digits."""
    if len(name) != 36:
        return False
    parts = name.split("-")
    if len(parts) != 5 or [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdef" for c in name.replace("-", "").lower())


def iter_records(agent_sessions_root: Path) -> Iterator[AgentSessionRecord]:
    """Walk ``local-agent-mode-sessions/`` and yield records.

    The root is expected to be ``<app_profile>/local-agent-mode-sessions``.
    Callers that hold an ``AppInstall`` should pass
    ``install.root("agent_sessions")``.

    Yields nothing silently if the root does not exist or is empty.
    """
    if not agent_sessions_root.exists() or not agent_sessions_root.is_dir():
        return

    for child in agent_sessions_root.iterdir():
        name = child.name
        try:
            if child.is_dir() and _looks_like_uuid(name):
                yield from _walk_session_dir(child)
            elif child.is_dir() and name == "skills-plugin":
                yield from _walk_skills_plugin(child)
            # Any other top-level child is ignored — future Cowork
            # features may land here and we do not want the watcher
            # to flood the DB with unknown shapes.
        except Exception as exc:  # pragma: no cover — defensive guard
            logger.warning(
                "skipping %s due to parse error: %s", child, exc,
            )


def _walk_session_dir(session_dir: Path) -> Iterator[AgentSessionRecord]:
    """Emit a ``session`` record for each manifest.json under a session uuid dir.

    Claude Desktop nests sub-session uuids one level deep (see module
    docstring). We walk 2 levels to pick up both outer and inner manifests.
    """
    session_uuid = session_dir.name

    for manifest_path in _find_manifests(session_dir, max_depth=3):
        body = _safe_read_json(manifest_path)
        if body is None:
            continue
        # skills-plugin manifests (shape has "skills") can live under a
        # session_dir if the user installed a skill scoped to that
        # session; branch on payload shape, not path.
        if isinstance(body.get("skills"), list):
            kind = "skills_catalogue"
            effective_session = session_uuid
        else:
            kind = "session"
            effective_session = session_uuid

        stat = _stat_safe(manifest_path)
        if stat is None:
            continue
        mtime_ns, size = stat

        last_updated_ms: Optional[int] = None
        raw_lu = body.get("lastUpdated")
        if isinstance(raw_lu, int):
            last_updated_ms = raw_lu

        yield AgentSessionRecord(
            kind=kind,
            session_id=effective_session,
            source_path=manifest_path,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=body,
            last_updated_ms=last_updated_ms,
        )


def _walk_skills_plugin(skills_root: Path) -> Iterator[AgentSessionRecord]:
    """Emit one ``skills_catalogue`` record per manifest.json under skills-plugin/."""
    for manifest_path in _find_manifests(skills_root, max_depth=4):
        body = _safe_read_json(manifest_path)
        if body is None:
            continue

        stat = _stat_safe(manifest_path)
        if stat is None:
            continue
        mtime_ns, size = stat

        if not isinstance(body.get("skills"), list):
            # Not the catalogue root — may be a per-skill manifest. Skip
            # for v0 (per-skill details live in SKILL.md; future
            # ``skill_files`` parser can pick those up).
            continue

        last_updated_ms: Optional[int] = None
        raw_lu = body.get("lastUpdated")
        if isinstance(raw_lu, int):
            last_updated_ms = raw_lu

        yield AgentSessionRecord(
            kind="skills_catalogue",
            session_id=None,
            source_path=manifest_path,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=body,
            last_updated_ms=last_updated_ms,
        )


def _find_manifests(root: Path, *, max_depth: int) -> Iterable[Path]:
    """Yield ``manifest.json`` files under ``root`` with bounded recursion.

    Uses a bounded BFS so a malformed deep tree cannot cause unbounded
    walking. ``max_depth=3`` covers all currently-known Cowork layouts
    (session/rpm/manifest.json) with one level of future-proofing.
    """
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            for child in path.iterdir():
                if child.is_file() and child.name == "manifest.json":
                    yield child
                elif child.is_dir():
                    stack.append((child, depth + 1))
        except OSError:
            continue


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def count(agent_sessions_root: Path) -> dict[str, int]:
    """Return ``{"session": N, "skills_catalogue": M}`` for discover mode."""
    counts = {"session": 0, "skills_catalogue": 0}
    for rec in iter_records(agent_sessions_root):
        counts[rec.kind] = counts.get(rec.kind, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Local-config surfaces (ADR-018 §6 C4 supplementary, mapped 2026-05-10)
# ---------------------------------------------------------------------------
#
# Claude Desktop's LocalCache profile root contains four directly-readable
# surfaces that L3g v1 collects before falling back to LevelDB. Each is
# plaintext, default-metadata-only, small (<1 KB), and present on a
# normally-installed Claude Desktop profile. Missing files are skipped
# silently — different versions / first-launch states omit some.
#
# Mapping: filename → logical surface name. The surface name is what
# downstream consumers (capture observer, dashboard) see and what gets
# baked into the capture path ``/<app_id>/local-config/<surface>``.

LOCAL_CONFIG_SURFACES: dict[str, str] = {
    "claude_desktop_config.json": "preferences",
    "cowork-enabled-cli-ops.json": "cowork_owner",
    "git-worktrees.json": "git_worktrees",
    "ant-did": "device_id",
}


def _read_ant_did(path: Path) -> Optional[dict]:
    """Decode the ``ant-did`` file: base64-encoded UUID → ``{"device_id": uuid}``.

    Returns None if the file is unreadable, not valid base64, decodes to
    a non-ASCII payload, or the decoded payload is not UUID-shaped.
    """
    try:
        raw = path.read_bytes().strip()
    except OSError as exc:
        logger.debug("cannot read ant-did at %s: %s", path, exc)
        return None
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("ascii").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        logger.warning("ant-did file at %s is not base64-ASCII: %s", path, exc)
        return None
    if not _looks_like_uuid(decoded):
        logger.warning("ant-did decoded value at %s is not UUID-shaped", path)
        return None
    return {"device_id": decoded}


def iter_local_config_records(profile_root: Path) -> Iterator[AgentSessionRecord]:
    """Walk Claude Desktop's profile-root local-config surfaces.

    ``profile_root`` is the LocalCache profile root — e.g.::

        %LOCALAPPDATA%\\Packages\\Claude_pzs8sxrjxfjjc\\LocalCache\\
            Roaming\\Claude\\

    Or for the test harness, any directory containing the four known
    files at its top level. ``AppInstall.root("app_profile")`` is the
    canonical caller-side accessor.

    Yields one ``AgentSessionRecord`` per surface present, with
    ``kind == "local_config"`` and ``surface`` set. Returns silently
    if ``profile_root`` does not exist or contains none of the known
    surfaces.
    """
    if not profile_root.exists() or not profile_root.is_dir():
        return

    for fname, surface in LOCAL_CONFIG_SURFACES.items():
        path = profile_root / fname
        if not path.is_file():
            continue

        stat = _stat_safe(path)
        if stat is None:
            continue
        mtime_ns, size = stat

        if fname == "ant-did":
            body = _read_ant_did(path)
        else:
            body = _safe_read_json(path)
        if body is None:
            continue

        last_updated_ms: Optional[int] = None
        # Some preferences blobs include an updatedAt; harvest it when present.
        raw_lu = body.get("updatedAt") if isinstance(body, dict) else None
        if isinstance(raw_lu, int):
            last_updated_ms = raw_lu

        yield AgentSessionRecord(
            kind="local_config",
            session_id=None,
            source_path=path,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=body,
            last_updated_ms=last_updated_ms,
            surface=surface,
        )


def count_local_config(profile_root: Path) -> dict[str, int]:
    """Return ``{"local_config": N, <surface>: M, ...}`` for discover mode."""
    counts: dict[str, int] = {"local_config": 0}
    for rec in iter_local_config_records(profile_root):
        counts["local_config"] += 1
        if rec.surface:
            counts[rec.surface] = counts.get(rec.surface, 0) + 1
    return counts
