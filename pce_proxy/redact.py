"""PCE Proxy – Header & body redaction utilities.

Ensures sensitive authentication material never reaches the database in
clear text.  The approach follows ARCHITECTURE.md §9 and TASK-001 §5.4:
- Listed header values are replaced with "REDACTED"
- Body content is kept as-is (bodies carry conversation data, not secrets)
"""

import json
from typing import Union

from .config import REDACT_HEADERS


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in REDACT_HEADERS:
            out[k] = "REDACTED"
        else:
            out[k] = v
    return out


def redact_headers_json(headers: dict[str, str]) -> str:
    """Redact and return a JSON string ready for storage."""
    return json.dumps(redact_headers(headers), ensure_ascii=False)


def safe_body_text(raw: bytes, limit: int = 2 * 1024 * 1024) -> tuple[str, str]:
    """Decode body bytes, truncating if excessively large.

    Returns (text, format) where format is 'json' or 'text'.
    """
    if not raw:
        return "", "text"

    text = raw[:limit].decode("utf-8", errors="replace")

    # Quick check: does it look like JSON?
    stripped = text.lstrip()
    fmt = "json" if stripped.startswith(("{", "[")) else "text"
    return text, fmt
