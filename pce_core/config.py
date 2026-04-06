"""PCE Core – centralised configuration.

Shared by all capture frontends (proxy, browser extension, MCP, etc.).
"""

import os
from pathlib import Path

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
    # Direct Provider APIs
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
    # Vendor Relay
    "api2.cursor.sh",
    "api.codeium.com",
    "copilot-proxy.githubusercontent.com",
    # Aggregators
    "openrouter.ai",
    # Audio / Media
    "api.elevenlabs.io",
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
