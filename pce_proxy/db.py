# SPDX-License-Identifier: Apache-2.0
"""PCE Proxy – SQLite storage engine – re-exports from pce_core.db.

This module exists for backward compatibility. All canonical DB logic
now lives in pce_core.db.
"""

from pce_core.db import (  # noqa: F401
    SOURCE_PROXY as DEFAULT_SOURCE_ID,
    get_connection,
    init_db,
    insert_capture,
    new_pair_id,
    query_recent,
)
