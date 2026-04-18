# SPDX-License-Identifier: Apache-2.0
"""PCE Proxy configuration – re-exports from pce_core.config.

This module exists for backward compatibility. All canonical config
now lives in pce_core.config.
"""

from pce_core.config import (  # noqa: F401
    ALLOWED_HOSTS,
    DATA_DIR,
    DB_PATH,
    PROXY_LISTEN_HOST,
    PROXY_LISTEN_PORT,
    REDACT_HEADERS,
)
