# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.claude_user_state - capture ``~/.claude/*`` user-level state.

P5.B.7.P2 (2026-05-12): Claude Code's persistent state lives in the
user-home ``~/.claude/`` directory plus the top-level
``~/.claude.json`` file. These complement the conversation
transcripts in ``~/.claude/projects/`` (already captured by
``agent_sessions.iter_code_tab_transcript_records``) and the
Desktop-side session pointers in ``claude-code-sessions/`` (captured
by ``agent_sessions.iter_code_tab_pointer_records``).

This module emits records for FIVE surfaces:

1. **Global state** (``~/.claude.json``, ~10 KB JSON). Top-level
   config for Claude Code / Desktop. Carries ``mcpServers``,
   per-project state (``projects[<cwd>].{lastSessionId, lastCost,
   lastAPIDuration, allowedTools[], mcpServers,
   hasTrustDialogAccepted}``), ``toolUsage`` (full tool palette
   histogram), ``userID``, ``oauthAccount``, ``numStartups``,
   ``installMethod``, ``cachedStatsigGates``, etc.

2. **User-level settings** (``~/.claude/settings.json``). Carries
   ``env``, ``permissions.allow[]`` / ``permissions.deny[]``,
   ``model``, ``MCP_TIMEOUT``, ``MCP_TOOL_TIMEOUT``.

3. **User-level settings overrides** (``~/.claude/settings.local.json``).
   Same shape as settings.json, applied as personal overrides on
   top of project + user settings.

4. **Todos** (``~/.claude/todos/<sessId>-agent-<agentId>.json``).
   TodoWrite tool products. Each file is a JSON array of
   ``{content, status, activeForm}`` objects; ``status`` is one of
   ``pending`` / ``in_progress`` / ``completed``.

5. **Slash command + prompt history** (``~/.claude/history.jsonl``).
   One line per prompt submission, including ``/clear`` etc. Fields:
   ``{display, pastedContents, timestamp, project, sessionId}``.

Redaction policy
----------------
Secrets are scrubbed at the walker boundary, **before** records
are handed to the capture observer. The RECON on the reference
machine (2026-05-12) found a live ``ANTHROPIC_AUTH_TOKEN`` in
``settings.json`` ``env`` block; this module guarantees no such
value can land in raw_captures.

Rules:

- ``~/.claude.json`` drops ``userID``, ``oauthAccount.*``, and
  ``clientDataCache.*``; scrubs ``mcpServers[*].env.*`` values
  whose key matches the secret-suffix allow-list.
- ``~/.claude/settings*.json`` scrubs ``env.*`` values whose key
  matches the same secret-suffix pattern (case-insensitive).
- ``~/.claude/todos/*.json`` and ``history.jsonl`` are
  user-content — NOT redacted, because the content is the user's
  own task descriptions / prompts. Operators who need stricter
  hygiene can use the watcher's ``--no-bodies`` flag to drop
  bodies entirely.

Record kinds emitted
--------------------
- ``kind="user_state_snapshot"`` with ``surface`` in
  {``"user_state_global"``, ``"user_state_settings"``,
  ``"user_state_settings_local"``, ``"user_state_todos"``} -
  point-in-time JSON snapshots; the capture observer dedups by
  content hash so an unchanged file is only emitted once.

- ``kind="user_state_line"`` with ``surface="user_state_history"``
  - one record per non-blank line of ``history.jsonl``; dedup
  uses ``line_index`` (the file has no per-line uuid).

Schema impact: none. The capture observer routes both kinds to
``host="local-config"`` with path
``/{app_id}/user-state/{surface}[/{key}]``. No sessions table row
is created for these records - they are config-level metadata,
not conversation turns.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator, Optional

from .agent_sessions import AgentSessionRecord

logger = logging.getLogger("pce.persistence_watcher.claude_user_state")


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

# Keys whose names end with any of these suffixes (case-insensitive,
# match against full key) are scrubbed before yielding. Chosen by
# surveying real SDKs/CI envs - we lean over-redacted because a false
# positive only means one env value becomes unreadable in raw_captures,
# while a false negative leaks a real secret.
_SECRET_SUFFIXES: tuple[str, ...] = (
    "_TOKEN",
    "_KEY",
    "_SECRET",
    "_PASSWORD",
    "_PASSWD",
    "_API_KEY",
    "_ACCESS_KEY",
    "_PRIVATE_KEY",
)

# Sentinel value substituted in place of a scrubbed secret. Includes
# the package name so anyone seeing the redaction can grep for the
# source.
_REDACTED = "<redacted-by-pce-watcher>"


def _looks_like_secret_key(key: str) -> bool:
    """Return True when an env-style key name suggests a credential.

    Matches case-insensitive suffixes commonly used by SDKs and CI
    systems (``ANTHROPIC_AUTH_TOKEN``, ``OPENAI_API_KEY``,
    ``GITHUB_TOKEN``, ``DATABASE_PASSWORD`` etc.).
    """
    if not isinstance(key, str):
        return False
    upper = key.upper()
    return any(upper.endswith(s) for s in _SECRET_SUFFIXES)


def _redact_env_block(env: Any) -> Any:
    """Scrub secret-shaped keys in a ``{"KEY": "VAL"}`` env dict.

    Returns a new dict (does not mutate input). Non-dict input is
    returned unchanged so the redactor is safe to apply blindly.
    """
    if not isinstance(env, dict):
        return env
    out: dict[str, Any] = {}
    for k, v in env.items():
        if _looks_like_secret_key(k):
            out[k] = _REDACTED
        else:
            out[k] = v
    return out


def _redact_global_state(body: dict) -> dict:
    """Strip PII / secrets from a ``~/.claude.json`` snapshot.

    Drops (replaced with the redaction sentinel):

    - ``userID`` (account-level identifier)
    - ``oauthAccount`` (account UUID + email + org UUID)
    - ``clientDataCache`` (often holds session-affecting
      personalisation; large blob; may contain PII)

    Scrubs:

    - ``mcpServers.<name>.env.*`` values with secret-suffix key.

    Preserves everything else, including ``mcpServers[*].command``
    /``args``/``url``/``type`` (the structural config), and
    per-project state under ``projects.<path>.*``.
    """
    if not isinstance(body, dict):
        return body
    out: dict[str, Any] = {}
    for k, v in body.items():
        if k in ("userID", "oauthAccount", "clientDataCache"):
            out[k] = _REDACTED
            continue
        if k == "mcpServers" and isinstance(v, dict):
            new_mcp: dict[str, Any] = {}
            for name, cfg in v.items():
                if isinstance(cfg, dict):
                    cfg_copy = dict(cfg)
                    if "env" in cfg_copy:
                        cfg_copy["env"] = _redact_env_block(cfg_copy["env"])
                    new_mcp[name] = cfg_copy
                else:
                    new_mcp[name] = cfg
            out[k] = new_mcp
            continue
        out[k] = v
    return out


def _redact_settings(body: dict) -> dict:
    """Strip secret env values from a settings.json / settings.local.json.

    Returns a new dict with the ``env`` block scrubbed; permissions
    and other keys are passed through unchanged.
    """
    if not isinstance(body, dict):
        return body
    out: dict[str, Any] = {}
    for k, v in body.items():
        if k == "env":
            out[k] = _redact_env_block(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stat_safe(path: Path) -> Optional[tuple[int, int]]:
    """Return ``(mtime_ns, size)`` or None on stat failure."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _safe_read_json(path: Path) -> Optional[Any]:
    """Read+parse JSON; log+return None on any failure."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("cannot read json at %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Per-surface emitters
# ---------------------------------------------------------------------------


def _emit_global_state(home_dir: Path) -> Iterator[AgentSessionRecord]:
    """Yield one record for ``<home_dir>/.claude.json`` if present."""
    path = home_dir / ".claude.json"
    if not path.is_file():
        return
    body = _safe_read_json(path)
    if not isinstance(body, dict):
        return
    stat = _stat_safe(path)
    if stat is None:
        return
    mtime_ns, size = stat
    redacted = _redact_global_state(body)
    yield AgentSessionRecord(
        kind="user_state_snapshot",
        session_id=None,
        source_path=path,
        mtime_ns=mtime_ns,
        size_bytes=size,
        body_json=redacted,
        last_updated_ms=None,
        surface="user_state_global",
    )


def _emit_settings(claude_home: Path) -> Iterator[AgentSessionRecord]:
    """Yield records for ``settings.json`` and ``settings.local.json``.

    Both files are scrubbed via :func:`_redact_settings` before yield.
    """
    for fname, surface in (
        ("settings.json", "user_state_settings"),
        ("settings.local.json", "user_state_settings_local"),
    ):
        path = claude_home / fname
        if not path.is_file():
            continue
        body = _safe_read_json(path)
        if not isinstance(body, dict):
            continue
        stat = _stat_safe(path)
        if stat is None:
            continue
        mtime_ns, size = stat
        yield AgentSessionRecord(
            kind="user_state_snapshot",
            session_id=None,
            source_path=path,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=_redact_settings(body),
            last_updated_ms=None,
            surface=surface,
        )


def _emit_todos(claude_home: Path) -> Iterator[AgentSessionRecord]:
    """Yield one record per non-empty TodoWrite product file.

    File-name pattern: ``<sessionId>-agent-<agentId>.json`` (per
    RECON 2026-05-12). The walker parses the filename to derive
    ``session_id`` and stamps ``agent_id`` into the body so the
    dashboard can join todos back to the parent session and the
    specific subagent that produced them.

    Empty files (``[]``, 2 bytes) are skipped - on the reference
    machine 689 of 696 todos files were empty, so this prunes
    raw_captures inflation by ~99 %.
    """
    todos_dir = claude_home / "todos"
    if not todos_dir.is_dir():
        return
    try:
        files = list(todos_dir.iterdir())
    except OSError:
        return
    for f in files:
        if not f.is_file() or f.suffix != ".json":
            continue
        stat = _stat_safe(f)
        if stat is None:
            continue
        mtime_ns, size = stat
        # Skip empty ``[]`` files (2-4 bytes).
        if size <= 4:
            continue
        body = _safe_read_json(f)
        if body is None:
            continue
        # Wrap the raw array under a dict so the meta envelope can
        # carry filename + derived join keys consistently with the
        # other snapshot surfaces.
        wrapped: dict[str, Any] = {"todos": body, "filename": f.name}
        stem = f.stem
        parts = stem.split("-agent-", 1)
        if len(parts) == 2:
            wrapped["session_id"] = parts[0]
            wrapped["agent_id"] = parts[1]
        yield AgentSessionRecord(
            kind="user_state_snapshot",
            session_id=wrapped.get("session_id"),
            source_path=f,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=wrapped,
            last_updated_ms=None,
            surface="user_state_todos",
        )


def _emit_history(claude_home: Path) -> Iterator[AgentSessionRecord]:
    """Yield one record per line of ``history.jsonl``.

    Lines have no per-line uuid (verified RECON 2026-05-12), so the
    capture observer's line-based dedup must rely on ``line_index``.
    """
    path = claude_home / "history.jsonl"
    if not path.is_file():
        return
    stat = _stat_safe(path)
    if stat is None:
        return
    mtime_ns, _ = stat
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("cannot open history.jsonl at %s: %s", path, exc)
        return
    try:
        for line_index, raw_line in enumerate(fh):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            try:
                body = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.debug(
                    "malformed history.jsonl line at %s:%d: %s",
                    path, line_index, exc,
                )
                continue
            if not isinstance(body, dict):
                continue
            ts = body.get("timestamp")
            last_updated_ms = ts if isinstance(ts, int) else None
            sess_id = body.get("sessionId")
            if not isinstance(sess_id, str):
                sess_id = None
            yield AgentSessionRecord(
                kind="user_state_line",
                session_id=sess_id,
                source_path=path,
                mtime_ns=mtime_ns,
                size_bytes=len(line.encode("utf-8", errors="replace")),
                body_json=body,
                last_updated_ms=last_updated_ms,
                surface="user_state_history",
                line_uuid=None,
                line_index=line_index,
            )
    finally:
        fh.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def iter_claude_user_state_records(
    claude_home: Path,
) -> Iterator[AgentSessionRecord]:
    """Walk ``~/.claude/`` plus ``~/.claude.json`` (one level up).

    ``claude_home`` is the user-home ``.claude`` directory
    (``Path.home() / ".claude"``). The function also reads
    ``claude_home.parent / ".claude.json"`` because the top-level
    state file lives OUTSIDE ``.claude/`` by Claude Code convention.

    Yields records of two kinds:

    - ``"user_state_snapshot"`` for point-in-time JSON surfaces
      (global state, settings, settings.local, todos).
    - ``"user_state_line"`` for ``history.jsonl`` lines.

    Returns silently if ``claude_home`` does not exist.
    """
    if not claude_home.is_dir():
        return
    yield from _emit_global_state(claude_home.parent)
    yield from _emit_settings(claude_home)
    yield from _emit_todos(claude_home)
    yield from _emit_history(claude_home)


def count_claude_user_state(claude_home: Path) -> dict[str, int]:
    """Return per-surface counts for ``discover`` mode output."""
    counts: dict[str, int] = {"user_state": 0}
    for rec in iter_claude_user_state_records(claude_home):
        counts["user_state"] += 1
        surf = rec.surface or "unknown"
        counts[surf] = counts.get(surf, 0) + 1
    return counts
