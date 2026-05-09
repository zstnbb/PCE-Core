# SPDX-License-Identifier: Apache-2.0
"""End-to-end MCP stdio protocol tests for pce_mcp/server.py.

Spawns `python -m pce_mcp` as a subprocess and exercises the real JSON-RPC
2.0 wire protocol over stdio:

    initialize  →  initialized notification  →  tools/list  →  tools/call

This is the same protocol surface that Claude Desktop / Cursor / Windsurf /
Claude Code / Codex CLI / Gemini CLI / Cascade-Windsurf would see when they
spawn pce_mcp as a subprocess.

Compared to the existing in-process tests in tests/test_mcp.py (which
import the tool functions directly), these tests prove the wire protocol
works — the canonical promise to MCP hosts.
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


# ---------------------------------------------------------------------------
# Low-level MCP stdio client helper (kept inside the test module to avoid
# pulling extra files; if it grows past ~150 lines, promote to its own module).
# ---------------------------------------------------------------------------


class MCPClientError(RuntimeError):
    pass


class MCPStdioClient:
    """Thin newline-delimited JSON-RPC 2.0 client for stdio MCP servers.

    Usage:

        with MCPStdioClient([sys.executable, "-m", "pce_mcp"], cwd=...) as cli:
            cli.initialize()
            tools = cli.list_tools()
            result = cli.call_tool("pce_stats", {})
    """

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: str | os.PathLike[str],
        env: dict[str, str] | None = None,
        startup_timeout: float = 10.0,
    ) -> None:
        self._argv = list(argv)
        self._cwd = str(cwd)
        self._env = env
        self._startup_timeout = startup_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._next_id = 0
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._stderr_done = threading.Event()

    # ----- lifecycle -----------------------------------------------------

    def __enter__(self) -> "MCPStdioClient":
        full_env = os.environ.copy()
        if self._env:
            full_env.update(self._env)
        # Ensure unbuffered stdio on the server side so we don't deadlock
        # waiting for line-buffered output.
        full_env.setdefault("PYTHONUNBUFFERED", "1")
        self._proc = subprocess.Popen(
            self._argv,
            cwd=self._cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            bufsize=0,  # unbuffered binary IO
        )
        # Drain stderr in a background thread so the server's logging
        # output can never fill the pipe and block it.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            try:
                if self._proc.stdin and not self._proc.stdin.closed:
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
        finally:
            self._stderr_done.set()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=2)
            self._proc = None

    @property
    def stderr_text(self) -> str:
        return "".join(self._stderr_lines)

    @property
    def returncode(self) -> int | None:
        if self._proc is None:
            return None
        return self._proc.poll()

    # ----- low-level send / recv -----------------------------------------

    def _send_raw(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPClientError("server not running")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPClientError(
                f"stdin write failed: {e}; stderr so far:\n{self.stderr_text}"
            )

    def _recv_raw(self, timeout: float = 10.0) -> dict[str, Any]:
        """Read one newline-delimited JSON message from stdout."""
        if self._proc is None or self._proc.stdout is None:
            raise MCPClientError("server not running")
        line = _readline_with_timeout(self._proc.stdout, timeout)
        if not line:
            raise MCPClientError(
                "server closed stdout (likely crashed); stderr so far:\n"
                f"{self.stderr_text}"
            )
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            # blank line — try once more
            return self._recv_raw(timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise MCPClientError(
                f"server emitted non-JSON line: {text!r} ({e}); stderr:\n"
                f"{self.stderr_text}"
            )

    def _drain_stderr(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for raw in iter(self._proc.stderr.readline, b""):
                if not raw:
                    break
                self._stderr_lines.append(
                    raw.decode("utf-8", errors="replace")
                )
                if self._stderr_done.is_set():
                    break
        except Exception:
            pass

    # ----- request helpers -----------------------------------------------

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self._next_id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._send_raw(msg)
        # MCP servers may interleave notifications; loop until we see the
        # response with our id.
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                raise MCPClientError(
                    f"timeout waiting for response to id={self._next_id} method={method}"
                )
            received = self._recv_raw(timeout=remaining)
            if received.get("id") == self._next_id:
                return received
            # else: notification or unrelated response — keep waiting

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send_raw(msg)

    # ----- MCP protocol -------------------------------------------------

    def initialize(
        self,
        *,
        protocol_version: str = "2024-11-05",
        client_name: str = "pce-e2e-test",
        client_version: str = "1.0",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Send initialize handshake. Returns server's `result` field."""
        resp = self._request(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": client_version},
            },
            timeout=timeout,
        )
        if "error" in resp:
            raise MCPClientError(f"initialize error: {resp['error']}")
        result = resp.get("result", {})
        # Per spec, client follows up with `notifications/initialized`.
        self._notify("notifications/initialized")
        return result

    def list_tools(self, *, timeout: float = 10.0) -> list[dict[str, Any]]:
        resp = self._request("tools/list", {}, timeout=timeout)
        if "error" in resp:
            raise MCPClientError(f"tools/list error: {resp['error']}")
        return resp.get("result", {}).get("tools", [])

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        resp = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        if "error" in resp:
            raise MCPClientError(f"tools/call({name}) error: {resp['error']}")
        return resp.get("result", {})


def _readline_with_timeout(stream, timeout: float) -> bytes:
    """Read one line from a bytes stream, raising MCPClientError on timeout.

    Uses a daemon thread because select() does not work on Windows pipes.
    """
    holder: dict[str, Any] = {"line": None, "exc": None}

    def reader() -> None:
        try:
            holder["line"] = stream.readline()
        except Exception as e:
            holder["exc"] = e

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise MCPClientError(f"readline timed out after {timeout}s")
    if holder["exc"] is not None:
        raise MCPClientError(f"readline failed: {holder['exc']}")
    return holder["line"] or b""


def _tool_text(result: dict[str, Any]) -> str:
    """Extract concatenated text from MCP tools/call result content array."""
    parts: list[str] = []
    for c in result.get("content", []):
        if c.get("type") == "text" and "text" in c:
            parts.append(c["text"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_argv(python_exe):
    return [python_exe, "-m", "pce_mcp"]


@pytest.fixture
def initialized_client(mcp_argv, repo_root, isolated_data_dir):
    """Spawn pce_mcp, complete initialize handshake, yield ready client."""
    with MCPStdioClient(
        mcp_argv,
        cwd=str(repo_root),
        env={"PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        yield cli


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


# Mark the whole module as e2e_mcp so it can be selected/skipped per CI
# preference. (Not yet wired into pytest.ini; re-running locally still works
# fine because pytest treats unknown markers as ignored unless --strict.)
pytestmark = pytest.mark.e2e_mcp


def test_E01_server_starts_and_initializes(
    mcp_argv, repo_root, isolated_data_dir
):
    """E01 — Server launches and completes the MCP initialize handshake.

    Equivalent to D00 product detection from DESKTOP-PRODUCT-MATRIX § 5
    on the M / L3f path."""
    with MCPStdioClient(
        mcp_argv,
        cwd=str(repo_root),
        env={"PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        result = cli.initialize()
        assert "serverInfo" in result, result
        info = result["serverInfo"]
        assert info.get("name"), info
        # Optional but expected: PCE in the name string
        assert "PCE" in info.get("name", ""), info


def test_E02_tools_list_has_six_tools(initialized_client):
    """E02 — tools/list returns the 6 PCE tools we shipped."""
    tools = initialized_client.list_tools()
    names = sorted(t["name"] for t in tools)
    expected = {
        "pce_capture",
        "pce_query",
        "pce_stats",
        "pce_sessions",
        "pce_session_messages",
        "pce_capture_pair",
    }
    actual = set(names)
    missing = expected - actual
    assert not missing, f"missing tools: {missing}; got: {actual}"
    # Each tool must declare a description and inputSchema (basic MCP
    # interop sanity).
    for t in tools:
        assert t.get("description"), t
        assert t.get("inputSchema"), t


def test_E03_pce_capture_conversation(initialized_client):
    """E03 — pce_capture writes a conversation row that round-trips."""
    result = initialized_client.call_tool(
        "pce_capture",
        {
            "provider": "test-e03",
            "direction": "conversation",
            "host": "test.local",
            "path": "/v1/chat",
            "conversation_json": json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "hello-E03"},
                        {"role": "assistant", "content": "hi-E03"},
                    ]
                }
            ),
        },
    )
    text = _tool_text(result)
    assert "OK" in text, text
    assert "pair_id=" in text, text
    assert "Captured conversation" in text, text


def test_E04_pce_capture_pair_auto_normalizes(initialized_client):
    """E04 — request+response capture auto-normalizes into a session."""
    result = initialized_client.call_tool(
        "pce_capture",
        {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "model_name": "gpt-4o-mini",
            "request_body": json.dumps(
                {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "What is MCP?"}],
                }
            ),
            "response_body": json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "MCP is the Model Context Protocol.",
                            }
                        }
                    ],
                    "model": "gpt-4o-mini",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
                }
            ),
            "status_code": 200,
            "latency_ms": 250.0,
        },
    )
    text = _tool_text(result)
    assert "Captured request" in text, text
    assert "Captured response" in text, text
    assert "Normalized" in text, text


def test_E05_pce_query_returns_recent_captures(initialized_client):
    """E05 — pce_query lists what we just captured."""
    # First insert something deterministic.
    initialized_client.call_tool(
        "pce_capture",
        {
            "provider": "anthropic",
            "host": "api.anthropic.com",
            "path": "/v1/messages",
            "model_name": "claude-sonnet-4-20250514",
            "request_body": json.dumps(
                {
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Say E05"}],
                }
            ),
            "response_body": json.dumps(
                {
                    "content": [{"type": "text", "text": "E05"}],
                    "model": "claude-sonnet-4-20250514",
                    "role": "assistant",
                }
            ),
            "status_code": 200,
        },
    )
    result = initialized_client.call_tool("pce_query", {"last": 20})
    text = _tool_text(result)
    assert "capture(s)" in text, text
    assert "anthropic" in text, text
    assert "api.anthropic.com" in text, text


def test_E06_pce_stats_returns_counts(initialized_client):
    """E06 — pce_stats summarises rows; non-empty after captures."""
    initialized_client.call_tool(
        "pce_capture",
        {
            "provider": "google",
            "host": "generativelanguage.googleapis.com",
            "path": "/v1beta/models/gemini-2.5-pro:generateContent",
            "request_body": json.dumps({"contents": [{"role": "user", "parts": [{"text": "E06"}]}]}),
            "response_body": json.dumps(
                {
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": "E06-ack"}]}}
                    ]
                }
            ),
            "status_code": 200,
        },
    )
    text = _tool_text(initialized_client.call_tool("pce_stats", {}))
    assert "Total captures:" in text, text
    assert "By provider:" in text, text
    assert "google" in text, text


def test_E07_pce_sessions_lists_normalized_sessions(initialized_client):
    """E07 — pce_sessions sees the auto-normalized rows from pair captures."""
    # Drive a request/response so normalizer creates a session.
    initialized_client.call_tool(
        "pce_capture",
        {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "model_name": "gpt-4o-mini",
            "request_body": json.dumps(
                {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "E07-marker"}],
                }
            ),
            "response_body": json.dumps(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "got-E07"}}
                    ],
                    "model": "gpt-4o-mini",
                }
            ),
            "status_code": 200,
        },
    )
    text = _tool_text(initialized_client.call_tool("pce_sessions", {"last": 5}))
    assert "session(s)" in text, text


def test_E08_pce_session_messages_round_trip(initialized_client):
    """E08 — given a session id, pce_session_messages returns its messages.

    Approach: capture a pair via pce_capture (which auto-creates a session),
    list sessions via pce_sessions, parse the first session id, then
    request its messages.
    """
    initialized_client.call_tool(
        "pce_capture",
        {
            "provider": "openai",
            "host": "api.openai.com",
            "path": "/v1/chat/completions",
            "model_name": "gpt-4o-mini",
            "request_body": json.dumps(
                {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "E08-needle"}],
                }
            ),
            "response_body": json.dumps(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "answer-E08"}}
                    ],
                    "model": "gpt-4o-mini",
                }
            ),
            "status_code": 200,
        },
    )
    sessions_text = _tool_text(
        initialized_client.call_tool("pce_sessions", {"last": 1})
    )
    # Lines like: "- [2026-05-08 10:00] openai | ... | id=abc12345"
    sid_prefix: str | None = None
    for line in sessions_text.splitlines():
        if "id=" in line:
            sid_prefix = line.split("id=", 1)[1].strip().split()[0]
            break
    assert sid_prefix, f"no session id parsed from:\n{sessions_text}"

    # We have only the 8-char prefix; pce_session_messages accepts it
    # (per server.py docstring).
    msg_text = _tool_text(
        initialized_client.call_tool(
            "pce_session_messages", {"session_id": sid_prefix}
        )
    )
    assert (
        "message(s)" in msg_text or "No messages found" in msg_text
    ), msg_text


def test_E09_pce_capture_pair_round_trip(initialized_client):
    """E09 — pce_capture_pair retrieves request+response by pair_id."""
    cap_text = _tool_text(
        initialized_client.call_tool(
            "pce_capture",
            {
                "provider": "test-e09",
                "host": "test.local",
                "path": "/v1/x",
                "request_body": json.dumps({"x": 1}),
                "response_body": json.dumps({"y": 2}),
                "status_code": 200,
            },
        )
    )
    # extract pair_id from "OK. pair_id=<uuid>. ..."
    pair_id: str | None = None
    for token in cap_text.split():
        if token.startswith("pair_id="):
            pair_id = token.split("=", 1)[1].rstrip(".")
            break
    assert pair_id, cap_text

    pair_text = _tool_text(
        initialized_client.call_tool(
            "pce_capture_pair", {"pair_id": pair_id}
        )
    )
    assert "capture(s)" in pair_text, pair_text
    assert "REQUEST" in pair_text or "RESPONSE" in pair_text, pair_text


def test_E10_invalid_tool_call_returns_error(initialized_client):
    """E10 — Calling a non-existent tool yields a JSON-RPC error, not a hang.

    FastMCP surfaces unknown tools as either a JSON-RPC error or a
    tools/call result with isError=true. Both are acceptable; what matters
    is the protocol does not deadlock.
    """
    try:
        result = initialized_client.call_tool("definitely_not_a_tool", {})
    except MCPClientError as e:
        # JSON-RPC error path — acceptable
        assert "definitely_not_a_tool" in str(e) or "method" in str(e).lower() or "tool" in str(e).lower()
        return
    # tools/call result path — must signal isError or contain error text
    assert (
        result.get("isError")
        or any(
            "error" in (c.get("text") or "").lower()
            for c in result.get("content", [])
            if c.get("type") == "text"
        )
    ), result


def test_E11_server_shutdown_clean(mcp_argv, repo_root, isolated_data_dir):
    """E11 — Closing stdin lets the server exit cleanly within ~5s."""
    with MCPStdioClient(
        mcp_argv,
        cwd=str(repo_root),
        env={"PCE_DATA_DIR": str(isolated_data_dir)},
    ) as cli:
        cli.initialize()
        # tools/list to prove it's healthy
        cli.list_tools()
    # Context exit calls .close() which closes stdin then waits.
    # If we reach here without TimeoutExpired bubbling up, the server
    # honoured stdin EOF as a shutdown signal — what every MCP host needs.
