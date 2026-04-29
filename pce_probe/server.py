# SPDX-License-Identifier: Apache-2.0
"""PCE Probe — asyncio WebSocket server.

Runs on ``127.0.0.1:9888`` (loopback only). Accepts at most one
extension client and zero-or-more agent clients. Routes agent requests
to the extension by ``request.id``; routes responses back to the right
agent waiter. Multiplexes safely under concurrency (each agent caller
holds its own asyncio.Future, identified by request id).

The server is intentionally small: it does no policy, no retries, no
verb validation beyond schema version. All semantics live in the
extension's verb handlers.

Two entry points:

  * ``serve(host=..., port=...)`` — coroutine for embedding.
  * ``ProbeServer`` — explicit class that ``client.connect()`` spins up
    in a background thread for synchronous code.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed
# We pin to the legacy server explicitly. The new ``websockets.asyncio``
# API in v14+ uses a different ``process_request`` callback signature
# (``(connection, request)`` with ``request.headers``), and a different
# handler signature (``async def handler(connection)``). Mixing those
# with the legacy ``WebSocketServerProtocol`` import produced
# ``'Request' object has no attribute 'get'`` at upgrade time. Sticking
# with the legacy API keeps both ends consistent and lets us deprecate
# wholesale later. The deprecation warning emitted on import is benign
# for v14.x; we'll migrate when we cap the dep at v15+.
from websockets.legacy.server import (  # type: ignore[import-untyped]
    WebSocketServerProtocol,
    serve as _legacy_serve,
)

from .types import (
    PROBE_SCHEMA_VERSION,
    HelloPayload,
    hello_ack,
    parse_hello,
)

LOG = logging.getLogger("pce_probe.server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9888

SERVER_VERSION = "0.1.0"

# Loopback origins we accept on the WS upgrade. ``null`` is what
# Chrome extensions and file:// pages send.
_ALLOWED_ORIGINS: frozenset[str | None] = frozenset({
    None,
    "null",
    "chrome-extension://",  # we suffix-check this prefix
    "moz-extension://",
})


# ---------------------------------------------------------------------------
# Routing state
# ---------------------------------------------------------------------------


@dataclass
class _ExtensionClient:
    """The single extension client currently connected (or None)."""

    ws: WebSocketServerProtocol
    hello: HelloPayload
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)


class ProbeRoutingError(RuntimeError):
    """Raised by the server when no extension is connected or routing
    fails for a non-extension reason. Agents see these as protocol-level
    errors (not wire errors).
    """


class ProbeServer:
    """asyncio WebSocket server that multiplexes between one extension
    client and many agent calls.

    Public API used by ``client.py``:

      * ``await server.start()`` / ``await server.stop()``
      * ``await server.send_request(request_dict, timeout)``
      * ``await server.wait_for_extension(timeout=...)``
      * ``server.is_extension_connected``
      * ``server.bound_address`` — ``(host, port)`` once started

    Thread-safe construction: the server itself is single-event-loop;
    sync clients must marshal via ``asyncio.run_coroutine_threadsafe``.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._ext: _ExtensionClient | None = None
        self._ext_changed = asyncio.Event()
        self._server: websockets.WebSocketServer | None = None
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()
        self._bound_address: tuple[str, int] | None = None

    # ----- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await _legacy_serve(
            self._handle,
            self.host,
            self.port,
            # Loopback-only — refuse anything else even if someone
            # punches a hole. ``websockets`` honors this for the bind.
            origins=None,  # we manually validate below in process_request
            process_request=self._process_request,
            max_size=8 * 1024 * 1024,  # 8 MiB frames (page snapshots etc.)
            ping_interval=20,
            ping_timeout=20,
        )
        # Resolve the actual bound port (so callers can pass port=0).
        sockets = list(self._server.sockets or [])
        if sockets:
            self._bound_address = sockets[0].getsockname()[:2]
        else:
            self._bound_address = (self.host, self.port)
        LOG.info("pce_probe server listening on %s:%d", *self._bound_address)

    async def stop(self) -> None:
        if self._server is None:
            return
        srv, self._server = self._server, None
        srv.close()
        await srv.wait_closed()
        self._stopped.set()
        LOG.info("pce_probe server stopped")

    @property
    def bound_address(self) -> tuple[str, int]:
        if self._bound_address is None:
            raise ProbeRoutingError("server has not been started")
        return self._bound_address

    @property
    def is_extension_connected(self) -> bool:
        return self._ext is not None

    @property
    def extension_hello(self) -> HelloPayload | None:
        return self._ext.hello if self._ext is not None else None

    # ----- agent-facing API ----------------------------------------------

    async def wait_for_extension(self, timeout: float = 10.0) -> HelloPayload:
        """Block until the extension's WS attaches. Raises
        ``ProbeRoutingError`` on timeout.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if self._ext is not None:
                return self._ext.hello
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise ProbeRoutingError(
                    f"extension did not attach within {timeout:.1f}s"
                )
            try:
                await asyncio.wait_for(self._ext_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise ProbeRoutingError(
                    f"extension did not attach within {timeout:.1f}s"
                ) from exc
            self._ext_changed.clear()

    async def send_request(
        self,
        request: dict[str, Any],
        timeout: float = 35.0,
    ) -> dict[str, Any]:
        """Forward an envelope to the extension and await its response.

        Returns the raw response dict (parsed from JSON). Caller is
        responsible for converting ``error`` to a Python exception.
        Raises ``ProbeRoutingError`` if no extension is attached or the
        extension WS dies before responding.
        """
        if self._ext is None:
            raise ProbeRoutingError(
                "no extension client is attached (start Chrome with the PCE "
                "extension installed and probe enabled)"
            )
        rid = str(request.get("id"))
        if not rid:
            raise ProbeRoutingError("request envelope is missing 'id'")

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        ext = self._ext
        ext.pending[rid] = fut

        async with self._lock:
            try:
                await ext.ws.send(json.dumps(request))
            except ConnectionClosed as exc:
                ext.pending.pop(rid, None)
                raise ProbeRoutingError(
                    "extension WS closed mid-send"
                ) from exc

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            ext.pending.pop(rid, None)
            raise ProbeRoutingError(
                f"extension did not reply to '{request.get('verb')}' within "
                f"{timeout:.1f}s (request id={rid})"
            ) from exc

    # ----- WS handler -----------------------------------------------------

    async def _process_request(
        self,
        path: str,
        request_headers: Any,
    ) -> tuple[int, list[tuple[str, str]], bytes] | None:
        """Reject non-loopback origins. Called by ``websockets`` before
        the upgrade. Returning ``None`` accepts the connection.
        """
        # ``websockets`` returns the socket family + peer; loopback is
        # already enforced by binding to 127.0.0.1, but we belt-and-
        # braces here in case the bind config is overridden.
        origin = request_headers.get("Origin")
        if origin is None or origin == "null":
            return None
        if any(origin.startswith(prefix) for prefix in (
            "chrome-extension://", "moz-extension://", "http://127.0.0.1",
            "http://localhost",
        )):
            return None
        LOG.warning("rejecting WS upgrade from origin=%r", origin)
        return (
            403,
            [("Content-Type", "text/plain")],
            b"forbidden: probe accepts loopback / extension origins only\n",
        )

    async def _handle(
        self,
        ws: WebSocketServerProtocol,
        path: str = "/",  # noqa: ARG002 — required by older websockets
    ) -> None:
        # First frame should be a hello (extension) OR a request (agent
        # using a raw client). We sniff the frame to decide.
        try:
            first = await asyncio.wait_for(ws.recv(), timeout=15.0)
        except (asyncio.TimeoutError, ConnectionClosed):
            return
        if not isinstance(first, str):
            await ws.close(code=1003, reason="non-text frames not supported")
            return
        try:
            parsed = json.loads(first)
        except json.JSONDecodeError:
            await ws.close(code=1003, reason="invalid JSON")
            return

        hello = parse_hello(parsed) if isinstance(parsed, dict) else None
        if hello is not None:
            # Treat this WS as the extension client.
            await self._handle_extension(ws, hello)
        else:
            # Some other frame; we don't currently accept agents over
            # WS (agents go through the in-process API in client.py).
            # Close cleanly.
            await ws.close(code=1003, reason="hello frame required first")

    async def _handle_extension(
        self,
        ws: WebSocketServerProtocol,
        hello: HelloPayload,
    ) -> None:
        if hello.schema_version != PROBE_SCHEMA_VERSION:
            LOG.warning(
                "extension schema mismatch: server=%d, ext=%d",
                PROBE_SCHEMA_VERSION,
                hello.schema_version,
            )
            await ws.close(code=1002, reason="schema_mismatch")
            return
        if self._ext is not None:
            LOG.warning("extension already connected; rejecting second")
            await ws.close(code=1013, reason="already_connected")
            return

        client = _ExtensionClient(ws=ws, hello=hello)
        self._ext = client
        self._ext_changed.set()
        LOG.info(
            "extension attached: version=%s, %d capabilities",
            hello.extension_version,
            len(hello.capabilities),
        )
        try:
            await ws.send(json.dumps(hello_ack(SERVER_VERSION)))
        except ConnectionClosed:
            self._cleanup_extension(client)
            return

        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("id")
                if not isinstance(rid, str):
                    # non-routed frame (e.g. unsolicited event); ignore for now
                    continue
                fut = client.pending.pop(rid, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except ConnectionClosed:
            pass
        finally:
            self._cleanup_extension(client)

    def _cleanup_extension(self, client: _ExtensionClient) -> None:
        # Cancel anyone still waiting; they'll get ProbeRoutingError.
        for fut in client.pending.values():
            if not fut.done():
                fut.set_exception(ProbeRoutingError("extension WS closed"))
        client.pending.clear()
        if self._ext is client:
            self._ext = None
            self._ext_changed.set()
            LOG.info("extension detached")


# ---------------------------------------------------------------------------
# Embeddable coroutine
# ---------------------------------------------------------------------------


async def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run a probe server until ``stop_event`` is set (or forever).

    For embedding inside an existing asyncio app::

        stop = asyncio.Event()
        await asyncio.gather(
            pce_probe.serve(stop_event=stop),
            my_other_coroutine(stop),
        )
    """
    srv = ProbeServer(host=host, port=port)
    await srv.start()
    try:
        if stop_event is None:
            await asyncio.Future()  # run forever
        else:
            await stop_event.wait()
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Background-thread runner (used by sync ProbeClient)
# ---------------------------------------------------------------------------


class _BackgroundLoop:
    """Owns an asyncio loop on a dedicated thread.

    The synchronous ``ProbeClient`` uses one of these to run the
    server + the WS protocol handlers without forcing the calling
    pytest test to be async.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> asyncio.AbstractEventLoop:
        if self.loop is not None:
            return self.loop

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            self._ready.set()
            try:
                loop.run_forever()
            finally:
                # Drain pending tasks so we don't warn about pending
                # cancellations on shutdown.
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                with contextlib.suppress(Exception):
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

        self._thread = threading.Thread(
            target=_run,
            name="pce-probe-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self.loop is None:
            raise RuntimeError("background asyncio loop failed to start")
        return self.loop

    def stop(self) -> None:
        if self.loop is None:
            return
        loop = self.loop
        self.loop = None
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None

    def submit(self, coro: Any) -> Any:
        if self.loop is None:
            raise RuntimeError("background loop not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)
