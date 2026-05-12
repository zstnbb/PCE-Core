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

    ``kind`` is one of:

    - ``"session"`` / ``"skills_catalogue"`` - Cowork agent-session
      manifests and skills catalogue (M2 baseline).
    - ``"local_config"`` - LocalCache profile-root config surfaces
      (preferences / cowork_owner / git_worktrees / device_id;
      ADR-018 §6 C4 supplementary, 2026-05-10).
    - ``"transcript_line"`` - one record per JSONL line; covers
      cowork transcripts, Code-tab transcripts, AND Code-tab
      sub-agent transcripts (P5.B.7.P2, 2026-05-12 — see
      ``iter_code_tab_subagent_records``).
    - ``"code_tab_session_pointer"`` - Code-tab session metadata
      pointer (claude-code-sessions/<user>/<org>/local_<sess>.json;
      P5.B.7, 2026-05-11).
    - ``"user_state_snapshot"`` / ``"user_state_line"`` -
      P5.B.7.P2 user-home surfaces. See
      ``claude_user_state.iter_claude_user_state_records`` for the
      full surface list. Snapshots are point-in-time JSON files
      (``~/.claude.json`` + ``settings*.json`` + ``todos/*.json``
      + ``sessions/<pid>.json`` + ``agents/*.md`` + ``plugins/*.json``);
      lines are per-line ``history.jsonl`` records.

    The observer uses ``kind`` to route the record into the right
    PCE capture envelope shape (host / path / dedup strategy).

    ``surface`` discriminates within a kind. For ``local_config``:
    one of ``"preferences"``, ``"cowork_owner"``, ``"git_worktrees"``,
    ``"device_id"``. For ``user_state_snapshot`` / ``user_state_line``:
    one of ``"user_state_global"``, ``"user_state_settings"``,
    ``"user_state_settings_local"``, ``"user_state_todos"``,
    ``"user_state_history"``, ``"user_state_pid_session"`` (P2.1),
    ``"user_state_agents"`` (P2.1), ``"user_state_plugins"`` (P2.1).

    ``line_uuid`` and ``line_index`` are set for line-oriented kinds
    (``"transcript_line"`` and ``"user_state_line"``). They were
    added 2026-05-11 after Round 3 RECON revealed cowork's JSONL
    transcript schema — see
    ``Docs/research/2026-05-11-cowork-recon-findings.md`` Q5.
    ``line_uuid`` is the line's own ``uuid`` field when present
    (used for per-line dedup so a growing append-only JSONL doesn't
    re-emit old lines). ``line_index`` is the 0-based position in
    the file for lines that have no ``uuid`` (``queue-operation``,
    ``ai-title``, ``last-prompt``, ``history.jsonl``). At least one
    of the two is always set for line-oriented records.
    """

    # See the class docstring for the full ``kind`` allow-list. The
    # runtime does not enforce the set; capture.observe_agent_session
    # falls through to a generic session-style envelope for unknown
    # kinds, which is the safest default.
    kind: str
    session_id: Optional[str]
    source_path: Path
    mtime_ns: int
    size_bytes: int
    body_json: dict  # parsed manifest content OR parsed JSONL line
    last_updated_ms: Optional[int]  # from manifest/line when present
    surface: Optional[str] = None  # see class docstring
    line_uuid: Optional[str] = None  # set for line-oriented kinds
    line_index: Optional[int] = None  # set for line-oriented kinds


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
# Cowork agent-mode JSONL transcript walker (P5.B.5.3, 2026-05-11)
#
# Discovered via Round 3 RECON (see
# ``Docs/research/2026-05-11-cowork-recon-findings.md`` Q5 closure):
# Cowork persists the full conversation transcript at
#
#   <agent_sessions_root>/<user_uuid>/<org_uuid>/local_<session_uuid>/
#       .claude/projects/<encoded-cwd>/<session_uuid>.jsonl
#
# Each line is a JSON event in one of six top-level ``type`` shapes:
# ``user`` / ``assistant`` / ``ai-title`` / ``queue-operation`` /
# ``last-prompt`` / ``attachment``. The ``user`` / ``assistant`` lines
# carry standard Anthropic Messages content blocks (text / thinking /
# tool_use / tool_result) and are the primary content channel for
# Cowork on the L3g axis — they bypass the WS-over-HTTP/2 gap (Q2).
# ---------------------------------------------------------------------------


def _parse_iso8601_to_ms(ts: object) -> Optional[int]:
    """Parse an ISO-8601 timestamp string into milliseconds-since-epoch.

    Returns None for non-string inputs, unparseable strings, or any
    other parse failure. Tolerates trailing ``Z`` and microsecond
    precision (Cowork emits e.g. ``2026-05-11T03:47:02.714Z``).
    """
    if not isinstance(ts, str) or not ts:
        return None
    import datetime as _dt
    s = ts.strip()
    # Python 3.11+ ``fromisoformat`` accepts trailing 'Z' natively;
    # 3.10 and earlier do not. Be defensive across versions.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp() * 1000)


def _find_transcript_jsonl_files(agent_sessions_root: Path) -> Iterator[Path]:
    """Yield every ``*.jsonl`` file under the Cowork transcript layout.

    Walks::

        <root>/<user_uuid>/<org_uuid>/local_<session_uuid>/
            .claude/projects/<encoded-cwd>/<session_uuid>.jsonl

    Bounded recursion: only descends through structurally-expected
    directory shapes (UUID user dir → UUID org dir → ``local_<uuid>``
    session dir → ``.claude`` → ``projects`` → encoded-cwd dir →
    jsonl files). A malformed deep tree cannot trigger an unbounded
    walk.
    """
    if not agent_sessions_root.exists() or not agent_sessions_root.is_dir():
        return
    try:
        users = list(agent_sessions_root.iterdir())
    except OSError:
        return
    for user_dir in users:
        if not user_dir.is_dir() or not _looks_like_uuid(user_dir.name):
            continue
        try:
            orgs = list(user_dir.iterdir())
        except OSError:
            continue
        for org_dir in orgs:
            if not org_dir.is_dir() or not _looks_like_uuid(org_dir.name):
                continue
            try:
                sessions = list(org_dir.iterdir())
            except OSError:
                continue
            for session_dir in sessions:
                if not session_dir.is_dir():
                    continue
                if not session_dir.name.startswith("local_"):
                    # Cowork session directories all use the ``local_<uuid>``
                    # naming convention; other top-level files (e.g.
                    # ``cowork-gb-cache.json``, ``local_<uuid>.json``) are
                    # session metadata pointers, not session content roots.
                    continue
                projects = session_dir / ".claude" / "projects"
                if not projects.is_dir():
                    continue
                try:
                    cwds = list(projects.iterdir())
                except OSError:
                    continue
                for cwd in cwds:
                    if not cwd.is_dir():
                        continue
                    try:
                        for f in cwd.iterdir():
                            if f.is_file() and f.suffix == ".jsonl":
                                yield f
                    except OSError:
                        continue


def iter_transcript_records(
    agent_sessions_root: Path,
) -> Iterator[AgentSessionRecord]:
    """Walk Cowork JSONL transcripts and yield one record per LINE.

    One ``AgentSessionRecord`` is emitted per non-blank JSON line.
    ``session_id`` is set from the line's ``sessionId`` field when
    present, falling back to the parent ``local_<uuid>`` directory
    name. ``line_uuid`` is the line's own ``uuid`` (when present) for
    per-line dedup; ``line_index`` is always set as a fallback so
    lines without ``uuid`` (queue-operation, ai-title, last-prompt,
    attachment) still get a stable dedup key.

    Parse failures on a single line are logged at WARNING and skipped;
    the rest of the file is still emitted. File-level read failures
    are logged at DEBUG and the whole file is skipped.
    """
    for jsonl_path in _find_transcript_jsonl_files(agent_sessions_root):
        stat = _stat_safe(jsonl_path)
        if stat is None:
            continue
        mtime_ns, _file_size = stat

        # Derive a fallback session_id from the parent ``local_<uuid>``
        # directory (5 levels up: jsonl → cwd → projects → .claude →
        # local_<session>). Used when a line lacks ``sessionId``.
        try:
            parts = jsonl_path.parts
            session_dir_name = parts[-5]  # local_<uuid>
        except (IndexError, ValueError):
            session_dir_name = jsonl_path.parent.name
        path_session_id: Optional[str] = None
        if session_dir_name.startswith("local_"):
            candidate = session_dir_name[len("local_"):]
            if _looks_like_uuid(candidate):
                path_session_id = candidate

        try:
            with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
                for line_index, raw_line in enumerate(fh):
                    line = raw_line.rstrip("\r\n")
                    if not line.strip():
                        continue
                    try:
                        body = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "malformed jsonl line at %s:%d: %s",
                            jsonl_path, line_index, exc,
                        )
                        continue
                    if not isinstance(body, dict):
                        logger.debug(
                            "skipping non-dict jsonl line at %s:%d (type=%s)",
                            jsonl_path, line_index, type(body).__name__,
                        )
                        continue

                    # session_id: prefer the line's sessionId, fall back
                    # to path-derived. Both Anthropic camelCase and
                    # snake_case have been observed in the wild — accept
                    # either.
                    line_session_id = (
                        body.get("sessionId")
                        or body.get("session_id")
                        or path_session_id
                    )

                    # line_uuid: prefer the line's uuid for stable dedup;
                    # if absent, the record will use line_index as the
                    # dedup key in the observer.
                    line_uuid = body.get("uuid") if isinstance(body.get("uuid"), str) else None

                    # timestamp → last_updated_ms (best-effort)
                    last_updated_ms = _parse_iso8601_to_ms(body.get("timestamp"))

                    yield AgentSessionRecord(
                        kind="transcript_line",
                        session_id=line_session_id,
                        source_path=jsonl_path,
                        mtime_ns=mtime_ns,
                        size_bytes=len(line.encode("utf-8", errors="replace")),
                        body_json=body,
                        last_updated_ms=last_updated_ms,
                        line_uuid=line_uuid,
                        line_index=line_index,
                    )
        except OSError as exc:
            logger.debug("cannot read jsonl %s: %s", jsonl_path, exc)
            continue


# ---------------------------------------------------------------------------
# Inline Code-tab JSONL transcript walker (P5.B.7, 2026-05-11)
#
# Discovered via the Code-tab RECON drive on 2026-05-11 (see
# ``Docs/research/2026-05-11-code-tab-recon-findings.md``): Claude
# Desktop's inline Code tab spawns the bundled ``claude.exe`` agent
# which writes its full transcript to::
#
#   ~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl
#
# The format is the standard Claude Code CLI JSONL (same parser that
# Cowork uses, minus the user/org/local_<sess>/.claude/projects nesting
# wrapper). Each line carries an ``entrypoint`` discriminator:
#
# - ``"claude-desktop"`` → Desktop Code tab (P1 P5.B.7 scope)
# - ``"cli"`` (or absent) → standalone Claude Code CLI (P6, deferred)
#
# The normaliser maps ``entrypoint`` to ``tool_family`` so a single
# walker feeding into the existing observer pipeline correctly
# distinguishes the two products without path-level branching.
# ---------------------------------------------------------------------------


def _find_code_tab_transcript_jsonl_files(
    claude_projects_root: Path,
) -> Iterator[Path]:
    """Yield every ``*.jsonl`` file under the flat Code-tab layout.

    Walks::

        <root>/<encoded-cwd>/<cliSessionId>.jsonl

    Bounded recursion: only descends one level (encoded-cwd dirs) and
    yields direct ``.jsonl`` children. Subdirectories under
    ``<encoded-cwd>/`` (e.g. ``subagents/``) are NOT followed in v0 —
    they hold subagent transcripts whose ingestion semantics differ
    and will be added in a follow-up sub-phase.
    """
    if not claude_projects_root.exists() or not claude_projects_root.is_dir():
        return
    try:
        cwd_dirs = list(claude_projects_root.iterdir())
    except OSError:
        return
    for cwd_dir in cwd_dirs:
        if not cwd_dir.is_dir():
            continue
        try:
            children = list(cwd_dir.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_file() and child.suffix == ".jsonl":
                yield child


def _detect_file_entrypoint(jsonl_path: Path) -> Optional[str]:
    """Pre-scan a JSONL file for any line carrying an ``entrypoint`` field.

    The Claude agent only stamps ``entrypoint`` on the very first user
    message of a session (empirically: 12 out of 4707 lines on a
    real install). Subsequent assistant / tool / queue-operation /
    attachment / progress lines all inherit the session's entrypoint
    implicitly but do not re-emit the field.

    For PCE's discriminator-driven ``tool_family`` selection
    (``claude-desktop-code`` vs ``cowork-local-agent``) to work on
    *every* line of a Code-tab session — not just the first — we
    hoist the entrypoint to a file-level fact at the walker boundary
    and inject it into every yielded line's ``body_json`` (without
    overwriting an existing field).

    Returns the first non-empty string entrypoint observed, or
    ``None`` if no line in the file declared one.
    """
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.rstrip("\r\n").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                ep = obj.get("entrypoint")
                if isinstance(ep, str) and ep:
                    return ep
    except OSError:
        return None
    return None


def iter_code_tab_transcript_records(
    claude_projects_root: Path,
) -> Iterator[AgentSessionRecord]:
    """Walk ``~/.claude/projects/`` JSONL transcripts and yield one record per LINE.

    Shape-identical to :func:`iter_transcript_records` so the
    ``ChromiumStateObserver.observe_agent_session`` pipeline handles
    Code-tab records via the same ``transcript_line`` branch — the
    normaliser's ``entrypoint`` discriminator does the
    ``claude-desktop-code`` vs ``cowork-local-agent`` routing
    downstream.

    Differences from the cowork walker:

    - Flat layout: 2 levels deep (cwd → jsonl) instead of 6
      (user/org/local_sess/.claude/projects/cwd → jsonl).
    - Fallback ``session_id``: derived from the filename stem
      (which equals ``cliSessionId``) when the line lacks a
      ``sessionId`` field, instead of the cowork ``local_<uuid>``
      ancestor directory.
    - **File-level entrypoint hoist** (P5.B.7 fix, 2026-05-11): the
      JSONL ``entrypoint`` field appears only on the first user line
      of a session, so we pre-scan once per file and inject the
      detected entrypoint into every yielded line's ``body_json``.
      This makes the normaliser's per-line discriminator behave as
      a sticky session-level tag, which is what
      ``upgrade_session_metadata`` requires (it refuses to overwrite
      an already-set ``tool_family``).
    """
    for jsonl_path in _find_code_tab_transcript_jsonl_files(claude_projects_root):
        stat = _stat_safe(jsonl_path)
        if stat is None:
            continue
        mtime_ns, _file_size = stat

        # Fallback session_id: filename stem == cliSessionId (UUID-shaped).
        # Used when a line lacks ``sessionId``; the field is rarely
        # absent in observed Code-tab transcripts but defensive.
        path_session_id: Optional[str] = None
        stem = jsonl_path.stem
        if _looks_like_uuid(stem):
            path_session_id = stem

        # Resolve the file's entrypoint once. For Code-tab sessions
        # this returns ``"claude-desktop"``; for legacy CLI sessions
        # it typically returns ``"cli"`` or ``None``.
        file_entrypoint = _detect_file_entrypoint(jsonl_path)

        try:
            with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
                for line_index, raw_line in enumerate(fh):
                    line = raw_line.rstrip("\r\n")
                    if not line.strip():
                        continue
                    try:
                        body = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "malformed jsonl line at %s:%d: %s",
                            jsonl_path, line_index, exc,
                        )
                        continue
                    if not isinstance(body, dict):
                        logger.debug(
                            "skipping non-dict jsonl line at %s:%d (type=%s)",
                            jsonl_path, line_index, type(body).__name__,
                        )
                        continue

                    # Hoist file-level entrypoint into every line that
                    # didn't carry it natively. Never overwrite an
                    # existing entrypoint field — that would mask a
                    # genuine per-line discriminator if one ever
                    # appears in a future schema rev.
                    if file_entrypoint and not body.get("entrypoint"):
                        body["entrypoint"] = file_entrypoint

                    line_session_id = (
                        body.get("sessionId")
                        or body.get("session_id")
                        or path_session_id
                    )
                    line_uuid = (
                        body.get("uuid") if isinstance(body.get("uuid"), str) else None
                    )
                    last_updated_ms = _parse_iso8601_to_ms(body.get("timestamp"))

                    yield AgentSessionRecord(
                        kind="transcript_line",
                        session_id=line_session_id,
                        source_path=jsonl_path,
                        mtime_ns=mtime_ns,
                        size_bytes=len(line.encode("utf-8", errors="replace")),
                        body_json=body,
                        last_updated_ms=last_updated_ms,
                        line_uuid=line_uuid,
                        line_index=line_index,
                    )
        except OSError as exc:
            logger.debug("cannot read jsonl %s: %s", jsonl_path, exc)
            continue


# ---------------------------------------------------------------------------
# Code-tab SUB-AGENT transcripts (P5.B.7.P2, 2026-05-12)
# ---------------------------------------------------------------------------
#
# When the main Code-tab session spawns a sub-agent via the ``Task``
# tool, the sub-agent's conversation tree is persisted to a SEPARATE
# JSONL file under::
#
#     ~/.claude/projects/<encoded-cwd>/<parentSessionId>/subagents/agent-<agentId>.jsonl
#
# RECON 2026-05-12 on the reference machine confirmed the layout is
# FOUR levels deep, not three:
#
# - The encoded-cwd directory holds BOTH the flat
#   ``<sessionId>.jsonl`` main transcripts (P5.B.7 P1 capture) AND,
#   for sessions that spawned at least one sub-agent, a sibling
#   DIRECTORY also named ``<sessionId>/`` whose only child is
#   ``subagents/`` containing one or more ``agent-<id>.jsonl``.
# - The sub-agent's lines carry the PARENT session's ``sessionId``
#   field — they are NOT a new top-level session by Claude's data
#   model. The discriminator is ``agentId`` (16-hex like
#   ``"a84429dd11b8f1cb8"``) and ``isSidechain: true``.
# - The first user line has ``parentUuid: null`` because the
#   sub-agent's conversation tree is rooted independently.
# - The first line has NO ``entrypoint`` field. Sub-agents only spawn
#   from the Code-tab Task tool today, so the walker hardcodes
#   ``entrypoint="claude-desktop"`` for downstream tool_family
#   routing. A standalone CLI Task tool that wrote subagent JSONLs
#   would currently be mis-attributed; that is acceptable for v0
#   because no such writer is known and the fix (read parent
#   ``<parentSessId>.jsonl``'s entrypoint) is local to this walker.
# - New ``type`` value: ``"progress"`` (streaming sub-agent execution
#   progress events). It joins ``user`` / ``assistant`` /
#   ``queue-operation`` etc. in the type histogram and lands in
#   ``content_text`` as ``"[Progress: ...]"`` via the normaliser's
#   existing fallback path.


def _find_code_tab_subagent_jsonl_files(
    claude_projects_root: Path,
) -> Iterator[Path]:
    """Yield every sub-agent ``agent-*.jsonl`` under the Code-tab layout.

    Walks the four-level layout::

        <root>/<encoded-cwd>/<parentSessionId>/subagents/agent-*.jsonl

    Bounded recursion: descends into encoded-cwd dirs, then into
    UUID-shaped session subdirs (skipping any other shape — they're
    out of scope for this walker), then into ``subagents/`` children.
    Yields the ``agent-*.jsonl`` files; everything else is ignored.

    Returns silently if ``claude_projects_root`` does not exist or
    is not a directory.
    """
    if not claude_projects_root.exists() or not claude_projects_root.is_dir():
        return
    try:
        cwd_dirs = list(claude_projects_root.iterdir())
    except OSError:
        return
    for cwd_dir in cwd_dirs:
        if not cwd_dir.is_dir():
            continue
        try:
            sess_entries = list(cwd_dir.iterdir())
        except OSError:
            continue
        for sess_dir in sess_entries:
            # The encoded-cwd dir holds a mix of ``<sessId>.jsonl``
            # files (main transcripts, handled by the P1 walker) and
            # ``<sessId>/`` directories (subagent containers, handled
            # here). Filter to directories whose name is a UUID.
            if not sess_dir.is_dir():
                continue
            if not _looks_like_uuid(sess_dir.name):
                continue
            sub_dir = sess_dir / "subagents"
            if not sub_dir.is_dir():
                continue
            try:
                children = list(sub_dir.iterdir())
            except OSError:
                continue
            for child in children:
                if (
                    child.is_file()
                    and child.suffix == ".jsonl"
                    and child.name.startswith("agent-")
                ):
                    yield child


def iter_code_tab_subagent_records(
    claude_projects_root: Path,
) -> Iterator[AgentSessionRecord]:
    """Walk Code-tab sub-agent JSONLs and yield one record per LINE.

    Each yielded record uses ``kind="transcript_line"`` so the
    LocalPersistenceNormalizer routes it through the same session +
    message machinery as the main transcript. To give the sub-agent
    its own session row, the walker REWRITES the line's
    ``sessionId`` to the composite key
    ``"<parentSessionId>__agent_<agentId>"`` and stamps three
    auxiliary fields onto the body so downstream consumers can
    surface the parent link without re-parsing the file path:

    - ``parent_session_id`` - original sessionId from the JSONL line
    - ``agent_id`` - agentId from the JSONL line
    - ``is_subagent: true`` - convenience flag for dashboard queries

    The injection only happens when the line was identified as a
    sub-agent line (``isSidechain == True`` and ``agentId`` is a
    non-empty string). Other lines are emitted unmodified.

    The walker also hoists ``entrypoint="claude-desktop"`` onto every
    line that lacks one. See module docstring for the rationale and
    the deferred CLI-Task-tool fix.
    """
    for jsonl_path in _find_code_tab_subagent_jsonl_files(claude_projects_root):
        stat = _stat_safe(jsonl_path)
        if stat is None:
            continue
        mtime_ns, _file_size = stat

        try:
            with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
                for line_index, raw_line in enumerate(fh):
                    line = raw_line.rstrip("\r\n")
                    if not line.strip():
                        continue
                    try:
                        body = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "malformed subagent jsonl line at %s:%d: %s",
                            jsonl_path, line_index, exc,
                        )
                        continue
                    if not isinstance(body, dict):
                        continue

                    parent_sess = body.get("sessionId")
                    agent_id = body.get("agentId")
                    if not (
                        isinstance(parent_sess, str)
                        and isinstance(agent_id, str)
                        and parent_sess
                        and agent_id
                    ):
                        # Unknown shape — keep the line but don't
                        # rewrite session_id; the normaliser will
                        # treat it like any other transcript line.
                        composite_id: Optional[str] = parent_sess if isinstance(parent_sess, str) else None
                    else:
                        composite_id = f"{parent_sess}__agent_{agent_id}"
                        body["parent_session_id"] = parent_sess
                        body["agent_id"] = agent_id
                        body["is_subagent"] = True
                        # Override the line's sessionId so the
                        # normaliser builds a separate session row.
                        # The original is preserved under
                        # parent_session_id (set above).
                        body["sessionId"] = composite_id

                    # Sub-agents only spawn from the Code-tab Task
                    # tool today, so the entrypoint is fixed. See
                    # the module-level comment for the CLI caveat.
                    if not body.get("entrypoint"):
                        body["entrypoint"] = "claude-desktop"

                    line_uuid = (
                        body.get("uuid") if isinstance(body.get("uuid"), str) else None
                    )
                    last_updated_ms = _parse_iso8601_to_ms(body.get("timestamp"))

                    yield AgentSessionRecord(
                        kind="transcript_line",
                        session_id=composite_id,
                        source_path=jsonl_path,
                        mtime_ns=mtime_ns,
                        size_bytes=len(line.encode("utf-8", errors="replace")),
                        body_json=body,
                        last_updated_ms=last_updated_ms,
                        line_uuid=line_uuid,
                        line_index=line_index,
                    )
        except OSError as exc:
            logger.debug("cannot read subagent jsonl %s: %s", jsonl_path, exc)
            continue


def iter_code_tab_pointer_records(
    claude_code_sessions_root: Path,
) -> Iterator[AgentSessionRecord]:
    """Walk ``claude-code-sessions/`` and yield per-session metadata pointers.

    Layout::

        <root>/<user_uuid>/<org_uuid>/local_<sessionId>.json

    Each pointer is ~1 KB JSON with fields::

        sessionId, cliSessionId, cwd, model, title, titleSource,
        permissionMode, enabledMcpTools{}, sessionPermissionUpdates[],
        createdAt, lastActivityAt

    Yields one ``AgentSessionRecord(kind="code_tab_session_pointer")``
    per pointer found. The pointer's ``cliSessionId`` field is the
    join key to the transcript JSONL filename
    (``~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl``).

    Returns silently if root is missing.
    """
    if not claude_code_sessions_root.exists() or not claude_code_sessions_root.is_dir():
        return
    try:
        users = list(claude_code_sessions_root.iterdir())
    except OSError:
        return
    for user_dir in users:
        if not user_dir.is_dir() or not _looks_like_uuid(user_dir.name):
            continue
        try:
            orgs = list(user_dir.iterdir())
        except OSError:
            continue
        for org_dir in orgs:
            if not org_dir.is_dir() or not _looks_like_uuid(org_dir.name):
                continue
            try:
                files = list(org_dir.iterdir())
            except OSError:
                continue
            for f in files:
                if not f.is_file() or f.suffix != ".json":
                    continue
                if not f.name.startswith("local_"):
                    continue
                body = _safe_read_json(f)
                if body is None:
                    continue
                stat = _stat_safe(f)
                if stat is None:
                    continue
                mtime_ns, size = stat
                # Pointer's sessionId == local_<uuid>; use it as session_id.
                pointer_session_id = body.get("sessionId")
                if not isinstance(pointer_session_id, str):
                    pointer_session_id = None
                # Last-activity time when present; else createdAt.
                last_updated_ms: Optional[int] = None
                for key in ("lastActivityAt", "createdAt"):
                    v = body.get(key)
                    if isinstance(v, int):
                        last_updated_ms = v
                        break
                yield AgentSessionRecord(
                    kind="code_tab_session_pointer",
                    session_id=pointer_session_id,
                    source_path=f,
                    mtime_ns=mtime_ns,
                    size_bytes=size,
                    body_json=body,
                    last_updated_ms=last_updated_ms,
                )


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
