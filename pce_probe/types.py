# SPDX-License-Identifier: Apache-2.0
"""PCE Probe — Python mirror of ``utils/probe-protocol.ts``.

These dataclasses are intentionally narrow and serializable: they are
the wire types only. Higher-level ergonomic wrappers live in
``pce_probe.client``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PROBE_SCHEMA_VERSION = 1

# All verb names known to v1 of the contract. The Python side validates
# verbs against this list before forwarding them to the extension so
# typos surface locally.
KNOWN_VERBS: tuple[str, ...] = (
    "system.ping",
    "system.version",
    "system.reload",
    "tab.list",
    "tab.open",
    "tab.activate",
    "tab.close",
    "tab.navigate",
    "tab.wait_for_load",
    "tab.find_by_url",
    "dom.query",
    "dom.wait_for_selector",
    "dom.click",
    "dom.type",
    "dom.press_key",
    "dom.scroll_to",
    "dom.execute_js",
    "page.dump_state",
    "page.screenshot",
    "page.network_log",
    "capture.wait_for_token",
    "capture.recent_events",
    "capture.pipeline_state",
)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


@dataclass
class ProbeRequest:
    """Outgoing request to the extension."""

    id: str
    verb: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int | None = None
    v: int = PROBE_SCHEMA_VERSION

    def to_wire(self) -> dict[str, Any]:
        """Serializable dict for ``json.dumps``."""
        out: dict[str, Any] = {
            "v": self.v,
            "id": self.id,
            "verb": self.verb,
            "params": self.params,
        }
        if self.timeout_ms is not None:
            out["timeout_ms"] = self.timeout_ms
        return out


@dataclass
class ProbeError:
    """Structured failure envelope. Every field is optional except ``code``
    and ``message`` so the dispatcher always emits a parseable shape.
    """

    code: str
    message: str
    context: dict[str, Any] | None = None
    agent_hint: str | None = None


@dataclass
class ProbeResponse:
    """Incoming response from the extension. Either ``ok=True`` with a
    ``result`` or ``ok=False`` with an ``error``.
    """

    id: str
    ok: bool
    elapsed_ms: int
    v: int = PROBE_SCHEMA_VERSION
    result: dict[str, Any] | None = None
    error: ProbeError | None = None

    @classmethod
    def from_wire(cls, raw: dict[str, Any]) -> ProbeResponse:
        ok = bool(raw.get("ok"))
        err = None
        if not ok and isinstance(raw.get("error"), dict):
            e = raw["error"]
            err = ProbeError(
                code=str(e.get("code", "extension_internal")),
                message=str(e.get("message", "")),
                context=e.get("context") if isinstance(e.get("context"), dict) else None,
                agent_hint=e.get("agent_hint") if isinstance(e.get("agent_hint"), str) else None,
            )
        result = raw.get("result") if ok else None
        if result is not None and not isinstance(result, dict):
            # Some verbs return primitives — wrap so client always gets a dict.
            result = {"value": result}
        return cls(
            v=int(raw.get("v", PROBE_SCHEMA_VERSION)),
            id=str(raw.get("id", "")),
            ok=ok,
            elapsed_ms=int(raw.get("elapsed_ms", 0) or 0),
            result=result,
            error=err,
        )


# ---------------------------------------------------------------------------
# Result-shape helpers (light dataclasses for nicer agent-side access)
# ---------------------------------------------------------------------------


@dataclass
class TabSummary:
    id: int
    url: str
    title: str
    active: bool


@dataclass
class DomMatch:
    outer_html_excerpt: str
    text_excerpt: str
    visible: bool


@dataclass
class CaptureEventSummary:
    ts: float
    kind: str
    provider: str | None = None
    host: str | None = None
    session_hint: str | None = None
    fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Hello frames (used by the server during handshake)
# ---------------------------------------------------------------------------


@dataclass
class HelloPayload:
    extension_version: str
    schema_version: int
    capabilities: list[str]
    user_agent: str | None = None


def parse_hello(raw: dict[str, Any]) -> HelloPayload | None:
    """Return ``None`` if the frame is not a valid hello."""
    if not isinstance(raw, dict):
        return None
    payload = raw.get("hello")
    if not isinstance(payload, dict):
        return None
    return HelloPayload(
        extension_version=str(payload.get("extension_version", "unknown")),
        schema_version=int(payload.get("schema_version", PROBE_SCHEMA_VERSION)),
        capabilities=[str(v) for v in payload.get("capabilities") or []],
        user_agent=str(payload["user_agent"])
        if isinstance(payload.get("user_agent"), str)
        else None,
    )


def hello_ack(server_version: str) -> dict[str, Any]:
    return {
        "v": PROBE_SCHEMA_VERSION,
        "ok": True,
        "server_version": server_version,
        "supported_schema_versions": [PROBE_SCHEMA_VERSION],
    }


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def envelope_to_dict(req: ProbeRequest) -> dict[str, Any]:
    return req.to_wire()


def asdict_safe(obj: Any) -> dict[str, Any]:
    """``dataclasses.asdict`` that strips ``None`` leaves, matching the
    extension's omission semantics on the wire.
    """
    if not hasattr(obj, "__dataclass_fields__"):
        return {}
    raw = asdict(obj)
    return {k: v for k, v in raw.items() if v is not None}
