# SPDX-License-Identifier: Apache-2.0
"""Tests for pce_core.normalizer.reconciler — capture quality scoring and merging."""

import sys
import os
import json
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.normalizer.base import NormalizedMessage, NormalizedResult
from pce_core.normalizer.reconciler import (
    MessageQuality,
    ResultQuality,
    score_message,
    score_result,
    merge_messages,
    reconcile,
    reconcile_into_session,
)


# ---------------------------------------------------------------------------
# Message quality scoring
# ---------------------------------------------------------------------------

class TestScoreMessage:
    def test_rich_message_scores_high(self):
        """Message with model, tokens, clean text, attachments → high score."""
        msg = NormalizedMessage(
            role="assistant",
            content_text="Python is a versatile programming language used for web development, data science, and automation.",
            content_json='{"attachments":[{"type":"code_block","language":"python","code":"print(1)"}]}',
            model_name="gpt-4",
            token_estimate=25,
        )
        q = score_message(msg)
        assert q.score > 0.6, f"Expected > 0.6, got {q.score}"
        assert q.has_model
        assert q.has_tokens
        assert q.has_attachments
        assert q.is_clean_text

    def test_bare_message_scores_low(self):
        """Message with just short text, no metadata → low score."""
        msg = NormalizedMessage(role="user", content_text="hi")
        q = score_message(msg)
        assert q.score < 0.3, f"Expected < 0.3, got {q.score}"
        assert not q.has_model
        assert not q.has_tokens
        assert not q.has_attachments

    def test_raw_json_penalized(self):
        """Message whose content looks like raw JSON → not clean text."""
        msg = NormalizedMessage(
            role="assistant",
            content_text='{"content_type":"text","parts":["Hello world"]}',
        )
        q = score_message(msg)
        assert not q.is_clean_text

    def test_tool_calls_detected(self):
        msg = NormalizedMessage(
            role="assistant",
            content_text="Let me search for that.\n[Tool call: web_search]",
        )
        q = score_message(msg)
        assert q.has_tool_calls

    def test_thinking_detected(self):
        msg = NormalizedMessage(
            role="assistant",
            content_text="<thinking>\nI need to consider...\n</thinking>\nHere's my answer.",
        )
        q = score_message(msg)
        assert q.has_thinking


# ---------------------------------------------------------------------------
# Result quality scoring
# ---------------------------------------------------------------------------

class TestScoreResult:
    def test_full_result_high_score(self):
        result = NormalizedResult(
            provider="openai",
            tool_family="api-direct",
            model_name="gpt-4",
            session_key="sess-001",
            messages=[
                NormalizedMessage(role="user", content_text="What is Python?"),
                NormalizedMessage(role="assistant", content_text="Python is a programming language.", model_name="gpt-4", token_estimate=10),
                NormalizedMessage(role="user", content_text="Tell me more"),
                NormalizedMessage(role="assistant", content_text="It's widely used for data science.", model_name="gpt-4", token_estimate=8),
            ],
            confidence=0.95,
        )
        q = score_result(result)
        assert q.score > 0.5
        assert q.has_both_roles
        assert q.has_session_key
        assert q.has_model
        assert q.msg_count == 4

    def test_empty_result_low_score(self):
        result = NormalizedResult(
            provider="unknown",
            tool_family="web",
            messages=[],
            confidence=0.3,
        )
        q = score_result(result)
        assert q.score < 0.3

    def test_source_type_detection(self):
        dom = NormalizedResult(provider="x", tool_family="deepseek-web", messages=[])
        net = NormalizedResult(provider="x", tool_family="openai-network", messages=[])
        api = NormalizedResult(provider="x", tool_family="api-direct", messages=[])

        assert score_result(dom).source_type == "dom"
        assert score_result(net).source_type == "network"
        assert score_result(api).source_type == "api"


# ---------------------------------------------------------------------------
# Message merging
# ---------------------------------------------------------------------------

class TestMergeMessages:
    def test_picks_better_text(self):
        """Longer clean text wins over shorter."""
        dom = NormalizedMessage(
            role="assistant",
            content_text="Python is a versatile programming language used for many things.",
        )
        net = NormalizedMessage(
            role="assistant",
            content_text="Python is",
            model_name="gpt-4",
            token_estimate=15,
        )
        merged = merge_messages(dom, net)
        # DOM has longer text
        assert "versatile" in merged.content_text
        # Network metadata preserved
        assert merged.model_name == "gpt-4"
        assert merged.token_estimate == 15

    def test_picks_clean_over_raw_json(self):
        """Clean text wins over raw JSON content."""
        clean = NormalizedMessage(
            role="assistant",
            content_text="Hello world! How can I help?",
        )
        raw = NormalizedMessage(
            role="assistant",
            content_text='{"content_type":"text","parts":["Hello world! How can I help?"]}',
            model_name="gpt-4",
        )
        merged = merge_messages(clean, raw)
        assert merged.content_text == "Hello world! How can I help?"
        assert merged.model_name == "gpt-4"

    def test_merges_attachments(self):
        """Attachments from both sources are combined."""
        a = NormalizedMessage(
            role="user",
            content_text="See this image",
            content_json='{"attachments":[{"type":"image_url","url":"http://img1.png"}]}',
        )
        b = NormalizedMessage(
            role="user",
            content_text="See this image",
            content_json='{"attachments":[{"type":"file","name":"report.pdf"}]}',
        )
        merged = merge_messages(a, b)
        cj = json.loads(merged.content_json)
        assert len(cj["attachments"]) == 2
        types = {att["type"] for att in cj["attachments"]}
        assert "image_url" in types
        assert "file" in types

    def test_prefers_earlier_timestamp(self):
        a = NormalizedMessage(role="user", content_text="hi", ts=1000.0)
        b = NormalizedMessage(role="user", content_text="hi", ts=999.0)
        merged = merge_messages(a, b)
        assert merged.ts == 999.0

    def test_deduplicates_same_attachments(self):
        """Same attachment in both sources → only one in merged."""
        cj = '{"attachments":[{"type":"image_url","url":"http://img.png"}]}'
        a = NormalizedMessage(role="user", content_text="image", content_json=cj)
        b = NormalizedMessage(role="user", content_text="image", content_json=cj)
        merged = merge_messages(a, b)
        data = json.loads(merged.content_json)
        assert len(data["attachments"]) == 1


# ---------------------------------------------------------------------------
# Result-level reconciliation
# ---------------------------------------------------------------------------

class TestReconcile:
    def test_both_none_returns_none(self):
        assert reconcile(None, None) is None

    def test_one_none_returns_other(self):
        r = NormalizedResult(provider="x", tool_family="web", messages=[
            NormalizedMessage(role="user", content_text="hi"),
        ])
        assert reconcile(r, None) is r
        assert reconcile(None, r) is r

    def test_merges_overlapping_messages(self):
        """DOM and network results with overlapping messages → merged."""
        dom = NormalizedResult(
            provider="openai",
            tool_family="openai-web",
            messages=[
                NormalizedMessage(role="user", content_text="What is AI?"),
                NormalizedMessage(role="assistant", content_text="AI is artificial intelligence, a branch of computer science."),
            ],
            confidence=0.7,
            session_key="sess-1",
        )
        net = NormalizedResult(
            provider="openai",
            tool_family="openai-network",
            model_name="gpt-4",
            messages=[
                NormalizedMessage(role="user", content_text="What is AI?"),
                NormalizedMessage(
                    role="assistant",
                    content_text="AI is artificial intelligence, a branch of computer science.",
                    model_name="gpt-4",
                    token_estimate=18,
                ),
            ],
            confidence=0.85,
            session_key="sess-1",
        )
        merged = reconcile(dom, net)
        assert merged is not None
        assert len(merged.messages) == 2
        # Network metadata should be preserved in merged assistant message
        asst = [m for m in merged.messages if m.role == "assistant"][0]
        assert asst.model_name == "gpt-4"
        assert asst.token_estimate == 18
        # Confidence should be boosted due to overlap
        assert merged.confidence >= 0.85

    def test_unique_messages_from_both_sources(self):
        """Messages unique to one source are preserved."""
        dom = NormalizedResult(
            provider="x",
            tool_family="web",
            messages=[
                NormalizedMessage(role="user", content_text="DOM only message"),
            ],
            confidence=0.5,
        )
        net = NormalizedResult(
            provider="x",
            tool_family="network",
            messages=[
                NormalizedMessage(role="assistant", content_text="Network only response"),
            ],
            confidence=0.5,
        )
        merged = reconcile(dom, net)
        assert merged is not None
        assert len(merged.messages) == 2
        texts = {m.content_text for m in merged.messages}
        assert "DOM only message" in texts
        assert "Network only response" in texts

    def test_normalizer_name_tagged(self):
        dom = NormalizedResult(provider="x", tool_family="web", messages=[
            NormalizedMessage(role="user", content_text="hi"),
        ], confidence=0.5)
        net = NormalizedResult(provider="x", tool_family="network", messages=[
            NormalizedMessage(role="user", content_text="hi"),
        ], confidence=0.6)
        merged = reconcile(dom, net)
        assert "Reconciler" in merged.normalizer_name


# ---------------------------------------------------------------------------
# Session-level reconciliation
# ---------------------------------------------------------------------------

class TestReconcileIntoSession:
    def test_new_messages_pass_through(self):
        """Messages not in existing session → returned as new."""
        result = NormalizedResult(
            provider="x", tool_family="web",
            messages=[
                NormalizedMessage(role="user", content_text="brand new question"),
            ],
        )
        output = reconcile_into_session([], result)
        assert len(output) == 1
        assert output[0].content_text == "brand new question"

    def test_existing_with_lower_quality_enriched(self):
        """Existing message without model → enriched when incoming has model."""
        existing = [
            {"id": "msg-1", "role": "assistant", "content_text": "Python is great.",
             "content_json": None, "model_name": None, "token_estimate": None},
        ]
        result = NormalizedResult(
            provider="x", tool_family="network",
            messages=[
                NormalizedMessage(
                    role="assistant", content_text="Python is great.",
                    model_name="gpt-4", token_estimate=5,
                ),
            ],
        )
        output = reconcile_into_session(existing, result)
        assert len(output) == 1
        assert output[0].model_name == "gpt-4"
        assert output[0].token_estimate == 5

    def test_existing_with_higher_quality_not_replaced(self):
        """Existing message with rich metadata → incoming without metadata skipped."""
        existing = [
            {"id": "msg-1", "role": "assistant",
             "content_text": "Python is a very versatile programming language used for many applications.",
             "content_json": '{"attachments":[{"type":"code_block","language":"py","code":"x=1"}]}',
             "model_name": "gpt-4", "token_estimate": 20},
        ]
        result = NormalizedResult(
            provider="x", tool_family="web",
            messages=[
                NormalizedMessage(
                    role="assistant",
                    content_text="Python is a very versatile programming language used for many applications.",
                ),
            ],
        )
        output = reconcile_into_session(existing, result)
        # Existing is better, incoming adds nothing → empty output
        assert len(output) == 0


# ---------------------------------------------------------------------------
# Integration: pipeline enrichment with reconciler
# ---------------------------------------------------------------------------

class TestPipelineReconcilerIntegration:
    """Test that the full pipeline uses reconciler quality scoring."""

    def test_dom_then_network_enriches_model(self):
        """DOM capture first (no model), then network capture → model enriched."""
        from pce_core.db import init_db, insert_capture, new_pair_id, query_messages, SOURCE_BROWSER_EXT
        from pce_core.normalizer.pipeline import normalize_conversation

        db_path = Path(_tmp) / "test_reconciler_integration.db"
        init_db(db_path)

        # 1. DOM capture (conversation direction) — no model_name
        pid1 = new_pair_id()
        insert_capture(
            direction="conversation",
            pair_id=pid1,
            host="chat.deepseek.com",
            path="/",
            method="",
            provider="deepseek",
            body_text_or_json=json.dumps({
                "messages": [
                    {"role": "user", "content": "What is Rust?"},
                    {"role": "assistant", "content": "Rust is a systems programming language focused on safety."},
                ],
                "conversation_id": "reconciler-test-001",
            }),
            body_format="json",
            source_id=SOURCE_BROWSER_EXT,
            session_hint="reconciler-test-001",
            db_path=db_path,
        )

        from pce_core.db import query_by_pair
        rows = query_by_pair(pid1, db_path=db_path)
        sid = normalize_conversation(rows[0], source_id=SOURCE_BROWSER_EXT, db_path=db_path)
        assert sid is not None

        msgs1 = query_messages(sid, db_path=db_path)
        assert len(msgs1) == 2
        asst1 = [m for m in msgs1 if m["role"] == "assistant"][0]
        assert asst1["model_name"] is None  # DOM doesn't know model

        # 2. Network capture for same conversation — has model_name
        pid2 = new_pair_id()
        insert_capture(
            direction="conversation",
            pair_id=pid2,
            host="chat.deepseek.com",
            path="/",
            method="",
            provider="deepseek",
            model_name="deepseek-chat",
            body_text_or_json=json.dumps({
                "messages": [
                    {"role": "user", "content": "What is Rust?"},
                    {"role": "assistant", "content": "Rust is a systems programming language focused on safety."},
                ],
                "conversation_id": "reconciler-test-001",
            }),
            body_format="json",
            source_id=SOURCE_BROWSER_EXT,
            session_hint="reconciler-test-001",
            db_path=db_path,
        )

        rows2 = query_by_pair(pid2, db_path=db_path)
        sid2 = normalize_conversation(rows2[0], source_id=SOURCE_BROWSER_EXT, db_path=db_path)
        assert sid2 == sid  # same session

        msgs2 = query_messages(sid, db_path=db_path)
        assert len(msgs2) == 2  # no new messages, just enriched
