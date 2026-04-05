"""PCE Proxy configuration: allowlist, paths, and defaults."""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Data directory
# ---------------------------------------------------------------------------
# Default: ~/.pce/data   (can be overridden via PCE_DATA_DIR env var)
DATA_DIR = Path(os.environ.get("PCE_DATA_DIR", Path.home() / ".pce" / "data"))
DB_PATH = DATA_DIR / "pce.db"

# ---------------------------------------------------------------------------
# AI domain allowlist – only these hosts will be captured
# ---------------------------------------------------------------------------
ALLOWED_HOSTS: set[str] = {
    "api.openai.com",
    "api.anthropic.com",
}

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
