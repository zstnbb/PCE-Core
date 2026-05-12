# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_cli_wrapper.capture``.

All hermetic — no subprocess. We hand-construct ``RelayResult`` values
and assert on the rows ``CliWrapperObserver.emit()`` writes (or would
write under ``dry_run=True``).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pce_cli_wrapper.capture import (
    CliWrapperObserver,
    RelayResult,
    _encode_stream,
    _fingerprint,
    _latency_ms,
)


L3H_SOURCE_ID = "l3h-cli-wrapper-default"


def _mk_result(
    *,
    args: list[str] | None = None,
    stdin_bytes: bytes = b"",
    stdout_bytes: bytes = b"",
    stderr_bytes: bytes = b"",
    exit_code: int = 0,
    started_at_ns: int | None = None,
    finished_at_ns: int | None = None,
    tty_passthrough: bool = False,
    timed_out: bool = False,
    target_version: str | None = "2.1.59",
    capture_label: str | None = None,
    target_path: Path | None = None,
    stdin_truncated: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> RelayResult:
    now = time.time_ns()
    return RelayResult(
        target_id="claude-code",
        command_name="claude",
        provider="anthropic",
        target_path=target_path or Path("C:/fake/claude.cmd"),
        args=list(args or []),
        cwd=Path("/tmp"),
        started_at_ns=started_at_ns if started_at_ns is not None else now,
        finished_at_ns=(
            finished_at_ns if finished_at_ns is not None else now + 5_000_000
        ),
        exit_code=exit_code,
        timed_out=timed_out,
        tty_passthrough=tty_passthrough,
        stdin_bytes=stdin_bytes,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdin_truncated=stdin_truncated,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        target_version=target_version,
        capture_label=capture_label,
    )


# ---------------------------------------------------------------------------
# Helpers — _encode_stream / _latency_ms / _fingerprint
# ---------------------------------------------------------------------------


class TestEncodeStream:
    def test_empty_stream(self) -> None:
        out = _encode_stream(b"")
        assert out == {"format": "utf8_text", "value": "", "len": 0}

    def test_utf8_round_trip(self) -> None:
        out = _encode_stream("hello world\n".encode("utf-8"))
        assert out["format"] == "utf8_text"
        assert out["value"] == "hello world\n"
        assert out["len"] == 12

    def test_unicode_passthrough(self) -> None:
        out = _encode_stream("中文测试".encode("utf-8"))
        assert out["format"] == "utf8_text"
        assert out["value"] == "中文测试"

    def test_binary_falls_back_to_base64(self) -> None:
        raw = b"\xff\x00\xfe\x01"
        out = _encode_stream(raw)
        assert out["format"] == "base64"
        assert out["len"] == 4
        import base64
        assert base64.b64decode(out["value"]) == raw


class TestLatencyMs:
    def test_normal_duration(self) -> None:
        r = _mk_result(started_at_ns=1_000_000_000, finished_at_ns=1_500_000_000)
        assert _latency_ms(r) == 500

    def test_zero_when_clock_inverted(self) -> None:
        # Defensive: clock reordering should yield 0, not negative.
        r = _mk_result(started_at_ns=2_000_000_000, finished_at_ns=1_000_000_000)
        assert _latency_ms(r) == 0


class TestFingerprint:
    def test_identical_results_match(self) -> None:
        r1 = _mk_result(args=["-p", "hello"], stdout_bytes=b"world")
        r2 = _mk_result(args=["-p", "hello"], stdout_bytes=b"world")
        # Body string regen is deterministic except for timing fields,
        # so we compare via the observer's own body builder.
        obs = CliWrapperObserver(dry_run=True)
        b1 = obs._build_body(r1)
        b2 = obs._build_body(r2)
        # Even though timing differs the fingerprint is over body, which
        # contains timing — sanity check that argv equality alone is not
        # enough. We then re-compute with matched timing to confirm
        # determinism over the structured inputs.
        r3 = _mk_result(
            args=["-p", "hello"],
            stdout_bytes=b"world",
            started_at_ns=1_000_000_000,
            finished_at_ns=1_500_000_000,
        )
        r4 = _mk_result(
            args=["-p", "hello"],
            stdout_bytes=b"world",
            started_at_ns=1_000_000_000,
            finished_at_ns=1_500_000_000,
        )
        b3 = obs._build_body(r3)
        b4 = obs._build_body(r4)
        assert _fingerprint(r3, b3) == _fingerprint(r4, b4)

    def test_different_args_diverge(self) -> None:
        obs = CliWrapperObserver(dry_run=True)
        r1 = _mk_result(args=["-p", "hello"])
        r2 = _mk_result(args=["-p", "world"])
        b1 = obs._build_body(r1)
        b2 = obs._build_body(r2)
        assert _fingerprint(r1, b1) != _fingerprint(r2, b2)

    def test_different_target_path_diverges(self) -> None:
        obs = CliWrapperObserver(dry_run=True)
        r1 = _mk_result(target_path=Path("/a/claude.cmd"))
        r2 = _mk_result(target_path=Path("/b/claude.cmd"))
        b = obs._build_body(r1)  # body is identical for both
        assert _fingerprint(r1, b) != _fingerprint(r2, b)


# ---------------------------------------------------------------------------
# CliWrapperObserver.emit — DB write
# ---------------------------------------------------------------------------


class TestEmitWritesRow:
    def test_one_emit_writes_one_row(
        self, tmp_pce_db: Path, count_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(args=["-p", "hi"]))
        assert obs.stats["rows_written"] == 1
        assert obs.stats["write_failures"] == 0
        assert count_l3h_rows(tmp_pce_db) == 1

    def test_row_envelope_shape(
        self, tmp_pce_db: Path, fetch_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(
            args=["-p", "hi"],
            stdout_bytes=b"hi back",
            stderr_bytes=b"",
            exit_code=0,
        ))
        rows = fetch_l3h_rows(tmp_pce_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "cli_wrapper"
        assert row["source_id"] == L3H_SOURCE_ID
        assert row["direction"] == "conversation"
        assert row["host"] == "cli-wrapper"
        assert row["path"] == "/claude-code/claude"
        assert row["provider"] == "anthropic"
        assert row["status_code"] == 0
        assert row["agent_name"] == "pce-cli-wrapper"
        assert row["latency_ms"] >= 0

    def test_meta_carries_diagnostic_fields(
        self, tmp_pce_db: Path, fetch_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(
            args=["-p", "hi"],
            stdout_bytes=b"hi back",
            stdout_truncated=True,
        ))
        rows = fetch_l3h_rows(tmp_pce_db)
        meta = json.loads(rows[0]["meta_json"])
        assert meta["target_id"] == "claude-code"
        assert meta["command_name"] == "claude"
        assert meta["target_version"] == "2.1.59"
        assert meta["capture_label"] == "claude-code"
        assert meta["capture_kind"] == "stdio_relay"
        assert meta["tty_passthrough"] is False
        assert meta["timed_out"] is False
        assert meta["stdout_bytes"] == len(b"hi back")
        assert meta["stdout_truncated"] is True
        assert meta["stdin_bytes"] == 0
        assert "fingerprint" in meta and len(meta["fingerprint"]) == 64
        assert "wrapper_version" in meta

    def test_body_carries_streams_and_args(
        self, tmp_pce_db: Path, fetch_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(
            args=["-p", "hi"],
            stdin_bytes=b"user prompt",
            stdout_bytes=b"agent reply",
            stderr_bytes=b"diagnostic",
        ))
        rows = fetch_l3h_rows(tmp_pce_db)
        body = json.loads(rows[0]["body_text_or_json"])
        assert body["args"] == ["-p", "hi"]
        assert body["exit_code"] == 0
        assert body["stdin"] == {
            "format": "utf8_text", "value": "user prompt", "len": 11,
        }
        assert body["stdout"] == {
            "format": "utf8_text", "value": "agent reply", "len": 11,
        }
        assert body["stderr"] == {
            "format": "utf8_text", "value": "diagnostic", "len": 10,
        }

    def test_capture_label_override_lands_in_meta(
        self, tmp_pce_db: Path, fetch_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(capture_label="my-claude"))
        meta = json.loads(fetch_l3h_rows(tmp_pce_db)[0]["meta_json"])
        assert meta["capture_label"] == "my-claude"


# ---------------------------------------------------------------------------
# TTY passthrough
# ---------------------------------------------------------------------------


class TestTtyPassthrough:
    def test_body_omits_streams(
        self, tmp_pce_db: Path, fetch_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(tty_passthrough=True, args=["-i"]))
        body = json.loads(fetch_l3h_rows(tmp_pce_db)[0]["body_text_or_json"])
        assert body["tty_passthrough"] is True
        assert "stdin" not in body
        assert "stdout" not in body
        assert "stderr" not in body
        assert body["args"] == ["-i"]

    def test_meta_flags_tty(
        self, tmp_pce_db: Path, fetch_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        obs.emit(_mk_result(tty_passthrough=True))
        meta = json.loads(fetch_l3h_rows(tmp_pce_db)[0]["meta_json"])
        assert meta["tty_passthrough"] is True


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_persist(
        self, tmp_pce_db: Path, count_l3h_rows
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db, dry_run=True)
        obs.emit(_mk_result(args=["-p", "hi"]))
        obs.emit(_mk_result(args=["-p", "hi2"]))
        assert obs.stats["rows_written"] == 2  # would-emit count
        assert count_l3h_rows(tmp_pce_db) == 0


# ---------------------------------------------------------------------------
# Git context integration
# ---------------------------------------------------------------------------


class TestGitContext:
    def test_body_includes_git_when_in_repo(
        self, tmp_pce_db: Path, fetch_l3h_rows, tmp_git_repo: Path
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        r = _mk_result(args=["-p", "hi"])
        # Override cwd to point at a real git repo
        r.cwd = tmp_git_repo
        obs.emit(r)
        rows = fetch_l3h_rows(tmp_pce_db)
        body = json.loads(rows[0]["body_text_or_json"])
        assert "git" in body
        assert "git_commit_sha" in body["git"]
        assert len(body["git"]["git_commit_sha"]) == 40
        assert "git_branch" in body["git"]

    def test_meta_includes_git_when_in_repo(
        self, tmp_pce_db: Path, fetch_l3h_rows, tmp_git_repo: Path
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        r = _mk_result(args=["-p", "hi"])
        r.cwd = tmp_git_repo
        obs.emit(r)
        rows = fetch_l3h_rows(tmp_pce_db)
        meta = json.loads(rows[0]["meta_json"])
        assert "git" in meta
        assert "git_commit_sha" in meta["git"]
        assert "git_short_sha" in meta["git"]

    def test_no_git_key_when_not_in_repo(
        self, tmp_pce_db: Path, fetch_l3h_rows, tmp_path: Path
    ) -> None:
        obs = CliWrapperObserver(db_path=tmp_pce_db)
        r = _mk_result(args=["-p", "hi"])
        r.cwd = tmp_path  # not a git repo
        obs.emit(r)
        rows = fetch_l3h_rows(tmp_pce_db)
        body = json.loads(rows[0]["body_text_or_json"])
        meta = json.loads(rows[0]["meta_json"])
        assert "git" not in body
        assert "git" not in meta
