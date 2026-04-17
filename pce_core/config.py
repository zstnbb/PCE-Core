"""PCE Core – centralised configuration.

Shared by all capture frontends (proxy, browser extension, MCP, etc.).
"""

import os
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Capture mode (controls proxy heuristic behaviour)
# ---------------------------------------------------------------------------

class CaptureMode(Enum):
    ALLOWLIST = "allowlist"   # Only capture traffic to known AI domains
    SMART = "smart"           # Allowlist + heuristic detection of unknown AI traffic
    ALL = "all"               # Capture all HTTPS traffic (advanced/experimental)

CAPTURE_MODE = CaptureMode(os.environ.get("PCE_CAPTURE_MODE", "allowlist"))

# ---------------------------------------------------------------------------
# Data directory
# ---------------------------------------------------------------------------
# Default: ~/.pce/data   (can be overridden via PCE_DATA_DIR env var)
DATA_DIR = Path(os.environ.get("PCE_DATA_DIR", Path.home() / ".pce" / "data"))
DB_PATH = DATA_DIR / "pce.db"

# ---------------------------------------------------------------------------
# AI domain allowlist – only these hosts will be captured by the proxy
# ---------------------------------------------------------------------------
ALLOWED_HOSTS: set[str] = {
    # ── Direct Provider APIs ──────────────────────────────
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.x.ai",
    "api.deepseek.com",
    "api.mistral.ai",
    "api.cohere.com",
    "api.groq.com",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.perplexity.ai",
    "api.replicate.com",
    "api-inference.huggingface.co",
    "api.ai21.com",
    "api.aleph-alpha.com",
    "api.minimax.chat",
    "api.moonshot.cn",
    "api.baichuan-ai.com",
    "api.zhipuai.cn",
    "chat.z.ai",
    "chatglm.cn",
    "chat.zhipuai.cn",
    "open.bigmodel.cn",
    "maas.aminer.cn",
    # ── Vendor Relay / IDE ────────────────────────────────
    "api2.cursor.sh",
    "api.codeium.com",
    "copilot-proxy.githubusercontent.com",
    "codewhisperer.amazonaws.com",
    "api.tabnine.com",
    "api.sourcegraph.com",
    # ── Aggregators / Routers ─────────────────────────────
    "openrouter.ai",
    "api.openrouter.ai",
    # ── Audio / Media / Image ─────────────────────────────
    "api.elevenlabs.io",
    "api.stability.ai",
    "api.midjourney.com",
    "cl.imagineapi.dev",
    # ── Local Model Servers ───────────────────────────────
    "localhost",
    "127.0.0.1",
}

# ---------------------------------------------------------------------------
# Known local model ports (Ollama, LM Studio, vLLM, LocalAI, etc.)
# ---------------------------------------------------------------------------
LOCAL_MODEL_PORTS: set[int] = {
    11434,  # Ollama default
    1234,   # LM Studio default
    8000,   # vLLM / FastChat default
    8080,   # LocalAI default
    5000,   # Text Generation WebUI default
    3000,   # Jan default
}

# Allow extending the allowlist via env var (comma-separated hostnames).
_extra_hosts = os.environ.get("PCE_EXTRA_HOSTS", "")
if _extra_hosts:
    ALLOWED_HOSTS.update(h.strip() for h in _extra_hosts.split(",") if h.strip())

# ---------------------------------------------------------------------------
# Headers that MUST be redacted before persistence
# ---------------------------------------------------------------------------
REDACT_HEADERS: set[str] = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}

# ---------------------------------------------------------------------------
# Proxy settings
# ---------------------------------------------------------------------------
PROXY_LISTEN_HOST = os.environ.get("PCE_PROXY_HOST", "127.0.0.1")
PROXY_LISTEN_PORT = int(os.environ.get("PCE_PROXY_PORT", "8080"))

# ---------------------------------------------------------------------------
# Ingest API settings
# ---------------------------------------------------------------------------
INGEST_HOST = os.environ.get("PCE_INGEST_HOST", "127.0.0.1")
INGEST_PORT = int(os.environ.get("PCE_INGEST_PORT", "9800"))

# ---------------------------------------------------------------------------
# Retention policy (P1) — applied by pce_core.retention.
# All disabled by default so upgraders keep existing data.
# ---------------------------------------------------------------------------

def _parse_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return max(int(raw), 0)
    except ValueError:
        return default


def _parse_float(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return default


RETENTION_DAYS = _parse_int("PCE_RETENTION_DAYS", 0)
RETENTION_MAX_ROWS = _parse_int("PCE_RETENTION_MAX_ROWS", 0)
RETENTION_INTERVAL_HOURS = _parse_float("PCE_RETENTION_INTERVAL_HOURS", 6.0)

# ---------------------------------------------------------------------------
# OpenTelemetry OTLP exporter (P1, opt-in) — see ADR-007.
# ---------------------------------------------------------------------------
# Secondary, non-default channel. Local SQLite remains the source of truth.
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
OTEL_EXPORTER_OTLP_PROTOCOL = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
OTEL_EXPORTER_OTLP_HEADERS = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "pce-core")
