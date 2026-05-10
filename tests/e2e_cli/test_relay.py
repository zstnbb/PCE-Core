# SPDX-License-Identifier: Apache-2.0
"""tests for ``pce_cli_wrapper.relay``.

The relay is exercised against a real Python subprocess (``sys.
executable`` running ``fake_shim.py``). This proves the stdio tee +
exit-code propagation + observer write-through work end-to-end
without depending on any external CLI agent.

Two operating modes are covered:

- **Pipe tee** (default): parent stdin / stdout / stderr go through
  pipes; tests read mirrored output via ``BytesIO``.
- **TTY passthrough** (monkey-patched ``_stdin_is_tty``): the relay
  inherits parent fds, captures only metadata.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import pce_cli_wrapper.relay as relay_mod
from pce_cli_wrapper.relay import (
    _build_command,
    _CapBuffer,
    _stdin_is_tty,
    relay,
)


L3H_SOURCE_ID = "l3h-cli-wrapper-default"


# ---------------------------------------------------------------------------
# _CapBuffer — internal byte-cap helper
# ---------------------------------------------------------------------------


class TestCapBuffer:
    def test_grows_under_cap(self) -> None:
        b = _CapBuffer(100)
        b.write(b"abc")
        b.write(b"de")
        assert b.value() == b"abcde"
        assert b.truncated is False

    def test_truncates_at_cap(self) -> None:
        b = _CapBuffer(5)
        b.write(b"abcdefg")
        assert b.value() == b"abcde"
        assert b.truncated is True

    def test_post_cap_writes_dropped(self) -> None:
        b = _CapBuffer(3)
        b.write(b"abc")
        b.write(b"def")
        assert b.value() == b"abc"
        assert b.truncated is True

    def test_zero_cap(self) -> None:
        b = _CapBuffer(0)
        b.write(b"x")
        assert b.value() == b""
        assert b.truncated is True

    def test_empty_write_noop(self) -> None:
        b = _CapBuffer(5)
        b.write(b"")
        assert b.value() == b""
        assert b.truncated is False


# ---------------------------------------------------------------------------
# _build_command — argv composition for each shim suffix
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_cmd_dispatch_via_comspec(self, tmp_path: Path) -> None:
        shim = tmp_path / "claude.cmd"
        shim.write_text("@echo off\r\n", encoding="utf-8")
        argv = _build_command(shim, ["--print", "hi"])
        # comspec is the first element; the shim path is somewhere in the tail
        assert any(arg == str(shim) for arg in argv)
        # /c flag is present (cmd.exe convention)
        assert "/c" in argv or "-File" in argv

    def test_ps1_dispatch_via_powershell(self, tmp_path: Path) -> None:
        shim = tmp_path / "claude.ps1"
        shim.write_text("# stub\n", encoding="utf-8")
        argv = _build_command(shim, ["--arg"])
        assert "-File" in argv
        assert str(shim) in argv

    def test_bare_dispatch_direct(self, tmp_path: Path) -> None:
        shim = tmp_path / "python_like_target"
        shim.write_text("", encoding="utf-8")
        argv = _build_command(shim, ["a", "b"])
        assert argv == [str(shim), "a", "b"]


# ---------------------------------------------------------------------------
# Pipe-tee mode end-to-end via real subprocess
# ---------------------------------------------------------------------------


class TestRelayPipeTee:
    def test_basic_run_records_one_row(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        count_l3h_rows,
        fetch_l3h_rows,
    ) -> None:
        parent_stdin = io.BytesIO(b"")
        parent_stdout = io.BytesIO()
        parent_stderr = io.BytesIO()
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "hello", "world"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            parent_stdin=parent_stdin,
            parent_stdout=parent_stdout,
            parent_stderr=parent_stderr,
        )
        assert rc == 0
        # Stdout was mirrored.
        out = parent_stdout.getvalue().decode("utf-8")
        assert "arg: hello" in out
        assert "arg: world" in out
        # One row landed.
        assert count_l3h_rows(tmp_pce_db) == 1
        row = fetch_l3h_rows(tmp_pce_db)[0]
        body = json.loads(row["body_text_or_json"])
        assert body["exit_code"] == 0
        assert "arg: hello" in body["stdout"]["value"]

    def test_stdin_is_forwarded_and_captured(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        fetch_l3h_rows,
    ) -> None:
        parent_stdin = io.BytesIO(b"prompt-from-user\n")
        parent_stdout = io.BytesIO()
        parent_stderr = io.BytesIO()
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script)],
            target_id="claude-code",
            db_path=tmp_pce_db,
            parent_stdin=parent_stdin,
            parent_stdout=parent_stdout,
            parent_stderr=parent_stderr,
        )
        assert rc == 0
        # The fake shim mirrors stdin → stderr prefixed with "stdin:".
        err = parent_stderr.getvalue()
        assert b"stdin:" in err
        assert b"prompt-from-user" in err
        # Captured stdin made it into the row.
        body = json.loads(fetch_l3h_rows(tmp_pce_db)[0]["body_text_or_json"])
        assert body["stdin"]["value"] == "prompt-from-user\n"

    def test_exit_code_is_propagated(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        fetch_l3h_rows,
    ) -> None:
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "--exit-code", "7"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        assert rc == 7
        row = fetch_l3h_rows(tmp_pce_db)[0]
        assert row["status_code"] == 7
        body = json.loads(row["body_text_or_json"])
        assert body["exit_code"] == 7

    def test_binary_stdout_falls_back_to_base64(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        fetch_l3h_rows,
    ) -> None:
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "--emit-binary"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        assert rc == 0
        body = json.loads(fetch_l3h_rows(tmp_pce_db)[0]["body_text_or_json"])
        # The stdout has both ASCII "arg: ...\n" header and binary bytes.
        # Either format is allowed, depending on whether the bytes
        # decoded as UTF-8 — but our 0xff 0x00 sequence is not valid
        # UTF-8 so the encoder must pick base64.
        assert body["stdout"]["format"] == "base64"
        import base64
        raw = base64.b64decode(body["stdout"]["value"])
        assert b"\xff\x00\xfe\x01" in raw

    def test_max_body_bytes_truncates_stdout(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        fetch_l3h_rows,
    ) -> None:
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "--echo-large", "5000"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            max_body_bytes=200,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        assert rc == 0
        row = fetch_l3h_rows(tmp_pce_db)[0]
        meta = json.loads(row["meta_json"])
        body = json.loads(row["body_text_or_json"])
        assert meta["stdout_truncated"] is True
        # Captured stdout cap honoured: ≤ 200 bytes.
        assert body["stdout"]["len"] <= 200

    def test_timeout_kills_child(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        fetch_l3h_rows,
    ) -> None:
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "--sleep", "30"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            timeout_s=0.5,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        # Killed children typically exit non-zero (or signal-derived);
        # the row metadata MUST flag timed_out=True.
        meta = json.loads(fetch_l3h_rows(tmp_pce_db)[0]["meta_json"])
        assert meta["timed_out"] is True


# ---------------------------------------------------------------------------
# TTY passthrough mode — monkey-patch _stdin_is_tty
# ---------------------------------------------------------------------------


class TestRelayTtyPassthrough:
    def test_passthrough_marks_row_no_streams(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        fetch_l3h_rows,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force the relay to take the passthrough branch even though
        # our test parent_stdin is a BytesIO.
        monkeypatch.setattr(relay_mod, "_stdin_is_tty", lambda _s: True)

        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "--exit-code", "0"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        assert rc == 0
        row = fetch_l3h_rows(tmp_pce_db)[0]
        meta = json.loads(row["meta_json"])
        body = json.loads(row["body_text_or_json"])
        assert meta["tty_passthrough"] is True
        assert body["tty_passthrough"] is True
        assert "stdin" not in body
        assert "stdout" not in body
        assert "stderr" not in body


# ---------------------------------------------------------------------------
# Defensive: target missing
# ---------------------------------------------------------------------------


class TestDefensive:
    def test_missing_target_returns_127(
        self,
        tmp_pce_db: Path,
        tmp_path: Path,
    ) -> None:
        rc = relay(
            target_path=tmp_path / "does_not_exist.exe",
            child_args=[],
            target_id="claude-code",
            db_path=tmp_pce_db,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        assert rc == 127

    def test_dry_run_does_not_persist(
        self,
        tmp_pce_db: Path,
        fake_shim_script: Path,
        fake_target: Path,
        count_l3h_rows,
    ) -> None:
        rc = relay(
            target_path=fake_target,
            child_args=[str(fake_shim_script), "--exit-code", "0"],
            target_id="claude-code",
            db_path=tmp_pce_db,
            dry_run=True,
            parent_stdin=io.BytesIO(b""),
            parent_stdout=io.BytesIO(),
            parent_stderr=io.BytesIO(),
        )
        assert rc == 0
        assert count_l3h_rows(tmp_pce_db) == 0
