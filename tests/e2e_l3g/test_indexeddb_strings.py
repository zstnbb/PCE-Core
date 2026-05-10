# SPDX-License-Identifier: Apache-2.0
"""tests/e2e_l3g/test_indexeddb_strings.py – plyvel-free IndexedDB scanner.

Covers the new ADR-018 §6 C4 supplementary plyvel-free path:

  - ``leveldb_reader.iter_log_strings``  — printable ASCII run extractor
  - ``leveldb_reader.scan_indexeddb_log`` — one-pass aggregate summary
  - ``leveldb_reader._redact_composer_draft`` — TipTap text redaction
  - ``leveldb_reader.find_log_files``       — Chromium .log file filter
  - ``ChromiumStateObserver.observe_indexeddb_summary`` — capture path

Hermetic: synthesises a binary blob mimicking the byte-shapes recovered
from the real Claude Desktop IndexedDB dump (ADR-018 §6 C4 supplementary
findings, 2026-05-10) and exercises the parser end-to-end without any
plyvel binding or real Claude install.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pce_core.db import init_db
from pce_persistence_watcher.capture import ChromiumStateObserver
from pce_persistence_watcher.leveldb_reader import (
    IndexedDbScanSummary,
    _redact_composer_draft,
    find_log_files,
    iter_log_strings,
    scan_indexeddb_log,
)


# ---------------------------------------------------------------------------
# Synthetic log file builder
# ---------------------------------------------------------------------------
#
# Real IndexedDB .log files are LevelDB log records: 32 KB blocks with
# checksum + length-prefixed payloads. For our parser, we don't care
# about the framing — we just look for printable ASCII runs. A test
# fixture that just writes "noise / printable / noise / printable / ..."
# is sufficient to exercise iter_log_strings + scan_indexeddb_log.
#
# Sentinel non-printable separator.
_SEP = b"\x00\x01\x02"

# A composer-draft TipTap JSON matching the real shape.
_DRAFT_JSON = json.dumps({
    "state": {
        "tipTapEditorState": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "secret user thought"},
                    ],
                },
            ],
        },
        "attachments": [],
        "files": [],
        "syncSourceUuids": [],
    },
    "version": 1,
    "updatedAt": 1778380861898,
}, separators=(",", ":"))

# A starred-ids JSON.
_STARRED_JSON = json.dumps({
    "state": {"starredIds": []},
    "version": 0,
    "updatedAt": 1778376829240,
}, separators=(",", ":"))

# Real-shape UUID v7 (version nibble = 7 in third group's first hex digit).
_UUID_V7 = "019e0fdf-ec56-7e85-999c-cf0ec70b8914"
# Plain (non-v7) UUID for comparison.
_UUID_V4 = "550e8400-e29b-41d4-a716-446655440000"
# Assistant message ref shape.
_ASSISTANT_REF = "new-assistant-message-uuid-f7c3a70c-d5c1-4341-9f01-d85a436f92af"


def _build_synth_log(path: Path, *, with_draft: bool = True) -> int:
    """Write a synthetic .log file mimicking Claude IndexedDB shapes.

    Returns the byte size written. Path's parent dir must exist.
    """
    # Each ASCII fragment is wrapped in non-printable separators to
    # form a clean run boundary. Order is deliberate so the test can
    # also assert specific extraction order.
    fragments = [
        _SEP * 4,
        b"keyval-store",                         # DB name hint
        _SEP,
        b"claude-notifications",                 # second DB name hint
        _SEP,
        b"composer-drafts",                      # object store hint
        _SEP,
        b"starredIds",                           # object store hint
        _SEP,
        b"conversations",                        # object store hint
        _SEP,
        _UUID_V7.encode(),                       # uuid v7
        _SEP,
        _UUID_V4.encode(),                       # plain uuid (counted, not v7)
        _SEP,
        _ASSISTANT_REF.encode(),                 # assistant message ref
        _SEP,
        b"conversations_v2:1778347175418",       # key prefix (NOT a known store; counted as plain run)
        _SEP,
    ]
    if with_draft:
        fragments += [_DRAFT_JSON.encode(), _SEP]
    fragments += [_STARRED_JSON.encode(), _SEP]
    # Add some short noise (5 chars, below min_run=6 → ignored).
    fragments += [b"abcd", _SEP, b"xy", _SEP * 6]
    blob = b"".join(fragments)
    path.write_bytes(blob)
    return len(blob)


# ---------------------------------------------------------------------------
# iter_log_strings
# ---------------------------------------------------------------------------


def test_iter_log_strings_yields_printable_runs(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    runs = list(iter_log_strings(log, min_run=6))
    # All known long fragments must be present.
    assert "keyval-store" in runs
    assert "composer-drafts" in runs
    assert "starredIds" in runs
    assert _UUID_V7 in runs
    assert _ASSISTANT_REF in runs


def test_iter_log_strings_respects_min_run(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    # Short noise "abcd" (4 chars) and "xy" (2 chars) must be filtered out
    # by the default min_run=6.
    runs = list(iter_log_strings(log, min_run=6))
    assert "abcd" not in runs
    assert "xy" not in runs

    # With min_run=2, "abcd" should now appear.
    runs2 = list(iter_log_strings(log, min_run=2))
    assert "abcd" in runs2


def test_iter_log_strings_respects_max_records(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    full = list(iter_log_strings(log, min_run=6))
    # Cap to 3 — must yield ≤3 even if more would have been available.
    capped = list(iter_log_strings(log, min_run=6, max_records=3))
    assert len(capped) <= 3
    assert len(capped) <= len(full)


def test_iter_log_strings_returns_silently_on_missing_file(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no-such.log"
    assert list(iter_log_strings(nonexistent)) == []


def test_iter_log_strings_returns_silently_on_directory(tmp_path: Path) -> None:
    a_dir = tmp_path / "im_a_dir"
    a_dir.mkdir()
    assert list(iter_log_strings(a_dir)) == []


def test_iter_log_strings_truncates_long_run(tmp_path: Path) -> None:
    log = tmp_path / "long.log"
    long_run = b"A" * 10_000
    log.write_bytes(_SEP + long_run + _SEP)

    runs = list(iter_log_strings(log, min_run=6, max_run=100))
    assert len(runs) == 1
    assert len(runs[0]) == 100
    assert runs[0] == "A" * 100


# ---------------------------------------------------------------------------
# scan_indexeddb_log
# ---------------------------------------------------------------------------


def test_scan_indexeddb_log_basic(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    size = _build_synth_log(log)

    summary = scan_indexeddb_log(log)

    assert isinstance(summary, IndexedDbScanSummary)
    assert summary.source_path == log
    assert summary.log_size_bytes == size
    assert summary.total_strings > 0


def test_scan_indexeddb_log_extracts_db_name_hints(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    summary = scan_indexeddb_log(log)

    assert "keyval-store" in summary.db_name_hints
    assert "claude-notifications" in summary.db_name_hints


def test_scan_indexeddb_log_extracts_object_store_hints(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    summary = scan_indexeddb_log(log)

    assert "composer-drafts" in summary.object_store_hints
    assert "starredIds" in summary.object_store_hints
    assert "conversations" in summary.object_store_hints


def test_scan_indexeddb_log_counts_uuids(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    summary = scan_indexeddb_log(log)

    # Two UUIDs (v4 + v7) plus the assistant-message ref → 3 total.
    assert summary.uuid_count == 3
    # Only one is v7.
    assert summary.uuid_v7_count == 1


def test_scan_indexeddb_log_counts_json_blobs(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log, with_draft=True)

    summary = scan_indexeddb_log(log)

    # We wrote 2 JSON blobs: composer-draft + starred-ids.
    assert summary.json_blob_count == 2


def test_scan_indexeddb_log_counts_composer_drafts(tmp_path: Path) -> None:
    log_with = tmp_path / "with_draft.log"
    _build_synth_log(log_with, with_draft=True)
    log_without = tmp_path / "without_draft.log"
    _build_synth_log(log_without, with_draft=False)

    s_with = scan_indexeddb_log(log_with)
    s_without = scan_indexeddb_log(log_without)

    assert s_with.composer_draft_count == 1
    assert s_without.composer_draft_count == 0


def test_scan_indexeddb_log_redacts_drafts_by_default(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    summary = scan_indexeddb_log(log)  # redact_drafts default True

    assert summary.composer_drafts_redacted is True
    # The example list must have the draft, but the user text replaced.
    draft_examples = [
        ex for ex in summary.json_blob_examples
        if isinstance(ex, dict) and "tipTapEditorState" in str(ex)
    ]
    assert draft_examples, "expected a composer-draft example"
    serialized = json.dumps(draft_examples[0])
    assert "secret user thought" not in serialized
    assert "[redacted]" in serialized


def test_scan_indexeddb_log_with_redact_off_keeps_text(tmp_path: Path) -> None:
    log = tmp_path / "000003.log"
    _build_synth_log(log)

    summary = scan_indexeddb_log(log, redact_drafts=False)

    assert summary.composer_drafts_redacted is False
    serialized = json.dumps(summary.json_blob_examples)
    assert "secret user thought" in serialized
    assert "[redacted]" not in serialized


def test_scan_indexeddb_log_caps_examples_at_max(tmp_path: Path) -> None:
    log = tmp_path / "many.log"
    parts = [_SEP]
    # Write 25 distinct minimal JSON blobs.
    for i in range(25):
        parts.append(json.dumps({"i": i, "blob": True}).encode())
        parts.append(_SEP)
    log.write_bytes(b"".join(parts))

    summary = scan_indexeddb_log(log, max_examples=5)
    assert summary.json_blob_count == 25
    assert len(summary.json_blob_examples) == 5


def test_scan_indexeddb_log_handles_missing_file(tmp_path: Path) -> None:
    summary = scan_indexeddb_log(tmp_path / "nope.log")
    assert summary.log_size_bytes == 0
    assert summary.total_strings == 0
    assert summary.json_blob_count == 0


# ---------------------------------------------------------------------------
# _redact_composer_draft (unit)
# ---------------------------------------------------------------------------


def test_redact_replaces_text_field_recursively() -> None:
    blob = {
        "state": {
            "tipTapEditorState": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "private"}],
                    },
                ],
            },
        },
    }
    out = _redact_composer_draft(blob)
    serialized = json.dumps(out)
    assert "private" not in serialized
    assert serialized.count("[redacted]") == 1


def test_redact_preserves_non_text_fields() -> None:
    blob = {"version": 1, "updatedAt": 12345, "ids": [1, 2, 3]}
    out = _redact_composer_draft(blob)
    assert out == blob  # no `text` keys → no changes


def test_redact_is_idempotent() -> None:
    blob = {"text": "original", "nested": {"text": "child"}}
    once = _redact_composer_draft(blob)
    twice = _redact_composer_draft(once)
    assert once == twice


def test_redact_only_replaces_string_text_values() -> None:
    # A `text` key whose value is non-string (eg dict) should NOT be replaced —
    # the function targets the user-content shape only.
    blob = {"text": {"unexpected": "shape"}}
    out = _redact_composer_draft(blob)
    assert out["text"] == {"unexpected": "shape"}


# ---------------------------------------------------------------------------
# find_log_files
# ---------------------------------------------------------------------------


def test_find_log_files_filters_to_seq_dot_log(tmp_path: Path) -> None:
    leveldb = tmp_path / "https_claude.ai_0.indexeddb.leveldb"
    leveldb.mkdir()
    # Create files Chromium would produce.
    (leveldb / "000003.log").write_bytes(b"")
    (leveldb / "000179.log").write_bytes(b"")
    (leveldb / "LOG").write_text("text log, NOT a leveldb log")
    (leveldb / "LOG.old").write_text("text")
    (leveldb / "MANIFEST-000001").write_bytes(b"")
    (leveldb / "CURRENT").write_bytes(b"MANIFEST-000001\n")
    (leveldb / "LOCK").write_bytes(b"")

    found = find_log_files(leveldb)
    names = sorted(p.name for p in found)
    assert names == ["000003.log", "000179.log"]


def test_find_log_files_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert find_log_files(tmp_path / "nope") == []


def test_find_log_files_returns_empty_for_file_input(tmp_path: Path) -> None:
    file = tmp_path / "im_a_file"
    file.write_bytes(b"")
    assert find_log_files(file) == []


# ---------------------------------------------------------------------------
# Observer integration
# ---------------------------------------------------------------------------


def test_observer_emits_indexeddb_summary_to_db(tmp_path: Path) -> None:
    db_path = tmp_path / "pce.db"
    init_db(db_path)

    log = tmp_path / "000003.log"
    _build_synth_log(log)
    summary = scan_indexeddb_log(log)

    observer = ChromiumStateObserver(
        app_id="claude-desktop",
        app_version="1.6608.2.0-test",
        app_channel="msix",
        db_path=db_path,
        state_path=tmp_path / "state.json",
        dry_run=False,
        include_bodies=True,
    )
    observer.observe_indexeddb_summary(summary, origin="https://claude.ai")
    observer.flush_state()

    assert observer.stats["records_seen"] == 1
    assert observer.stats["records_emitted"] == 1
    assert observer.stats["indexeddb_summary"] == 1

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT host, path, body_text_or_json, meta_json "
        "FROM raw_captures WHERE host = 'chromium-indexeddb'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    host, path, body, meta_json = rows[0]
    assert host == "chromium-indexeddb"
    assert path == "/claude-desktop/indexeddb-summary/https://claude.ai"

    body_dict = json.loads(body)
    assert "keyval-store" in body_dict["db_name_hints"]
    assert "composer-drafts" in body_dict["object_store_hints"]
    assert body_dict["composer_drafts_redacted"] is True
    assert body_dict["composer_draft_count"] == 1

    meta = json.loads(meta_json)
    assert meta["source_kind"] == "indexeddb_summary"
    assert meta["origin"] == "https://claude.ai"


def test_observer_dedups_indexeddb_summary_on_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    state_path = tmp_path / "state.json"

    log = tmp_path / "000003.log"
    _build_synth_log(log)

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
        ob.observe_indexeddb_summary(scan_indexeddb_log(log), origin="https://claude.ai")
        ob.flush_state()
        return ob

    first = _run()
    assert first.stats["records_emitted"] == 1
    assert first.stats["records_deduped"] == 0

    second = _run()
    assert second.stats["records_emitted"] == 0
    assert second.stats["records_deduped"] == 1


def test_observer_provider_routes_by_app_id(tmp_path: Path) -> None:
    db_path = tmp_path / "pce.db"
    init_db(db_path)

    log = tmp_path / "000003.log"
    _build_synth_log(log)

    chatgpt = ChromiumStateObserver(
        app_id="chatgpt-desktop",
        app_version="test",
        app_channel="msix",
        db_path=db_path,
        state_path=tmp_path / "state.json",
        dry_run=False,
        include_bodies=True,
    )
    chatgpt.observe_indexeddb_summary(scan_indexeddb_log(log), origin="https://chatgpt.com")
    chatgpt.flush_state()

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT provider, path FROM raw_captures WHERE host = 'chromium-indexeddb'"
    ).fetchone()
    conn.close()

    assert row is not None
    provider, path = row
    assert provider == "openai"
    assert path == "/chatgpt-desktop/indexeddb-summary/https://chatgpt.com"
