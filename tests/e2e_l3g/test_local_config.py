# SPDX-License-Identifier: Apache-2.0
"""tests/e2e_l3g/test_local_config.py – ADR-018 §6 C4 supplementary surfaces.

Covers ``pce_persistence_watcher.agent_sessions.iter_local_config_records``
plus its integration through ``ChromiumStateObserver``. The four surfaces
(per ADR-018 §6 C4 supplementary, mapped 2026-05-10):

  - ``claude_desktop_config.json``  → surface ``preferences``
  - ``cowork-enabled-cli-ops.json`` → surface ``cowork_owner``
  - ``git-worktrees.json``          → surface ``git_worktrees``
  - ``ant-did``                     → surface ``device_id``  (base64-UUID)

These are the lowest-friction L3g collection points: plaintext, small,
default-metadata-only, no LevelDB / no plyvel dependency. Tests here are
fully hermetic — they synthesise a profile dir under ``tmp_path`` and
never touch any real Claude install.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest

from pce_core.db import init_db
from pce_persistence_watcher.agent_sessions import (
    LOCAL_CONFIG_SURFACES,
    AgentSessionRecord,
    count_local_config,
    iter_local_config_records,
)
from pce_persistence_watcher.capture import ChromiumStateObserver


# A real-looking ant-did base64 → UUID (matches the user-side example
# documented in ADR-018 §6 C4 supplementary).
_ANT_DID_B64 = "YmI1NDdiMjktMmY2Yy00ZjNmLWI4OGMtNmU2MDMxMzJlYWVi"
_ANT_DID_UUID = "bb547b29-2f6c-4f3f-b88c-6e603132eaeb"


def _write_all_surfaces(profile_root: Path) -> None:
    """Write all 4 known local-config surfaces under ``profile_root``.

    Sample payloads mirror the actual shapes documented in ADR-018 §6 C4
    so the tests double as a contract regression for future Claude
    Desktop schema changes.
    """
    profile_root.mkdir(parents=True, exist_ok=True)
    (profile_root / "claude_desktop_config.json").write_text(
        json.dumps({
            "preferences": {
                "coworkScheduledTasksEnabled": True,
                "ccdScheduledTasksEnabled": True,
                "sidebarMode": "chat",
                "coworkWebSearchEnabled": True,
                "epitaxyPrefs": {
                    "starred-local-code-sessions": [],
                    "starred-cowork-spaces": [],
                    "starred-session-groups": [],
                    "dframe-local-slice": {
                        "pinnedOrder": [],
                        "customGroupAssignments": {},
                        "customGroupOrder": {},
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    (profile_root / "cowork-enabled-cli-ops.json").write_text(
        json.dumps({"ownerAccountId": "11111111-2222-3333-4444-555555555555"}),
        encoding="utf-8",
    )
    (profile_root / "git-worktrees.json").write_text(
        json.dumps({"worktrees": {}, "schemaVersion": 2}),
        encoding="utf-8",
    )
    (profile_root / "ant-did").write_text(_ANT_DID_B64, encoding="utf-8")


# ---------------------------------------------------------------------------
# Schema / parsing
# ---------------------------------------------------------------------------


def test_finds_all_four_surfaces(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    records = list(iter_local_config_records(profile))

    assert len(records) == 4
    surfaces = {r.surface for r in records}
    assert surfaces == {"preferences", "cowork_owner", "git_worktrees", "device_id"}
    for r in records:
        assert isinstance(r, AgentSessionRecord)
        assert r.kind == "local_config"
        assert r.session_id is None
        assert isinstance(r.body_json, dict)
        assert r.size_bytes > 0
        assert r.mtime_ns > 0


def test_count_local_config_breaks_down_by_surface(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    counts = count_local_config(profile)

    assert counts["local_config"] == 4
    assert counts["preferences"] == 1
    assert counts["cowork_owner"] == 1
    assert counts["git_worktrees"] == 1
    assert counts["device_id"] == 1


def test_skips_missing_profile_root(tmp_path: Path) -> None:
    # profile_root does not exist
    nonexistent = tmp_path / "nope"
    assert list(iter_local_config_records(nonexistent)) == []


def test_skips_when_profile_is_a_file_not_dir(tmp_path: Path) -> None:
    bogus = tmp_path / "i-am-a-file"
    bogus.write_text("not a directory", encoding="utf-8")
    assert list(iter_local_config_records(bogus)) == []


def test_partial_surfaces_yields_what_is_present(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    # Only write 2 of the 4
    (profile / "claude_desktop_config.json").write_text(
        json.dumps({"preferences": {}}), encoding="utf-8"
    )
    (profile / "ant-did").write_text(_ANT_DID_B64, encoding="utf-8")

    records = list(iter_local_config_records(profile))
    surfaces = {r.surface for r in records}
    assert surfaces == {"preferences", "device_id"}


def test_malformed_json_is_skipped_silently(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    (profile / "claude_desktop_config.json").write_text("{not valid json", encoding="utf-8")
    # Plus one well-formed surface so we can confirm only the bad one was dropped
    (profile / "git-worktrees.json").write_text(
        json.dumps({"worktrees": {}, "schemaVersion": 2}), encoding="utf-8"
    )

    records = list(iter_local_config_records(profile))
    surfaces = {r.surface for r in records}
    assert surfaces == {"git_worktrees"}


def test_json_array_top_level_is_skipped(tmp_path: Path) -> None:
    """``_safe_read_json`` rejects non-dict roots (e.g. list at top level)."""
    profile = tmp_path / "Claude"
    profile.mkdir()
    (profile / "git-worktrees.json").write_text("[1, 2, 3]", encoding="utf-8")

    records = list(iter_local_config_records(profile))
    assert records == []


# ---------------------------------------------------------------------------
# ant-did specifics (base64 → UUID)
# ---------------------------------------------------------------------------


def test_ant_did_decodes_base64_to_uuid(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    (profile / "ant-did").write_text(_ANT_DID_B64, encoding="utf-8")

    records = list(iter_local_config_records(profile))
    assert len(records) == 1
    rec = records[0]
    assert rec.surface == "device_id"
    assert rec.body_json == {"device_id": _ANT_DID_UUID}


def test_ant_did_with_trailing_whitespace_still_decodes(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    (profile / "ant-did").write_text(_ANT_DID_B64 + "\r\n  ", encoding="utf-8")

    records = list(iter_local_config_records(profile))
    assert len(records) == 1
    assert records[0].body_json == {"device_id": _ANT_DID_UUID}


def test_ant_did_invalid_base64_skipped(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    (profile / "ant-did").write_text("!!!not&&&base64$$$", encoding="utf-8")

    records = list(iter_local_config_records(profile))
    assert records == []


def test_ant_did_decoded_to_non_uuid_skipped(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    # base64 of "hello world" — valid base64, but not UUID-shaped after decode
    (profile / "ant-did").write_text(
        base64.b64encode(b"hello world").decode("ascii"), encoding="utf-8"
    )

    records = list(iter_local_config_records(profile))
    assert records == []


def test_ant_did_empty_file_skipped(tmp_path: Path) -> None:
    profile = tmp_path / "Claude"
    profile.mkdir()
    (profile / "ant-did").write_text("", encoding="utf-8")

    records = list(iter_local_config_records(profile))
    assert records == []


# ---------------------------------------------------------------------------
# Surface mapping
# ---------------------------------------------------------------------------


def test_local_config_surfaces_constant_is_complete() -> None:
    """Defensive: ensure the four documented surfaces are wired."""
    assert LOCAL_CONFIG_SURFACES == {
        "claude_desktop_config.json": "preferences",
        "cowork-enabled-cli-ops.json": "cowork_owner",
        "git-worktrees.json": "git_worktrees",
        "ant-did": "device_id",
    }


# ---------------------------------------------------------------------------
# Observer integration
# ---------------------------------------------------------------------------


def test_observer_writes_local_config_to_db_with_correct_routing(tmp_path: Path) -> None:
    """One full pass: profile → iter_local_config_records → observer → DB rows.

    Asserts that local_config records emit with host=local-config and
    path=/<app_id>/local-config/<surface> per ADR-018 §6 C4 supplementary.
    """
    db_path = tmp_path / "pce.db"
    init_db(db_path)

    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    observer = ChromiumStateObserver(
        app_id="claude-desktop",
        app_version="1.6608.2.0-test",
        app_channel="msix",
        db_path=db_path,
        state_path=tmp_path / "state.json",
        dry_run=False,
        include_bodies=True,
    )
    for rec in iter_local_config_records(profile):
        observer.observe_agent_session(rec)
    observer.flush_state()

    assert observer.stats["records_seen"] == 4
    assert observer.stats["records_emitted"] == 4
    assert observer.stats["records_deduped"] == 0
    assert observer.stats["capture_failures"] == 0
    assert observer.stats["local_config"] == 4

    # Verify DB rows
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT host, path, source FROM raw_captures WHERE host = 'local-config' ORDER BY path"
    ).fetchall()
    conn.close()

    assert len(rows) == 4
    paths = {r[1] for r in rows}
    assert paths == {
        "/claude-desktop/local-config/preferences",
        "/claude-desktop/local-config/cowork_owner",
        "/claude-desktop/local-config/git_worktrees",
        "/claude-desktop/local-config/device_id",
    }
    sources = {r[2] for r in rows}
    assert sources == {"local_persistence"}


def test_observer_dedups_local_config_on_rerun(tmp_path: Path) -> None:
    """Re-running the observer against the same profile must not re-emit."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    state_path = tmp_path / "state.json"

    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    def _run() -> ChromiumStateObserver:
        ob = ChromiumStateObserver(
            app_id="claude-desktop",
            app_version="test",
            app_channel="msix",
            db_path=db_path,
            state_path=state_path,
            dry_run=False,
            include_bodies=True,
        )
        for rec in iter_local_config_records(profile):
            ob.observe_agent_session(rec)
        ob.flush_state()
        return ob

    first = _run()
    assert first.stats["records_emitted"] == 4
    assert first.stats["records_deduped"] == 0

    second = _run()
    assert second.stats["records_seen"] == 4
    assert second.stats["records_emitted"] == 0
    assert second.stats["records_deduped"] == 4


def test_observer_dry_run_does_not_pollute_dedup_state(tmp_path: Path) -> None:
    """Dry-run pass must not advance the dedup state file (ADR-018 invariant)."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    state_path = tmp_path / "state.json"

    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    # Dry run first
    dry = ChromiumStateObserver(
        app_id="claude-desktop", app_version="test", app_channel="msix",
        db_path=db_path, state_path=state_path,
        dry_run=True, include_bodies=True,
    )
    for rec in iter_local_config_records(profile):
        dry.observe_agent_session(rec)
    dry.flush_state()

    # Dry-run state file should not have been written
    assert not state_path.exists()

    # Real run after dry run must still emit 4 records
    real = ChromiumStateObserver(
        app_id="claude-desktop", app_version="test", app_channel="msix",
        db_path=db_path, state_path=state_path,
        dry_run=False, include_bodies=True,
    )
    for rec in iter_local_config_records(profile):
        real.observe_agent_session(rec)
    real.flush_state()

    assert real.stats["records_emitted"] == 4
    assert real.stats["records_deduped"] == 0


def test_observer_meta_includes_surface(tmp_path: Path) -> None:
    """Capture meta_json must carry the ``surface`` field for downstream routing."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)

    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    ob = ChromiumStateObserver(
        app_id="claude-desktop", app_version="test", app_channel="msix",
        db_path=db_path, state_path=tmp_path / "state.json",
        dry_run=False, include_bodies=True,
    )
    for rec in iter_local_config_records(profile):
        ob.observe_agent_session(rec)
    ob.flush_state()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT path, meta_json FROM raw_captures WHERE host = 'local-config' ORDER BY path"
    ).fetchall()
    conn.close()

    assert rows, "expected local-config rows in DB"
    for path, meta_json in rows:
        meta = json.loads(meta_json)
        assert "surface" in meta, f"meta missing surface for {path}: {meta}"
        # Path suffix must match meta surface
        suffix = path.rsplit("/", 1)[1]
        assert meta["surface"] == suffix, f"path suffix {suffix} != meta surface {meta['surface']}"
        assert meta["source_kind"] == "local_config"


def test_observer_body_includes_full_payload_for_preferences(tmp_path: Path) -> None:
    """Sanity: the preferences body should contain the synthesised JSON in full."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)

    profile = tmp_path / "Claude"
    _write_all_surfaces(profile)

    ob = ChromiumStateObserver(
        app_id="claude-desktop", app_version="test", app_channel="msix",
        db_path=db_path, state_path=tmp_path / "state.json",
        dry_run=False, include_bodies=True,
    )
    for rec in iter_local_config_records(profile):
        ob.observe_agent_session(rec)
    ob.flush_state()

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT body_text_or_json FROM raw_captures "
        "WHERE host = 'local-config' AND path LIKE '%/preferences'"
    ).fetchone()
    conn.close()

    assert row is not None, "expected a preferences row"
    body = json.loads(row[0])
    assert "preferences" in body
    assert body["preferences"]["sidebarMode"] == "chat"
