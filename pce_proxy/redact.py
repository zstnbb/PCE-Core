# SPDX-License-Identifier: Apache-2.0
"""PCE Proxy – Header & body redaction utilities – re-exports from pce_core.redact.

This module exists for backward compatibility. All canonical redaction logic
now lives in pce_core.redact.
"""

from pce_core.redact import (  # noqa: F401
    redact_headers,
    redact_headers_json,
    safe_body_text,
)
