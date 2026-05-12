# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for tests/e2e_cli/.

Each fixture is small + composable:

- ``tmp_pce_db``: fresh sqlite DB at ``<tmp>/pce.db`` with all
  migrations applied (so ``l3h-cli-wrapper-default`` is registered).
- ``fake_shim_script``: a tiny Python helper used as the relay's
  child argv[0]. Echoes args, mirrors stdin → stderr, supports
  ``--exit-code`` and ``--sleep`` flags.
- ``fake_target``: a ``Path`` pointing at ``sys.executable``. Tests
  pass ``[fake_shim_script, *user_args]`` as ``child_args`` so the
  relay's ``_build_command`` defaults to direct invocation, and the
  child is the fake shim.
- ``isolated_bin_dir``: a tmp directory used for install / uninstall
  tests so we never touch the real ``%LOCALAPPDATA%\\PCE\\bin``.
"""
from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pce_core.db import init_db  # noqa: E402  (sys.path tweaked above)


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def tmp_pce_db(tmp_path: Path) -> Path:
    """A fresh sqlite DB at ``<tmp>/pce.db`` with all migrations applied."""
    db_path = tmp_path / "pce.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def isolated_bin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force the wrapper's default bin dir to ``<tmp>/bin``."""
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("PCE_CLI_WRAPPER_BIN_DIR", str(bin_dir))
    return bin_dir


# ---------------------------------------------------------------------------
# Fake shim — a python script the relay can spawn
# ---------------------------------------------------------------------------


_FAKE_SHIM_SOURCE = '''
"""Test-only fake shim for pce_cli_wrapper relay tests.

Behaviour:

- Echoes its argv (after the script path) to stdout, one per line,
  prefixed with ``arg: `` so tests can match.
- Reads stdin to EOF, writes the exact bytes (binary) to stderr
  preceded by ``stdin:`` marker so tests can match.
- Honours ``--exit-code N`` (default 0).
- Honours ``--sleep N`` (sleeps N seconds before exiting). Used for
  timeout tests.
- Honours ``--emit-binary`` (writes 4 raw bytes 0xff 0x00 0xfe 0x01
  to stdout). Used for base64 encoding tests.
- Honours ``--echo-large N`` (writes N bytes of ``a`` to stdout).
  Used for body-cap truncation tests.
"""
import sys
import time

argv = sys.argv[1:]
exit_code = 0
sleep_for = 0.0
emit_binary = False
echo_large = 0

i = 0
positional = []
while i < len(argv):
    a = argv[i]
    if a == "--exit-code":
        exit_code = int(argv[i + 1])
        i += 2
        continue
    if a == "--sleep":
        sleep_for = float(argv[i + 1])
        i += 2
        continue
    if a == "--emit-binary":
        emit_binary = True
        i += 1
        continue
    if a == "--echo-large":
        echo_large = int(argv[i + 1])
        i += 2
        continue
    positional.append(a)
    i += 1

# Echo positional args.
for a in positional:
    sys.stdout.write(f"arg: {a}\\n")
sys.stdout.flush()

if emit_binary:
    sys.stdout.buffer.write(b"\\xff\\x00\\xfe\\x01")
    sys.stdout.buffer.flush()

if echo_large:
    sys.stdout.write("a" * echo_large)
    sys.stdout.flush()

# Drain stdin → stderr.
data = sys.stdin.buffer.read()
if data:
    sys.stderr.buffer.write(b"stdin:")
    sys.stderr.buffer.write(data)
    sys.stderr.buffer.flush()

if sleep_for > 0:
    time.sleep(sleep_for)

sys.exit(exit_code)
'''


@pytest.fixture
def fake_shim_script(tmp_path: Path) -> Path:
    """Create a fake-shim helper at ``<tmp>/fake_shim.py`` and return its path."""
    p = tmp_path / "fake_shim.py"
    p.write_text(_FAKE_SHIM_SOURCE, encoding="utf-8")
    return p


@pytest.fixture
def fake_target() -> Path:
    """``sys.executable`` — used as the relay's ``target_path``.

    Combined with ``fake_shim_script`` as the first ``child_args``
    entry, this lets us exercise ``relay.relay()`` against a real
    Python child without depending on any external CLI install.
    """
    return Path(sys.executable)


# ---------------------------------------------------------------------------
# Helpers re-exported for test files
# ---------------------------------------------------------------------------


L3H_SOURCE_ID = "l3h-cli-wrapper-default"


@pytest.fixture
def count_l3h_rows():
    def _count(db_path: Path) -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return int(conn.execute(
                "SELECT count(*) FROM raw_captures WHERE source_id = ?",
                (L3H_SOURCE_ID,),
            ).fetchone()[0])
        finally:
            conn.close()

    return _count


@pytest.fixture
def fetch_l3h_rows():
    def _fetch(db_path: Path) -> list[dict]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT id, source_id, source, direction, host, path, "
                "provider, status_code, latency_ms, body_text_or_json, "
                "meta_json, agent_name "
                "FROM raw_captures WHERE source_id = ? ORDER BY created_at",
                (L3H_SOURCE_ID,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    return _fetch


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init"], cwd=str(repo),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), capture_output=True, check=True,
    )
    (repo / "README.md").write_text("init")
    subprocess.run(
        ["git", "add", "."], cwd=str(repo),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), capture_output=True, check=True,
    )
    return repo
