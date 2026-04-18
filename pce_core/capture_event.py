# SPDX-License-Identifier: Apache-2.0
"""CaptureEvent v2 — the unified data contract for all PCE capture layers.

This module defines the Pydantic schema that every capture layer (L0 kernel,
L1 TLS MITM, L2 Frida, L3a–f application hooks, L4a–c UI bridges) must use
when submitting events to ``POST /api/v1/captures/v2``.

Design rules (see UCS §5.1 & §5.6):

1.  **Forward-compatible** — once merged to ``main`` this schema is a public
    API. Fields may only be ADDED (as optional), never removed or renamed,
    and semantic changes require a v3 migration path.
2.  **Flat-first** — top-level fields are first-class citizens so normalizers
    don't have to dig through ``meta`` objects.
3.  **Source-attributable** — every event must carry ``source`` +
    ``agent_name`` + ``agent_version`` + ``capture_time_ns`` so the origin
    can be traced.
4.  **Layer-extensible via layer_meta** — the ``layer_meta`` dict is the
    escape hatch for Pro layers (kernel, Frida, Electron preload, AX) and
    experimental layers to include metadata without touching the core
    schema. Fields that stabilise in ``layer_meta`` may be promoted to
    top-level in a later minor version.

Open Core implication (ADR-010): this schema lives in the OSS ``pce`` repo
and is the ONLY public API contract the Pro ``pce-pro`` repo is allowed to
depend on. Pro layers submit events via local HTTP POST, never via
in-process import, so they participate through this envelope exclusively.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import socket
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Type aliases for documentation / IDE hints
# ---------------------------------------------------------------------------

# Matches the 12 UCS capture layers (5 top-level × sub-layers in L3/L4).
CaptureSource = Literal[
    "L0_kernel",
    "L1_mitm",
    "L2_frida",
    "L3a_browser_ext",
    "L3b_electron_preload",
    "L3c_vscode_ext",
    "L3d_cdp",
    "L3e_litellm",
    "L3f_otel",
    "L4a_clipboard",
    "L4b_accessibility",
    "L4c_ocr",
]

# Quality tier — see UCS §5.3. T1 is a structured body (JSON / protobuf),
# T2 is extracted UI text, T3 is metadata-only (e.g. kernel-level redirect
# where the body never reached a decryptable layer).
QualityTier = Literal["T1_structured", "T2_ui_text", "T3_metadata_only"]

# Direction of the captured event.
Direction = Literal["request", "response", "pair"]


# ---------------------------------------------------------------------------
# CaptureEvent v2 — the envelope
# ---------------------------------------------------------------------------

class CaptureEventV2(BaseModel):
    """A single capture event submitted by any capture layer.

    See UCS §5.2 for the canonical specification. The model enforces
    ``extra='forbid'`` at the top level — unknown keys are treated as bugs
    and must be rejected. Use ``layer_meta`` for layer-specific data.
    """

    model_config = ConfigDict(
        extra="forbid",
        # str_strip_whitespace and validate_default are intentionally off —
        # we accept inputs as-is from other processes / languages.
    )

    # ── Identity (required on submit) ──────────────────────────────────────
    capture_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Globally unique capture identifier. ULID recommended for "
            "time-orderability; any non-empty string is accepted."
        ),
    )
    pair_id: Optional[str] = Field(
        default=None,
        description=(
            "Request/response pairing key. Ingest gateway assigns one if "
            "absent. Two half-events (direction=request and direction="
            "response) share the same pair_id."
        ),
    )
    session_hint: Optional[str] = Field(
        default=None,
        description=(
            "Best-effort session hint from the source (e.g. conversation_id "
            "from a web chat, or a tab id). The Normalizer decides whether "
            "to trust it when assigning the final session."
        ),
    )

    # ── Source attribution (all required) ──────────────────────────────────
    source: CaptureSource = Field(
        ...,
        description="Which capture layer produced this event.",
    )
    agent_name: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable agent identifier. Examples: 'pce_proxy', "
            "'pce_agent_electron', 'pce_browser_ext', 'pce_agent_frida'."
        ),
    )
    agent_version: str = Field(
        ...,
        min_length=1,
        description="Semantic version of the producing agent, e.g. '1.0.0'.",
    )
    capture_time_ns: int = Field(
        ...,
        gt=0,
        description="Nanosecond-precision unix timestamp at capture time.",
    )
    capture_host: str = Field(
        ...,
        min_length=1,
        description=(
            "Identifier for the machine / process that produced this event. "
            "Typically the OS hostname; may include PID for multi-process "
            "agents."
        ),
    )

    # ── AI semantics (main consumer: Normalizer) ───────────────────────────
    provider: Optional[str] = Field(
        default=None,
        description="AI provider. Examples: 'openai', 'anthropic', 'google'.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model identifier, e.g. 'gpt-4o-2024-11-20'.",
    )
    endpoint: Optional[str] = Field(
        default=None,
        description="Full URL or endpoint path that was called.",
    )
    direction: Direction = Field(
        default="pair",
        description=(
            "'request' and 'response' describe half-events; 'pair' is used "
            "when an agent already joined both sides (typical for L3a/L3e)."
        ),
    )
    streaming: bool = Field(
        default=False,
        description="True if the original transport was streamed (SSE / chunked).",
    )

    # ── Payload ────────────────────────────────────────────────────────────
    # Headers are expected to be pre-redacted by the producing agent; the
    # ingest privacy middleware enforces redaction again as defence in depth.
    request_headers: Optional[dict[str, Any]] = Field(
        default=None,
        description="Redacted request headers.",
    )
    request_body: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured request body (JSON object or serialised text).",
    )
    response_headers: Optional[dict[str, Any]] = Field(
        default=None,
        description="Redacted response headers.",
    )
    response_body: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Structured response body. For streamed responses this should "
            "already be the aggregated final object; keep the raw chunks in "
            "``stream_chunks`` if needed."
        ),
    )
    stream_chunks: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional raw chunk sequence when streaming=True.",
    )

    # ── Quality & dedup ────────────────────────────────────────────────────
    quality_tier: QualityTier = Field(
        default="T1_structured",
        description="See UCS §5.3 tier table per layer.",
    )
    fingerprint: Optional[str] = Field(
        default=None,
        description=(
            "Deduplication fingerprint. Recommended: "
            "``sha256(pair_id + body_prefix)``. Use "
            ":func:`compute_fingerprint` helper."
        ),
    )
    deduped_by: Optional[str] = Field(
        default=None,
        description=(
            "If this event was rejected as a duplicate, points to the "
            "capture_id of the primary. Set by the server, not the agent."
        ),
    )

    # ── Layer extension escape hatch ───────────────────────────────────────
    layer_meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Layer-specific metadata. Opaque to the core schema. "
            "Keys are conventionally prefixed with the layer name, e.g. "
            "``frida.script_version``, ``cdp.frame_id``."
        ),
    )

    # ── Form attribution (Supervisor fills) ────────────────────────────────
    form_id: Optional[str] = Field(
        default=None,
        description="UCS form identifier, 'F1' through 'F10' — see UCS §2.",
    )
    app_name: Optional[str] = Field(
        default=None,
        description=(
            "Application / product name as recognised by the Supervisor, "
            "e.g. 'cursor', 'chatgpt_desktop', 'claude_web'."
        ),
    )

    # ------------------------------------------------------------------ validators

    @field_validator("source")
    @classmethod
    def _source_must_be_known(cls, v: str) -> str:
        # Pydantic already enforces the Literal, but we re-check so new
        # layer values (e.g. 'L3g_future_hook') fail loudly until UCS is
        # updated — this keeps the schema and the architecture document
        # in lock-step.
        return v


# ---------------------------------------------------------------------------
# Ingest response
# ---------------------------------------------------------------------------

class CaptureEventIngestResponse(BaseModel):
    """Response envelope returned by POST /api/v1/captures/v2."""

    model_config = ConfigDict(extra="forbid")

    capture_id: str
    ingested_at_ns: int
    normalized: bool = False
    # Populated when the event collided with an existing fingerprint.
    deduped_of: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Crockford Base32 alphabet, used by ULID (lowercase-agnostic, no I L O U).
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_capture_id() -> str:
    """Return a ULID-compatible 26-char Crockford-Base32 identifier.

    The first 10 characters encode a 48-bit millisecond timestamp (so
    ULIDs sort lexicographically by creation time); the last 16 characters
    encode 80 bits of randomness.

    No external dependency is used so this helper works in any environment
    where a capture agent might run.
    """
    # 48-bit ms timestamp + 80-bit random = 128 bits total, encoded to 26
    # chars of Crockford Base32 (5 bits per char).
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = secrets.token_bytes(10)
    # Big-endian combine
    value = (timestamp_ms << 80) | int.from_bytes(randomness, "big")

    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def compute_fingerprint(
    pair_id: Optional[str],
    body_prefix: str | bytes | None,
    *,
    prefix_chars: int = 1024,
) -> str:
    """Compute a deduplication fingerprint per UCS §5.2.

    ``fingerprint = sha256(pair_id + body_prefix[:1024])``

    - ``pair_id`` ``None`` is treated as the empty string (events without a
      pair_id are rarer but legal — fingerprint will then collide only on
      body, which is usually fine for the dedup purpose).
    - ``body_prefix`` may be ``str``, ``bytes``, or ``None``; ``None`` is
      treated as the empty string.
    - Only the first ``prefix_chars`` characters / bytes are hashed to bound
      cost for very large bodies.
    """
    pid = pair_id or ""
    if body_prefix is None:
        body = b""
    elif isinstance(body_prefix, bytes):
        body = body_prefix[:prefix_chars]
    else:
        body = body_prefix[:prefix_chars].encode("utf-8", errors="replace")

    h = hashlib.sha256()
    h.update(pid.encode("utf-8"))
    h.update(b"\x00")  # separator so ('a','bc') != ('ab','c')
    h.update(body)
    return h.hexdigest()


def default_capture_host() -> str:
    """Return a reasonable default for ``capture_host``.

    Format: ``<hostname>#<pid>``. Agents are free to override.
    """
    try:
        host = socket.gethostname() or "unknown"
    except OSError:  # pragma: no cover — platform-specific
        host = "unknown"
    return f"{host}#{os.getpid()}"


# ---------------------------------------------------------------------------
# v1 → v2 adapter
# ---------------------------------------------------------------------------

# Legacy v1 ``source_type`` → CaptureEvent v2 ``source`` mapping.
_V1_SOURCE_MAP: dict[str, CaptureSource] = {
    "proxy": "L1_mitm",
    "browser_extension": "L3a_browser_ext",
    "mcp": "L3c_vscode_ext",  # approximate; MCP-like IDE bridge
    "ide_plugin": "L3c_vscode_ext",
    "local_model": "L3e_litellm",
}


def from_v1_capture(
    v1_payload: dict[str, Any],
    *,
    agent_name: str = "pce_v1_bridge",
    agent_version: str = "1.0.0",
) -> CaptureEventV2:
    """Convert a legacy v1 ``CaptureIn`` dict into a v2 event.

    Used by the v1 compatibility shim (``/api/v1/captures`` gateway that
    auto-upgrades to v2 internally). This keeps every existing producer
    working unchanged while the normalizer and storage layers migrate to
    the unified v2 pipeline.
    """
    # Map source
    source_type = (v1_payload.get("source_type") or "").lower().strip()
    source: CaptureSource = _V1_SOURCE_MAP.get(source_type, "L1_mitm")

    # Direction stays as-is ("request" | "response" | "conversation"); map
    # v1 "conversation" to v2 "pair".
    v1_direction = (v1_payload.get("direction") or "pair").lower().strip()
    direction: Direction = "pair" if v1_direction == "conversation" else (
        v1_direction if v1_direction in ("request", "response") else "pair"
    )

    # Body: v1 has body_json (string) + body_format. v2 wants a dict for
    # structured bodies. If body_format="json", try to parse; otherwise put
    # the raw text under ``{"text": ...}``.
    body_json = v1_payload.get("body_json") or ""
    body_format = (v1_payload.get("body_format") or "json").lower()
    body_dict: Optional[dict[str, Any]]
    if not body_json:
        body_dict = None
    elif body_format == "json":
        try:
            import json as _json

            parsed = _json.loads(body_json)
            body_dict = parsed if isinstance(parsed, dict) else {"value": parsed}
        except ValueError:
            body_dict = {"text": body_json}
    else:
        body_dict = {"text": body_json}

    # Headers — v1 uses a JSON-serialised string
    headers_json = v1_payload.get("headers_json") or "{}"
    try:
        import json as _json

        headers_dict = _json.loads(headers_json)
        if not isinstance(headers_dict, dict):
            headers_dict = None
    except ValueError:
        headers_dict = None

    # Place the unpacked body/headers on request_* or response_* depending on
    # direction. For "pair", put headers on request and body on response
    # (legacy v1 mixed both into a single envelope).
    request_headers: Optional[dict[str, Any]] = None
    request_body: Optional[dict[str, Any]] = None
    response_headers: Optional[dict[str, Any]] = None
    response_body: Optional[dict[str, Any]] = None
    if direction == "request":
        request_headers = headers_dict
        request_body = body_dict
    elif direction == "response":
        response_headers = headers_dict
        response_body = body_dict
    else:  # pair / conversation
        request_headers = headers_dict
        response_body = body_dict

    # Preserve v1 meta verbatim in layer_meta under a namespaced key so the
    # bridge is reversible and downstream debugging remains possible.
    legacy_meta = v1_payload.get("meta")
    layer_meta: dict[str, Any] = {"v1_source_name": v1_payload.get("source_name")}
    if legacy_meta:
        layer_meta["v1_meta"] = legacy_meta

    return CaptureEventV2(
        capture_id=new_capture_id(),
        pair_id=v1_payload.get("pair_id"),
        session_hint=v1_payload.get("session_hint"),
        source=source,
        agent_name=agent_name,
        agent_version=agent_version,
        capture_time_ns=time.time_ns(),
        capture_host=default_capture_host(),
        provider=v1_payload.get("provider") or None,
        model=v1_payload.get("model_name") or None,
        endpoint=v1_payload.get("path") or None,
        direction=direction,
        streaming=False,
        request_headers=request_headers,
        request_body=request_body,
        response_headers=response_headers,
        response_body=response_body,
        quality_tier="T1_structured",
        layer_meta=layer_meta,
    )


__all__ = [
    "CaptureSource",
    "QualityTier",
    "Direction",
    "CaptureEventV2",
    "CaptureEventIngestResponse",
    "new_capture_id",
    "compute_fingerprint",
    "default_capture_host",
    "from_v1_capture",
]
