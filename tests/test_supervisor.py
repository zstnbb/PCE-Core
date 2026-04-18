# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``pce_core.supervisor``.

Uses ``sys.executable`` with short Python one-liners so every path is
exercised against a real OS subprocess without needing platform-specific
binaries. Each async test is wrapped in ``asyncio.run`` so the suite
works without ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from pce_core.supervisor import (
    ManagedProcess,
    ProcessState,
    RestartPolicy,
    Supervisor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _status_of(sup: Supervisor, name: str):
    for p in sup.get_status().processes:
        if p.name == name:
            return p
    raise AssertionError(f"no such managed process: {name}")


async def _wait_until(
    predicate, *, timeout: float = 5.0, interval: float = 0.05,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _run(coro):
    """asyncio.run with a fresh event loop — avoids leftover-task surprises."""
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_duplicate_names_rejected(self):
        with pytest.raises(ValueError):
            Supervisor([
                ManagedProcess(name="a", argv=_py("pass")),
                ManagedProcess(name="a", argv=_py("pass")),
            ])

    def test_initial_status_is_pending(self):
        sup = Supervisor([ManagedProcess(name="a", argv=_py("pass"))])
        st = sup.get_status()
        assert st.running is False
        assert len(st.processes) == 1
        assert st.processes[0].state == ProcessState.PENDING


# ---------------------------------------------------------------------------
# Restart policies
# ---------------------------------------------------------------------------

class TestRestartPolicies:
    def test_never_policy_does_not_restart(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="once",
                argv=_py("import sys; sys.exit(0)"),
                restart=RestartPolicy.NEVER,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "once").state == ProcessState.STOPPED,
                    timeout=5.0,
                )
                assert ok, "expected STOPPED"
                st = _status_of(sup, "once")
                assert st.restarts == 0
                assert st.last_exit_code == 0
            finally:
                await sup.stop_all()
        _run(_go())

    def test_on_failure_policy_does_not_restart_on_zero(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="once",
                argv=_py("import sys; sys.exit(0)"),
                restart=RestartPolicy.ON_FAILURE,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "once").state == ProcessState.STOPPED,
                    timeout=5.0,
                )
                assert ok
                assert _status_of(sup, "once").restarts == 0
            finally:
                await sup.stop_all()
        _run(_go())

    def test_on_failure_policy_restarts_after_nonzero(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="crasher",
                argv=_py("import sys; sys.exit(7)"),
                restart=RestartPolicy.ON_FAILURE,
                max_restarts=2,
                initial_backoff_s=0.05,
                max_backoff_s=0.2,
                healthy_after_s=60.0,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "crasher").state == ProcessState.FAILED,
                    timeout=10.0,
                )
                assert ok, (
                    f"expected FAILED after max_restarts=2, got "
                    f"{_status_of(sup, 'crasher').state}"
                )
                st = _status_of(sup, "crasher")
                assert st.restarts == 2
                assert st.last_exit_code == 7
            finally:
                await sup.stop_all()
        _run(_go())


# ---------------------------------------------------------------------------
# Lifecycle & graceful stop
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_graceful_stop_waits_for_child_exit(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="sleeper",
                argv=_py("import time; time.sleep(60)"),
                restart=RestartPolicy.ALWAYS,
                stop_timeout_s=2.0,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "sleeper").state == ProcessState.RUNNING,
                    timeout=5.0,
                )
                assert ok, "sleeper never became RUNNING"
            finally:
                t0 = time.time()
                await sup.stop_all(timeout=10.0)
                assert time.time() - t0 < 5.0, "stop_all took too long"
                assert sup.get_status().running is False
        _run(_go())

    def test_disabled_when_executable_missing(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="ghost",
                argv=["no-such-binary-pce-p2-xyz"],
                restart=RestartPolicy.ALWAYS,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "ghost").state == ProcessState.DISABLED,
                    timeout=5.0,
                )
                assert ok
                assert "not found" in (_status_of(sup, "ghost").last_error or "")
            finally:
                await sup.stop_all()
        _run(_go())


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------

class TestBackoff:
    def test_backoff_grows_between_consecutive_crashes(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="bouncer",
                argv=_py("import sys; sys.exit(1)"),
                restart=RestartPolicy.ALWAYS,
                initial_backoff_s=0.05,
                max_backoff_s=1.0,
                healthy_after_s=60.0,
                max_restarts=4,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "bouncer").state == ProcessState.FAILED,
                    timeout=15.0,
                )
                assert ok
                # After 4 restarts with base 0.05, backoff should have grown.
                assert _status_of(sup, "bouncer").next_backoff_s >= 0.4
            finally:
                await sup.stop_all()
        _run(_go())


# ---------------------------------------------------------------------------
# Manual restart
# ---------------------------------------------------------------------------

class TestManualRestart:
    def test_restart_unknown_name_returns_false(self):
        async def _go():
            sup = Supervisor([ManagedProcess(name="a", argv=_py("pass"))])
            await sup.start_all()
            try:
                assert await sup.restart("nope") is False
            finally:
                await sup.stop_all()
        _run(_go())

    def test_restart_terminates_and_lets_supervisor_respawn(self):
        async def _go():
            sup = Supervisor([ManagedProcess(
                name="loop",
                argv=_py("import time\nwhile True: time.sleep(0.1)"),
                restart=RestartPolicy.ALWAYS,
                initial_backoff_s=0.05,
                stop_timeout_s=2.0,
            )])
            await sup.start_all()
            try:
                ok = await _wait_until(
                    lambda: _status_of(sup, "loop").state == ProcessState.RUNNING,
                    timeout=5.0,
                )
                assert ok
                first_pid = _status_of(sup, "loop").pid
                assert first_pid is not None
                assert await sup.restart("loop") is True
                ok = await _wait_until(
                    lambda: (
                        _status_of(sup, "loop").state == ProcessState.RUNNING
                        and _status_of(sup, "loop").pid
                        and _status_of(sup, "loop").pid != first_pid
                    ),
                    timeout=8.0,
                )
                assert ok, "loop did not respawn after restart()"
            finally:
                await sup.stop_all()
        _run(_go())


# ---------------------------------------------------------------------------
# get_status shape
# ---------------------------------------------------------------------------

class TestStatusShape:
    def test_as_dict_is_json_serialisable(self):
        import json
        sup = Supervisor([ManagedProcess(name="x", argv=_py("pass"))])
        payload = sup.get_status().as_dict()
        s = json.dumps(payload)
        data = json.loads(s)
        assert data["running"] is False
        assert data["processes"][0]["name"] == "x"
        assert data["processes"][0]["state"] == "pending"
