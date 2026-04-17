"""Tests for export (P1 §5.3) + import (P1 §5.3) round-trip.

Coverage:

- OTLP-JSONL export produces one OpenInference span per capture pair.
- JSON envelope export contains the full ``sessions`` / ``messages``
  trees and optionally ``raw_captures``.
- Importing exported data into a fresh DB reproduces the same
  sessions/messages.
- Re-importing the same payload is a no-op (idempotency).
- Malformed records are skipped with error entries rather than
  aborting the whole batch.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pce_core import db as pce_db
from pce_core.db import (
    SOURCE_PROXY,
    insert_capture,
    insert_message,
    insert_session,
    new_pair_id,
    query_messages,
    query_sessions,
)
from pce_core.export import (
    export_summary,
    iter_oi_spans,
    iter_session_envelopes,
    stream_json_envelope,
    stream_jsonl,
)
from pce_core.import_data import import_document, import_jsonl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """Build a DB with one session, two pairs, five messages, full raw trail."""
    db = tmp_path / "seed.db"
    pce_db.init_db(db)

    now = time.time()
    session_id = insert_session(
        source_id=SOURCE_PROXY, started_at=now - 60,
        provider="openai", tool_family="api-direct",
        title_hint="Round-trip test", created_via="proxy",
        db_path=db,
    )
    assert session_id

    # Pair 1: user→assistant
    pair1 = new_pair_id()
    insert_capture(
        direction="request", pair_id=pair1, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4",
        body_text_or_json='{"model":"gpt-4","messages":[{"role":"user","content":"hello"}]}',
        body_format="json", source_id=SOURCE_PROXY, db_path=db,
    )
    insert_capture(
        direction="response", pair_id=pair1, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4", status_code=200, latency_ms=120.0,
        body_text_or_json='{"choices":[{"message":{"role":"assistant","content":"hi!"}}]}',
        body_format="json", source_id=SOURCE_PROXY, db_path=db,
    )
    insert_message(
        session_id=session_id, ts=now - 50, role="user", content_text="hello",
        capture_pair_id=pair1, token_estimate=1,
        oi_role_raw="user", oi_input_tokens=1,
        db_path=db,
    )
    insert_message(
        session_id=session_id, ts=now - 49, role="assistant", content_text="hi!",
        capture_pair_id=pair1, model_name="gpt-4", token_estimate=2,
        oi_role_raw="assistant", oi_output_tokens=2,
        db_path=db,
    )

    # Pair 2: user→assistant with richer rich content
    pair2 = new_pair_id()
    insert_capture(
        direction="request", pair_id=pair2, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4", body_text_or_json="{}", body_format="json",
        source_id=SOURCE_PROXY, db_path=db,
    )
    insert_capture(
        direction="response", pair_id=pair2, host="api.openai.com",
        path="/v1/chat/completions", method="POST", provider="openai",
        model_name="gpt-4", status_code=200, latency_ms=80.0,
        body_text_or_json="{}", body_format="json",
        source_id=SOURCE_PROXY, db_path=db,
    )
    insert_message(
        session_id=session_id, ts=now - 30, role="user",
        content_text="summarise this [Image]",
        content_json=json.dumps({"attachments": [
            {"type": "image_url", "url": "https://ex.com/a.png"}
        ]}),
        capture_pair_id=pair2, oi_role_raw="user",
        db_path=db,
    )
    insert_message(
        session_id=session_id, ts=now - 29, role="assistant",
        content_text="A cat.", capture_pair_id=pair2, model_name="gpt-4",
        oi_role_raw="assistant", oi_output_tokens=3,
        db_path=db,
    )
    insert_message(
        session_id=session_id, ts=now - 10, role="user", content_text="thanks",
        capture_pair_id=None, oi_role_raw="user",
        db_path=db,
    )
    return db


# ---------------------------------------------------------------------------
# Export summary
# ---------------------------------------------------------------------------

class TestExportSummary:

    def test_summary_counts_match_reality(self, seeded_db: Path):
        s = export_summary(db_path=seeded_db)
        assert s["sessions"] == 1
        assert s["messages"] == 5
        assert s["pairs"] == 2  # one orphan message has no pair_id

    def test_summary_honours_since_filter(self, seeded_db: Path):
        # Very recent filter — only "thanks" matches (ts = now - 10s)
        now = time.time()
        s = export_summary(since=now - 20, db_path=seeded_db)
        assert s["messages"] == 1
        assert s["pairs"] == 0


# ---------------------------------------------------------------------------
# OTLP JSONL export
# ---------------------------------------------------------------------------

class TestOTLPJSONLExport:

    def test_one_span_per_pair(self, seeded_db: Path):
        spans = list(iter_oi_spans(db_path=seeded_db))
        assert len(spans) == 2
        assert all(s["kind"] == "LLM" for s in spans)

    def test_spans_contain_openinference_keys(self, seeded_db: Path):
        spans = list(iter_oi_spans(db_path=seeded_db))
        for s in spans:
            attrs = s["attributes"]
            assert attrs["openinference.span.kind"] == "LLM"
            assert attrs["llm.provider"] == "openai"
            assert attrs["session.id"]
            # Must include at least one output message
            assert any(
                k.startswith("llm.output_messages.") and k.endswith(".message.role")
                for k in attrs
            )

    def test_jsonl_stream_is_parseable_linewise(self, seeded_db: Path):
        payload = b"".join(stream_jsonl(iter_oi_spans(db_path=seeded_db)))
        lines = [line for line in payload.split(b"\n") if line.strip()]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "attributes" in obj
            assert "trace_id_hex" in obj


# ---------------------------------------------------------------------------
# JSON envelope export
# ---------------------------------------------------------------------------

class TestJSONEnvelopeExport:

    def test_envelope_includes_session_and_messages(self, seeded_db: Path):
        envs = list(iter_session_envelopes(db_path=seeded_db))
        assert len(envs) == 1
        env = envs[0]
        assert env["schema"].startswith("pce.session.envelope/")
        assert env["session"]["provider"] == "openai"
        assert len(env["messages"]) == 5

    def test_include_raw_pulls_raw_captures(self, seeded_db: Path):
        envs = list(iter_session_envelopes(include_raw=True, db_path=seeded_db))
        env = envs[0]
        assert len(env["raw_captures"]) == 4  # 2 pairs × 2 rows

    def test_stream_json_envelope_is_valid_json(self, seeded_db: Path):
        payload = b"".join(stream_json_envelope(include_raw=False, db_path=seeded_db))
        doc = json.loads(payload)
        assert doc["schema"] == "pce.export/v1"
        assert isinstance(doc["sessions"], list)
        assert len(doc["sessions"]) == 1


# ---------------------------------------------------------------------------
# Round-trip & idempotency
# ---------------------------------------------------------------------------

class TestRoundTrip:

    def test_envelope_import_recreates_sessions_and_messages(
        self, seeded_db: Path, tmp_path: Path,
    ):
        # Export from seeded DB
        exported = b"".join(stream_json_envelope(include_raw=False, db_path=seeded_db))
        doc = json.loads(exported)

        # Fresh target DB
        target = tmp_path / "target.db"
        pce_db.init_db(target)

        summary = import_document(doc, db_path=target)
        assert summary["sessions_created"] == 1
        assert summary["messages_created"] == 5
        assert summary["sessions_matched"] == 0
        assert summary["messages_skipped_duplicate"] == 0
        assert summary["errors"] == []

        sessions = query_sessions(last=10, db_path=target)
        assert len(sessions) == 1
        sid = sessions[0]["id"]
        assert sessions[0]["provider"] == "openai"

        msgs = query_messages(sid, db_path=target)
        assert len(msgs) == 5
        roles = sorted(m["role"] for m in msgs)
        assert roles.count("user") == 3
        assert roles.count("assistant") == 2

    def test_re_importing_same_document_is_a_no_op(
        self, seeded_db: Path, tmp_path: Path,
    ):
        exported = b"".join(stream_json_envelope(include_raw=False, db_path=seeded_db))
        doc = json.loads(exported)
        target = tmp_path / "target_idemp.db"
        pce_db.init_db(target)

        first = import_document(doc, db_path=target)
        second = import_document(doc, db_path=target)

        assert first["sessions_created"] == 1
        assert first["messages_created"] == 5
        assert second["sessions_created"] == 0
        assert second["sessions_matched"] == 1
        assert second["messages_created"] == 0
        assert second["messages_skipped_duplicate"] == 5

        # Target row counts unchanged after second import
        sessions = query_sessions(last=10, db_path=target)
        assert len(sessions) == 1
        msgs = query_messages(sessions[0]["id"], db_path=target)
        assert len(msgs) == 5

    def test_otlp_jsonl_round_trip_preserves_pair_messages(
        self, seeded_db: Path, tmp_path: Path,
    ):
        lines = list(stream_jsonl(iter_oi_spans(db_path=seeded_db)))
        target = tmp_path / "target_otlp.db"
        pce_db.init_db(target)
        summary = import_jsonl(lines, db_path=target)

        # Both spans produce one session-id each (same in source so ideally
        # one session, but each span carries session.id so that's fine).
        assert summary["errors"] == []
        assert summary["messages_created"] >= 4  # at least the 4 pair-bound msgs
        assert summary["messages_skipped_duplicate"] == 0

        sessions = query_sessions(last=10, db_path=target)
        # Exporter puts the same session.id in both spans → only 1 session.
        assert len(sessions) == 1
        msgs = query_messages(sessions[0]["id"], db_path=target)
        assert len(msgs) >= 4


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestImportErrorHandling:

    def test_malformed_line_is_skipped_not_fatal(self, tmp_path: Path):
        target = tmp_path / "err.db"
        pce_db.init_db(target)
        lines = [b"not json\n", b'{"kind":"LLM"}\n']  # 2nd line missing required
        summary = import_jsonl(lines, db_path=target)
        assert len(summary["errors"]) >= 1
        # Nothing created from the error-only batch
        assert summary["sessions_created"] == 0

    def test_bad_top_level_shape_returns_error_not_raise(self, tmp_path: Path):
        target = tmp_path / "err2.db"
        pce_db.init_db(target)
        summary = import_document({"wrong_key": []}, db_path=target)
        assert len(summary["errors"]) >= 1

    def test_oi_span_without_session_id_is_rejected(self, tmp_path: Path):
        target = tmp_path / "err3.db"
        pce_db.init_db(target)
        bad_span = {
            "kind": "LLM",
            "trace_id_hex": "a" * 32,
            "span_id_hex": "b" * 16,
            "attributes": {"llm.provider": "openai"},  # no session.id
        }
        summary = import_jsonl([json.dumps(bad_span).encode()], db_path=target)
        assert any("session.id" in e.get("error", "") for e in summary["errors"])
