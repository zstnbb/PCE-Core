"""Tests for session auto-tagger."""

import json
import time

import pytest

from pce_core.db import init_db, insert_session, insert_message, get_connection
from pce_core.tagger import (
    _detect_language,
    _extract_topics,
    tag_session,
    tag_all_sessions,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def _make_session(tmp_db, provider, messages):
    """Helper: create a session with given messages, return session_id."""
    sid = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider=provider,
        title_hint="Test Session",
        db_path=tmp_db,
    )
    for role, text, model in messages:
        insert_message(
            session_id=sid, ts=time.time(), role=role,
            content_text=text, model_name=model,
            token_estimate=len(text.split()),
            db_path=tmp_db,
        )
    return sid


class TestDetectLanguage:
    def test_english(self):
        msgs = [{"content_text": "How do I write a Python decorator?"}]
        assert _detect_language(msgs) == "en"

    def test_chinese(self):
        msgs = [{"content_text": "请帮我写一个排序算法，使用快速排序的方式来实现"}]
        assert _detect_language(msgs) == "zh"

    def test_mixed(self):
        msgs = [
            {"content_text": "Please translate this: 今天天气不错"},
            {"content_text": "The weather is nice today 你说呢"},
        ]
        result = _detect_language(msgs)
        assert result in ("mixed", "en", "zh")

    def test_empty(self):
        assert _detect_language([]) == "en"
        assert _detect_language([{"content_text": ""}]) == "en"
        assert _detect_language([{"content_text": None}]) == "en"


class TestExtractTopics:
    def test_coding(self):
        msgs = [
            {"content_text": "Write a Python function to sort an array"},
            {"content_text": "Here is the code with a loop and variable:"},
        ]
        topics = _extract_topics(msgs)
        assert "coding" in topics

    def test_math(self):
        msgs = [
            {"content_text": "Calculate the derivative of this equation"},
            {"content_text": "Using the formula for integration:"},
        ]
        topics = _extract_topics(msgs)
        assert "math" in topics

    def test_translation(self):
        msgs = [
            {"content_text": "请帮我翻译这段话"},
            {"content_text": "Here is the translation:"},
        ]
        topics = _extract_topics(msgs)
        assert "translation" in topics

    def test_no_topics(self):
        msgs = [{"content_text": "PONG"}]
        assert _extract_topics(msgs) == []

    def test_multiple_topics(self):
        msgs = [
            {"content_text": "Write Python code to calculate the formula for this equation"},
        ]
        topics = _extract_topics(msgs)
        assert "coding" in topics or "math" in topics

    def test_empty(self):
        assert _extract_topics([]) == []


class TestTagSession:
    def test_tags_english_coding(self, tmp_db):
        sid = _make_session(tmp_db, "openai", [
            ("user", "Write a Python function to debug this error in my code", None),
            ("assistant", "Here is the function with the bug fix:", "gpt-4o"),
        ])
        tags = tag_session(sid, db_path=tmp_db)
        assert tags["language"] == "en"
        assert "coding" in (tags.get("topic_tags") or "")
        assert tags["total_tokens"] > 0
        assert "gpt-4o" in (tags.get("model_names") or "")

        # Verify DB was updated
        conn = get_connection(tmp_db)
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        conn.close()
        assert row["language"] == "en"
        assert row["total_tokens"] > 0

    def test_tags_chinese(self, tmp_db):
        sid = _make_session(tmp_db, "moonshot", [
            ("user", "请帮我写一个排序算法的代码实现", None),
            ("assistant", "这是一个快速排序的Python代码实现", "moonshot-v1"),
        ])
        tags = tag_session(sid, db_path=tmp_db)
        assert tags["language"] == "zh"

    def test_empty_session(self, tmp_db):
        sid = insert_session(
            source_id="browser-extension-default",
            started_at=time.time(), provider="test",
            db_path=tmp_db,
        )
        tags = tag_session(sid, db_path=tmp_db)
        assert tags == {}


class TestTagAllSessions:
    def test_tags_untagged_only(self, tmp_db):
        sid1 = _make_session(tmp_db, "openai", [
            ("user", "Hello", None),
            ("assistant", "Hi there!", "gpt-4o"),
        ])
        # Tag first session
        tag_session(sid1, db_path=tmp_db)

        sid2 = _make_session(tmp_db, "google", [
            ("user", "Test", None),
            ("assistant", "OK", "gemini"),
        ])

        # Only sid2 should be untagged
        count = tag_all_sessions(db_path=tmp_db)
        assert count == 1

    def test_force_retags_all(self, tmp_db):
        _make_session(tmp_db, "openai", [
            ("user", "Hello", None),
            ("assistant", "Hi!", "gpt-4o"),
        ])
        _make_session(tmp_db, "google", [
            ("user", "Test", None),
            ("assistant", "OK", "gemini"),
        ])
        tag_all_sessions(db_path=tmp_db)

        count = tag_all_sessions(force=True, db_path=tmp_db)
        assert count == 2
