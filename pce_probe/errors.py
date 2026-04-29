# SPDX-License-Identifier: Apache-2.0
"""PCE Probe — exception types one-to-one with the wire ``error.code``.

Agents catch ``ProbeError`` for the union; specific subclasses for fine
control. Every instance carries the full ``ProbeError`` envelope from
the wire so failure handlers can read ``self.context``,
``self.agent_hint``, and ``self.elapsed_ms`` without doing a separate
lookup.
"""
from __future__ import annotations

from typing import Any

from .types import ProbeError as WireError


class ProbeError(Exception):
    """Base class for all probe-side failures.

    Use ``isinstance(e, ProbeError)`` to catch any wire-level failure.
    Use specific subclasses (``SelectorNotFoundError``,
    ``CaptureNotSeenError``, etc.) when the recovery strategy depends
    on the cause.
    """

    code: str = "extension_internal"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        agent_hint: str | None = None,
        elapsed_ms: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.context = context
        self.agent_hint = agent_hint
        self.elapsed_ms = elapsed_ms
        self.request_id = request_id

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        bits = [f"[{self.code}] {self.message}"]
        if self.agent_hint:
            bits.append(f"hint: {self.agent_hint}")
        return " | ".join(bits)


# ---------------------------------------------------------------------------
# One subclass per wire error code
# ---------------------------------------------------------------------------


class SchemaMismatchError(ProbeError):
    code = "schema_mismatch"


class UnknownVerbError(ProbeError):
    code = "unknown_verb"


class ParamsInvalidError(ProbeError):
    code = "params_invalid"


class TabNotFoundError(ProbeError):
    code = "tab_not_found"


class SelectorNotFoundError(ProbeError):
    code = "selector_not_found"


class SelectorAmbiguousError(ProbeError):
    code = "selector_ambiguous"


class NavigationFailedError(ProbeError):
    code = "navigation_failed"


class ScriptThrewError(ProbeError):
    code = "script_threw"


class TimeoutError(ProbeError):  # noqa: A001 - intentional shadow
    code = "timeout"


class CaptureNotSeenError(ProbeError):
    code = "capture_not_seen"


class HostBlockedError(ProbeError):
    code = "host_blocked"


class ExtensionInternalError(ProbeError):
    code = "extension_internal"


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


_CODE_TO_CLASS: dict[str, type[ProbeError]] = {
    cls.code: cls
    for cls in (
        SchemaMismatchError,
        UnknownVerbError,
        ParamsInvalidError,
        TabNotFoundError,
        SelectorNotFoundError,
        SelectorAmbiguousError,
        NavigationFailedError,
        ScriptThrewError,
        TimeoutError,
        CaptureNotSeenError,
        HostBlockedError,
        ExtensionInternalError,
    )
}


def error_class_for_code(code: str) -> type[ProbeError]:
    """Return the most specific ProbeError subclass for the given wire
    code. Falls back to ``ExtensionInternalError`` for unknown codes so
    agents can still ``except ProbeError``.
    """
    return _CODE_TO_CLASS.get(code, ExtensionInternalError)


def raise_from_wire(
    err: WireError,
    *,
    elapsed_ms: int | None = None,
    request_id: str | None = None,
) -> "ProbeError":
    """Construct (don't raise) the right exception subclass from a
    wire-format ``ProbeError``. Caller is expected to ``raise`` the
    return value so tracebacks point at the call site.
    """
    cls = error_class_for_code(err.code)
    return cls(
        err.message,
        context=err.context,
        agent_hint=err.agent_hint,
        elapsed_ms=elapsed_ms,
        request_id=request_id,
    )
