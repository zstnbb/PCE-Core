"""Shared fixtures for the P0 smoke suite.

Key guarantee: every smoke test runs against a fresh temp database.
``PCE_DATA_DIR`` is set before ``pce_core`` is imported so module-level
``init_db()`` calls in ``pce_proxy.addon`` / ``pce_mcp.server`` land in
the isolated temp directory instead of the user's real data path.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Global env bootstrapping
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Set a temp data dir at collection time so any import-time ``init_db()``
# (e.g. inside pce_proxy.addon) targets an isolated location. Individual
# tests can override by passing ``db_path`` to db helpers, but they must
# NOT rely on the user's real ~/.pce/data.
_SESSION_DIR = Path(tempfile.mkdtemp(prefix="pce_smoke_session_"))
os.environ.setdefault("PCE_DATA_DIR", str(_SESSION_DIR))
# Force JSON logging in CI so smoke assertions can parse structured events
# from captured log output. Tests that care check records via caplog.
os.environ.setdefault("PCE_LOG_JSON", "0")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """Return a freshly-initialised DB path for a single test.

    Every test that wants its own clean DB should take this fixture and
    pass the result as ``db_path=`` to the pce_core.* helpers.
    """
    from pce_core.db import init_db

    db = tmp_path / "smoke.db"
    init_db(db)
    return db


@pytest.fixture(scope="session")
def session_tmp_dir() -> Path:
    """Return the session-wide temp directory used by module-level inits."""
    return _SESSION_DIR
