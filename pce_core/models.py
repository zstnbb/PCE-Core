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


class CaptureOut(BaseModel):
    """Response returned after a successful capture insert."""

    id: str
    pair_id: str
    source_id: str


# ---------------------------------------------------------------------------
# Query API models
# ---------------------------------------------------------------------------

class CaptureRecord(BaseModel):
    """A single raw_captures row."""

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


class HealthOut(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
    db_path: str
    total_captures: int
