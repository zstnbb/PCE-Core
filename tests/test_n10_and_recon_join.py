# SPDX-License-Identifier: Apache-2.0
"""Regression tests for N10 + fu_recon_join.

N10 — multi-turn / branch user messages were dropped because the session
reconciler keyed dedup on ``(role, content[:200])`` and collapsed any
collision onto the first hit. This test suite locks in the position-based
bucket matching introduced in ``message_processor.persist_result``.

fu_recon_join — five session-reconciler join gaps surfaced by the
2026-04-26 / 2026-04-27 Claude C01-C20 manual sweeps:

  1. ``tool_use.input_json_delta`` accumulation (artifact bodies)
  2. ``file_uuid`` join across upload + completion
  3. ``files`` (newer) vs ``attachments`` (legacy) schema unification
  4. ``thinking_delta`` -> dedicated thinking block in assembled content
  5. ``personalized_styles`` -> session ``layer_meta``

Tests run against a temp SQLite DB so the persist + layer_meta surfacing
round-trips end-to-end.
"""

import json
import os
import sys
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional

# Isolate from any user data dir before importing pce_core.
_TMP_DIR = tempfile.mkdtemp(prefix="pce_n10_test_")
os.environ["PCE_DATA_DIR"] = _TMP_DIR

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import (
    init_db,
    insert_capture,
    insert_session,
    new_pair_id,
    query_messages,
    query_by_pair,
    SOURCE_BROWSER_EXT,
    get_connection,
)
from pce_core.normalizer.anthropic import (
    AnthropicMessagesNormalizer,
    _extract_request_layer_meta,
    _maybe_synthesize_artifact_attachment,
    _attach_files_to_last_user_message,
)
from pce_core.normalizer.base import NormalizedMessage, NormalizedResult
from pce_core.normalizer.message_processor import (
    persist_result,
    get_existing_message_hash_buckets,
    message_hash,
)
from pce_core.normalizer.pipeline import normalize_conversation
from pce_core.normalizer.sse import assemble_anthropic_sse, assemble_any_sse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db(name: str) -> Path:
    db_path = Path(_TMP_DIR) / f"{name}.db"
    if db_path.exists():
        db_path.unlink()
    init_db(db_path)
    return db_path


def _read_session_oi_attrs(session_id: str, db_path: Path) -> dict:
    conn = get_connection(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT oi_attributes_json FROM sessions WHERE id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row or not row["oi_attributes_json"]:
            return {}
        try:
            return json.loads(row["oi_attributes_json"])
        except (json.JSONDecodeError, TypeError):
            return {}
    finally:
        conn.close()


def _make_session(db_path: Path, *, session_key: str = "n10-test") -> str:
    sid = insert_session(
        source_id=SOURCE_BROWSER_EXT,
        started_at=time.time(),
        provider="anthropic",
        tool_family="api-direct",
        session_key=session_key,
        title_hint="n10 test",
        created_via="test",
        db_path=db_path,
    )
    assert sid is not None
    return sid


def _make_result(
    messages: list[NormalizedMessage],
    *,
    session_key: str = "n10-test",
    model_name: Optional[str] = None,
) -> NormalizedResult:
    """Build a NormalizedResult. ``model_name`` defaults to None so that the
    test can simulate DOM-style captures (no result-level model) without
    triggering the result.model_name fallback in insert_message.
    """
    return NormalizedResult(
        provider="anthropic",
        tool_family="api-direct",
        model_name=model_name,
        session_key=session_key,
        title_hint="n10 test",
        messages=messages,
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# N10 — multi-turn / branch user message dedup
# ---------------------------------------------------------------------------

class TestN10MultiTurnDedup:
    def test_repeated_short_user_msg_preserved(self):
        """User says 'go on' three times in one conversation -> all three persisted."""
        db_path = _fresh_db("n10_repeated_short")
        sid = _make_session(db_path, session_key="n10-repeat-short")

        # Snapshot 1: u1 "go on" + a1 "Sure, ..."
        r1 = _make_result(
            [
                NormalizedMessage(role="user", content_text="go on", ts=1.0),
                NormalizedMessage(role="assistant", content_text="Sure, more about Rust ownership.", ts=1.1),
            ],
            session_key="n10-repeat-short",
        )
        persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        # Snapshot 2: u1 + a1 (re-snapshot, same content) + u2 "go on" + a2
        r2 = _make_result(
            [
                NormalizedMessage(role="user", content_text="go on", ts=1.0),
                NormalizedMessage(role="assistant", content_text="Sure, more about Rust ownership.", ts=1.1),
                NormalizedMessage(role="user", content_text="go on", ts=2.0),
                NormalizedMessage(role="assistant", content_text="And here's the borrow checker.", ts=2.1),
            ],
            session_key="n10-repeat-short",
        )
        persist_result(
            r2, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=2.0, db_path=db_path,
        )

        # Snapshot 3: full thread + u3 "go on" + a3
        r3 = _make_result(
            [
                NormalizedMessage(role="user", content_text="go on", ts=1.0),
                NormalizedMessage(role="assistant", content_text="Sure, more about Rust ownership.", ts=1.1),
                NormalizedMessage(role="user", content_text="go on", ts=2.0),
                NormalizedMessage(role="assistant", content_text="And here's the borrow checker.", ts=2.1),
                NormalizedMessage(role="user", content_text="go on", ts=3.0),
                NormalizedMessage(role="assistant", content_text="Lifetimes next.", ts=3.1),
            ],
            session_key="n10-repeat-short",
        )
        persist_result(
            r3, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=3.0, db_path=db_path,
        )

        msgs = query_messages(sid, db_path=db_path)
        # 6 messages total: 3 user + 3 assistant
        roles = [m["role"] for m in msgs]
        assert roles.count("user") == 3, f"Expected 3 user msgs, got {roles}"
        assert roles.count("assistant") == 3, f"Expected 3 assistant msgs, got {roles}"

    def test_branch_flip_distinct_content_both_kept(self):
        """Edit branch: original user msg + edited user msg both persisted."""
        db_path = _fresh_db("n10_branch_distinct")
        sid = _make_session(db_path, session_key="n10-branch")

        # Branch A snapshot
        r1 = _make_result(
            [
                NormalizedMessage(role="user", content_text="What is X?", ts=1.0),
                NormalizedMessage(role="assistant", content_text="Original answer A.", ts=1.1),
            ],
            session_key="n10-branch",
        )
        persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        # Branch B snapshot (edited user prompt + new assistant reply)
        r2 = _make_result(
            [
                NormalizedMessage(role="user", content_text="What is X? (edited)", ts=2.0),
                NormalizedMessage(role="assistant", content_text="Edited answer B.", ts=2.1),
            ],
            session_key="n10-branch",
        )
        persist_result(
            r2, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=2.0, db_path=db_path,
        )

        msgs = query_messages(sid, db_path=db_path)
        texts = sorted([m["content_text"] for m in msgs])
        # All 4 messages distinct -> all persisted
        assert "What is X?" in texts
        assert "What is X? (edited)" in texts
        assert "Original answer A." in texts
        assert "Edited answer B." in texts
        assert len(msgs) == 4

    def test_within_batch_repeats_all_inserted(self):
        """Two identical user messages in one result -> both persisted (not collapsed)."""
        db_path = _fresh_db("n10_batch_repeats")
        sid = _make_session(db_path, session_key="n10-batch")

        r = _make_result(
            [
                NormalizedMessage(role="user", content_text="hi", ts=1.0),
                NormalizedMessage(role="assistant", content_text="hello there!", ts=1.1),
                NormalizedMessage(role="user", content_text="hi", ts=2.0),
                NormalizedMessage(role="assistant", content_text="hello again!", ts=2.1),
            ],
            session_key="n10-batch",
        )
        persist_result(
            r, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        msgs = query_messages(sid, db_path=db_path)
        roles = [m["role"] for m in msgs]
        assert roles.count("user") == 2, f"Expected 2 user msgs, got {roles}"
        assert roles.count("assistant") == 2, f"Expected 2 assistant msgs, got {roles}"

    def test_existing_position_match_still_enriches(self):
        """Re-sending the same first-turn snapshot must enrich, not duplicate."""
        db_path = _fresh_db("n10_enrich")
        sid = _make_session(db_path, session_key="n10-enrich")

        r1 = _make_result(
            [
                NormalizedMessage(role="user", content_text="What is Python?", ts=1.0),
                NormalizedMessage(role="assistant", content_text="Python is a programming language.", ts=1.1),
            ],
            session_key="n10-enrich",
        )
        persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        # Same content, plus richer metadata on the assistant turn
        r2 = _make_result(
            [
                NormalizedMessage(role="user", content_text="What is Python?", ts=1.0),
                NormalizedMessage(
                    role="assistant",
                    content_text="Python is a programming language.",
                    model_name="claude-sonnet-4",
                    token_estimate=42,
                    ts=1.1,
                ),
            ],
            session_key="n10-enrich",
        )
        persist_result(
            r2, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.5, db_path=db_path,
        )

        msgs = query_messages(sid, db_path=db_path)
        # Should still be 2 messages — second snapshot enriched, did NOT add
        assert len(msgs) == 2, f"Expected 2 (enriched), got {len(msgs)}: {[m['role'] for m in msgs]}"
        asst = [m for m in msgs if m["role"] == "assistant"][0]
        assert asst["token_estimate"] == 42

    def test_get_buckets_returns_chronological_order(self):
        """The bucket helper preserves insertion order (ts ASC)."""
        db_path = _fresh_db("n10_buckets_order")
        sid = _make_session(db_path, session_key="n10-buckets")

        r = _make_result(
            [
                NormalizedMessage(role="user", content_text="ping", ts=1.0),
                NormalizedMessage(role="assistant", content_text="pong A", ts=1.1),
                NormalizedMessage(role="user", content_text="ping", ts=2.0),
                NormalizedMessage(role="assistant", content_text="pong B", ts=2.1),
                NormalizedMessage(role="user", content_text="ping", ts=3.0),
                NormalizedMessage(role="assistant", content_text="pong C", ts=3.1),
            ],
            session_key="n10-buckets",
        )
        persist_result(
            r, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        buckets = get_existing_message_hash_buckets(sid, db_path=db_path)
        h = message_hash("user", "ping")
        assert h in buckets
        assert len(buckets[h]) == 3


# ---------------------------------------------------------------------------
# fu_recon_join.1 + .4 — SSE delta accumulation
# ---------------------------------------------------------------------------

class TestSSEDeltaAccumulation:
    def test_input_json_delta_assembles_tool_use(self):
        """Claude artifact create_file streams partial_json -> assembled input."""
        sse = "\n".join([
            'data: {"type":"message_start","message":{"model":"claude-sonnet-4","role":"assistant"}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Done! I created Todo-1."}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_01","name":"create_file","input":{}}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\":\\"todo.md\\","}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"content\\":\\"# TODO\\\\n- [ ] task A\\"}"}}',
            'data: {"type":"content_block_stop","index":1}',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}',
        ])
        result = assemble_anthropic_sse(sse)
        assert result is not None
        assert result["model"] == "claude-sonnet-4"
        blocks = result["content"]
        assert len(blocks) == 2
        text_block = blocks[0]
        tool_block = blocks[1]
        assert text_block["type"] == "text"
        assert "Todo-1" in text_block["text"]
        assert tool_block["type"] == "tool_use"
        assert tool_block["id"] == "toolu_01"
        assert tool_block["name"] == "create_file"
        assert tool_block["input"]["file_path"] == "todo.md"
        assert tool_block["input"]["content"] == "# TODO\n- [ ] task A"

    def test_thinking_delta_emits_thinking_block(self):
        """Anthropic extended-thinking SSE -> thinking block in assembled content."""
        sse = "\n".join([
            'data: {"type":"message_start","message":{"model":"claude-opus-4","role":"assistant"}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Let me work through this. "}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Step 1 is to identify..."}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"abc123"}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"The answer is 42."}}',
            'data: {"type":"content_block_stop","index":1}',
        ])
        result = assemble_anthropic_sse(sse)
        assert result is not None
        blocks = result["content"]
        assert len(blocks) == 2
        thinking, text = blocks[0], blocks[1]
        assert thinking["type"] == "thinking"
        assert "Step 1 is to identify" in thinking["thinking"]
        assert thinking.get("signature") == "abc123"
        assert text["type"] == "text"
        assert text["text"] == "The answer is 42."

    def test_legacy_text_only_sse_still_works(self):
        """Old-style Anthropic SSE without explicit content_block_start still parses."""
        sse = "\n".join([
            'data: {"type":"message_start","message":{"model":"claude-3-haiku","role":"assistant"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world!"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
        ])
        result = assemble_anthropic_sse(sse)
        assert result is not None
        assert result["model"] == "claude-3-haiku"
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Hello world!"

    def test_truncated_partial_json_preserved(self):
        """If the SSE ends mid-tool_use, partial_json is preserved for forensics."""
        sse = "\n".join([
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"t","name":"create_file","input":{}}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\":\\"truncated"}}',
        ])
        result = assemble_anthropic_sse(sse)
        assert result is not None
        block = result["content"][0]
        assert block["type"] == "tool_use"
        # JSON was incomplete -> the raw partial is retained, not silently dropped
        assert "_partial_json" in block["input"]
        assert "truncated" in block["input"]["_partial_json"]

    def test_assemble_any_sse_routes_anthropic(self):
        sse = (
            'data: {"type":"message_start","message":{"model":"claude-3"}}\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hey"}}\n'
        )
        result = assemble_any_sse(sse)
        assert result is not None
        assert result["content"][0]["text"] == "Hey"


# ---------------------------------------------------------------------------
# fu_recon_join.2 + .3 — files / attachments unification + manifest
# ---------------------------------------------------------------------------

class TestFilesManifest:
    def test_files_array_uuid_only(self):
        """Newer schema: ``files: [<uuid>, <uuid>]`` -> manifest with file_uuid each."""
        req = {"files": ["uuid-1", "uuid-2"], "messages": []}
        meta = _extract_request_layer_meta(req)
        files = meta.get("files")
        assert isinstance(files, list)
        assert len(files) == 2
        assert files[0]["file_uuid"] == "uuid-1"
        assert files[1]["file_uuid"] == "uuid-2"

    def test_attachments_array_with_metadata(self):
        """Legacy schema: ``attachments: [{file_uuid, file_name, ...}]`` preserved."""
        req = {
            "attachments": [
                {
                    "file_uuid": "uuid-A",
                    "file_name": "report.pdf",
                    "file_kind": "document",
                    "size_bytes": 12345,
                }
            ],
        }
        meta = _extract_request_layer_meta(req)
        files = meta["files"]
        assert len(files) == 1
        f = files[0]
        assert f["file_uuid"] == "uuid-A"
        assert f["file_name"] == "report.pdf"
        assert f["file_kind"] == "document"
        assert f["size_bytes"] == 12345

    def test_files_and_attachments_dedupe(self):
        """When both arrays carry the same uuid -> deduplicated."""
        req = {
            "files": ["shared-uuid"],
            "attachments": [{"file_uuid": "shared-uuid", "file_name": "x.txt"}],
        }
        meta = _extract_request_layer_meta(req)
        files = meta["files"]
        # Both schemas listed the same uuid; manifest should not double-count.
        uuids = [f.get("file_uuid") for f in files]
        assert uuids.count("shared-uuid") == 1

    def test_attach_files_to_last_user_message(self):
        msgs = [
            NormalizedMessage(role="user", content_text="first"),
            NormalizedMessage(role="assistant", content_text="reply"),
            NormalizedMessage(role="user", content_text="describe this"),
        ]
        manifest = [{"file_uuid": "u1", "file_name": "img.png", "file_kind": "image"}]
        _attach_files_to_last_user_message(msgs, manifest)
        # Only the *last* user message gets the file attachment
        first_cj = msgs[0].content_json
        last_cj = msgs[2].content_json
        assert first_cj is None or "file" not in first_cj
        assert last_cj and "img.png" in last_cj
        atts = json.loads(last_cj).get("attachments", [])
        types = {a.get("type") for a in atts}
        assert "file" in types
        file_att = next(a for a in atts if a.get("type") == "file")
        assert file_att["file_uuid"] == "u1"
        assert file_att["file_id"] == "u1"  # canonical alias for downstream lookups
        assert file_att["name"] == "img.png"


# ---------------------------------------------------------------------------
# fu_recon_join.5 — personalized_styles
# ---------------------------------------------------------------------------

class TestPersonalizedStyles:
    def test_styles_extracted_with_active_pick(self):
        req = {
            "personalized_styles": [
                {"type": "default", "key": "normal", "name": "Normal"},
                {
                    "type": "custom",
                    "key": "concise",
                    "name": "Concise",
                    "prompt": "Reply tersely.",
                },
            ],
            "style_key": "concise",
        }
        meta = _extract_request_layer_meta(req)
        styles = meta["personalized_styles"]
        assert len(styles) == 2
        active = meta["style"]
        assert active["key"] == "concise"
        assert active["name"] == "Concise"
        assert active["prompt"] == "Reply tersely."

    def test_single_style_auto_active(self):
        """Only one style listed, no style_key -> that one is the active style."""
        req = {
            "personalized_styles": [
                {"type": "custom", "key": "explanatory", "name": "Explanatory"},
            ],
        }
        meta = _extract_request_layer_meta(req)
        assert meta.get("style", {}).get("key") == "explanatory"

    def test_no_styles_no_meta(self):
        """No personalized_styles in request -> no style key in meta."""
        meta = _extract_request_layer_meta({"messages": []})
        assert "personalized_styles" not in meta
        assert "style" not in meta


# ---------------------------------------------------------------------------
# fu_recon_join.1 — artifact attachment synthesis
# ---------------------------------------------------------------------------

class TestArtifactSynthesis:
    def test_create_file_md_becomes_canvas(self):
        att = _maybe_synthesize_artifact_attachment(
            "create_file",
            {"file_path": "todo.md", "content": "# TODO\n- [ ] task"},
        )
        # markdown is a canvas-rendered language
        assert att is not None
        assert att["type"] == "canvas"
        assert att["title"] == "todo.md"
        assert att["content_type"] == "markdown"
        assert "# TODO" in att["content"]

    def test_create_file_python_short_becomes_code_block(self):
        att = _maybe_synthesize_artifact_attachment(
            "create_file",
            {"file_path": "hello.py", "content": "print('hi')\n"},
        )
        assert att is not None
        assert att["type"] == "code_block"
        assert att["language"] == "python"
        assert "print('hi')" in att["code"]

    def test_str_replace_editor_recognised(self):
        att = _maybe_synthesize_artifact_attachment(
            "str_replace_editor",
            {"path": "main.rs", "new_str": "fn main(){}\n"},
        )
        assert att is not None
        assert att["language"] == "rust"

    def test_unknown_tool_returns_none(self):
        att = _maybe_synthesize_artifact_attachment(
            "web_search",
            {"query": "rust borrow checker"},
        )
        assert att is None

    def test_react_artifact_via_declared_type(self):
        att = _maybe_synthesize_artifact_attachment(
            "artifacts",
            {
                "id": "Counter",
                "type": "application/vnd.ant.react",
                "content": "export default function Counter(){return <div/>}",
            },
        )
        assert att is not None
        assert att["type"] == "canvas"
        assert att["content_type"] == "jsx"


# ---------------------------------------------------------------------------
# Anthropic normalizer end-to-end with layer_meta
# ---------------------------------------------------------------------------

class TestAnthropicNormalizerLayerMeta:
    def test_personalized_styles_round_trip(self):
        n = AnthropicMessagesNormalizer()
        req = json.dumps({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "Hi"}],
            "personalized_styles": [
                {
                    "type": "custom", "key": "concise", "name": "Concise",
                    "prompt": "Be concise.",
                }
            ],
            "style_key": "concise",
        })
        resp = json.dumps({
            "content": [{"type": "text", "text": "Sure."}],
            "model": "claude-sonnet-4",
            "role": "assistant",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
        result = n.normalize(req, resp, provider="anthropic", host="api.anthropic.com", path="/v1/messages")
        assert result is not None
        assert result.layer_meta is not None
        assert result.layer_meta["style"]["key"] == "concise"
        assert "personalized_styles" in result.layer_meta

    def test_files_attach_to_last_user_message(self):
        n = AnthropicMessagesNormalizer()
        req = json.dumps({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "Describe this"}],
            "files": ["uuid-img-1"],
        })
        resp = json.dumps({
            "content": [{"type": "text", "text": "I see a cat."}],
            "model": "claude-sonnet-4",
            "role": "assistant",
        })
        result = n.normalize(req, resp, provider="anthropic", host="api.anthropic.com", path="/v1/messages")
        assert result is not None
        user_msg = [m for m in result.messages if m.role == "user"][0]
        assert user_msg.content_json is not None
        atts = json.loads(user_msg.content_json)["attachments"]
        file_atts = [a for a in atts if a.get("type") == "file"]
        assert len(file_atts) == 1
        assert file_atts[0]["file_uuid"] == "uuid-img-1"

    def test_artifact_tool_use_emits_canvas(self):
        n = AnthropicMessagesNormalizer()
        req = json.dumps({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "Make a todo list"}],
        })
        resp = json.dumps({
            "model": "claude-sonnet-4",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Done!"},
                {
                    "type": "tool_use",
                    "id": "toolu_a",
                    "name": "create_file",
                    "input": {"file_path": "todo.md", "content": "# TODO\n- [ ] x"},
                },
            ],
        })
        result = n.normalize(req, resp, provider="anthropic", host="api.anthropic.com", path="/v1/messages")
        assert result is not None
        asst = [m for m in result.messages if m.role == "assistant"][0]
        atts = json.loads(asst.content_json)["attachments"]
        types = {a.get("type") for a in atts}
        assert "tool_call" in types  # forensic copy preserved
        assert "canvas" in types  # synthesized artifact
        canvas = next(a for a in atts if a.get("type") == "canvas")
        assert canvas["content_type"] == "markdown"
        assert "# TODO" in canvas["content"]


# ---------------------------------------------------------------------------
# End-to-end: persist_result surfaces layer_meta into session OI attrs
# ---------------------------------------------------------------------------

class TestLayerMetaPersistence:
    def test_layer_meta_lands_in_session_oi_attrs(self):
        db_path = _fresh_db("layer_meta_persist")
        sid = _make_session(db_path, session_key="lm-test")

        result = NormalizedResult(
            provider="anthropic",
            tool_family="api-direct",
            model_name="claude-sonnet-4",
            session_key="lm-test",
            messages=[
                NormalizedMessage(role="user", content_text="Q?", ts=1.0),
                NormalizedMessage(role="assistant", content_text="A.", ts=1.1),
            ],
            confidence=0.9,
            layer_meta={
                "style": {"key": "concise", "name": "Concise"},
                "files": [{"file_uuid": "u1", "file_name": "x.pdf"}],
            },
        )
        persist_result(
            result, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        attrs = _read_session_oi_attrs(sid, db_path)
        meta = attrs.get("pce.layer_meta")
        assert isinstance(meta, dict), f"Expected pce.layer_meta in {list(attrs.keys())}"
        assert meta["style"]["key"] == "concise"
        assert meta["files"][0]["file_uuid"] == "u1"

    def test_layer_meta_merge_accumulates_files(self):
        """Two captures with different file_uuids -> session manifest has both."""
        db_path = _fresh_db("layer_meta_merge")
        sid = _make_session(db_path, session_key="lm-merge")

        r1 = NormalizedResult(
            provider="anthropic", tool_family="api-direct",
            session_key="lm-merge",
            messages=[NormalizedMessage(role="user", content_text="turn 1", ts=1.0),
                      NormalizedMessage(role="assistant", content_text="reply 1", ts=1.1)],
            layer_meta={"files": [{"file_uuid": "u1"}]},
        )
        persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db_path,
        )

        r2 = NormalizedResult(
            provider="anthropic", tool_family="api-direct",
            session_key="lm-merge",
            messages=[NormalizedMessage(role="user", content_text="turn 2", ts=2.0),
                      NormalizedMessage(role="assistant", content_text="reply 2", ts=2.1)],
            layer_meta={"files": [{"file_uuid": "u2"}]},
        )
        persist_result(
            r2, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=2.0, db_path=db_path,
        )

        attrs = _read_session_oi_attrs(sid, db_path)
        files = attrs["pce.layer_meta"]["files"]
        uuids = {f.get("file_uuid") for f in files}
        assert uuids == {"u1", "u2"}, f"Expected both uuids merged, got {uuids}"


if __name__ == "__main__":
    # Tiny manual driver — pytest does the real run, but this helps when
    # poking at a specific failure interactively.
    import inspect

    failures = 0
    for cls_name, cls in list(globals().items()):
        if not inspect.isclass(cls) or not cls_name.startswith("Test"):
            continue
        instance = cls()
        for name, fn in inspect.getmembers(instance, predicate=inspect.ismethod):
            if not name.startswith("test_"):
                continue
            try:
                fn()
                print(f"[PASS] {cls_name}.{name}")
            except Exception as exc:
                failures += 1
                print(f"[FAIL] {cls_name}.{name}: {type(exc).__name__}: {exc}")
    if failures:
        sys.exit(f"\n{failures} failure(s)")
    print("\n=== ALL TESTS PASSED ===")
