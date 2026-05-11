# SPDX-License-Identifier: Apache-2.0
"""Tests for ``pce_core.normalizer.local_persistence`` — the L3g Cowork
agent-mode JSONL transcript normaliser.

Fixture: ``tests/fixtures/cowork_transcript_sample.jsonl`` is a verbatim
copy of the 38-line JSONL transcript captured during Round 3 RECON on
2026-05-11. See ``Docs/research/2026-05-11-cowork-recon-findings.md``
§ Q5 for the schema discovery context.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import pytest

from pce_core import db as pce_db
from pce_core.normalizer.local_persistence import LocalPersistenceNormalizer

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cowork_transcript_sample.jsonl"
TEST_PATH = "/claude-desktop/agent-transcript/8bff8cb1-43df-4452-b4bb-454de97b3bf1/abcd-1234"


def _load_lines() -> list[dict]:
    """Parse the fixture file into a list of line dicts."""
    out: list[dict] = []
    if not FIXTURE_PATH.exists():
        pytest.skip(f"fixture missing: {FIXTURE_PATH}")
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _first_line_of_type(lines: list[dict], t: str) -> Optional[dict]:
    for line in lines:
        if line.get("type") == t:
            return line
    return None


def _first_assistant_with_block_type(
    lines: list[dict],
    block_type: str,
) -> Optional[dict]:
    """Return the first ``assistant`` line whose message.content contains
    a block of the given type (e.g. ``"tool_use"``).
    """
    for line in lines:
        if line.get("type") != "assistant":
            continue
        msg = line.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == block_type:
                    return line
    return None


# ---------------------------------------------------------------------------
# can_handle()
# ---------------------------------------------------------------------------


class TestCanHandle:
    def test_handles_transcript_path(self):
        norm = LocalPersistenceNormalizer()
        assert norm.can_handle("anthropic", "local-agent-mode", TEST_PATH)

    def test_rejects_session_path(self):
        norm = LocalPersistenceNormalizer()
        assert not norm.can_handle(
            "anthropic", "local-agent-mode",
            "/claude-desktop/agent-session/8bff8cb1",
        )

    def test_rejects_other_host(self):
        norm = LocalPersistenceNormalizer()
        assert not norm.can_handle("anthropic", "claude.ai", TEST_PATH)

    def test_rejects_empty(self):
        norm = LocalPersistenceNormalizer()
        assert not norm.can_handle("", "", "")


# ---------------------------------------------------------------------------
# normalize() per line type
# ---------------------------------------------------------------------------


class TestNormalizeLines:
    def setup_method(self):
        self.norm = LocalPersistenceNormalizer()
        self.lines = _load_lines()
        assert self.lines, "fixture must yield at least one line"

    def _normalize_line(self, line: dict):
        body = json.dumps(line, ensure_ascii=False)
        return self.norm.normalize(
            body, body,
            provider="anthropic",
            host="local-agent-mode",
            path=TEST_PATH,
        )

    def test_user_line_parses(self):
        # Find the very first user line (matches Round 3 sample line 3)
        line = _first_line_of_type(self.lines, "user")
        assert line is not None, "fixture must contain a user line"
        result = self._normalize_line(line)
        assert result is not None
        assert result.provider == "anthropic"
        assert result.tool_family == "cowork-local-agent"
        assert result.session_key  # from path
        assert result.normalizer_name == "LocalPersistenceNormalizer"
        # Either we got a NormalizedMessage with role=user, OR (if it's a
        # tool_result-wrapping user line) we still got messages.
        assert len(result.messages) >= 1
        first = result.messages[0]
        assert first.role in ("user", "assistant")

    def test_assistant_text_line_parses(self):
        # Find an assistant line that has a "text" block
        assistant = _first_assistant_with_block_type(self.lines, "text")
        assert assistant is not None, "fixture must contain a text-bearing assistant line"
        result = self._normalize_line(assistant)
        assert result is not None
        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg.role == "assistant"
        assert msg.content_text is not None
        # The Round 3 assistant lines run Haiku 4.5 — verify model name
        # is propagated when present.
        if assistant.get("message", {}).get("model"):
            assert msg.model_name == assistant["message"]["model"]
        # provider_message_uuid should be the line uuid when present
        if assistant.get("uuid"):
            assert msg.provider_message_uuid == assistant["uuid"]

    def test_assistant_tool_use_line_parses(self):
        # Find an assistant line that has a "tool_use" block (the
        # mcp__workspace__bash openpyxl call from Round 3)
        assistant = _first_assistant_with_block_type(self.lines, "tool_use")
        assert assistant is not None, "fixture must contain a tool_use assistant line"
        result = self._normalize_line(assistant)
        assert result is not None
        assert len(result.messages) == 1
        msg = result.messages[0]
        assert msg.role == "assistant"
        # tool_use blocks should land in content_json.attachments[].type=="tool_call"
        assert msg.content_json is not None
        cj = json.loads(msg.content_json)
        attachments = cj.get("attachments") or []
        tool_calls = [a for a in attachments if a.get("type") == "tool_call"]
        assert len(tool_calls) >= 1
        # Round 3 captured mcp__workspace__bash and Skill as the two tools.
        assert any(
            tc.get("name") in {"mcp__workspace__bash", "Skill"}
            for tc in tool_calls
        )

    def test_tool_result_line_parses(self):
        # tool_result lines come back as type="user" with message.content
        # being a list containing one tool_result block.
        target = None
        for line in self.lines:
            if line.get("type") != "user":
                continue
            msg = line.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "tool_result"
                for c in content
            ):
                target = line
                break
        assert target is not None, "fixture must contain a user tool_result line"
        result = self._normalize_line(target)
        assert result is not None
        assert len(result.messages) == 1
        msg = result.messages[0]
        cj = json.loads(msg.content_json or "{}")
        attachments = cj.get("attachments") or []
        assert any(a.get("type") == "tool_result" for a in attachments)

    def test_queue_operation_metadata_only(self):
        # queue-operation lines exist BEFORE the first user message in
        # Round 3 (enqueue/dequeue) — they don't carry message content.
        line = _first_line_of_type(self.lines, "queue-operation")
        assert line is not None, "fixture must contain a queue-operation line"
        result = self._normalize_line(line)
        assert result is not None  # session row still gets created/touched
        assert result.session_key  # session_key from path is still set
        assert len(result.messages) == 0  # but no message rows emitted
        # layer_meta should record the metadata line type
        assert result.layer_meta is not None
        assert result.layer_meta.get("line_type") == "queue-operation"

    def test_session_key_falls_back_to_line_field(self):
        """When path doesn't carry the session uuid, normalise should
        pull it from the line's ``sessionId`` field."""
        line = _first_line_of_type(self.lines, "user")
        assert line is not None
        body = json.dumps(line, ensure_ascii=False)
        result = self.norm.normalize(
            body, body,
            provider="anthropic",
            host="local-agent-mode",
            path="/claude-desktop/agent-transcript/unknown/idx-0",
        )
        assert result is not None
        # session_key should fall back to line.sessionId
        assert result.session_key == line.get("sessionId")

    def test_invalid_json_returns_none(self):
        result = self.norm.normalize(
            "not json", "not json",
            provider="anthropic",
            host="local-agent-mode",
            path=TEST_PATH,
        )
        assert result is None

    def test_threading_metadata_in_content_json(self):
        # The normaliser packs sessionId/promptId/etc. into threading so
        # downstream branch detection can stitch things together.
        line = _first_line_of_type(self.lines, "user")
        assert line is not None
        result = self._normalize_line(line)
        assert result is not None
        if result.messages:
            cj = json.loads(result.messages[0].content_json or "{}")
            threading = cj.get("threading") or {}
            assert threading.get("session_id") or threading.get("line_type")


# ---------------------------------------------------------------------------
# Integration — full transcript through the normaliser sequence
# ---------------------------------------------------------------------------


class TestIntegrationFullTranscript:
    def test_full_transcript_produces_expected_sequence(self):
        norm = LocalPersistenceNormalizer()
        lines = _load_lines()
        assert lines, "fixture must yield at least one line"

        per_type: dict[str, int] = {}
        total_messages = 0
        roles: list[str] = []

        for line in lines:
            t = line.get("type", "?")
            per_type[t] = per_type.get(t, 0) + 1
            body = json.dumps(line, ensure_ascii=False)
            result = norm.normalize(
                body, body,
                provider="anthropic",
                host="local-agent-mode",
                path=TEST_PATH,
            )
            assert result is not None, f"line type {t} returned None"
            total_messages += len(result.messages)
            for m in result.messages:
                roles.append(m.role)

        # Round 3 sample shape: 16 assistant + 8 user + 5 ai-title +
        # 4 queue-operation + 3 last-prompt + 2 attachment = 38.
        # We only require that all 6 types are present and total
        # messages roughly equals user+assistant (24 ish, with some
        # variation if attachment/last-prompt lines occasionally carry
        # a flat content).
        assert "assistant" in per_type
        assert "user" in per_type
        # All content lines must produce ≥1 message; for the Round 3
        # sample that's at least 16 + 8 = 24, but some tool_use lines
        # are duplicates in the streaming arc so allow ≥ user_count.
        assert total_messages >= per_type.get("user", 0)
        # Roles must include both user and assistant
        assert "user" in roles
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# End-to-end: normalize a fixture line through the full pipeline +
# verify a sessions row + messages row land in a temp DB.
# ---------------------------------------------------------------------------


class TestEndToEndWatcherIngestion:
    """Drive the full L3g → normaliser → DB path against a temp DB.

    Builds a real ``local-agent-mode-sessions/<user>/<org>/local_<session>/
    .claude/projects/<cwd>/<session>.jsonl`` tree, runs
    ``iter_transcript_records`` + ``ChromiumStateObserver`` against it,
    and asserts that the messages table receives the expected user +
    assistant rows.
    """

    def test_watcher_ingests_transcript_into_messages_table(self, tmp_path):
        # 1. Build the directory tree
        user = "e323c763-c6b3-4761-a126-8d80870ee7c3"
        org = "8a522742-17e5-43c6-b1aa-b28bf3d7ad32"
        sess = "8bff8cb1-43df-4452-b4bb-454de97b3bf1"
        cwd_dir = "C--Users-test"
        leaf = (
            tmp_path
            / user / org / f"local_{sess}" / ".claude" / "projects" / cwd_dir
        )
        leaf.mkdir(parents=True)
        # Copy the fixture in
        target = leaf / f"{sess}.jsonl"
        target.write_text(
            FIXTURE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        # 2. Set up a temp PCE DB
        db_path = tmp_path / "pce.db"
        pce_db.init_db(db_path)

        # 3. Run the watcher pieces over the temp tree
        from pce_persistence_watcher.agent_sessions import iter_transcript_records
        from pce_persistence_watcher.capture import ChromiumStateObserver

        observer = ChromiumStateObserver(
            app_id="claude-desktop",
            app_version="test",
            app_channel="msix",
            db_path=db_path,
            state_path=tmp_path / "dedup_state.json",
            include_bodies=True,
        )
        n = 0
        for rec in iter_transcript_records(tmp_path):
            observer.observe_agent_session(rec)
            n += 1
        observer.flush_state()

        # 4. Assertions
        assert n >= 30, f"expected ~38 transcript records, got {n}"
        assert observer.stats["transcript_line"] == n
        assert observer.stats["records_emitted"] >= n - observer.stats["records_deduped"]

        # 5. Verify sessions + messages rows
        sessions = pce_db.query_sessions(last=10, db_path=db_path)
        assert sessions, "at least one session row must exist"
        # Session key should be the line's sessionId (matches fixture)
        assert any(s.get("session_key") == sess for s in sessions)

        # 6. Find the cowork session and verify messages
        cowork_sessions = [s for s in sessions if s.get("session_key") == sess]
        assert cowork_sessions
        sid = cowork_sessions[0]["id"]
        messages = pce_db.query_messages(sid, db_path=db_path)
        assert len(messages) >= 2, (
            f"expected ≥2 messages (user + assistant), got {len(messages)}"
        )
        roles = [m.get("role") for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    def test_dedup_does_not_re_emit_same_lines(self, tmp_path):
        """A second scan against an unchanged JSONL must dedup ALL records."""
        user = "e323c763-c6b3-4761-a126-8d80870ee7c3"
        org = "8a522742-17e5-43c6-b1aa-b28bf3d7ad32"
        sess = "8bff8cb1-43df-4452-b4bb-454de97b3bf1"
        leaf = (
            tmp_path
            / user / org / f"local_{sess}" / ".claude" / "projects" / "C--cwd"
        )
        leaf.mkdir(parents=True)
        (leaf / f"{sess}.jsonl").write_text(
            FIXTURE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        db_path = tmp_path / "pce.db"
        pce_db.init_db(db_path)

        from pce_persistence_watcher.agent_sessions import iter_transcript_records
        from pce_persistence_watcher.capture import ChromiumStateObserver

        state_path = tmp_path / "dedup_state.json"

        # Pass 1
        obs1 = ChromiumStateObserver(
            app_id="claude-desktop", app_version="t", app_channel="msix",
            db_path=db_path, state_path=state_path,
        )
        for rec in iter_transcript_records(tmp_path):
            obs1.observe_agent_session(rec)
        obs1.flush_state()
        emitted_first = obs1.stats["records_emitted"]
        assert emitted_first > 0

        # Pass 2 (fresh observer, same state file)
        obs2 = ChromiumStateObserver(
            app_id="claude-desktop", app_version="t", app_channel="msix",
            db_path=db_path, state_path=state_path,
        )
        for rec in iter_transcript_records(tmp_path):
            obs2.observe_agent_session(rec)
        obs2.flush_state()

        # No NEW records emitted on pass 2 — all dedup hits
        assert obs2.stats["records_emitted"] == 0
        assert obs2.stats["records_deduped"] == obs1.stats["records_seen"]
