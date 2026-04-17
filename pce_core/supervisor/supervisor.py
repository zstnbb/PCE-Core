"""Async subprocess supervisor.

One :class:`asyncio.Task` per managed process runs a loop::

    while not stop_event:
        try spawn
        await process.wait()
        if policy allows restart:
            sleep backoff
            continue
        else: break

Backoff is exponential, capped, and resets whenever a child has been
alive for at least ``healthy_after_s`` seconds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from ..logging_config import log_event
from .models import (
    ManagedProcess,
    ProcessState,
    ProcessStatus,
    RestartPolicy,
    SupervisorStatus,
)

logger = logging.getLogger("pce.supervisor")


class Supervisor:
    """Async supervisor for a fixed set of long-running children."""

    def __init__(self, specs: Iterable[ManagedProcess]) -> None:
        self._specs: Dict[str, ManagedProcess] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"duplicate managed process name: {spec.name!r}")
            self._specs[spec.name] = spec
        self._status: Dict[str, ProcessStatus] = {
            name: ProcessStatus(name=name, state=ProcessState.PENDING)
            for name in self._specs
        }
        self._tasks: Dict[str, asyncio.Task] = {}
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        self._started_at: Optional[float] = None
        self._global_stop = asyncio.Event()

    # ---- lifecycle ---------------------------------------------------------

    async def start_all(self) -> None:
        """Spawn supervisor tasks for every spec. Returns immediately."""
        if self._tasks:
            return
        self._started_at = time.time()
        self._global_stop = asyncio.Event()
        for name in self._specs:
            self._stop_events[name] = asyncio.Event()
            self._tasks[name] = asyncio.create_task(
                self._run_one(name), name=f"supervisor.{name}",
            )
        log_event(logger, "supervisor.started",
                  processes=list(self._specs.keys()))

    async def stop_all(self, timeout: float = 10.0) -> None:
        """Signal every supervisor task to stop and await completion."""
        if not self._tasks:
            return
        log_event(logger, "supervisor.stopping",
                  processes=list(self._specs.keys()))
        self._global_stop.set()
        for ev in self._stop_events.values():
            ev.set()
        # Ask each child to terminate
        await asyncio.gather(
            *(self._terminate(name) for name in list(self._processes.keys())),
            return_exceptions=True,
        )
        # Wait for tasks to finish
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks.values(), return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for t in self._tasks.values():
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._processes.clear()
        log_event(logger, "supervisor.stopped")

    # ---- introspection ------------------------------------------------------

    def get_status(self) -> SupervisorStatus:
        return SupervisorStatus(
            running=bool(self._tasks),
            started_at=self._started_at,
            processes=[self._snapshot(name) for name in self._specs],
        )

    def _snapshot(self, name: str) -> ProcessStatus:
        st = self._status[name]
        # Copy to detach from internal state mutation.
        return ProcessStatus(
            name=st.name,
            state=st.state,
            pid=st.pid,
            started_at=st.started_at,
            last_exit_code=st.last_exit_code,
            restarts=st.restarts,
            next_backoff_s=st.next_backoff_s,
            last_error=st.last_error,
        )

    # ---- core loop ---------------------------------------------------------

    async def _run_one(self, name: str) -> None:
        spec = self._specs[name]
        status = self._status[name]
        backoff = spec.initial_backoff_s
        restarts = 0
        stop_ev = self._stop_events[name]
        while not stop_ev.is_set():
            try:
                proc = await self._spawn(spec)
            except FileNotFoundError as e:
                status.state = ProcessState.DISABLED
                status.last_error = f"executable not found: {e}"
                log_event(logger, "supervisor.disabled",
                          level=logging.WARNING, name=name, reason=str(e))
                return
            except Exception as e:      # noqa: BLE001
                status.state = ProcessState.BACKOFF
                status.last_error = f"spawn failed: {e}"
                log_event(logger, "supervisor.spawn_failed",
                          level=logging.WARNING, name=name, error=str(e),
                          next_backoff_s=backoff)
                if await self._wait_backoff(stop_ev, backoff):
                    break
                backoff = min(backoff * 2.0, spec.max_backoff_s)
                continue

            self._processes[name] = proc
            status.pid = proc.pid
            status.started_at = time.time()
            status.state = ProcessState.RUNNING
            status.last_error = None
            log_event(logger, "supervisor.running",
                      name=name, pid=proc.pid, argv=list(spec.argv))

            rc = await proc.wait()
            run_duration = time.time() - (status.started_at or time.time())
            status.last_exit_code = rc
            self._processes.pop(name, None)

            # Reset backoff if the child was alive long enough to be considered
            # healthy.
            if run_duration >= spec.healthy_after_s:
                backoff = spec.initial_backoff_s

            log_event(
                logger, "supervisor.exited",
                level=logging.INFO if rc == 0 else logging.WARNING,
                name=name, rc=rc, run_duration_s=round(run_duration, 2),
                restarts=restarts,
            )

            if stop_ev.is_set() or self._global_stop.is_set():
                status.state = ProcessState.STOPPED
                break

            # Restart decision
            if spec.restart == RestartPolicy.NEVER or (
                spec.restart == RestartPolicy.ON_FAILURE and rc == 0
            ):
                status.state = ProcessState.STOPPED if rc == 0 else ProcessState.FAILED
                break
            if (
                spec.max_restarts is not None
                and restarts >= spec.max_restarts
            ):
                status.state = ProcessState.FAILED
                status.last_error = f"exceeded max_restarts={spec.max_restarts}"
                log_event(logger, "supervisor.max_restarts_hit",
                          level=logging.WARNING, name=name,
                          max_restarts=spec.max_restarts)
                break

            status.state = ProcessState.BACKOFF
            status.next_backoff_s = backoff
            if await self._wait_backoff(stop_ev, backoff):
                status.state = ProcessState.STOPPED
                break
            restarts += 1
            status.restarts = restarts
            backoff = min(backoff * 2.0, spec.max_backoff_s)

    async def _spawn(self, spec: ManagedProcess) -> asyncio.subprocess.Process:
        env = None
        if spec.env is not None:
            env = dict(os.environ)
            env.update(spec.env)
        return await asyncio.create_subprocess_exec(
            *spec.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=spec.cwd,
            env=env,
        )

    async def _wait_backoff(self, stop_ev: asyncio.Event, backoff: float) -> bool:
        """Sleep for ``backoff`` seconds or return early if stop requested.

        Returns True if the stop event fired (caller should break out).
        """
        try:
            await asyncio.wait_for(stop_ev.wait(), timeout=backoff)
            return True
        except asyncio.TimeoutError:
            return False

    async def _terminate(self, name: str) -> None:
        proc = self._processes.get(name)
        if proc is None or proc.returncode is not None:
            return
        spec = self._specs[name]
        status = self._status[name]
        try:
            if sys.platform == "win32":
                proc.terminate()    # CTRL_BREAK isn't always plumbed; terminate is safest
            else:
                proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception as e:      # noqa: BLE001
            status.last_error = f"terminate failed: {e}"
        try:
            await asyncio.wait_for(proc.wait(), timeout=spec.stop_timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:       # noqa: BLE001
                pass
            try:
                await proc.wait()
            except Exception:       # noqa: BLE001
                pass

    # ---- single-process APIs (optional, used by /supervisor/{name}/restart) -

    async def restart(self, name: str) -> bool:
        """Stop and immediately restart a single managed process."""
        if name not in self._specs:
            return False
        proc = self._processes.get(name)
        if proc is not None and proc.returncode is None:
            await self._terminate(name)
        # The supervisor loop will notice the exit and restart via normal path.
        return True
