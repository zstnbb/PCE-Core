# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_persistence_watcher.agent_sessions``.

The parser is stateless: we hand it a synthetic
``local-agent-mode-sessions/`` tree and assert on the records yielded.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from pce_persistence_watcher.agent_sessions import (
    AgentSessionRecord,
    count,
    iter_records,
)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestParseHappy:
    def test_emits_one_session_record(self, fake_app_profile: Callable[..., dict]) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        records = list(iter_records(layout["agent_sessions"]))

        sessions = [r for r in records if r.kind == "session"]
        assert len(sessions) == 1
        rec = sessions[0]
        assert isinstance(rec, AgentSessionRecord)
        assert rec.session_id == layout["session_uuids"][0]
        assert rec.source_path == layout["session_manifests"][0]
        assert rec.body_json["plugins"] == []
        assert rec.last_updated_ms == 1_700_000_000_000
        assert rec.size_bytes > 0
        assert rec.mtime_ns > 0

    def test_emits_skills_catalogue_record(self, fake_app_profile: Callable[..., dict]) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=True)
        records = list(iter_records(layout["agent_sessions"]))

        catalogues = [r for r in records if r.kind == "skills_catalogue"]
        assert len(catalogues) == 1
        rec = catalogues[0]
        assert rec.session_id is None
        assert rec.source_path == layout["skills_manifest"]
        assert isinstance(rec.body_json.get("skills"), list)
        assert len(rec.body_json["skills"]) == 2
        skill_ids = {s["skillId"] for s in rec.body_json["skills"]}
        assert skill_ids == {"skill.test.echo", "skill.test.fortune"}

    def test_emits_one_record_per_session_uuid(self, fake_app_profile: Callable[..., dict]) -> None:
        layout = fake_app_profile(num_sessions=3, include_skills=False)
        records = list(iter_records(layout["agent_sessions"]))
        sessions = [r for r in records if r.kind == "session"]
        assert len(sessions) == 3
        # Each session uuid round-trips into the record.
        recovered = {r.session_id for r in sessions}
        assert recovered == set(layout["session_uuids"])

    def test_count_helper_matches_iter_records(
        self, fake_app_profile: Callable[..., dict]
    ) -> None:
        layout = fake_app_profile(num_sessions=2, include_skills=True)
        counts = count(layout["agent_sessions"])
        assert counts == {"session": 2, "skills_catalogue": 1}


# ---------------------------------------------------------------------------
# Soft-fail paths — malformed / missing data must not raise
# ---------------------------------------------------------------------------


class TestSoftFail:
    def test_missing_root_yields_nothing(self, tmp_path: Path) -> None:
        ghost = tmp_path / "never-created"
        assert list(iter_records(ghost)) == []

    def test_root_is_a_file_yields_nothing(self, tmp_path: Path) -> None:
        # Defensive: if the path resolves to a regular file, the parser
        # must not crash.
        f = tmp_path / "agent-sessions.txt"
        f.write_text("not a dir", encoding="utf-8")
        assert list(iter_records(f)) == []

    def test_malformed_session_manifest_is_skipped(
        self, fake_app_profile: Callable[..., dict]
    ) -> None:
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        # Corrupt the only manifest in place.
        layout["session_manifests"][0].write_text("{not-json", encoding="utf-8")

        records = list(iter_records(layout["agent_sessions"]))
        assert records == []  # corrupt manifest skipped, no other entries

    def test_one_corrupt_does_not_block_a_sibling(
        self, fake_app_profile: Callable[..., dict]
    ) -> None:
        # Two sessions; corrupt the first, the second still emits.
        layout = fake_app_profile(num_sessions=2, include_skills=False)
        layout["session_manifests"][0].write_text("not json", encoding="utf-8")

        records = list(iter_records(layout["agent_sessions"]))
        sessions = [r for r in records if r.kind == "session"]
        assert len(sessions) == 1
        assert sessions[0].source_path == layout["session_manifests"][1]

    def test_unknown_top_level_dir_is_ignored(
        self, fake_app_profile: Callable[..., dict]
    ) -> None:
        # Future Cowork features may add new top-level dirs; the parser
        # MUST ignore anything that's not a UUID-shaped session dir or
        # the literal ``skills-plugin`` dir.
        layout = fake_app_profile(num_sessions=1, include_skills=False)
        unknown = layout["agent_sessions"] / "some-future-feature"
        unknown.mkdir()
        (unknown / "manifest.json").write_text(
            json.dumps({"future": True}), encoding="utf-8"
        )

        records = list(iter_records(layout["agent_sessions"]))
        # Only the legitimate session record is emitted.
        assert len(records) == 1
        assert records[0].session_id == layout["session_uuids"][0]

    def test_non_dict_json_is_skipped(self, tmp_path: Path) -> None:
        # A manifest that decodes to a JSON list (not a dict) is unexpected
        # and must be rejected without raising.
        root = tmp_path / "local-agent-mode-sessions"
        sid = "00000000-0000-0000-0000-000000000000"
        sd = root / sid
        sd.mkdir(parents=True)
        (sd / "manifest.json").write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

        records = list(iter_records(root))
        assert records == []


# ---------------------------------------------------------------------------
# UUID-shape gating — non-UUID top-level dirs are not session candidates
# ---------------------------------------------------------------------------


class TestUuidGating:
    def test_non_uuid_dir_is_ignored(self, tmp_path: Path) -> None:
        root = tmp_path / "local-agent-mode-sessions"
        root.mkdir()
        # Looks plausible but not 8-4-4-4-12 hex.
        bad = root / "1234-not-a-uuid"
        bad.mkdir()
        (bad / "manifest.json").write_text(
            json.dumps({"plugins": []}), encoding="utf-8"
        )
        assert list(iter_records(root)) == []

    def test_uppercase_uuid_is_accepted(self, tmp_path: Path) -> None:
        # ``_looks_like_uuid`` lowercases internally so uppercase hex
        # should still match.
        root = tmp_path / "local-agent-mode-sessions"
        sid = "ABCDEF12-3456-7890-ABCD-EF1234567890"
        sd = root / sid
        sd.mkdir(parents=True)
        (sd / "manifest.json").write_text(
            json.dumps({"plugins": []}), encoding="utf-8"
        )
        records = list(iter_records(root))
        assert len(records) == 1
        assert records[0].session_id == sid
