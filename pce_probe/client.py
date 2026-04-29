# SPDX-License-Identifier: Apache-2.0
"""PCE Probe — agent-facing Python client.

Two flavors:

  * ``AsyncProbeClient`` — exposes ``async`` methods on namespaced
    handles (``client.tab.open(url)``, etc.) and is the right choice
    when the calling agent is itself ``asyncio``-native.
  * ``ProbeClient`` — synchronous wrapper that spins up a private
    asyncio loop on a daemon thread and proxies every call. Use this
    from pytest, scripts, or any sync agent.

The two classes share the verb surface; every namespaced method is
generated from the same dispatcher so their behaviour is identical.

A typical agent flow::

    from pce_probe import connect

    with connect() as probe:                 # blocks until extension attaches
        probe.tab.open("https://chatgpt.com/")
        probe.dom.wait_for_selector("#prompt-textarea")
        probe.dom.type("#prompt-textarea", "hello", submit=True)
        ev = probe.capture.wait_for_token("hello", timeout_ms=60_000)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from .errors import ProbeError, raise_from_wire
from .server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ProbeRoutingError,
    ProbeServer,
    _BackgroundLoop,
)
from .types import (
    KNOWN_VERBS,
    PROBE_SCHEMA_VERSION,
    HelloPayload,
    ProbeRequest,
    ProbeResponse,
)

LOG = logging.getLogger("pce_probe.client")


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------


class AsyncProbeClient:
    """Async client, multiplexed over an in-process ``ProbeServer``.

    Construction does NOT start the server; call ``await self.start()``
    or use ``async with``. ``start()`` waits for the extension WS to
    attach (configurable timeout); raises ``ProbeRoutingError`` if it
    never does.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        attach_timeout: float = 30.0,
        owns_server: bool = True,
        external_server: Optional[ProbeServer] = None,
    ) -> None:
        self._owns_server = owns_server
        self._server = external_server or ProbeServer(host=host, port=port)
        self._attach_timeout = attach_timeout
        self._started = False
        self._extension_hello: HelloPayload | None = None

        # Verb namespaces (instantiated lazily via attribute access for
        # cheaper construction; bind here for type-checker friendliness).
        self.system = _SystemNamespace(self)
        self.tab = _TabNamespace(self)
        self.dom = _DomNamespace(self)
        self.page = _PageNamespace(self)
        self.capture = _CaptureNamespace(self)

    # ----- lifecycle ------------------------------------------------------

    async def start(self) -> "AsyncProbeClient":
        if self._started:
            return self
        if self._owns_server:
            await self._server.start()
        self._extension_hello = await self._server.wait_for_extension(
            timeout=self._attach_timeout,
        )
        self._started = True
        return self

    async def close(self) -> None:
        if self._owns_server:
            with contextlib.suppress(Exception):
                await self._server.stop()
        self._started = False

    async def __aenter__(self) -> "AsyncProbeClient":
        return await self.start()

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    # ----- properties -----------------------------------------------------

    @property
    def extension_hello(self) -> HelloPayload | None:
        return self._extension_hello

    @property
    def is_attached(self) -> bool:
        return self._server.is_extension_connected

    @property
    def bound_address(self) -> tuple[str, int]:
        return self._server.bound_address

    # ----- low-level call -------------------------------------------------

    async def call(
        self,
        verb: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Send one RPC and return the success result (a dict).

        Raises a ``ProbeError`` subclass on failure (the wire ``error``
        envelope is mapped to the right Python exception).
        """
        if verb not in KNOWN_VERBS:
            raise ProbeError(
                f"unknown verb '{verb}'; not in v{PROBE_SCHEMA_VERSION} catalog",
            )
        rid = str(uuid.uuid4())
        req = ProbeRequest(id=rid, verb=verb, params=dict(params or {}))
        if timeout_ms is not None:
            req.timeout_ms = timeout_ms

        # Hard cap the asyncio waiter slightly above the verb's own
        # timeout so the extension always gets a chance to respond
        # with its own structured timeout error first.
        verb_timeout_s = (timeout_ms or 30_000) / 1000
        wait_timeout_s = verb_timeout_s + 5.0

        raw = await self._server.send_request(req.to_wire(), timeout=wait_timeout_s)
        resp = ProbeResponse.from_wire(raw)
        if resp.ok and resp.result is not None:
            return resp.result
        if not resp.ok and resp.error is not None:
            raise raise_from_wire(
                resp.error,
                elapsed_ms=resp.elapsed_ms,
                request_id=resp.id,
            )
        # Defensive: malformed envelope.
        raise ProbeError(
            f"malformed response envelope (id={resp.id}, ok={resp.ok})",
            context={"raw": raw},
        )


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


class ProbeClient:
    """Synchronous client.

    Internally runs an asyncio loop on a dedicated thread and
    submit-and-blocks every call. Methods mirror ``AsyncProbeClient``
    via the shared namespace classes (each namespace introspects the
    parent and routes appropriately).

    Suitable for pytest fixtures and ad-hoc scripts. Concurrent calls
    from multiple threads serialize through the loop's task queue.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        attach_timeout: float = 30.0,
    ) -> None:
        self._bg = _BackgroundLoop()
        self._async: AsyncProbeClient | None = None
        self._host = host
        self._port = port
        self._attach_timeout = attach_timeout

        # Mirror the namespace handles so users can call probe.tab.open
        # before start() has resolved the underlying async client; the
        # namespace just walks self._async lazily.
        self.system = _SystemNamespace(self)
        self.tab = _TabNamespace(self)
        self.dom = _DomNamespace(self)
        self.page = _PageNamespace(self)
        self.capture = _CaptureNamespace(self)

    # ----- lifecycle ------------------------------------------------------

    def start(self) -> "ProbeClient":
        loop = self._bg.start()
        self._async = AsyncProbeClient(
            host=self._host,
            port=self._port,
            attach_timeout=self._attach_timeout,
        )
        fut = asyncio.run_coroutine_threadsafe(self._async.start(), loop)
        try:
            fut.result(timeout=self._attach_timeout + 10.0)
        except Exception:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._async is not None and self._bg.loop is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._async.close(),
                    self._bg.loop,
                )
                fut.result(timeout=5.0)
            except Exception:  # pragma: no cover - best-effort shutdown
                LOG.exception("probe client close failed")
        self._bg.stop()
        self._async = None

    def __enter__(self) -> "ProbeClient":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ----- properties -----------------------------------------------------

    @property
    def extension_hello(self) -> HelloPayload | None:
        return self._async.extension_hello if self._async else None

    @property
    def is_attached(self) -> bool:
        return bool(self._async and self._async.is_attached)

    @property
    def bound_address(self) -> tuple[str, int]:
        if not self._async:
            raise ProbeRoutingError("client not started")
        return self._async.bound_address

    # ----- low-level call -------------------------------------------------

    def call(
        self,
        verb: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        if self._async is None or self._bg.loop is None:
            raise ProbeRoutingError("client not started; call .start() first")
        coro = self._async.call(verb, params, timeout_ms=timeout_ms)
        fut = asyncio.run_coroutine_threadsafe(coro, self._bg.loop)
        # We give the future ~5s headroom over the verb's own timeout.
        wait = ((timeout_ms or 30_000) / 1000) + 10.0
        return fut.result(timeout=wait)


# ---------------------------------------------------------------------------
# Namespace facades — shared between Async and Sync
# ---------------------------------------------------------------------------


_AnyClient = "AsyncProbeClient | ProbeClient"


@dataclass
class _NamespaceBase:
    parent: Any  # AsyncProbeClient | ProbeClient

    def _call(
        self,
        verb: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        # If parent is async, this returns a coroutine; if sync,
        # returns a result. Methods below are defined to accommodate
        # both by checking the parent's type.
        return self.parent.call(verb, params, timeout_ms=timeout_ms)


class _SystemNamespace(_NamespaceBase):
    def ping(self) -> Any:
        return self._call("system.ping", {})

    def version(self) -> Any:
        return self._call("system.version", {})


class _TabNamespace(_NamespaceBase):
    def list(self) -> Any:
        return self._call("tab.list", {})

    def open(
        self,
        url: str,
        *,
        active: bool = True,
        timeout_ms: int | None = None,
    ) -> Any:
        return self._call(
            "tab.open",
            {"url": url, "active": active},
            timeout_ms=timeout_ms,
        )

    def activate(self, tab_id: int) -> Any:
        return self._call("tab.activate", {"tab_id": tab_id})

    def close(self, tab_id: int) -> Any:
        return self._call("tab.close", {"tab_id": tab_id})

    def navigate(self, tab_id: int, url: str) -> Any:
        return self._call("tab.navigate", {"tab_id": tab_id, "url": url})

    def wait_for_load(
        self,
        tab_id: int,
        *,
        timeout_ms: int = 15_000,
    ) -> Any:
        return self._call(
            "tab.wait_for_load",
            {"tab_id": tab_id, "timeout_ms": timeout_ms},
            timeout_ms=timeout_ms,
        )

    def find_by_url(self, url_pattern: str) -> Any:
        return self._call("tab.find_by_url", {"url_pattern": url_pattern})


class _DomNamespace(_NamespaceBase):
    def query(
        self,
        tab_id: int,
        selector: str,
        *,
        all: bool = False,  # noqa: A002 - matches wire shape
        require_unique: bool = False,
    ) -> Any:
        return self._call(
            "dom.query",
            {
                "tab_id": tab_id,
                "selector": selector,
                "all": all,
                "require_unique": require_unique,
            },
        )

    def wait_for_selector(
        self,
        tab_id: int,
        selector: str,
        *,
        timeout_ms: int = 15_000,
        visible: bool = True,
    ) -> Any:
        return self._call(
            "dom.wait_for_selector",
            {
                "tab_id": tab_id,
                "selector": selector,
                "timeout_ms": timeout_ms,
                "visible": visible,
            },
            timeout_ms=timeout_ms,
        )

    def click(
        self,
        tab_id: int,
        selector: str,
        *,
        scroll_into_view: bool = True,
    ) -> Any:
        return self._call(
            "dom.click",
            {
                "tab_id": tab_id,
                "selector": selector,
                "scroll_into_view": scroll_into_view,
            },
        )

    def type(  # noqa: A003 - matches wire verb
        self,
        tab_id: int,
        selector: str,
        text: str,
        *,
        clear: bool = True,
        submit: bool = False,
    ) -> Any:
        return self._call(
            "dom.type",
            {
                "tab_id": tab_id,
                "selector": selector,
                "text": text,
                "clear": clear,
                "submit": submit,
            },
        )

    def press_key(
        self,
        tab_id: int,
        key: str,
        *,
        selector: Optional[str] = None,
        modifiers: Optional[Sequence[str]] = None,
    ) -> Any:
        params: dict[str, Any] = {"tab_id": tab_id, "key": key}
        if selector is not None:
            params["selector"] = selector
        if modifiers:
            params["modifiers"] = list(modifiers)
        return self._call("dom.press_key", params)

    def scroll_to(
        self,
        tab_id: int,
        *,
        selector: Optional[str] = None,
        y: Optional[int] = None,
    ) -> Any:
        params: dict[str, Any] = {"tab_id": tab_id}
        if selector is not None:
            params["selector"] = selector
        if y is not None:
            params["y"] = y
        return self._call("dom.scroll_to", params)

    def execute_js(
        self,
        tab_id: int,
        code: str,
        *,
        args: Optional[Sequence[Any]] = None,
    ) -> Any:
        return self._call(
            "dom.execute_js",
            {"tab_id": tab_id, "code": code, "args": list(args or [])},
        )


class _PageNamespace(_NamespaceBase):
    def dump_state(
        self,
        tab_id: int,
        *,
        dom_max_chars: int = 8_000,
    ) -> Any:
        return self._call(
            "page.dump_state",
            {"tab_id": tab_id, "dom_max_chars": dom_max_chars},
        )

    def screenshot(
        self,
        tab_id: int,
        *,
        format: str = "png",  # noqa: A002 - matches wire shape
    ) -> Any:
        return self._call(
            "page.screenshot",
            {"tab_id": tab_id, "format": format},
        )

    def network_log(
        self,
        tab_id: int,
        *,
        last_n: int = 50,
    ) -> Any:
        return self._call(
            "page.network_log",
            {"tab_id": tab_id, "last_n": last_n},
        )


class _CaptureNamespace(_NamespaceBase):
    def wait_for_token(
        self,
        token: str,
        *,
        timeout_ms: int = 60_000,
        provider: Optional[str] = None,
    ) -> Any:
        params: dict[str, Any] = {"token": token, "timeout_ms": timeout_ms}
        if provider is not None:
            params["provider"] = provider
        return self._call(
            "capture.wait_for_token",
            params,
            timeout_ms=timeout_ms,
        )

    def recent_events(
        self,
        *,
        last_n: int = 20,
        provider: Optional[str] = None,
    ) -> Any:
        params: dict[str, Any] = {"last_n": last_n}
        if provider is not None:
            params["provider"] = provider
        return self._call("capture.recent_events", params)

    def pipeline_state(self) -> Any:
        return self._call("capture.pipeline_state", {})


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def connect(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    attach_timeout: float = 30.0,
) -> ProbeClient:
    """Synchronous: start a probe server, wait for the extension WS to
    attach, return a ready-to-use sync client.

    Use as a context manager for guaranteed cleanup::

        with connect() as probe:
            probe.tab.open("https://chatgpt.com/")
    """
    client = ProbeClient(host=host, port=port, attach_timeout=attach_timeout)
    return client.start()


async def connect_async(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    attach_timeout: float = 30.0,
) -> AsyncProbeClient:
    """Async equivalent of :func:`connect`."""
    client = AsyncProbeClient(host=host, port=port, attach_timeout=attach_timeout)
    return await client.start()
