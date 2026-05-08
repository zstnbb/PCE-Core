# SPDX-License-Identifier: Apache-2.0
"""G4 — network-channel branch detection (ADR-2026-04-26 §5.2).

Locks in the contract that two completions referencing the same
``parent_message_uuid`` but producing different texts land on
SEPARATE branches in the ``messages`` table. Covers:

- Anthropic normalizer extracting ``parent_message_uuid`` (claude.ai
  web shape) + assistant ``uuid`` (SSE-assembled response).
- ``_threading_payload`` shim filtering empty uuids out of
  ``content_json.threading``.
- ``_detect_branch_fork`` returning ``None`` for linear / idempotent
  inputs and ``(new_branch_id, parent_id)`` on a real fork.
- End-to-end: replaying two completions on the same session yields
  ``branch_count == 2`` and the default-collapse view stays linear
  (matching what the user sees in the provider UI).
- Idempotent replay: feeding the same completion twice never bumps
  branch_count above what the underlying conversation requires.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Isolate from any user data dir before importing pce_core.
_TMP_DIR = tempfile.mkdtemp(prefix="pce_g4_test_")
os.environ.setdefault("PCE_DATA_DIR", _TMP_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.db import (  # noqa: E402
    init_db,
    new_pair_id,
    query_messages,
    query_sessions,
    SOURCE_BROWSER_EXT,
)
from pce_core.normalizer.anthropic import (  # noqa: E402
    AnthropicMessagesNormalizer,
    _threading_payload,
)
from pce_core.normalizer.base import NormalizedMessage, NormalizedResult  # noqa: E402
from pce_core.normalizer.message_processor import (  # noqa: E402
    persist_result,
    _detect_branch_fork,
)
from pce_core.rich_content import (  # noqa: E402
    build_content_json,
    load_threading_from_content_json,
)


def _fresh_db(name: str) -> Path:
    db_path = Path(_TMP_DIR) / f"{name}.db"
    if db_path.exists():
        db_path.unlink()
    init_db(db_path)
    return db_path


def _make_completion(
    *,
    user_text: str,
    asst_text: str,
    parent_uuid: str,
    asst_uuid: str,
    session_key: str = "g4-claude",
) -> NormalizedResult:
    """Build a NormalizedResult mirroring what the Anthropic normalizer
    produces for a single claude.ai-web completion (one user + one
    assistant, both stamped with the network-channel uuids that drive
    branch detection)."""
    user_cj = build_content_json(
        attachments=None,
        plain_text=user_text,
        threading=_threading_payload(provider_parent_uuid=parent_uuid),
    )
    asst_cj = build_content_json(
        attachments=None,
        plain_text=asst_text,
        threading=_threading_payload(
            provider_message_uuid=asst_uuid,
            provider_parent_uuid=parent_uuid,
        ),
    )
    return NormalizedResult(
        provider="anthropic",
        tool_family="claude-web",
        session_key=session_key,
        messages=[
            NormalizedMessage(
                role="user",
                content_text=user_text,
                content_json=user_cj,
                provider_parent_uuid=parent_uuid,
            ),
            NormalizedMessage(
                role="assistant",
                content_text=asst_text,
                content_json=asst_cj,
                provider_message_uuid=asst_uuid,
                provider_parent_uuid=parent_uuid,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 1. _threading_payload helper
# ---------------------------------------------------------------------------

class TestThreadingPayloadShim:

    def test_returns_none_when_both_uuids_missing(self):
        assert _threading_payload() is None
        assert _threading_payload(provider_message_uuid=None,
                                  provider_parent_uuid=None) is None

    def test_filters_empty_strings(self):
        # Empty strings are just as useless as None for branch detection.
        assert _threading_payload(provider_parent_uuid="") is None

    def test_keeps_only_valid_strings(self):
        out = _threading_payload(provider_parent_uuid="abc-123")
        assert out == {"provider_parent_uuid": "abc-123"}

    def test_keeps_both_when_both_present(self):
        out = _threading_payload(
            provider_message_uuid="msg-x",
            provider_parent_uuid="par-y",
        )
        assert out == {
            "provider_message_uuid": "msg-x",
            "provider_parent_uuid": "par-y",
        }


# ---------------------------------------------------------------------------
# 2. Anthropic normalizer — claude.ai web shape extraction
# ---------------------------------------------------------------------------

class TestAnthropicWebShapeUUIDExtraction:
    """The claude.ai-web completion endpoint sends ``{"prompt": "...",
    "parent_message_uuid": "..."}``. The normalizer must surface those
    uuids onto NormalizedMessage AND echo them into content_json.threading
    so a SECOND completion can SQL-find the sibling row."""

    def test_user_message_carries_parent_uuid(self):
        norm = AnthropicMessagesNormalizer()
        req = json.dumps({
            "prompt": "What is APPLES?",
            "parent_message_uuid": "parent-abc",
            "timezone": "UTC",
        })
        # Minimal SSE-assembled response with assistant uuid.
        resp = json.dumps({
            "uuid": "asst-001",
            "role": "assistant",
            "model": "claude-sonnet-4",
            "content": [{"type": "text", "text": "An apple is a fruit."}],
        })
        result = norm.normalize(
            req, resp, provider="anthropic", host="claude.ai",
            path="/api/organizations/x/chat_conversations/y/completion",
            created_at=1.0,
        )
        assert result is not None
        assert len(result.messages) == 2
        user, asst = result.messages[0], result.messages[1]
        assert user.role == "user"
        assert user.content_text == "What is APPLES?"
        assert user.provider_parent_uuid == "parent-abc"
        # User has no message uuid in claude.ai web shape (server mints
        # it after the request) — that's the documented gap.
        assert user.provider_message_uuid is None

        assert asst.role == "assistant"
        assert asst.content_text == "An apple is a fruit."
        assert asst.provider_message_uuid == "asst-001"
        assert asst.provider_parent_uuid == "parent-abc"

    def test_threading_round_trips_into_content_json(self):
        norm = AnthropicMessagesNormalizer()
        req = json.dumps({
            "prompt": "hi",
            "parent_message_uuid": "ROOT",
        })
        resp = json.dumps({
            "uuid": "ASST",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
        })
        result = norm.normalize(
            req, resp, provider="anthropic", host="claude.ai", path="/x",
            created_at=1.0,
        )
        assert result is not None
        # Both messages must persist their uuids inside content_json so
        # the persist layer can match siblings on subsequent replays.
        u_threading = load_threading_from_content_json(
            result.messages[0].content_json,
        )
        a_threading = load_threading_from_content_json(
            result.messages[1].content_json,
        )
        assert u_threading.get("provider_parent_uuid") == "ROOT"
        assert a_threading.get("provider_parent_uuid") == "ROOT"
        assert a_threading.get("provider_message_uuid") == "ASST"

    def test_official_api_shape_unaffected(self):
        # The /v1/messages public API has no parent_message_uuid concept.
        # Threading must stay empty so we don't accidentally write a
        # sentinel that triggers a phantom branch.
        norm = AnthropicMessagesNormalizer()
        req = json.dumps({
            "model": "claude-sonnet-4",
            "messages": [{"role": "user", "content": "hi"}],
        })
        resp = json.dumps({
            "role": "assistant",
            "model": "claude-sonnet-4",
            "content": [{"type": "text", "text": "hello"}],
        })
        result = norm.normalize(
            req, resp, provider="anthropic", host="api.anthropic.com",
            path="/v1/messages", created_at=1.0,
        )
        assert result is not None
        for m in result.messages:
            assert m.provider_message_uuid is None
            assert m.provider_parent_uuid is None


# ---------------------------------------------------------------------------
# 3. _detect_branch_fork — pure-DB-layer detector
# ---------------------------------------------------------------------------

class TestDetectBranchFork:

    def test_no_parent_uuids_short_circuits(self, tmp_path: Path):
        # API captures with no provider_parent_uuid must never trigger
        # branch detection — there's no way to know if it's a fork.
        db = _fresh_db("g4-no-uuid")
        msgs = [NormalizedMessage(role="user", content_text="hi")]
        assert _detect_branch_fork("any-session", msgs, db_path=db) is None

    def test_no_existing_session_rows_returns_none(self, tmp_path: Path):
        # Even with parent_uuid set, if the session has no prior rows
        # (first completion), there's nothing to fork off of.
        db = _fresh_db("g4-no-rows")
        msgs = [
            NormalizedMessage(
                role="assistant", content_text="reply v1",
                provider_parent_uuid="par-1",
            ),
        ]
        assert _detect_branch_fork("empty-session", msgs, db_path=db) is None

    def test_regenerate_assistant_with_diff_text_forks(self, tmp_path: Path):
        # C08 — regenerate. Two completions, same parent uuid, different
        # assistant texts. Must mint a new branch.
        db = _fresh_db("g4-regen")
        # Completion 1 lands first.
        r1 = _make_completion(
            user_text="prompt", asst_text="reply v1",
            parent_uuid="par-1", asst_uuid="asst-1",
            session_key="regen-1",
        )
        sid1 = persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db,
        )
        assert sid1 is not None

        # Completion 2 — regenerate. Same parent_uuid, different
        # assistant text.
        new_msgs = [
            NormalizedMessage(
                role="user", content_text="prompt",
                provider_parent_uuid="par-1",
            ),
            NormalizedMessage(
                role="assistant", content_text="reply v2",
                provider_message_uuid="asst-2",
                provider_parent_uuid="par-1",
            ),
        ]
        detected = _detect_branch_fork(sid1, new_msgs, db_path=db)
        assert detected is not None
        new_branch_id, parent_msg_id = detected
        assert isinstance(new_branch_id, str) and new_branch_id
        # parent_msg_id points at the existing sibling we're forking off.
        assert isinstance(parent_msg_id, str) and parent_msg_id

    def test_edit_user_message_with_diff_text_forks(self, tmp_path: Path):
        # C07 — user edits their message. Same parent_uuid, different
        # user text → fork.
        db = _fresh_db("g4-edit")
        r1 = _make_completion(
            user_text="What is APPLES?", asst_text="apples reply",
            parent_uuid="root-uuid", asst_uuid="asst-1",
            session_key="edit-1",
        )
        sid1 = persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db,
        )
        assert sid1 is not None

        edited = [
            NormalizedMessage(
                role="user", content_text="What is BANANAS?",
                provider_parent_uuid="root-uuid",
            ),
            NormalizedMessage(
                role="assistant", content_text="bananas reply",
                provider_message_uuid="asst-2",
                provider_parent_uuid="root-uuid",
            ),
        ]
        detected = _detect_branch_fork(sid1, edited, db_path=db)
        assert detected is not None
        new_branch_id, _parent = detected
        assert isinstance(new_branch_id, str) and new_branch_id

    def test_idempotent_replay_does_not_fork(self, tmp_path: Path):
        # Re-ingesting the same completion (same parent_uuid + same
        # text) MUST NOT mint a new branch. This is the safety net for
        # at-least-once delivery from the proxy / extension.
        db = _fresh_db("g4-idem")
        r1 = _make_completion(
            user_text="prompt", asst_text="reply",
            parent_uuid="par-1", asst_uuid="asst-1",
            session_key="idem-1",
        )
        sid1 = persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db,
        )
        assert sid1 is not None

        # Same content again — different message-instance, same data.
        replay_msgs = [
            NormalizedMessage(
                role="user", content_text="prompt",
                provider_parent_uuid="par-1",
            ),
            NormalizedMessage(
                role="assistant", content_text="reply",
                provider_message_uuid="asst-1",
                provider_parent_uuid="par-1",
            ),
        ]
        assert _detect_branch_fork(sid1, replay_msgs, db_path=db) is None


# ---------------------------------------------------------------------------
# 4. End-to-end persist_result — branch_count goes from 1 to 2 on a fork
# ---------------------------------------------------------------------------

class TestPersistResultEndToEnd:

    def test_regenerate_creates_second_branch(self, tmp_path: Path):
        db = _fresh_db("g4-e2e-regen")
        # Completion 1 — lands on default branch '0'.
        r1 = _make_completion(
            user_text="ask", asst_text="reply v1",
            parent_uuid="par-1", asst_uuid="asst-1",
            session_key="e2e-regen",
        )
        sid = persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db,
        )
        assert sid is not None

        # branch_count starts at 1 (single branch '0').
        sessions_before = query_sessions(last=10, db_path=db)
        s_before = next(s for s in sessions_before if s["id"] == sid)
        assert s_before["branch_count"] == 1

        # Completion 2 — regenerate. Same parent_uuid, new assistant text.
        r2 = _make_completion(
            user_text="ask", asst_text="reply v2",
            parent_uuid="par-1", asst_uuid="asst-2",
            session_key="e2e-regen",
        )
        persist_result(
            r2, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=2.0, db_path=db,
        )

        # Now branch_count should be 2 (default '0' + minted alt).
        sessions_after = query_sessions(last=10, db_path=db)
        s_after = next(s for s in sessions_after if s["id"] == sid)
        assert s_after["branch_count"] == 2

    def test_collapse_view_stays_linear_after_fork(self, tmp_path: Path):
        # The dashboard's default GET /messages?branches=collapse must
        # still return a linear conversation even though the DB now has
        # an alternate branch — that's the whole UX promise of ADR §5.4.
        db = _fresh_db("g4-e2e-collapse")
        r1 = _make_completion(
            user_text="ask", asst_text="reply v1",
            parent_uuid="par-1", asst_uuid="asst-1",
            session_key="e2e-collapse",
        )
        sid = persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db,
        )
        r2 = _make_completion(
            user_text="ask", asst_text="reply v2",
            parent_uuid="par-1", asst_uuid="asst-2",
            session_key="e2e-collapse",
        )
        persist_result(
            r2, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=2.0, db_path=db,
        )

        # Default ``branches=collapse``: the latest assistant wins.
        collapsed = query_messages(sid, branches="collapse", db_path=db)
        roles = [m["role"] for m in collapsed]
        # Single user + single assistant — no orphan, no duplicate.
        assert roles == ["user", "assistant"]
        # And the assistant we see is the one from the LATEST completion
        # (matches what the user sees in the provider UI after clicking
        # the regenerate arrow).
        assert collapsed[-1]["content_text"] == "reply v2"

        # Expand returns every row including the alternate branch.
        expanded = query_messages(sid, branches="expand", db_path=db)
        # 1 user (deduped) + 2 assistants on different branches = 3 rows.
        # (The dedup of the user message across branches is N10 territory;
        # for our purposes here we just assert that an alternate branch
        # exists.)
        branch_ids = {m["branch_id"] for m in expanded}
        assert len(branch_ids) >= 2

    def test_idempotent_replay_does_not_grow_branch_count(self, tmp_path: Path):
        # Replaying the SAME completion twice must keep branch_count at
        # 1 — at-least-once delivery is the proxy/extension's contract,
        # the reconciler must absorb it gracefully.
        db = _fresh_db("g4-e2e-idem")
        r1 = _make_completion(
            user_text="ask", asst_text="reply",
            parent_uuid="par-1", asst_uuid="asst-1",
            session_key="e2e-idem",
        )
        sid = persist_result(
            r1, pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=1.0, db_path=db,
        )
        # Replay — same data, different pair_id (proxy retransmit).
        persist_result(
            _make_completion(
                user_text="ask", asst_text="reply",
                parent_uuid="par-1", asst_uuid="asst-1",
                session_key="e2e-idem",
            ),
            pair_id=new_pair_id(), source_id=SOURCE_BROWSER_EXT,
            created_via="test", created_at=2.0, db_path=db,
        )
        sessions = query_sessions(last=10, db_path=db)
        s = next(s for s in sessions if s["id"] == sid)
        assert s["branch_count"] == 1
