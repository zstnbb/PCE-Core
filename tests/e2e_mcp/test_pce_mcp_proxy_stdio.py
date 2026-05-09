# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for ``pce_mcp_proxy`` (UCS L3f, capture posture B).

Topology under test::

    Test client (MCPStdioClient)
        │ stdio / JSON-RPC 2.0
        ▼
    pce-mcp-proxy (subprocess: python -m pce_mcp_proxy -- ...)
        │ stdio / JSON-RPC 2.0
        ▼
    mock upstream (subprocess: python tests/e2e_mcp/_mock_upstream.py)

The proxy MUST be transparent: the test client should observe behaviour
identical to talking directly to the mock upstream. Concurrently, every
JSON-RPC frame in either direction should land in ``raw_captures`` with
``source_id = 'mcp-proxy-default'`` and matching method/path. Paired
``tools/call`` frames must trigger Tier 1 normalisation into a session
with ``role=assistant`` + ``role=tool`` messages.

These tests run the same harness as ``test_pce_mcp_stdio.py`` (subprocess
+ stdio JSON-RPC); they do NOT reach into the proxy's internals.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

# Reuse the stdio client + helpers from the posture-A e2e module so the
# wire protocol stays identical. The whole point of posture B is that
# the host can't tell the difference between A and B at the protocol
# layer.
from .test_pce_mcp_stdio import (
    MCPClientError,
    MCPStdioClient,
    _readline_with_timeout,
)


pytestmark = pytest.mark.e2e_mcp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_upstream_path() -> Path:
    return Path(__file__).resolve().parent / "_mock_upstream.py"


@pytest.fixture
def proxy_argv_factory(python_exe, mock_upstream_path, repo_root):
    """Build a proxy invocation that wraps the mock upstream."""

    def _make(
        *,
        upstream_name: str = "mock",
        responses: dict[str, Any] | None = None,
        proxy_extra: list[str] | None = None,
    ) -> tuple[list[str], dict[str, str]]:
        argv = [
            python_exe,
            "-m",
            "pce_mcp_proxy",
            "--upstream-name",
            upstream_name,
            "--quiet",
        ]
        if proxy_extra:
            argv.extend(proxy_extra)
        argv.extend([
            "--",
            python_exe,
            str(mock_upstream_path),
        ])
        env = {
            "PCE_MOCK_RESPONSES": json.dumps(responses or {}),
            "PYTHONUNBUFFERED": "1",
        }
        return argv, env

    return _make


def _query_captures(db_path: Path) -> list[dict[str, Any]]:
    """Read every raw_captures row created by this test."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, created_at, source_id, direction, pair_id, host, "
            "path, method, provider, status_code, latency_ms, "
            "body_text_or_json, meta_json "
            "FROM raw_captures WHERE source_id = 'mcp-proxy-default' "
            "ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _query_sessions_messages(db_path: Path) -> list[dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.id AS session_id, s.title_hint, s.provider, "
            "       m.role, m.content_text, m.content_json "
            "FROM sessions s JOIN messages m ON m.session_id = s.id "
            "WHERE s.source_id = 'mcp-proxy-default' "
            "ORDER BY s.started_at, m.ts, m.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _wait_for_normalization(db_path: Path, *, expected_msgs: int, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Poll until the expected number of normalised messages exist.

    Normalisation happens on a worker thread inside the proxy; we may
    observe the wire response before the SQL write commits, so retry
    briefly.
    """
    deadline = time.monotonic() + timeout
    rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        rows = _query_sessions_messages(db_path)
        if len(rows) >= expected_msgs:
            return rows
        time.sleep(0.1)
    return rows


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


_INITIALIZE_RESULT = {
    "result": {
        "protocolVersion": "2024-11-05",
        "serverInfo": {"name": "mock-upstream", "version": "0.1"},
        "capabilities": {"tools": {}},
    }
}

_TOOLS_LIST_RESULT = {
    "result": {
        "tools": [
            {
                "name": "echo",
                "description": "Echo back the message argument.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                },
            },
            {
                "name": "boom",
                "description": "Always fails (tool-level error).",
                "inputSchema": {"type": "object"},
            },
        ]
    }
}

_TOOLS_CALL_ECHO_OK = {
    "result": {
        "content": [{"type": "text", "text": "echoed: hello"}],
        "isError": False,
    }
}

_TOOLS_CALL_BOOM_TOOL_ERROR = {
    "result": {
        "content": [{"type": "text", "text": "kaboom"}],
        "isError": True,
    }
}

_TOOLS_CALL_NUKE_RPC_ERROR = {
    "error": {"code": -32602, "message": "invalid params"}
}


def _baseline_responses(extra_calls: dict[str, Any] | None = None) -> dict[str, Any]:
    calls = {"echo": _TOOLS_CALL_ECHO_OK}
    if extra_calls:
        calls.update(extra_calls)
    return {
        "initialize": _INITIALIZE_RESULT,
        "tools/list": _TOOLS_LIST_RESULT,
        "tools/call": calls,
    }


def test_R01_proxy_forwards_initialize_handshake(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R01 — Proxy launches, spawns mock upstream, forwards initialize.

    The host (test client) sees the upstream's serverInfo end-to-end,
    proving lossless forwarding. Equivalent to E01 from the posture-A
    suite but now exercising the L3f middleware.
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        result = cli.initialize()
        assert result.get("serverInfo", {}).get("name") == "mock-upstream", result


def test_R02_proxy_forwards_tools_list(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R02 — tools/list passes through unchanged.

    Names and inputSchemas are byte-equivalent to what the upstream
    returned (no proxy-side rewriting).
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        tools = cli.list_tools()
        names = sorted(t["name"] for t in tools)
        assert names == ["boom", "echo"], names


def test_R03_proxy_captures_tools_call_pair(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R03 — tools/call request + response both land in raw_captures.

    Verifies:
    - Two rows with source_id='mcp-proxy-default'
    - Same pair_id linking them
    - host='stdio', method='JSONRPC', path='tools/call'
    - provider='mcp:mock'
    - response row has status_code=200 and a non-null latency_ms
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        out = cli.call_tool("echo", {"message": "hello"})
        # Wire transparency
        assert out.get("isError") is False
        text = "".join(c["text"] for c in out["content"] if c.get("type") == "text")
        assert "echoed: hello" in text

    # The proxy may still be flushing its observer queue; give the
    # background thread a moment to drain.
    time.sleep(0.3)

    rows = _query_captures(isolated_data_dir / "pce.db")
    paths = sorted(r["path"] for r in rows)
    # We expect at least initialize (handshake) and tools/call (the
    # call we made). The notifications/initialized notification adds
    # one more direction='request' row. tools/list is NOT expected
    # because the test client never called list_tools().
    assert "tools/call" in paths
    assert "initialize" in paths
    assert "notifications/initialized" in paths

    # tools/call pair specifically
    tc_rows = [r for r in rows if r["path"] == "tools/call"]
    assert len(tc_rows) == 2, [r["direction"] for r in tc_rows]
    pair_ids = {r["pair_id"] for r in tc_rows}
    assert len(pair_ids) == 1, "request and response must share pair_id"

    by_dir = {r["direction"]: r for r in tc_rows}
    assert by_dir["request"]["provider"] == "mcp:mock"
    assert by_dir["request"]["host"] == "stdio"
    assert by_dir["request"]["method"] == "JSONRPC"
    assert by_dir["response"]["status_code"] == 200
    assert by_dir["response"]["latency_ms"] is not None
    assert by_dir["response"]["latency_ms"] >= 0


def test_R04_tools_call_normalizes_into_session(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R04 — tools/call pair triggers Tier 1 normalisation.

    Expected output: 1 session, 2 messages — one role=assistant carrying
    the tool_calls envelope, one role=tool with the text result and a
    matching tool_call_id.
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mockfs",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        cli.call_tool("echo", {"message": "hello"})

    msgs = _wait_for_normalization(isolated_data_dir / "pce.db", expected_msgs=2)
    by_role = {m["role"]: m for m in msgs}
    assert set(by_role) == {"assistant", "tool"}, [m["role"] for m in msgs]

    # Assistant carries the tool_calls envelope
    assistant_json = json.loads(by_role["assistant"]["content_json"])
    tc = assistant_json["tool_calls"][0]
    assert tc["function"]["name"] == "mockfs.echo"
    args = json.loads(tc["function"]["arguments"])
    assert args == {"message": "hello"}

    # Tool message carries matching tool_call_id and the upstream's text
    tool_json = json.loads(by_role["tool"]["content_json"])
    assert tool_json["tool_call_id"] == tc["id"]
    assert tool_json["name"] == "mockfs.echo"
    assert tool_json["is_error"] is False
    assert "echoed: hello" in (by_role["tool"]["content_text"] or "")


def test_R05_tool_level_error_marked_as_error(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R05 — A tool that returns ``isError: true`` is recorded as an error.

    Tier 0: status_code=500 on the response row + meta_json.kind='response_error'.
    Wait — actually for tool-level errors the JSON-RPC level still
    returns `result`, not `error`, so the proxy classifies it as a
    success at the wire layer. The "tool error" is detected by the
    normaliser instead. Tier 1: tool message text starts with
    "[tool error]" and `is_error: true` in content_json.
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses({"boom": _TOOLS_CALL_BOOM_TOOL_ERROR}),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        out = cli.call_tool("boom", {})
        assert out.get("isError") is True

    msgs = _wait_for_normalization(isolated_data_dir / "pce.db", expected_msgs=2)
    assert len(msgs) == 2, msgs
    by_role = {m["role"]: m for m in msgs}
    assert "tool" in by_role, [m["role"] for m in msgs]
    tool_msg = by_role["tool"]
    assert (tool_msg["content_text"] or "").startswith("[tool error]")
    tool_json = json.loads(tool_msg["content_json"])
    assert tool_json["is_error"] is True


def test_R06_jsonrpc_level_error_recorded(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R06 — JSON-RPC level error (e.g. -32602) sets status_code=500.

    The MCPStdioClient raises MCPClientError on JSON-RPC errors, so we
    catch and inspect the DB.
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses({"nuke": _TOOLS_CALL_NUKE_RPC_ERROR}),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        with pytest.raises(MCPClientError) as exc_info:
            cli.call_tool("nuke", {})
        assert "-32602" in str(exc_info.value) or "invalid params" in str(exc_info.value)

    time.sleep(0.3)
    rows = _query_captures(isolated_data_dir / "pce.db")
    tc_responses = [
        r for r in rows
        if r["path"] == "tools/call" and r["direction"] == "response"
    ]
    assert tc_responses, "expected at least one tools/call response row"
    err_row = tc_responses[-1]
    assert err_row["status_code"] == 500
    meta = json.loads(err_row["meta_json"])
    assert meta.get("kind") == "response_error"
    assert meta.get("jsonrpc_error_code") == -32602


def test_R07_notification_recorded_without_pair(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R07 — ``notifications/initialized`` lands as a request with no response.

    JSON-RPC notifications have no id, so the observer must classify
    them as kind='notification' with no pending-pair entry and no
    second row.
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()  # this also sends notifications/initialized

    time.sleep(0.3)
    rows = _query_captures(isolated_data_dir / "pce.db")
    notifs = [r for r in rows if r["path"] == "notifications/initialized"]
    assert len(notifs) == 1, [r["direction"] for r in notifs]
    assert notifs[0]["direction"] == "request"
    meta = json.loads(notifs[0]["meta_json"])
    assert meta.get("kind") == "notification"


def test_R08_multiple_tool_calls_share_session(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R08 — Two sequential tool calls on the same upstream live in one session.

    Sessions are keyed by ``mcp-proxy:<upstream>:<YYYY-MM-DD>`` so two
    calls on the same upstream within one local day must collapse into
    one session containing 4 messages (2 × assistant + tool).
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        cli.call_tool("echo", {"message": "first"})
        cli.call_tool("echo", {"message": "second"})

    msgs = _wait_for_normalization(isolated_data_dir / "pce.db", expected_msgs=4)
    assert len(msgs) == 4, msgs
    session_ids = {m["session_id"] for m in msgs}
    assert len(session_ids) == 1, f"expected 1 session, got {session_ids}"


def test_R09_passthrough_preserves_upstream_payload(
    proxy_argv_factory, repo_root, isolated_data_dir
):
    """R09 — Bytes the host receives equal what the upstream emitted.

    Not a deep byte-equality check (whitespace and key order are
    JSON-implementation-defined), but the structural content (server
    name, tool names, tool result text) must round-trip unchanged.
    """
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=_baseline_responses(),
    )
    with MCPStdioClient(
        argv,
        cwd=str(repo_root),
        env={**mock_env, "PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        init = cli.initialize()
        assert init["serverInfo"]["name"] == "mock-upstream"
        assert init["serverInfo"]["version"] == "0.1"
        tools = cli.list_tools()
        assert {t["name"] for t in tools} == {"echo", "boom"}
        out = cli.call_tool("echo", {"message": "round-trip"})
        text = "".join(
            c["text"] for c in out["content"] if c.get("type") == "text"
        )
        assert text == "echoed: hello", text  # mock returns the same text always


def test_R10_upstream_exit_propagates_return_code(
    proxy_argv_factory, repo_root, isolated_data_dir, python_exe
):
    """R10 — When upstream exits non-zero, the proxy exits with the same code.

    Spawn a proxy whose upstream exits 7 immediately on EOF.
    """
    responses = {**_baseline_responses(), "@@exit_code": 7}
    argv, mock_env = proxy_argv_factory(
        upstream_name="mock",
        responses=responses,
    )
    full_env = os.environ.copy()
    full_env.update(mock_env)
    full_env["PCE_DATA_DIR"] = str(isolated_data_dir)
    full_env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        argv,
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=full_env,
        bufsize=0,
    )
    # Send a single initialize and then close stdin.
    init_frame = (
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "rc-test", "version": "1.0"},
            },
        }) + "\n"
    )
    try:
        proc.stdin.write(init_frame.encode("utf-8"))
        proc.stdin.flush()
        # Drain one response so the upstream had work to do
        line = _readline_with_timeout(proc.stdout, timeout=10.0)
        assert line, "no response from proxy"
        # Now close stdin to signal shutdown to upstream
        proc.stdin.close()
        rc = proc.wait(timeout=10.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert rc == 7, f"expected proxy to forward upstream rc=7, got {rc}"


def test_R11_missing_upstream_exits_127(
    python_exe, repo_root, isolated_data_dir
):
    """R11 — Proxy returns 127 when the upstream binary is missing.

    Validates the error-path docstring contract in ``__main__.py``.
    """
    argv = [
        python_exe,
        "-m",
        "pce_mcp_proxy",
        "--upstream-name",
        "ghost",
        "--quiet",
        "--",
        # An executable that almost certainly does not exist.
        "this_definitely_does_not_exist_pce_mcp_proxy_test_xyzzy",
    ]
    proc = subprocess.run(
        argv,
        cwd=str(repo_root),
        env={
            **os.environ,
            "PCE_DATA_DIR": str(isolated_data_dir),
            "PYTHONUNBUFFERED": "1",
        },
        capture_output=True,
        timeout=15,
    )
    assert proc.returncode == 127, (
        f"expected rc=127 when upstream missing, got {proc.returncode}; "
        f"stderr={proc.stderr.decode('utf-8', errors='replace')!r}"
    )
    assert b"cannot spawn" in proc.stderr or b"upstream" in proc.stderr
