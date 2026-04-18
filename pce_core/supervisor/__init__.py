# SPDX-License-Identifier: Apache-2.0
"""PCE Supervisor — async subprocess health guardian.

Keeps a small set of long-running child processes alive with exponential
backoff. Designed to be embedded in the FastAPI lifespan, but also
usable standalone from a CLI (see ``python -m pce_core.supervisor``).

Core contract:

- Each child is described by a :class:`ManagedProcess` spec (argv, env,
  cwd, restart policy).
- The supervisor starts every spec, monitors for exits, and restarts
  according to the policy.
- Backoff: starts at 0.5 s, doubles after each crash, caps at 60 s,
  **resets** when the child has been alive for >= ``healthy_after_seconds``
  (default 30 s).
- All operations are async; callers never block on OS waitpid.
- Fail-open: supervisor errors never propagate to the FastAPI event loop;
  they surface through :meth:`Supervisor.get_status` and the logger.

Example::

    from pce_core.supervisor import Supervisor, ManagedProcess

    sup = Supervisor([
        ManagedProcess(name="mitmdump",
                       argv=["mitmdump", "-s", "run_proxy.py", "-p", "8080"]),
    ])
    await sup.start_all()
    # … eventually
    await sup.stop_all()
"""

from __future__ import annotations

from .models import (
    ManagedProcess,
    ProcessState,
    ProcessStatus,
    RestartPolicy,
    SupervisorStatus,
)
from .supervisor import Supervisor

__all__ = [
    "ManagedProcess",
    "ProcessState",
    "ProcessStatus",
    "RestartPolicy",
    "Supervisor",
    "SupervisorStatus",
]
