# SPDX-License-Identifier: Apache-2.0
"""PCE Probe — agent-facing debug API for the AI-native sites that PCE
captures from.

See ``Docs/docs/engineering/PCE-PROBE-API.md`` for the full design.

Usage::

    from pce_probe import connect

    probe = connect()  # blocks until the extension's WS attaches
    probe.tab.open("https://chatgpt.com/")
    probe.tab.wait_for_load(timeout_ms=15000)
    probe.dom.type("#prompt-textarea", "hello", submit=True)
    matched = probe.capture.wait_for_token("hello", timeout_ms=60000)
"""

from .client import (
    AsyncProbeClient,
    ProbeClient,
    connect,
    connect_async,
)
from .errors import (
    CaptureNotSeenError,
    HostBlockedError,
    NavigationFailedError,
    ParamsInvalidError,
    ProbeError,
    SchemaMismatchError,
    ScriptThrewError,
    SelectorAmbiguousError,
    SelectorNotFoundError,
    TabNotFoundError,
    TimeoutError as ProbeTimeoutError,
    UnknownVerbError,
    error_class_for_code,
)
from .server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ProbeServer,
    serve,
)
from .types import (
    PROBE_SCHEMA_VERSION,
    CaptureEventSummary,
    DomMatch,
    ProbeRequest,
    ProbeResponse,
    TabSummary,
)

__all__ = [
    "PROBE_SCHEMA_VERSION",
    "AsyncProbeClient",
    "CaptureEventSummary",
    "CaptureNotSeenError",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DomMatch",
    "HostBlockedError",
    "NavigationFailedError",
    "ParamsInvalidError",
    "ProbeClient",
    "ProbeError",
    "ProbeRequest",
    "ProbeResponse",
    "ProbeServer",
    "ProbeTimeoutError",
    "SchemaMismatchError",
    "ScriptThrewError",
    "SelectorAmbiguousError",
    "SelectorNotFoundError",
    "TabNotFoundError",
    "TabSummary",
    "UnknownVerbError",
    "connect",
    "connect_async",
    "error_class_for_code",
    "serve",
]

__version__ = "0.1.0"
