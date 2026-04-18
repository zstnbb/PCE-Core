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
    schema_version: Optional[int] = Field(
        default=None,
        description="Capture payload format version. Defaults to current server version if omitted.",
    )


class CaptureOut(BaseModel):
    """Response returned after a successful capture insert."""

    id: str
    pair_id: str
    source_id: str


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
    """A single sessions row."""

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


class MessageRecord(BaseModel):
    """A single messages row."""

    id: str
    session_id: str
    capture_pair_id: Optional[str] = None
    ts: float
    role: str
    content_text: Optional[str] = None
    content_json: Optional[str] = None
    model_name: Optional[str] = None
    token_estimate: Optional[int] = None


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
