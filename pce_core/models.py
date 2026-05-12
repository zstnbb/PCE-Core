# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Pydantic models for the Ingest & Query API."""

from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Ingest API models
# ---------------------------------------------------------------------------

class CaptureIn(BaseModel):
    """Payload accepted by POST /api/v1/captures."""

    source_type: str = Field(
        ...,
        description="Origin of the capture: proxy | browser_extension | mcp | ide_plugin | local_model",
    )
    source_name: str = Field(
        default="unknown",
        description="Human-readable tool name, e.g. 'mitmproxy', 'chrome-ext', 'pce-mcp-server'",
    )
    direction: str = Field(
        ...,
        description="request | response | conversation",
    )
    pair_id: Optional[str] = Field(
        default=None,
        description="Links a request to its response. If omitted, a new one is generated.",
    )
    provider: str = Field(
        default="unknown",
        description="AI provider: openai | anthropic | google | deepseek | ...",
    )
    host: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    model_name: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    headers_json: str = Field(
        default="{}",
        description="Redacted headers as JSON string",
    )
    body_json: str = Field(
        default="",
        description="Body content (text or JSON string)",
    )
    body_format: str = Field(
        default="json",
        description="json | text",
    )
    error: Optional[str] = None
    session_hint: Optional[str] = None
    meta: Optional[dict] = Field(
        default=None,
        description="Source-specific metadata (e.g. page URL for browser ext, tool name for MCP)",
    )
    layer_meta: Optional[dict] = Field(
        default=None,
        description="Layer-specific metadata preserved in raw_captures.layer_meta_json",
    )
    schema_version: Optional[int] = Field(
        default=None,
        description="Capture payload format version. Defaults to current server version if omitted.",
    )


class CaptureOut(BaseModel):
    """Response returned after a successful capture insert."""

    id: str
    pair_id: str
    source_id: str


class McpCaptureIn(BaseModel):
    """Payload accepted by POST /api/v1/mcp/capture.

    Mirrors the 12 arguments of the ``pce_capture`` MCP tool defined in
    ``pce_mcp/server.py`` so the ``pce-mcp.mcpb`` Node proxy (ADR-016
    §3.3) can forward tool calls as-is without having to translate into
    the v1 ``CaptureIn`` / v2 ``CaptureEventIn`` shapes.

    The endpoint applies the same source tagging (``SOURCE_MCP``) and
    auto-normalization as the Python tool — the two paths are intended
    to be behaviourally identical.
    """

    provider: str = Field(..., description="AI provider name (openai | anthropic | google | ...)")
    direction: str = Field(default="conversation", description="request | response | conversation")
    host: str = Field(default="")
    path: str = Field(default="")
    method: str = Field(default="POST")
    model_name: str = Field(default="")
    request_body: str = Field(default="", description="JSON string of the request body")
    response_body: str = Field(default="", description="JSON string of the response body")
    conversation_json: str = Field(default="", description="JSON string of a full conversation")
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    meta: Optional[dict] = None


class McpCaptureOut(BaseModel):
    """Response returned by POST /api/v1/mcp/capture."""

    ok: bool
    pair_id: str
    summary: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Query API models
# ---------------------------------------------------------------------------

class CaptureRecord(BaseModel):
    """A single raw_captures row.

    The ``source`` / ``agent_*`` / ``capture_time_ns`` / ``quality_tier`` /
    ``fingerprint`` / ``deduped_by`` / ``form_id`` / ``app_name`` /
    ``layer_meta_json`` fields are the CaptureEvent v2 native columns added
    by migration 0006 (UCS §5.5). They are optional so that rows inserted
    before migration 0006 (or by legacy v1 producers that don't supply
    them) still round-trip through the Query API.
    """

    id: str
    created_at: float
    source_id: str
    direction: str
    pair_id: str
    host: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    headers_redacted_json: Optional[str] = None
    body_text_or_json: Optional[str] = None
    body_format: Optional[str] = None
    error: Optional[str] = None
    session_hint: Optional[str] = None
    meta_json: Optional[str] = None
    schema_version: int = 1
    # ── CaptureEvent v2 native columns (migration 0006) ────────────────────
    source: Optional[str] = None
    agent_name: Optional[str] = None
    agent_version: Optional[str] = None
    capture_time_ns: Optional[int] = None
    quality_tier: Optional[str] = None
    fingerprint: Optional[str] = None
    deduped_by: Optional[str] = None
    form_id: Optional[str] = None
    app_name: Optional[str] = None
    layer_meta_json: Optional[str] = None


class HostPinningStats(BaseModel):
    """Per-host TLS failure aggregation (P5.A-6).

    One entry per host that has had at least one TLS handshake failure
    within the report window. ``suspected_pinning`` becomes ``True`` only
    when both the absolute failure count and the failure rate cross the
    configured thresholds — see ``pce_core.db.query_pinning_stats``.
    """

    host: str
    failures: int
    successes: int
    failure_rate: float
    suspected_pinning: bool
    last_failure_ts: Optional[float] = None
    last_error_category: str = "unknown"


class PinningReport(BaseModel):
    """Response body of ``GET /api/v1/health/pinning`` (P5.A-6).

    The dashboard renders the red-dot indicator when
    ``suspected_pinning_count > 0`` and expands a per-host detail panel
    using the ``hosts`` array. The threshold fields are echoed so the UI
    can explain *why* a host is flagged.
    """

    window_hours: float
    min_failures_threshold: int
    failure_rate_threshold: float
    suspected_pinning_count: int
    hosts: list[HostPinningStats] = []


class AppBypassEntry(BaseModel):
    """One row of ``GET /api/v1/bypass/apps`` (P5.A-7).

    ``name`` is the canonical slug the launcher keys off (matches
    ``ElectronApp.name``). ``display_name`` is human-facing.
    ``bypassed`` reflects the current persisted state.
    """

    name: str
    display_name: str
    bypassed: bool
    ai_domains: list[str] = []


class AppBypassReport(BaseModel):
    """Response body for ``GET /api/v1/bypass/apps`` (P5.A-7)."""

    apps: list[AppBypassEntry] = []
    bypassed_count: int = 0
    updated_at: float = 0.0


class AppBypassUpdate(BaseModel):
    """Request body for ``PUT /api/v1/bypass/apps`` (P5.A-7).

    Replaces the whole bypass set atomically — callers send the complete
    desired list, not a delta. The server normalises (dedup / lowercase)
    before persisting.
    """

    bypassed: list[str] = []


class OnboardingCaptureVerify(BaseModel):
    """Response body for ``GET /api/v1/onboarding/verify`` (P5.A-5).

    ``captured=True`` flips as soon as any new capture row is recorded
    after ``since_epoch``. The wizard polls this while prompting the
    user to send a test message through ChatGPT / Claude / etc.
    """

    captured: bool = False
    count: int = 0
    since_epoch: float = 0.0
    latest_ts_epoch: Optional[float] = None
    error: Optional[str] = None


class StorageInfo(BaseModel):
    """Storage usage statistics."""

    db_size_mb: float = 0
    raw_captures_count: int = 0
    sessions_count: int = 0
    messages_count: int = 0
    oldest_capture_days: float = 0


class StatsOut(BaseModel):
    """Summary statistics."""

    total_captures: int
    by_provider: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    earliest: Optional[float] = None
    latest: Optional[float] = None
    storage: Optional[StorageInfo] = None


class SessionRecord(BaseModel):
    """A single sessions row.

    The ``branch_count`` field was added in G2 (ADR-2026-04-26 §5.3) to
    let dashboard / API consumers know whether a session has any
    alternate branches without forcing them to fetch the full message
    list. ``1`` is the no-fork default — a linear conversation has one
    branch (``'0'``) and that's still a "branch" for counting purposes.
    """

    id: str
    source_id: str
    started_at: float
    ended_at: Optional[float] = None
    provider: Optional[str] = None
    tool_family: Optional[str] = None
    session_key: Optional[str] = None
    message_count: int = 0
    title_hint: Optional[str] = None
    created_via: Optional[str] = None
    language: Optional[str] = None
    topic_tags: Optional[str] = None
    total_tokens: Optional[int] = None
    model_names: Optional[str] = None
    favorited: int = 0
    branch_count: int = 1


class MessageRecord(BaseModel):
    """A single messages row.

    The ``branch_*`` and ``turn_index`` fields were added in migration
    0008 (ADR-2026-04-26 §5.1) to expose the regenerate/edit branch
    semantics at the API surface. Default-branch rows carry
    ``branch_id="0"`` and ``branch_parent_id=None``; alternates carry
    the ids minted by the normalizer's threading contract.
    """

    id: str
    session_id: str
    capture_pair_id: Optional[str] = None
    ts: float
    role: str
    content_text: Optional[str] = None
    content_json: Optional[str] = None
    model_name: Optional[str] = None
    token_estimate: Optional[int] = None
    branch_id: str = "0"
    branch_parent_id: Optional[str] = None
    turn_index: Optional[int] = None


class SnippetIn(BaseModel):
    """Payload accepted by POST /api/v1/snippets."""

    content_text: str = Field(..., min_length=1, description="Selected text content")
    source_url: Optional[str] = Field(default=None, description="URL where text was selected")
    source_domain: Optional[str] = Field(default=None, description="Domain of the source page")
    provider: Optional[str] = Field(default=None, description="AI provider if applicable")
    category: str = Field(default="general", description="Category: general | code | prompt | output | reference")
    note: Optional[str] = Field(default=None, description="Optional user note")


class SnippetRecord(BaseModel):
    """A single snippets row."""

    id: str
    created_at: float
    source_url: Optional[str] = None
    source_domain: Optional[str] = None
    provider: Optional[str] = None
    category: str = "general"
    content_text: str
    note: Optional[str] = None
    favorited: int = 0


class HealthOut(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
    db_path: str
    total_captures: int


# ---------------------------------------------------------------------------
# Health beacon (P5.C.1 Meta-Pipeline) — see Docs/stability/PCE-PIPELINE-HEALTH-MATRIX.md
# ---------------------------------------------------------------------------

class HealthBeaconIn(BaseModel):
    """Payload accepted by POST /api/v1/health/beacon.

    Mirrors the canonical ``HealthBeacon`` dataclass in
    ``pce_core/health.py``. Validation (lane / layer / status enums,
    target/case_id regex, PII deny-list, ts skew, meta size) happens
    server-side in ``record_beacon`` so this Pydantic model is
    intentionally permissive — the goal is to *receive* a poorly-formed
    beacon and return a clean ``400 {"error": "<code>"}`` rather than
    Pydantic-422 the caller into confusion.
    """

    lane: str = Field(..., description="browser | desktop | cli | mcp")
    layer: str = Field(..., description="L0..L4 (UCS sub-layer)")
    target: str = Field(..., max_length=128, description="short product id, e.g. 'chatgpt'")
    status: str = Field(..., description="pass | fail | skip | degraded | infra_error")
    ts: float = Field(..., description="client-side unix epoch seconds")
    case_id: Optional[str] = Field(default=None, description="e.g. 'T01' / 'D03' / null")
    elapsed_ms: Optional[int] = Field(default=None, ge=0)
    meta: Optional[dict] = Field(
        default=None,
        description="Lane-specific metadata. Must not contain PII / secrets / business body.",
    )
    dom_selector_hits: Optional[dict] = Field(
        default=None,
        description="Browser/Desktop only — selector → hit-count map.",
    )


class HealthBeaconRecord(BaseModel):
    """A single health_beacons row, as returned by GET /api/v1/health/beacon/{id}."""

    id: int
    lane: str
    layer: str
    target: str
    case_id: Optional[str] = None
    status: str
    ts: float
    elapsed_ms: Optional[int] = None
    meta: dict = Field(default_factory=dict)
    dom_selector_hits: Optional[dict] = None
    created_at: float


class HealthBeaconAccepted(BaseModel):
    """Response returned after a successful beacon insert."""

    id: int
    accepted: bool = True
