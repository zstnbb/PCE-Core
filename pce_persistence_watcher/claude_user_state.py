# SPDX-License-Identifier: Apache-2.0
"""pce_persistence_watcher.claude_user_state - capture ``~/.claude/*`` user-level state.

P5.B.7.P2 (2026-05-12): Claude Code's persistent state lives in the
user-home ``~/.claude/`` directory plus the top-level
``~/.claude.json`` file. These complement the conversation
transcripts in ``~/.claude/projects/`` (already captured by
``agent_sessions.iter_code_tab_transcript_records``) and the
Desktop-side session pointers in ``claude-code-sessions/`` (captured
by ``agent_sessions.iter_code_tab_pointer_records``).

This module emits records for EIGHT surfaces (P2.1 audit 2026-05-12
extended the original five with three more after a full
``Get-ChildItem ~/.claude`` walk found surfaces missed by the
documented-state-only RECON):

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

6. **PID-keyed session metadata** (``~/.claude/sessions/<pid>.json``,
   P2.1). One small (~228 B) JSON per recently-active CLI / Code-tab
   process. Body shape: ``{pid, sessionId, cwd, startedAt,
   procStart, version, peerProtocol, kind, entrypoint}``. This is
   the **PID ↔ sessionId Rosetta Stone** — the only on-disk surface
   that ties an OS process to a Claude session, and the
   ``entrypoint`` field directly discriminates desktop vs. CLI
   without re-reading the transcript JSONL.

7. **User-defined sub-agents** (``~/.claude/agents/<name>.md``,
   P2.1). User-authored markdown files with YAML frontmatter
   defining a custom Task-tool sub-agent (name, description, model,
   colour, tools). The body is the system prompt. Treated as
   user-content; not redacted.

8. **Plugin install state** (``~/.claude/plugins/{installed_plugins,
   blocklist,known_marketplaces,config}.json``, P2.1). Four JSON
   files at the plugins/ root: which plugins the user has
   installed (per-project), which marketplaces are configured,
   which plugins are on the user's blocklist (with reasons), and
   the active repository config. The ``cache/``, ``repos/``,
   ``marketplaces/`` subdirs are NOT walked - they hold ~3 MB of
   plugin source files that mirror upstream git repos and offer no
   PCE-specific signal.

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
- ``~/.claude/sessions/<pid>.json``, ``~/.claude/agents/*.md``,
  and ``~/.claude/plugins/*.json`` (P2.1) are NOT redacted - the
  RECON on the reference machine confirmed none of them carry
  secret-shaped fields. (``sessions/<pid>.json`` is purely
  structural metadata; ``agents/*.md`` is user-authored content
  by definition; ``plugins/*.json`` carries install paths +
  blocklist reasons but no credentials.) If future Claude
  versions add secrets to any of these the redactor must be
  extended; the unit tests in ``test_p2_user_state_and_subagent``
  pin this assumption.

Record kinds emitted
--------------------
- ``kind="user_state_snapshot"`` with ``surface`` in
  {``"user_state_global"``, ``"user_state_settings"``,
  ``"user_state_settings_local"``, ``"user_state_todos"``,
  ``"user_state_pid_session"`` (P2.1),
  ``"user_state_agents"`` (P2.1),
  ``"user_state_plugins"`` (P2.1)} - point-in-time JSON
  snapshots; the capture observer dedups by content hash so an
  unchanged file is only emitted once.

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
# P2.1 surfaces (2026-05-12) — sessions/<pid>.json, agents/*.md, plugins/*.json
# ---------------------------------------------------------------------------


# Plugin-state files are a fixed allow-list at the plugins/ root.
# We deliberately exclude ``cache/``, ``repos/``, ``marketplaces/``
# subdirs (they hold ~3 MB of mirrored upstream plugin source and
# would be high-volume + low-signal in raw_captures).
_PLUGIN_STATE_FILES: tuple[str, ...] = (
    "installed_plugins.json",
    "blocklist.json",
    "known_marketplaces.json",
    "config.json",
)


def _parse_md_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse simple YAML-style frontmatter from a markdown file.

    Returns ``(frontmatter_dict, body_text)``. Frontmatter is the
    block delimited by leading and trailing ``---`` lines (Jekyll /
    Claude Code agent file convention). Each non-indented
    ``key: value`` line becomes a dict entry; indented or
    continuation lines are appended to the previous key's value
    (lenient YAML subset — sufficient for the
    ``{name, description, model, color, tools}`` shape Claude Code
    emits).

    If no opening ``---`` is found, returns ``({}, text)`` (whole
    file becomes body). If the opening ``---`` is found but the
    closing ``---`` is missing, returns ``({}, text)`` as well —
    we don't want to swallow the entire file as frontmatter on a
    truncated parse.

    No external YAML dependency: we don't add ``pyyaml`` for the
    handful of trivial key:value lines Claude Code writes here.
    """
    if not isinstance(text, str):
        return {}, ""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm: dict[str, str] = {}
    last_key: Optional[str] = None
    end_idx = -1
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            end_idx = i
            break
        # Top-level key (no leading whitespace, contains ``:``).
        if line and not line[0].isspace():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip():
                k = parts[0].strip()
                v = parts[1].strip()
                fm[k] = v
                last_key = k
                continue
        # Continuation line — append to last key's value.
        if last_key is not None:
            fm[last_key] = (fm[last_key] + "\n" + line).rstrip()
    if end_idx < 0:
        # No closing ``---``; treat as malformed and don't strip.
        return {}, text
    body = "\n".join(lines[end_idx + 1:]).lstrip("\n")
    return fm, body


def _emit_pid_sessions(claude_home: Path) -> Iterator[AgentSessionRecord]:
    """Yield one record per ``~/.claude/sessions/<pid>.json``.

    Each file is small (~228 B) and carries the body shape
    ``{pid, sessionId, cwd, startedAt, procStart, version,
    peerProtocol, kind, entrypoint}`` (P2.1 RECON 2026-05-12).
    The ``sessionId`` field is propagated to ``rec.session_id`` so
    the dashboard can JOIN this PID-keyed snapshot back to the
    actual session row materialised by the transcript walker.

    Filename filter: only ``<integer>.json`` files (defensive
    against Claude writing other shapes here in the future).
    """
    sessions_dir = claude_home / "sessions"
    if not sessions_dir.is_dir():
        return
    try:
        files = list(sessions_dir.iterdir())
    except OSError:
        return
    for f in files:
        if not f.is_file() or f.suffix != ".json":
            continue
        # Only ``<pid>.json`` — pid is a positive integer.
        if not f.stem.isdigit():
            continue
        body = _safe_read_json(f)
        if not isinstance(body, dict):
            continue
        stat = _stat_safe(f)
        if stat is None:
            continue
        mtime_ns, size = stat
        sess_id = body.get("sessionId")
        if not isinstance(sess_id, str):
            sess_id = None
        started_at = body.get("startedAt")
        last_updated_ms = started_at if isinstance(started_at, int) else None
        yield AgentSessionRecord(
            kind="user_state_snapshot",
            session_id=sess_id,
            source_path=f,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=body,
            last_updated_ms=last_updated_ms,
            surface="user_state_pid_session",
        )


def _emit_user_agents(claude_home: Path) -> Iterator[AgentSessionRecord]:
    """Yield one record per ``~/.claude/agents/<name>.md``.

    User-defined sub-agent prompts. Each file is markdown with
    YAML frontmatter; body is the system prompt.

    Emitted body shape::

        {
            "name": "<frontmatter.name or filename stem>",
            "filename": "<basename>",
            "frontmatter": {<parsed key:value pairs>},
            "system_prompt": "<body text after the closing --->",
        }

    Not redacted — the content is user-authored by definition.
    """
    agents_dir = claude_home / "agents"
    if not agents_dir.is_dir():
        return
    try:
        files = list(agents_dir.iterdir())
    except OSError:
        return
    for f in files:
        if not f.is_file() or f.suffix.lower() != ".md":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.debug("cannot read agent file %s: %s", f, exc)
            continue
        stat = _stat_safe(f)
        if stat is None:
            continue
        mtime_ns, size = stat
        fm, body_text = _parse_md_frontmatter(text)
        name = fm.get("name") or f.stem
        body: dict[str, Any] = {
            "name": name,
            "filename": f.name,
            "frontmatter": fm,
            "system_prompt": body_text,
        }
        yield AgentSessionRecord(
            kind="user_state_snapshot",
            session_id=None,
            source_path=f,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=body,
            last_updated_ms=None,
            surface="user_state_agents",
        )


def _emit_plugin_state(claude_home: Path) -> Iterator[AgentSessionRecord]:
    """Yield one record per top-level plugin-state JSON file.

    Walks the four files in :data:`_PLUGIN_STATE_FILES` only:
    ``installed_plugins.json``, ``blocklist.json``,
    ``known_marketplaces.json``, ``config.json``. Subdirs
    (``cache/``, ``repos/``, ``marketplaces/``) are intentionally
    skipped — they're plugin source mirrors, not PCE-relevant
    state.

    Emitted body wraps the parsed JSON under
    ``{"filename": "<name>.json", "data": <parsed>}`` so multiple
    plugin-state captures can be distinguished without
    re-parsing their path.

    Not redacted — RECON 2026-05-12 confirmed no secret-shaped
    fields in any of the four files on the reference machine.
    """
    plugins_dir = claude_home / "plugins"
    if not plugins_dir.is_dir():
        return
    for fname in _PLUGIN_STATE_FILES:
        f = plugins_dir / fname
        if not f.is_file():
            continue
        body = _safe_read_json(f)
        if body is None:
            continue
        stat = _stat_safe(f)
        if stat is None:
            continue
        mtime_ns, size = stat
        wrapped: dict[str, Any] = {"filename": fname, "data": body}
        yield AgentSessionRecord(
            kind="user_state_snapshot",
            session_id=None,
            source_path=f,
            mtime_ns=mtime_ns,
            size_bytes=size,
            body_json=wrapped,
            last_updated_ms=None,
            surface="user_state_plugins",
        )


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
      (global state, settings, settings.local, todos, pid_session,
      agents, plugins).
    - ``"user_state_line"`` for ``history.jsonl`` lines.

    Returns silently if ``claude_home`` does not exist.
    """
    if not claude_home.is_dir():
        return
    yield from _emit_global_state(claude_home.parent)
    yield from _emit_settings(claude_home)
    yield from _emit_todos(claude_home)
    yield from _emit_history(claude_home)
    # P2.1 (2026-05-12) — surfaces caught by the post-P2 audit.
    yield from _emit_pid_sessions(claude_home)
    yield from _emit_user_agents(claude_home)
    yield from _emit_plugin_state(claude_home)


def count_claude_user_state(claude_home: Path) -> dict[str, int]:
    """Return per-surface counts for ``discover`` mode output."""
    counts: dict[str, int] = {"user_state": 0}
    for rec in iter_claude_user_state_records(claude_home):
        counts["user_state"] += 1
        surf = rec.surface or "unknown"
        counts[surf] = counts.get(surf, 0) + 1
    return counts
