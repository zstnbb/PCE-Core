# SPDX-License-Identifier: Apache-2.0
"""Unit + integration tests for the ``pce_probe`` package.

Coverage:

* ``test_types.py`` equivalents — request/response/hello envelopes.
* ``test_errors.py`` equivalents — code -> class table, ``raise_from_wire``.
* In-process integration: spin up a ``ProbeServer`` on an ephemeral
  port, connect a *fake* extension via the ``websockets`` client, then
  drive the real ``ProbeClient`` against it. Every verb the dispatcher
  supports has a stub answer so we exercise the full async dance
  without needing a real Chrome.

These tests do NOT require Chrome / the browser extension to be
running. The full E2E pass with the real extension lives under
``tests/e2e_probe/``.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Awaitable, Callable, Iterator

import pytest
import websockets

from pce_probe import (
    PROBE_SCHEMA_VERSION,
    AsyncProbeClient,
    CaptureNotSeenError,
    ProbeClient,
    ProbeError,
    ProbeServer,
    SchemaMismatchError,
    SelectorNotFoundError,
    TabNotFoundError,
    UnknownVerbError,
)
from pce_probe.errors import (
    ExtensionInternalError,
    ParamsInvalidError,
    error_class_for_code,
    raise_from_wire,
)
from pce_probe.types import (
    HelloPayload,
    ProbeRequest,
    ProbeResponse,
    hello_ack,
    parse_hello,
)
from pce_probe.types import ProbeError as WireError


# ---------------------------------------------------------------------------
# 1. Type roundtrips
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_request_to_wire_includes_required_fields(self) -> None:
        req = ProbeRequest(id="r1", verb="tab.list", params={})
        wire = req.to_wire()
        assert wire == {
            "v": PROBE_SCHEMA_VERSION,
            "id": "r1",
            "verb": "tab.list",
            "params": {},
        }

    def test_request_to_wire_includes_timeout_when_set(self) -> None:
        req = ProbeRequest(id="r2", verb="dom.click", params={"x": 1}, timeout_ms=2_000)
        wire = req.to_wire()
        assert wire["timeout_ms"] == 2_000

    def test_response_from_wire_success(self) -> None:
        raw = {"v": 1, "id": "r1", "ok": True, "result": {"a": 1}, "elapsed_ms": 5}
        resp = ProbeResponse.from_wire(raw)
        assert resp.ok is True
        assert resp.result == {"a": 1}
        assert resp.error is None
        assert resp.elapsed_ms == 5

    def test_response_from_wire_failure(self) -> None:
        raw = {
            "v": 1,
            "id": "r2",
            "ok": False,
            "error": {
                "code": "selector_not_found",
                "message": "no match",
                "context": {"tab_id": 7},
                "agent_hint": "check selector",
            },
            "elapsed_ms": 12,
        }
        resp = ProbeResponse.from_wire(raw)
        assert resp.ok is False
        assert resp.error is not None
        assert resp.error.code == "selector_not_found"
        assert resp.error.context == {"tab_id": 7}
        assert resp.error.agent_hint == "check selector"

    def test_response_from_wire_wraps_primitive_results(self) -> None:
        raw = {"v": 1, "id": "r3", "ok": True, "result": 42, "elapsed_ms": 1}
        resp = ProbeResponse.from_wire(raw)
        assert resp.result == {"value": 42}

    def test_parse_hello_accepts_real_frame(self) -> None:
        raw = {
            "v": 1,
            "hello": {
                "extension_version": "1.0.1",
                "schema_version": 1,
                "capabilities": ["system.ping"],
                "user_agent": "Mozilla/5.0",
            },
        }
        hello = parse_hello(raw)
        assert isinstance(hello, HelloPayload)
        assert hello.extension_version == "1.0.1"
        assert hello.capabilities == ["system.ping"]
        assert hello.user_agent == "Mozilla/5.0"

    def test_parse_hello_returns_none_for_non_hello(self) -> None:
        assert parse_hello({"v": 1, "id": "r1", "verb": "tab.list"}) is None
        assert parse_hello({}) is None

    def test_hello_ack_shape(self) -> None:
        ack = hello_ack("0.9.9")
        assert ack["ok"] is True
        assert ack["server_version"] == "0.9.9"
        assert PROBE_SCHEMA_VERSION in ack["supported_schema_versions"]


# ---------------------------------------------------------------------------
# 2. Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.mark.parametrize(
        "code,cls",
        [
            ("schema_mismatch", SchemaMismatchError),
            ("unknown_verb", UnknownVerbError),
            ("params_invalid", ParamsInvalidError),
            ("tab_not_found", TabNotFoundError),
            ("selector_not_found", SelectorNotFoundError),
            ("capture_not_seen", CaptureNotSeenError),
            ("extension_internal", ExtensionInternalError),
        ],
    )
    def test_error_class_for_code(self, code: str, cls: type) -> None:
        assert error_class_for_code(code) is cls

    def test_unknown_code_falls_through_to_internal(self) -> None:
        assert error_class_for_code("not_a_real_code") is ExtensionInternalError

    def test_raise_from_wire_preserves_context(self) -> None:
        wire = WireError(
            code="selector_not_found",
            message="boom",
            context={"tab_id": 9, "url": "https://x"},
            agent_hint="rerun",
        )
        exc = raise_from_wire(wire, elapsed_ms=42, request_id="r99")
        assert isinstance(exc, SelectorNotFoundError)
        assert isinstance(exc, ProbeError)
        assert exc.context == {"tab_id": 9, "url": "https://x"}
        assert exc.agent_hint == "rerun"
        assert exc.elapsed_ms == 42
        assert exc.request_id == "r99"


# ---------------------------------------------------------------------------
# 3. End-to-end (in-process) — fake extension echoes back
# ---------------------------------------------------------------------------


# Stub answers for each verb the client may call. Keep this small and
# deterministic so we exercise the full async loop without external state.
def _stub_response(req: dict[str, Any]) -> dict[str, Any]:
    rid = req["id"]
    verb = req["verb"]
    params = req.get("params", {})

    base = {"v": PROBE_SCHEMA_VERSION, "id": rid, "elapsed_ms": 1}

    if verb == "system.ping":
        return {**base, "ok": True, "result": {"pong": True, "extension_version": "test"}}
    if verb == "system.version":
        return {
            **base,
            "ok": True,
            "result": {
                "schema_version": PROBE_SCHEMA_VERSION,
                "extension_version": "test",
                "capabilities": ["system.ping", "tab.list"],
            },
        }
    if verb == "tab.list":
        return {**base, "ok": True, "result": {"tabs": []}}
    if verb == "tab.open":
        return {**base, "ok": True, "result": {"tab_id": 42, "url": params.get("url")}}
    if verb == "tab.close":
        # echo back a structured failure when tab_id is 999 (test-only)
        if params.get("tab_id") == 999:
            return {
                **base,
                "ok": False,
                "error": {
                    "code": "tab_not_found",
                    "message": "tab 999 not found",
                    "context": {"tab_id": 999},
                },
            }
        return {**base, "ok": True, "result": {"ok": True}}
    if verb == "dom.wait_for_selector":
        # Simulate a found-selector path.
        return {**base, "ok": True, "result": {"matched": 1}}
    if verb == "capture.wait_for_token":
        # Simulate a not-seen failure with rich context.
        return {
            **base,
            "ok": False,
            "error": {
                "code": "capture_not_seen",
                "message": "no match in window",
                "context": {"last_capture_events": [{"ts": 1, "kind": "PCE_CAPTURE"}]},
                "agent_hint": "check that the prompt actually contained the token",
            },
        }
    # Catch-all so unintended verbs surface clearly in test output.
    return {
        **base,
        "ok": False,
        "error": {"code": "unknown_verb", "message": f"stub has no answer for {verb}"},
    }


class FakeExtension:
    """Connects to the probe server's WS, sends a hello, and replies to
    every routed request via ``_stub_response``. Runs on its own
    asyncio task so tests can ``await`` while it serves.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self.task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()
        self.received: list[dict[str, Any]] = []

    async def run(self) -> None:
        url = f"ws://127.0.0.1:{self.port}"
        async with websockets.connect(url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "v": PROBE_SCHEMA_VERSION,
                        "hello": {
                            "extension_version": "test",
                            "schema_version": PROBE_SCHEMA_VERSION,
                            "capabilities": ["system.ping", "tab.list"],
                        },
                    }
                )
            )
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    break
                if not isinstance(raw, str):
                    continue
                msg = json.loads(raw)
                # Skip the hello-ack frame (no 'verb').
                if "verb" not in msg:
                    continue
                self.received.append(msg)
                resp = _stub_response(msg)
                await ws.send(json.dumps(resp))

    async def start(self) -> None:
        self.task = asyncio.create_task(self.run())
        # Give the connect a tick to land.
        await asyncio.sleep(0.05)

    async def stop(self) -> None:
        self._stop.set()
        if self.task is not None:
            await asyncio.wait_for(self.task, timeout=3.0)


# ---------------------------------------------------------------------------
# 3a. Async client smoke
# ---------------------------------------------------------------------------


async def _with_fake(
    body: Callable[[AsyncProbeClient, ProbeServer], Awaitable[None]],
) -> None:
    """Helper: spin up server + fake extension + client, run body, tear
    down. Keeps each test focused on one assertion.
    """
    srv = ProbeServer(host="127.0.0.1", port=0)
    await srv.start()
    host, port = srv.bound_address
    ext = FakeExtension(port=port)
    try:
        await ext.start()
        client = AsyncProbeClient(
            host=host,
            port=port,
            attach_timeout=5.0,
            external_server=srv,
            owns_server=False,
        )
        await client.start()
        try:
            await body(client, srv)
        finally:
            await client.close()
    finally:
        await ext.stop()
        await srv.stop()


# We don't depend on pytest-asyncio: each test wraps its coroutine
# body in ``asyncio.run`` and asserts on the eventual result.
class TestAsyncProbeClient:
    def test_ping_roundtrip(self) -> None:
        async def body(client: AsyncProbeClient, _srv: ProbeServer) -> None:
            result = await client.call("system.ping", {})
            assert result["pong"] is True

        asyncio.run(_with_fake(body))

    def test_tab_open_roundtrip(self) -> None:
        async def body(client: AsyncProbeClient, _srv: ProbeServer) -> None:
            result = await client.call("tab.open", {"url": "https://example.com"})
            assert result["tab_id"] == 42
            assert result["url"] == "https://example.com"

        asyncio.run(_with_fake(body))

    def test_tab_close_failure_propagates_typed_exception(self) -> None:
        async def body(client: AsyncProbeClient, _srv: ProbeServer) -> None:
            with pytest.raises(TabNotFoundError) as excinfo:
                await client.call("tab.close", {"tab_id": 999})
            assert excinfo.value.context == {"tab_id": 999}

        asyncio.run(_with_fake(body))

    def test_capture_wait_for_token_failure_carries_context(self) -> None:
        async def body(client: AsyncProbeClient, _srv: ProbeServer) -> None:
            with pytest.raises(CaptureNotSeenError) as excinfo:
                await client.call(
                    "capture.wait_for_token",
                    {"token": "ABC", "timeout_ms": 100},
                )
            assert excinfo.value.agent_hint
            assert excinfo.value.context is not None
            assert "last_capture_events" in excinfo.value.context

        asyncio.run(_with_fake(body))

    def test_unknown_verb_fails_locally(self) -> None:
        async def body(client: AsyncProbeClient, _srv: ProbeServer) -> None:
            with pytest.raises(ProbeError):
                await client.call("not.a.verb", {})

        asyncio.run(_with_fake(body))


# ---------------------------------------------------------------------------
# 3b. Sync client smoke (uses dedicated background loop)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_extension_thread() -> Iterator[Callable[[int], FakeExtension]]:
    """Spawn a fake extension on a background thread bound to a port.

    Returned callable: ``factory(port) -> FakeExtension``. The extension's
    asyncio loop is owned by this fixture and torn down on test exit.
    """
    threads: list[tuple[threading.Thread, asyncio.AbstractEventLoop, FakeExtension]] = []

    def make(port: int) -> FakeExtension:
        ready = threading.Event()
        loop = asyncio.new_event_loop()
        ext = FakeExtension(port=port)

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(ext.start())
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=_run, name=f"fake-ext-{port}", daemon=True)
        t.start()
        ready.wait(timeout=5.0)
        threads.append((t, loop, ext))
        return ext

    try:
        yield make
    finally:
        for t, loop, ext in threads:
            try:
                fut = asyncio.run_coroutine_threadsafe(ext.stop(), loop)
                fut.result(timeout=3.0)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=3.0)


class TestSyncProbeClient:
    def test_sync_ping(
        self,
        fake_extension_thread: Callable[[int], FakeExtension],
    ) -> None:
        # Pick a free port by starting the server first with port=0.
        srv = ProbeServer(host="127.0.0.1", port=0)
        # Run the server on its own loop+thread to mimic real deployment.
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(srv.start())
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=_run, name="srv-thread", daemon=True)
        t.start()
        ready.wait(timeout=5.0)
        host, port = srv.bound_address

        try:
            ext = fake_extension_thread(port)
            time.sleep(0.1)  # allow ext to connect + handshake

            client = ProbeClient(host=host, port=port, attach_timeout=5.0)
            # Override the owned server with the externally-managed one.
            client._async = AsyncProbeClient(
                host=host, port=port, attach_timeout=5.0,
                external_server=srv, owns_server=False,
            )
            client._bg.start()
            fut = asyncio.run_coroutine_threadsafe(client._async.start(), client._bg.loop)  # type: ignore[arg-type]
            fut.result(timeout=5.0)

            try:
                result = client.call("system.ping", {})
                assert result["pong"] is True
            finally:
                client.close()
        finally:
            try:
                fut2 = asyncio.run_coroutine_threadsafe(srv.stop(), loop)
                fut2.result(timeout=3.0)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=3.0)
