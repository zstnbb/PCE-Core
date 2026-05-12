# SPDX-License-Identifier: Apache-2.0
"""tests/e2e_l3g/test_p2_user_state_and_subagent.py - P5.B.7.P2 (2026-05-12).

Covers the P5.B.7.P2 capture surfaces:

1. ``pce_persistence_watcher.claude_user_state`` - five user-home
   surfaces (~/.claude.json + ~/.claude/{settings*.json, todos/*.json,
   history.jsonl}) plus the secret-redaction layer.
2. ``pce_persistence_watcher.agent_sessions.iter_code_tab_subagent_records``
   - four-level layout ``<root>/<encoded-cwd>/<sessId>/subagents/
   agent-*.jsonl`` with composite session_id rewriting.
3. End-to-end ingestion via ``ChromiumStateObserver`` - asserts the
   raw_captures rows land with the expected host/path/meta shape.

All tests are hermetic — they synthesise the relevant tree under
``tmp_path`` and never touch the real ``~/.claude/`` of the test
runner.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from pce_core.db import init_db
from pce_persistence_watcher.agent_sessions import (
    AgentSessionRecord,
    iter_code_tab_subagent_records,
)
from pce_persistence_watcher.capture import ChromiumStateObserver
from pce_persistence_watcher.claude_user_state import (
    _REDACTED,
    _looks_like_secret_key,
    _redact_env_block,
    _redact_global_state,
    _redact_settings,
    count_claude_user_state,
    iter_claude_user_state_records,
)


# ===========================================================================
# Redaction helpers (pure functions)
# ===========================================================================


class TestSecretKeyDetection:
    """``_looks_like_secret_key`` matches common credential suffixes."""

    @pytest.mark.parametrize(
        "key",
        [
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "GITHUB_TOKEN",
            "DATABASE_PASSWORD",
            "AWS_SECRET_ACCESS_KEY",
            "PRIVATE_KEY",
            "MY_TOKEN",
            "lowercase_token",  # case-insensitive
            "Mixed_Case_KEY",
        ],
    )
    def test_matches_secret_suffix(self, key: str) -> None:
        assert _looks_like_secret_key(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "ANTHROPIC_BASE_URL",
            "MCP_TIMEOUT",
            "MCP_TOOL_TIMEOUT",
            "HOME",
            "PATH",
            "USER",
            "PYTHONPATH",
            "model",
        ],
    )
    def test_doesnt_match_clean_key(self, key: str) -> None:
        assert _looks_like_secret_key(key) is False

    @pytest.mark.parametrize("bad", [None, 123, [], {}, b"bytes"])
    def test_rejects_non_string(self, bad: Any) -> None:
        assert _looks_like_secret_key(bad) is False


class TestRedactEnvBlock:
    def test_scrubs_secrets_keeps_clean(self) -> None:
        env = {
            "ANTHROPIC_AUTH_TOKEN": "sk-secret-XXXXXXXXXXXX",
            "ANTHROPIC_BASE_URL": "https://api.example.com",
            "MCP_TIMEOUT": "86400000",
            "DATABASE_PASSWORD": "hunter2",
        }
        out = _redact_env_block(env)
        assert out["ANTHROPIC_AUTH_TOKEN"] == _REDACTED
        assert out["DATABASE_PASSWORD"] == _REDACTED
        assert out["ANTHROPIC_BASE_URL"] == "https://api.example.com"
        assert out["MCP_TIMEOUT"] == "86400000"

    def test_returns_new_dict_not_mutating(self) -> None:
        env = {"MY_TOKEN": "secret"}
        out = _redact_env_block(env)
        assert out is not env
        assert env["MY_TOKEN"] == "secret"  # original untouched
        assert out["MY_TOKEN"] == _REDACTED

    @pytest.mark.parametrize("bad", [None, "not-a-dict", [1, 2], 42])
    def test_non_dict_passes_through(self, bad: Any) -> None:
        assert _redact_env_block(bad) == bad


class TestRedactGlobalState:
    def test_drops_pii_and_secrets(self) -> None:
        body = {
            "userID": "abc123",
            "oauthAccount": {"email": "u@example.com", "accountUuid": "x"},
            "clientDataCache": {"big": "blob"},
            "numStartups": 42,
            "installMethod": "global",
            "mcpServers": {
                "myserver": {
                    "command": "node",
                    "args": ["./srv.js"],
                    "env": {
                        "MY_TOKEN": "secret",
                        "DEBUG_LEVEL": "info",
                    },
                },
            },
            "projects": {"/p/a": {"lastSessionId": "abc"}},
        }
        out = _redact_global_state(body)
        assert out["userID"] == _REDACTED
        assert out["oauthAccount"] == _REDACTED
        assert out["clientDataCache"] == _REDACTED
        # Non-sensitive fields preserved verbatim.
        assert out["numStartups"] == 42
        assert out["installMethod"] == "global"
        assert out["projects"] == {"/p/a": {"lastSessionId": "abc"}}
        # MCP server structural config preserved; env scrubbed.
        srv = out["mcpServers"]["myserver"]
        assert srv["command"] == "node"
        assert srv["args"] == ["./srv.js"]
        assert srv["env"]["MY_TOKEN"] == _REDACTED
        assert srv["env"]["DEBUG_LEVEL"] == "info"

    def test_mcp_server_without_env_block_passes_through(self) -> None:
        body = {
            "mcpServers": {
                "http-server": {"type": "http", "url": "https://x.example"},
            },
        }
        out = _redact_global_state(body)
        srv = out["mcpServers"]["http-server"]
        assert srv["type"] == "http"
        assert srv["url"] == "https://x.example"


class TestRedactSettings:
    def test_scrubs_env_keeps_permissions(self) -> None:
        body = {
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-secret",
                "MCP_TIMEOUT": "86400000",
            },
            "permissions": {"allow": ["Bash(cd:*)"], "deny": []},
            "model": "opus[1m]",
        }
        out = _redact_settings(body)
        assert out["env"]["ANTHROPIC_AUTH_TOKEN"] == _REDACTED
        assert out["env"]["MCP_TIMEOUT"] == "86400000"
        assert out["permissions"] == {"allow": ["Bash(cd:*)"], "deny": []}
        assert out["model"] == "opus[1m]"

    def test_no_env_block_passes_through_unchanged(self) -> None:
        body = {"permissions": {"allow": []}, "model": "haiku"}
        out = _redact_settings(body)
        assert out == body


# ===========================================================================
# Synthetic ~/.claude/ tree fixture
# ===========================================================================


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    """Build a synthetic ``~/.claude/`` tree with all 5 P2 surfaces.

    Layout::

        tmp_path/
        ├── .claude.json                       # global state (with secrets)
        └── .claude/
            ├── settings.json                  # with ANTHROPIC_AUTH_TOKEN
            ├── settings.local.json            # permissions override
            ├── history.jsonl                  # 3 lines
            └── todos/
                ├── <sess1>-agent-<aid1>.json  # 3-item array
                ├── <sess2>-agent-<aid2>.json  # 1-item array
                └── <sess3>-agent-<aid3>.json  # empty []  (must be skipped)
    """
    home = tmp_path
    cd = home / ".claude"
    cd.mkdir()

    # ~/.claude.json
    (home / ".claude.json").write_text(
        json.dumps({
            "numStartups": 42,
            "installMethod": "global",
            "userID": "secret-uid-123",
            "oauthAccount": {"email": "u@example.com"},
            "clientDataCache": {"big": "blob"},
            "mcpServers": {
                "srv": {
                    "command": "node",
                    "env": {"NODE_API_KEY": "leak-me", "DEBUG": "1"},
                },
            },
            "projects": {
                "/p/a": {"lastSessionId": "abc", "lastCost": 0.05},
            },
            "toolUsage": {"Bash": 10, "Read": 20},
        }),
        encoding="utf-8",
    )

    # settings.json (with secret)
    (cd / "settings.json").write_text(
        json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "sk-leak-me",
                "ANTHROPIC_BASE_URL": "https://api.example.com",
                "MCP_TIMEOUT": "86400000",
            },
            "permissions": {"allow": [], "deny": []},
            "model": "opus[1m]",
        }),
        encoding="utf-8",
    )

    # settings.local.json
    (cd / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(cd:*)"]}}),
        encoding="utf-8",
    )

    # history.jsonl (3 lines)
    (cd / "history.jsonl").write_text(
        "\n".join([
            json.dumps({
                "display": "hello",
                "pastedContents": {},
                "timestamp": 1700000000000,
                "project": "/p/a",
                "sessionId": "s1",
            }),
            json.dumps({
                "display": "/clear",
                "pastedContents": {},
                "timestamp": 1700000001000,
                "project": "/p/a",
            }),
            json.dumps({
                "display": "world",
                "pastedContents": {},
                "timestamp": 1700000002000,
                "project": "/p/b",
                "sessionId": "s2",
            }),
        ]) + "\n",
        encoding="utf-8",
    )

    # todos
    td = cd / "todos"
    td.mkdir()
    sess1 = "11111111-2222-3333-4444-555555555555"
    aid1 = "abcdef0123456789a"
    (td / f"{sess1}-agent-{aid1}.json").write_text(
        json.dumps([
            {"content": "task 1", "status": "completed", "activeForm": "Doing 1"},
            {"content": "task 2", "status": "in_progress", "activeForm": "Doing 2"},
            {"content": "task 3", "status": "pending", "activeForm": "Doing 3"},
        ]),
        encoding="utf-8",
    )
    sess2 = "22222222-3333-4444-5555-666666666666"
    aid2 = "abcdef0123456789b"
    (td / f"{sess2}-agent-{aid2}.json").write_text(
        json.dumps([{"content": "only", "status": "pending", "activeForm": "X"}]),
        encoding="utf-8",
    )
    # Empty file — must be skipped by the walker.
    sess3 = "33333333-4444-5555-6666-777777777777"
    aid3 = "abcdef0123456789c"
    (td / f"{sess3}-agent-{aid3}.json").write_text("[]", encoding="utf-8")

    return cd


# ===========================================================================
# User-state walker
# ===========================================================================


class TestUserStateWalker:
    def test_yields_all_five_surfaces(self, claude_home: Path) -> None:
        recs = list(iter_claude_user_state_records(claude_home))

        surfaces = [r.surface for r in recs]
        # Global, settings, settings_local, 2 todos (1 skipped empty),
        # 3 history lines = 7 total records, 5 distinct surfaces.
        assert "user_state_global" in surfaces
        assert "user_state_settings" in surfaces
        assert "user_state_settings_local" in surfaces
        assert surfaces.count("user_state_todos") == 2  # empty file skipped
        assert surfaces.count("user_state_history") == 3
        assert len(recs) == 8  # 1 global + 1 settings + 1 settings_local + 2 todos + 3 history

    def test_global_state_redacted(self, claude_home: Path) -> None:
        recs = [
            r for r in iter_claude_user_state_records(claude_home)
            if r.surface == "user_state_global"
        ]
        assert len(recs) == 1
        body = recs[0].body_json
        assert body["userID"] == _REDACTED
        assert body["oauthAccount"] == _REDACTED
        assert body["clientDataCache"] == _REDACTED
        # Non-sensitive preserved.
        assert body["numStartups"] == 42
        assert body["projects"]["/p/a"]["lastSessionId"] == "abc"
        # MCP env scrubbed.
        assert body["mcpServers"]["srv"]["env"]["NODE_API_KEY"] == _REDACTED
        assert body["mcpServers"]["srv"]["env"]["DEBUG"] == "1"

    def test_settings_token_redacted(self, claude_home: Path) -> None:
        recs = [
            r for r in iter_claude_user_state_records(claude_home)
            if r.surface == "user_state_settings"
        ]
        assert len(recs) == 1
        env = recs[0].body_json["env"]
        assert env["ANTHROPIC_AUTH_TOKEN"] == _REDACTED
        assert env["ANTHROPIC_BASE_URL"] == "https://api.example.com"
        assert env["MCP_TIMEOUT"] == "86400000"
        assert recs[0].body_json["model"] == "opus[1m]"

    def test_todos_derive_session_and_agent_id(self, claude_home: Path) -> None:
        todos_recs = [
            r for r in iter_claude_user_state_records(claude_home)
            if r.surface == "user_state_todos"
        ]
        assert len(todos_recs) == 2
        for rec in todos_recs:
            body = rec.body_json
            assert "todos" in body
            assert "session_id" in body
            assert "agent_id" in body
            assert rec.session_id == body["session_id"]
            assert body["filename"].endswith(".json")
            # The session_id is a UUID (36-char with dashes).
            assert len(body["session_id"]) == 36
            assert body["agent_id"].startswith("abcdef")

    def test_empty_todos_file_skipped(self, claude_home: Path) -> None:
        todos_recs = [
            r for r in iter_claude_user_state_records(claude_home)
            if r.surface == "user_state_todos"
        ]
        # 3 files on disk, 1 is empty []; walker yields only 2.
        assert len(todos_recs) == 2

    def test_history_lines_have_line_index(self, claude_home: Path) -> None:
        hist_recs = [
            r for r in iter_claude_user_state_records(claude_home)
            if r.surface == "user_state_history"
        ]
        assert len(hist_recs) == 3
        # line_index is 0/1/2 in order; line_uuid is None for history.
        assert [r.line_index for r in hist_recs] == [0, 1, 2]
        assert all(r.line_uuid is None for r in hist_recs)
        assert all(r.kind == "user_state_line" for r in hist_recs)
        # session_id picked up from each line's sessionId field.
        assert hist_recs[0].session_id == "s1"
        assert hist_recs[1].session_id is None  # /clear had no sessionId
        assert hist_recs[2].session_id == "s2"
        # last_updated_ms reflects the line's timestamp.
        assert hist_recs[0].last_updated_ms == 1700000000000

    def test_missing_claude_home_yields_nothing(self, tmp_path: Path) -> None:
        ghost = tmp_path / "no-such-claude"
        assert list(iter_claude_user_state_records(ghost)) == []

    def test_missing_global_state_doesnt_block_other_surfaces(
        self, tmp_path: Path
    ) -> None:
        # Build a claude_home dir with only settings.json (no .claude.json).
        cd = tmp_path / ".claude"
        cd.mkdir()
        (cd / "settings.json").write_text(
            json.dumps({"model": "haiku"}), encoding="utf-8"
        )
        recs = list(iter_claude_user_state_records(cd))
        assert len(recs) == 1
        assert recs[0].surface == "user_state_settings"

    def test_malformed_history_line_skipped(self, tmp_path: Path) -> None:
        cd = tmp_path / ".claude"
        cd.mkdir()
        (cd / "history.jsonl").write_text(
            json.dumps({"display": "ok", "timestamp": 1, "project": "/p"}) + "\n"
            + "{not-json\n"
            + json.dumps({"display": "ok2", "timestamp": 2, "project": "/p"}) + "\n",
            encoding="utf-8",
        )
        recs = list(iter_claude_user_state_records(cd))
        hist = [r for r in recs if r.surface == "user_state_history"]
        # Bad line is at index 1; the walker emits indices 0 and 2 (the
        # iterator increments line_index even for malformed lines so
        # later well-formed lines keep a stable file position).
        assert [r.line_index for r in hist] == [0, 2]

    def test_count_helper_matches_iter(self, claude_home: Path) -> None:
        counts = count_claude_user_state(claude_home)
        assert counts["user_state"] == 8
        assert counts["user_state_global"] == 1
        assert counts["user_state_settings"] == 1
        assert counts["user_state_settings_local"] == 1
        assert counts["user_state_todos"] == 2
        assert counts["user_state_history"] == 3


# ===========================================================================
# Sub-agent walker
# ===========================================================================


def _write_subagent_jsonl(path: Path, *, session_id: str, agent_id: str, n: int) -> None:
    """Write ``n`` JSONL lines mimicking a real subagent transcript."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(n):
        body: dict[str, Any] = {
            "type": "user" if i % 2 == 0 else "assistant",
            "sessionId": session_id,
            "agentId": agent_id,
            "isSidechain": True,
            "parentUuid": None if i == 0 else f"uuid-{i-1}",
            "uuid": f"uuid-{i}",
            "timestamp": f"2026-05-12T00:00:{i:02d}.000Z",
            "message": {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"},
        }
        lines.append(json.dumps(body))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestSubagentWalker:
    def test_finds_subagents_at_four_level_layout(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        cwd_dir = root / "F--encoded--cwd"
        sess_uuid = "11111111-2222-3333-4444-555555555555"
        agent_id = "abcdef0123456789a"
        _write_subagent_jsonl(
            cwd_dir / sess_uuid / "subagents" / f"agent-{agent_id}.jsonl",
            session_id=sess_uuid,
            agent_id=agent_id,
            n=4,
        )

        recs = list(iter_code_tab_subagent_records(root))
        assert len(recs) == 4
        # All lines share the composite session_id.
        composite = f"{sess_uuid}__agent_{agent_id}"
        assert all(r.session_id == composite for r in recs)
        # body injection: parent_session_id, agent_id, is_subagent.
        for rec in recs:
            assert rec.body_json["parent_session_id"] == sess_uuid
            assert rec.body_json["agent_id"] == agent_id
            assert rec.body_json["is_subagent"] is True
            # entrypoint is hoisted to "claude-desktop".
            assert rec.body_json["entrypoint"] == "claude-desktop"
            # Body's sessionId is rewritten to the composite key so
            # the normaliser builds a separate session row.
            assert rec.body_json["sessionId"] == composite

    def test_non_uuid_session_dirs_are_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        cwd_dir = root / "F--encoded--cwd"
        # Non-UUID-shaped dir; the walker should ignore it.
        agent_id = "abc"
        _write_subagent_jsonl(
            cwd_dir / "not-a-uuid" / "subagents" / f"agent-{agent_id}.jsonl",
            session_id="anything",
            agent_id=agent_id,
            n=2,
        )
        assert list(iter_code_tab_subagent_records(root)) == []

    def test_missing_subagents_dir_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        cwd_dir = root / "F--encoded--cwd"
        # Create the session-uuid dir but no subagents/ child.
        (cwd_dir / "11111111-2222-3333-4444-555555555555").mkdir(parents=True)
        assert list(iter_code_tab_subagent_records(root)) == []

    def test_main_transcripts_ignored_at_cwd_level(self, tmp_path: Path) -> None:
        """A flat ``<sessId>.jsonl`` at the cwd level (P1 main transcript)
        must NOT be picked up by the subagent walker — that file is
        handled by ``iter_code_tab_transcript_records``.
        """
        root = tmp_path / "projects"
        cwd_dir = root / "F--encoded--cwd"
        cwd_dir.mkdir(parents=True)
        # Flat main transcript: should be invisible to subagent walker.
        (cwd_dir / "11111111-2222-3333-4444-555555555555.jsonl").write_text(
            json.dumps({"type": "user", "sessionId": "x"}),
            encoding="utf-8",
        )
        assert list(iter_code_tab_subagent_records(root)) == []

    def test_non_agent_files_ignored(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        sub = (
            root / "F--encoded--cwd"
            / "11111111-2222-3333-4444-555555555555"
            / "subagents"
        )
        sub.mkdir(parents=True)
        # Non agent-prefixed file in subagents/ — should be ignored.
        (sub / "notes.txt").write_text("hi", encoding="utf-8")
        (sub / "other.jsonl").write_text("{}\n", encoding="utf-8")
        assert list(iter_code_tab_subagent_records(root)) == []

    def test_lines_without_sessionid_dont_get_rewritten(
        self, tmp_path: Path
    ) -> None:
        """A line that lacks ``sessionId`` or ``agentId`` falls through
        without the composite-id rewrite — defensive against schema
        drift in future Claude Code releases.
        """
        root = tmp_path / "projects"
        sub = (
            root / "F--encoded--cwd"
            / "11111111-2222-3333-4444-555555555555"
            / "subagents"
        )
        sub.mkdir(parents=True)
        (sub / "agent-abc.jsonl").write_text(
            json.dumps({"type": "user", "uuid": "u0"}),  # no sessionId
            encoding="utf-8",
        )
        recs = list(iter_code_tab_subagent_records(root))
        assert len(recs) == 1
        rec = recs[0]
        # No rewrite happened — body keeps shape, no is_subagent flag.
        assert "is_subagent" not in rec.body_json
        # entrypoint hoist still applied (defensive default).
        assert rec.body_json["entrypoint"] == "claude-desktop"

    def test_missing_root_yields_nothing(self, tmp_path: Path) -> None:
        ghost = tmp_path / "no-projects"
        assert list(iter_code_tab_subagent_records(ghost)) == []


# ===========================================================================
# End-to-end: walker → ChromiumStateObserver → raw_captures
# ===========================================================================


def _make_observer(tmp_path: Path) -> tuple[ChromiumStateObserver, Path]:
    db_path = tmp_path / "pce_test.db"
    init_db(db_path=db_path)
    obs = ChromiumStateObserver(
        app_id="claude-desktop",
        app_channel="msix",
        app_version="9.9.9-test",
        db_path=db_path,
        state_path=None,
        dry_run=False,
        include_bodies=True,
    )
    return obs, db_path


def _query_captures(db_path: Path, like: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT host, path, session_hint, body_text_or_json, meta_json "
        "FROM raw_captures WHERE path LIKE ?",
        (like,),
    ).fetchall()
    con.close()
    return rows


class TestUserStateE2E:
    def test_user_state_records_land_in_raw_captures(
        self, claude_home: Path, tmp_path: Path
    ) -> None:
        obs, db_path = _make_observer(tmp_path)
        for rec in iter_claude_user_state_records(claude_home):
            obs.observe_agent_session(rec)

        # All user-state captures share host=local-config and the
        # /<app>/user-state/<surface>[/<key>] path shape.
        rows = _query_captures(db_path, "/claude-desktop/user-state/%")
        assert len(rows) == 8
        for r in rows:
            assert r["host"] == "local-config"
            assert r["path"].startswith("/claude-desktop/user-state/")

    def test_settings_capture_is_redacted_in_raw_captures(
        self, claude_home: Path, tmp_path: Path
    ) -> None:
        obs, db_path = _make_observer(tmp_path)
        for rec in iter_claude_user_state_records(claude_home):
            obs.observe_agent_session(rec)

        rows = _query_captures(
            db_path, "/claude-desktop/user-state/user_state_settings"
        )
        assert len(rows) == 1
        body = json.loads(rows[0]["body_text_or_json"])
        # The plaintext token must NEVER appear in the stored body.
        assert "sk-leak-me" not in rows[0]["body_text_or_json"]
        assert body["env"]["ANTHROPIC_AUTH_TOKEN"] == _REDACTED

    def test_global_state_capture_redacts_userid(
        self, claude_home: Path, tmp_path: Path
    ) -> None:
        obs, db_path = _make_observer(tmp_path)
        for rec in iter_claude_user_state_records(claude_home):
            obs.observe_agent_session(rec)

        rows = _query_captures(
            db_path, "/claude-desktop/user-state/user_state_global"
        )
        assert len(rows) == 1
        assert "secret-uid-123" not in rows[0]["body_text_or_json"]
        body = json.loads(rows[0]["body_text_or_json"])
        assert body["userID"] == _REDACTED

    def test_history_capture_has_line_index_in_meta(
        self, claude_home: Path, tmp_path: Path
    ) -> None:
        obs, db_path = _make_observer(tmp_path)
        for rec in iter_claude_user_state_records(claude_home):
            obs.observe_agent_session(rec)

        rows = _query_captures(
            db_path, "/claude-desktop/user-state/user_state_history/%"
        )
        assert len(rows) == 3
        for r in rows:
            meta = json.loads(r["meta_json"])
            assert "line_index" in meta
            assert meta["surface"] == "user_state_history"


class TestSubagentE2E:
    def test_subagent_capture_path_and_meta(self, tmp_path: Path) -> None:
        # Build minimal subagent layout.
        root = tmp_path / "projects"
        sess_uuid = "11111111-2222-3333-4444-555555555555"
        agent_id = "abcdef0123456789a"
        _write_subagent_jsonl(
            root / "F--cwd" / sess_uuid / "subagents" / f"agent-{agent_id}.jsonl",
            session_id=sess_uuid,
            agent_id=agent_id,
            n=3,
        )

        obs, db_path = _make_observer(tmp_path)
        for rec in iter_code_tab_subagent_records(root):
            obs.observe_agent_session(rec)

        composite = f"{sess_uuid}__agent_{agent_id}"
        like = f"/claude-desktop/agent-transcript/{composite}/%"
        rows = _query_captures(db_path, like)
        assert len(rows) == 3
        for r in rows:
            assert r["host"] == "local-agent-mode"
            assert r["session_hint"] == composite
            meta = json.loads(r["meta_json"])
            assert meta["is_subagent"] is True
            assert meta["agent_id"] == agent_id
            assert meta["parent_session_id"] == sess_uuid
            # line metadata stamped.
            assert "line_index" in meta
            assert "line_type" in meta
            assert meta["line_type"] in ("user", "assistant")

    def test_subagent_creates_separate_session_row(self, tmp_path: Path) -> None:
        """Verify the normaliser builds a separate session row for the
        composite session_key (not merged into the parent's row).
        """
        root = tmp_path / "projects"
        sess_uuid = "11111111-2222-3333-4444-555555555555"
        agent_id = "abcdef0123456789a"
        _write_subagent_jsonl(
            root / "F--cwd" / sess_uuid / "subagents" / f"agent-{agent_id}.jsonl",
            session_id=sess_uuid,
            agent_id=agent_id,
            n=2,
        )

        obs, db_path = _make_observer(tmp_path)
        for rec in iter_code_tab_subagent_records(root):
            obs.observe_agent_session(rec)

        # Query the sessions table for the composite key.
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        composite = f"{sess_uuid}__agent_{agent_id}"
        rows = con.execute(
            "SELECT id, session_key, tool_family, provider FROM sessions "
            "WHERE session_key = ?",
            (composite,),
        ).fetchall()
        con.close()
        assert len(rows) == 1, f"expected exactly one subagent session, got {len(rows)}"
        r = rows[0]
        # Hoisted entrypoint routes to the Code-tab family.
        assert r["tool_family"] == "claude-desktop-code"
        assert r["provider"] == "anthropic"
