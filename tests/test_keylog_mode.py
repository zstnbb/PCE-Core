# SPDX-License-Identifier: Apache-2.0
"""Wave 2 — SSLKEYLOGFILE production path unit tests.

Coverage matrix per `Docs/stability/redundancy-sprint/02-wave2-sslkeylogfile.md` §5.
16 tests:

  - parse / format         (5)
  - watcher / rotation     (3)
  - lookup                 (2)
  - env-var management     (3)
  - meta_json safety       (3)

All tests are hermetic — they don't open Chrome or write to system env.
The `cli_*` tests use monkeypatch to keep ``set_keylog_env_user`` from
touching the real registry.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pce_core.cert_wizard import keylog as keylog_env
from pce_proxy.keylog_mode import (
    ALL_KNOWN_LABELS,
    KeylogSession,
    KeylogWatcher,
    TLS13_LABELS,
    emit_evidence_from_flow,
    parse_keylog_line,
    parse_keylog_text,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CR_A = "a" * 64
CR_B = "b" * 64
SECRET = "f" * 64


def _full_tls13_lines(cr: str = CR_A) -> str:
    return "\n".join(f"{lbl} {cr} {SECRET}" for lbl in sorted(TLS13_LABELS))


# ===========================================================================
# 1. PARSE / FORMAT — 5 tests
# ===========================================================================

def test_parse_simple_keylog():
    text = _full_tls13_lines(CR_A)
    sessions = parse_keylog_text(text)
    assert len(sessions) == 1
    sess = sessions[CR_A]
    assert len(sess.secrets) == 5
    assert sess.completeness() == "full"


def test_parse_multi_session():
    text = _full_tls13_lines(CR_A) + "\n" + _full_tls13_lines(CR_B)
    sessions = parse_keylog_text(text)
    assert set(sessions.keys()) == {CR_A, CR_B}
    assert all(s.completeness() == "full" for s in sessions.values())


def test_parse_partial_handshake():
    text = (
        f"CLIENT_HANDSHAKE_TRAFFIC_SECRET {CR_A} {SECRET}\n"
        f"SERVER_HANDSHAKE_TRAFFIC_SECRET {CR_A} {SECRET}\n"
    )
    sessions = parse_keylog_text(text)
    assert sessions[CR_A].completeness() == "partial_handshake_only"


def test_parse_malformed_line_skipped():
    text = (
        "# this is a comment\n"
        f"BOGUS_LABEL {CR_A} {SECRET}\n"
        f"CLIENT_HANDSHAKE_TRAFFIC_SECRET nothex {SECRET}\n"
        f"CLIENT_HANDSHAKE_TRAFFIC_SECRET {CR_A} {SECRET}\n"
    )
    sessions = parse_keylog_text(text)
    assert CR_A in sessions
    assert "BOGUS_LABEL" not in sessions[CR_A].secrets


def test_parse_empty_file():
    assert parse_keylog_text("") == {}
    assert parse_keylog_line("") is None
    assert parse_keylog_line("# comment only") is None


# ===========================================================================
# 2. WATCHER / ROTATION — 3 tests
# ===========================================================================

def test_watcher_initial_read(tmp_path: Path):
    keylog_file = tmp_path / "keylog.txt"
    keylog_file.write_text(_full_tls13_lines(CR_A))
    w = KeylogWatcher(keylog_file)
    assert w.poll() == 5
    assert w.session_count() == 1


def test_watcher_incremental_tail(tmp_path: Path):
    keylog_file = tmp_path / "keylog.txt"
    keylog_file.write_text(_full_tls13_lines(CR_A))
    w = KeylogWatcher(keylog_file)
    w.poll()  # absorbs A
    # Append B; only B should be re-read.
    with keylog_file.open("a", encoding="utf-8") as fh:
        fh.write("\n" + _full_tls13_lines(CR_B))
    new_lines = w.poll()
    assert new_lines == 5
    assert w.session_count() == 2


def test_watcher_rotation_resets(tmp_path: Path):
    keylog_file = tmp_path / "keylog.txt"
    keylog_file.write_text(_full_tls13_lines(CR_A))
    w = KeylogWatcher(keylog_file)
    w.poll()
    # Force rotation by deleting+recreating with smaller content.
    keylog_file.unlink()
    short_content = (
        f"CLIENT_HANDSHAKE_TRAFFIC_SECRET {CR_B} {SECRET}\n"
        f"SERVER_HANDSHAKE_TRAFFIC_SECRET {CR_B} {SECRET}\n"
    )
    keylog_file.write_text(short_content)
    new_lines = w.poll()
    # After rotation watcher resets and re-reads everything → 2 lines.
    assert new_lines == 2
    # Old session evicted on reset.
    assert w.session_count() == 1
    assert w.lookup(CR_B) is not None
    assert w.lookup(CR_A) is None


# ===========================================================================
# 3. LOOKUP — 2 tests
# ===========================================================================

def test_watcher_lookup_hit(tmp_path: Path):
    keylog_file = tmp_path / "keylog.txt"
    keylog_file.write_text(_full_tls13_lines(CR_A))
    w = KeylogWatcher(keylog_file)
    w.poll()
    sess = w.lookup(CR_A)
    assert sess is not None
    assert sess.completeness() == "full"


def test_watcher_lookup_miss(tmp_path: Path):
    keylog_file = tmp_path / "keylog.txt"
    keylog_file.write_text(_full_tls13_lines(CR_A))
    w = KeylogWatcher(keylog_file)
    w.poll()
    assert w.lookup(CR_B) is None


# ===========================================================================
# 4. ENV-VAR MANAGEMENT — 3 tests
# ===========================================================================

def test_env_set_in_process(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    # Stub away the registry persistence so the test stays hermetic.
    monkeypatch.setattr(keylog_env, "_persist_user_env_windows",
                        lambda *a, **kw: None)
    target = tmp_path / "k.txt"
    resolved = keylog_env.set_keylog_env_user(target)
    assert os.environ["SSLKEYLOGFILE"] == str(target)
    assert resolved == target
    assert resolved.parent.exists()


def test_env_clear(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "k.txt"))
    monkeypatch.setattr(keylog_env, "_remove_user_env_windows",
                        lambda *a, **kw: None)
    keylog_env.clear_keylog_env()
    assert "SSLKEYLOGFILE" not in os.environ


def test_env_get(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "k.txt"))
    assert keylog_env.get_keylog_env() == str(tmp_path / "k.txt")
    monkeypatch.delenv("SSLKEYLOGFILE")
    assert keylog_env.get_keylog_env() is None


# ===========================================================================
# 5. META_JSON SAFETY — 3 tests
# ===========================================================================

def test_evidence_meta_does_not_leak_secrets(tmp_path: Path):
    keylog_file = tmp_path / "keylog.txt"
    keylog_file.write_text(_full_tls13_lines(CR_A))
    w = KeylogWatcher(keylog_file)
    w.poll()
    blob = emit_evidence_from_flow(w, client_random_hex=CR_A)
    assert blob is not None
    # The blob must contain ONLY metadata (label names, count, completeness).
    # Verify the actual hex secret material never leaks.
    assert SECRET not in repr(blob)
    assert CR_A not in repr(blob)
    # Whitelist of allowed top-level keys.
    assert set(blob.keys()) == {"completeness", "label_count", "labels"}


def test_evidence_meta_completeness_full():
    sess = KeylogSession(client_random_hex=CR_A)
    for lbl in TLS13_LABELS:
        sess.add(lbl, SECRET)
    blob = sess.evidence_meta()
    assert blob["completeness"] == "full"
    assert blob["label_count"] == 5
    assert sorted(blob["labels"]) == sorted(TLS13_LABELS)


def test_evidence_meta_returns_none_when_no_random():
    w = KeylogWatcher(Path("/tmp/nope-doesnt-exist"))
    assert emit_evidence_from_flow(w, client_random_hex=None) is None
    assert emit_evidence_from_flow(w, client_random_hex="") is None
