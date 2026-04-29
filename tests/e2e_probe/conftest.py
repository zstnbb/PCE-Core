# SPDX-License-Identifier: Apache-2.0
"""Pytest config for the probe-driven E2E suite.

Activates the ``pce_probe`` plugin so tests can request the
``pce_probe`` / ``pce_probe_fresh`` fixtures. Agents that drive these
tests can opt out cleanly by passing ``--pce-probe-skip`` (defined in
``pce_probe.pytest_plugin``).

The PCE Core API (``http://127.0.0.1:9800``) is the verification
endpoint for round-trip assertions. We expose a thin ``pce_core``
fixture that returns an ``httpx.Client`` configured to bypass any
system proxy (the L1 mitmproxy proxy may otherwise loop traffic back
through itself).
"""
from __future__ import annotations

import os
from typing import Iterator

import httpx
import pytest

# Activate the probe plugin for this directory.
pytest_plugins = ["pce_probe.pytest_plugin"]


PCE_CORE_BASE_URL = os.environ.get("PCE_CORE_BASE_URL", "http://127.0.0.1:9800")


@pytest.fixture(scope="session")
def pce_core() -> Iterator[httpx.Client]:
    """HTTP client for talking to PCE Core's local ingest/query API.

    ``trust_env=False`` deliberately bypasses HTTP_PROXY etc. so that
    when the L1 mitmproxy is on, our verification reads go straight to
    the loopback API instead of being captured back through the proxy
    (which would otherwise produce a recursive capture event).
    """
    with httpx.Client(
        base_url=PCE_CORE_BASE_URL,
        trust_env=False,
        timeout=10.0,
    ) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def _verify_pce_core_reachable(pce_core: httpx.Client) -> None:
    """Soft check — the probe E2E suite is meaningless if PCE Core
    isn't running, so we surface a clear skip rather than letting
    every test drown in connection errors.
    """
    try:
        r = pce_core.get("/api/v1/health", timeout=2.0)
        if r.status_code != 200:
            pytest.skip(
                f"PCE Core /api/v1/health returned {r.status_code}; "
                "start the server with `python -m pce_core.server`",
                allow_module_level=True,
            )
    except Exception as exc:
        pytest.skip(
            f"PCE Core not reachable at {PCE_CORE_BASE_URL} ({exc}); "
            "start the server with `python -m pce_core.server`",
            allow_module_level=True,
        )
