"""Tests for FTS5 full-text search functionality."""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from pce_core.db import (
    init_db,
    insert_session,
    insert_message,
    search_messages,
    get_connection,
)


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database with FTS enabled."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def seeded_db(tmp_db):
    """DB with sample sessions and messages for search testing."""
    sid1 = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider="openai",
        title_hint="Python Hello World",
        db_path=tmp_db,
    )
    insert_message(
        session_id=sid1, ts=time.time(), role="user",
        content_text="Write a hello world program in Python",
        token_estimate=8, db_path=tmp_db,
    )
    insert_message(
        session_id=sid1, ts=time.time(), role="assistant",
        content_text='Here is a simple hello world program:\n\nprint("Hello, World!")',
        model_name="gpt-4o", token_estimate=15, db_path=tmp_db,
    )

    sid2 = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider="google",
        title_hint="Rust Tutorial",
        db_path=tmp_db,
    )
    insert_message(
        session_id=sid2, ts=time.time(), role="user",
        content_text="Explain ownership in Rust programming language",
        token_estimate=7, db_path=tmp_db,
    )
    insert_message(
        session_id=sid2, ts=time.time(), role="assistant",
        content_text="Ownership is Rust's most unique feature. Each value has a variable that is its owner.",
        model_name="gemini-2.5-pro", token_estimate=20, db_path=tmp_db,
    )

    sid3 = insert_session(
        source_id="browser-extension-default",
        started_at=time.time(),
        provider="openai",
        title_hint="Math Help",
        db_path=tmp_db,
    )
    insert_message(
        session_id=sid3, ts=time.time(), role="user",
        content_text="What is the derivative of x squared?",
        token_estimate=8, db_path=tmp_db,
    )
    insert_message(
        session_id=sid3, ts=time.time(), role="assistant",
        content_text="The derivative of x^2 is 2x.",
        model_name="gpt-4o", token_estimate=10, db_path=tmp_db,
    )

    return tmp_db


class TestFTSSearch:
    def test_basic_search(self, seeded_db):
        results = search_messages("hello", db_path=seeded_db)
        assert len(results) >= 1
        texts = [r["content_text"] for r in results]
        assert any("hello" in t.lower() for t in texts)

    def test_search_returns_snippet(self, seeded_db):
        results = search_messages("hello", db_path=seeded_db)
        assert len(results) >= 1
        assert results[0]["snippet"] is not None
        assert "<mark>" in results[0]["snippet"]

    def test_search_returns_session_info(self, seeded_db):
        results = search_messages("hello", db_path=seeded_db)
        assert len(results) >= 1
        r = results[0]
        assert r["provider"] is not None
        assert r["session_id"] is not None
        assert r["title_hint"] is not None

    def test_search_with_provider_filter(self, seeded_db):
        results = search_messages("hello", provider="openai", db_path=seeded_db)
        assert len(results) >= 1
        for r in results:
            assert r["provider"] == "openai"

        results_google = search_messages("hello", provider="google", db_path=seeded_db)
        assert len(results_google) == 0

    def test_search_no_results(self, seeded_db):
        results = search_messages("xyznonexistent", db_path=seeded_db)
        assert len(results) == 0

    def test_search_limit(self, seeded_db):
        results = search_messages("the", limit=1, db_path=seeded_db)
        assert len(results) <= 1

    def test_fts_auto_syncs_on_insert(self, seeded_db):
        # Search for something not yet in DB
        before = search_messages("quantum", db_path=seeded_db)
        assert len(before) == 0

        # Insert new message
        sid = insert_session(
            source_id="browser-extension-default",
            started_at=time.time(),
            provider="deepseek",
            title_hint="Physics",
            db_path=seeded_db,
        )
        insert_message(
            session_id=sid, ts=time.time(), role="user",
            content_text="Explain quantum entanglement",
            token_estimate=4, db_path=seeded_db,
        )

        after = search_messages("quantum", db_path=seeded_db)
        assert len(after) >= 1
        assert "quantum" in after[0]["content_text"].lower()

    def test_fts_syncs_on_delete(self, seeded_db):
        # Verify "derivative" is found
        before = search_messages("derivative", db_path=seeded_db)
        assert len(before) >= 1

        # Delete the message
        conn = get_connection(seeded_db)
        conn.execute("DELETE FROM messages WHERE content_text LIKE '%derivative%'")
        conn.commit()
        conn.close()

        after = search_messages("derivative", db_path=seeded_db)
        assert len(after) == 0

    def test_search_chinese_text(self, tmp_db):
        sid = insert_session(
            source_id="browser-extension-default",
            started_at=time.time(),
            provider="moonshot",
            title_hint="Chinese Chat",
            db_path=tmp_db,
        )
        insert_message(
            session_id=sid, ts=time.time(), role="user",
            content_text="Please help me translate this",
            token_estimate=5, db_path=tmp_db,
        )
        insert_message(
            session_id=sid, ts=time.time(), role="assistant",
            content_text="Hello World",
            token_estimate=6, db_path=tmp_db,
        )

        results = search_messages("translate", db_path=tmp_db)
        assert len(results) >= 1

    def test_empty_db_search(self, tmp_db):
        results = search_messages("anything", db_path=tmp_db)
        assert results == []
