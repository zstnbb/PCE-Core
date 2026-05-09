# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for tests/e2e_mcp/.

Each test gets its own subprocess + isolated PCE_DATA_DIR so tests do not
contaminate each other or the developer's real PCE database.
"""

from __future__ import annotations

import os
import sys
import pytest
from pathlib import Path

# Make the repo root importable so `pce_mcp` / `pce_core` resolve when
# tests run from any working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Point PCE_DATA_DIR at a fresh temp dir for the test's lifetime."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def python_exe():
    return sys.executable
