# SPDX-License-Identifier: Apache-2.0
"""Tests for data export functionality."""

import json
import time

import pytest

from pce_core.db import init_db, insert_session, insert_message
from pce_core.tagger import tag_session
from pce_core.exporter import (
    export_session_markdown,
    export_session_json,
    export_sessions_jsonl,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def _make_session(tmp_db, provider, title, messages):
    sid = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider=provider,
        title_hint=title,
        db_path=tmp_db,
    )
    for role, text, model in messages:
        insert_message(
            session_id=sid, ts=time.time(), role=role,
            content_text=text, model_name=model,
            token_estimate=len(text.split()),
            db_path=tmp_db,
        )
    tag_session(sid, db_path=tmp_db)
    return sid


class TestMarkdownExport:
    def test_basic_export(self, tmp_db):
        sid = _make_session(tmp_db, "openai", "Python Help", [
            ("user", "How do I use decorators?", None),
            ("assistant", "Decorators wrap functions.", "gpt-4o"),
        ])
        md = export_session_markdown(sid, db_path=tmp_db)
        assert "# Python Help" in md
        assert "Provider: openai" in md
        assert "gpt-4o" in md
        assert "## User" in md
        assert "## Assistant" in md
        assert "How do I use decorators?" in md
        assert "Decorators wrap functions." in md

    def test_includes_metadata(self, tmp_db):
        sid = _make_session(tmp_db, "openai", "Code Debug", [
            ("user", "Debug this Python function code error please", None),
            ("assistant", "The bug is in the function variable loop", "gpt-4o"),
        ])
        md = export_session_markdown(sid, db_path=tmp_db)
        assert "Language: en" in md

    def test_nonexistent_session(self, tmp_db):
        md = export_session_markdown("nonexistent", db_path=tmp_db)
        assert md == ""


class TestJsonExport:
    def test_basic_export(self, tmp_db):
        sid = _make_session(tmp_db, "google", "Math Question", [
            ("user", "What is 2+2?", None),
            ("assistant", "2+2 equals 4.", "gemini"),
        ])
        data = export_session_json(sid, db_path=tmp_db)
        assert data["session_id"] == sid
        assert data["provider"] == "google"
        assert data["title"] == "Math Question"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "What is 2+2?"
        assert data["messages"][1]["model"] == "gemini"

    def test_includes_tags(self, tmp_db):
        sid = _make_session(tmp_db, "openai", "Coding Session", [
            ("user", "Write Python code for a sorting algorithm function", None),
            ("assistant", "Here is the code with a loop:", "gpt-4o"),
        ])
        data = export_session_json(sid, db_path=tmp_db)
        assert data["language"] == "en"
        assert data["total_tokens"] is not None

    def test_nonexistent_session(self, tmp_db):
        data = export_session_json("nonexistent", db_path=tmp_db)
        assert data == {}


class TestJsonlExport:
    def test_basic_export(self, tmp_db):
        _make_session(tmp_db, "openai", "Session 1", [
            ("user", "Hello", None),
            ("assistant", "Hi!", "gpt-4o"),
        ])
        _make_session(tmp_db, "google", "Session 2", [
            ("user", "Test", None),
            ("assistant", "OK", "gemini"),
        ])

        lines = list(export_sessions_jsonl(db_path=tmp_db))
        assert len(lines) == 4  # 2 sessions x 2 messages each

        for line in lines:
            data = json.loads(line)
            assert "session_id" in data
            assert "role" in data
            assert "content" in data

    def test_provider_filter(self, tmp_db):
        _make_session(tmp_db, "openai", "OAI Session", [
            ("user", "Hello", None),
            ("assistant", "Hi!", "gpt-4o"),
        ])
        _make_session(tmp_db, "google", "Google Session", [
            ("user", "Test", None),
            ("assistant", "OK", "gemini"),
        ])

        lines = list(export_sessions_jsonl(provider="openai", db_path=tmp_db))
        assert len(lines) == 2
        for line in lines:
            assert json.loads(line)["provider"] == "openai"

    def test_empty_db(self, tmp_db):
        lines = list(export_sessions_jsonl(db_path=tmp_db))
        assert lines == []
