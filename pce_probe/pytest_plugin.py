# SPDX-License-Identifier: Apache-2.0
"""Pytest plugin exposing a session-scoped ``pce_probe`` fixture.

The fixture starts a :class:`ProbeServer` on a configurable host/port
and waits for the PCE browser extension (running in an attached
Chrome) to connect. Tests then drive the browser through the probe
client. When the test session ends the server is torn down cleanly.

Activate by installing this package (``pip install -e .`` from the
repo root) — pytest auto-discovers the entry point declared in
``setup.cfg`` / ``pyproject.toml``. Or import directly via::

    pytest_plugins = ["pce_probe.pytest_plugin"]

CLI knobs:

  --pce-probe-host  (default 127.0.0.1)
  --pce-probe-port  (default 9888)
  --pce-probe-attach-timeout  (default 30.0)
  --pce-probe-skip  (skip every test that requests pce_probe; useful
                    for CI without a Chrome)
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

import pytest

from .client import ProbeClient
from .server import DEFAULT_HOST, DEFAULT_PORT

LOG = logging.getLogger("pce_probe.pytest")

# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------


def pytest_addoption(parser: Any) -> None:  # type: ignore[no-untyped-def]
    group = parser.getgroup("pce_probe", "PCE Probe — agent-facing E2E driver")
    group.addoption(
        "--pce-probe-host",
        action="store",
        default=DEFAULT_HOST,
        help="Loopback host the probe server binds. Default 127.0.0.1.",
    )
    group.addoption(
        "--pce-probe-port",
        action="store",
        type=int,
        default=DEFAULT_PORT,
        help="Port the probe server binds. Default 9888. Use 0 for a "
             "random ephemeral port (test-only).",
    )
    group.addoption(
        "--pce-probe-attach-timeout",
        action="store",
        type=float,
        default=30.0,
        help="Seconds to wait for the extension's WS to attach before "
             "failing the fixture. Default 30.",
    )
    group.addoption(
        "--pce-probe-skip",
        action="store_true",
        default=False,
        help="Skip every test that requests the pce_probe fixture. "
             "Useful for CI runs without a Chrome instance.",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pce_probe_config(request: pytest.FixtureRequest) -> dict[str, Any]:
    return {
        "host": request.config.getoption("--pce-probe-host"),
        "port": request.config.getoption("--pce-probe-port"),
        "attach_timeout": request.config.getoption("--pce-probe-attach-timeout"),
        "skip": request.config.getoption("--pce-probe-skip"),
    }


@pytest.fixture(scope="session")
def pce_probe(pce_probe_config: dict[str, Any]) -> Iterator[ProbeClient]:
    """Session-scoped probe client; binds the WS server, waits for the
    extension to attach, and tears everything down on session end.

    For per-test isolation use the function-scoped ``pce_probe_fresh``
    fixture below — it shares the same server but resets per-test
    state (e.g., closes any tabs the previous test opened).
    """
    if pce_probe_config["skip"]:
        pytest.skip(
            "PCE Probe disabled by --pce-probe-skip "
            "(no Chrome attached in this run)",
        )

    client = ProbeClient(
        host=pce_probe_config["host"],
        port=pce_probe_config["port"],
        attach_timeout=pce_probe_config["attach_timeout"],
    )
    try:
        client.start()
    except Exception as exc:
        pytest.fail(
            f"PCE Probe failed to start: {exc}\n"
            "Hint: launch Chrome with the PCE extension installed and "
            "ensure the extension's probe WS client is enabled "
            "(chrome.storage.local.pce_probe_enabled !== false). The "
            "extension auto-connects to ws://"
            f"{pce_probe_config['host']}:{pce_probe_config['port']}.",
        )

    LOG.info(
        "pce_probe ready: extension=%s, %d capabilities",
        client.extension_hello.extension_version if client.extension_hello else "?",
        len(client.extension_hello.capabilities) if client.extension_hello else 0,
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def pce_probe_fresh(pce_probe: ProbeClient) -> Iterator[ProbeClient]:
    """Function-scoped fixture: yields the session probe but closes any
    tabs that didn't exist at the start of the test, on teardown.

    Use this when a test opens AI tabs that should not leak into the
    next test's view of ``probe.tab.list()``.
    """
    before = {t["id"] for t in pce_probe.tab.list().get("tabs", [])}
    try:
        yield pce_probe
    finally:
        # Catch tab.list() errors here (extension might have detached)
        # so the rest of the cleanup is skipped without a bare return
        # inside finally (which pyright flags as an antipattern).
        after: list[dict[str, Any]] = []
        try:
            after = pce_probe.tab.list().get("tabs", []) or []
        except Exception:
            after = []
        for t in after:
            if t["id"] not in before:
                try:
                    pce_probe.tab.close(t["id"])
                except Exception:
                    LOG.debug("could not close tab %s on teardown", t["id"])


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "pce_probe: mark a test as requiring an attached PCE Probe / browser",
    )
