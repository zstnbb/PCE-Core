"""Shared dataclasses for the supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class RestartPolicy(str, Enum):
    ALWAYS = "always"          # restart no matter the exit code
    ON_FAILURE = "on_failure"  # restart only if exit code != 0
    NEVER = "never"            # do not restart


class ProcessState(str, Enum):
    PENDING = "pending"          # not yet started
    STARTING = "starting"        # spawn in flight
    RUNNING = "running"          # healthy, PID alive
    BACKOFF = "backoff"          # crashed, waiting to restart
    STOPPED = "stopped"          # intentionally stopped by us
    FAILED = "failed"            # restart policy said no, last exit != 0
    DISABLED = "disabled"        # spec present but start() bypassed (e.g. exe missing)


@dataclass
class ManagedProcess:
    """Spec for one child process."""
    name: str
    argv: List[str]
    env: Optional[Dict[str, str]] = None          # merged with os.environ
    cwd: Optional[str] = None
    restart: RestartPolicy = RestartPolicy.ALWAYS
    max_restarts: Optional[int] = None            # None = unlimited
    initial_backoff_s: float = 0.5
    max_backoff_s: float = 60.0
    healthy_after_s: float = 30.0
    # Stop signal fallbacks (Windows translates SIGTERM → TerminateProcess).
    stop_timeout_s: float = 5.0


@dataclass
class ProcessStatus:
    name: str
    state: ProcessState
    pid: Optional[int] = None
    started_at: Optional[float] = None
    last_exit_code: Optional[int] = None
    restarts: int = 0
    next_backoff_s: float = 0.0
    last_error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "pid": self.pid,
            "started_at": self.started_at,
            "last_exit_code": self.last_exit_code,
            "restarts": self.restarts,
            "next_backoff_s": round(self.next_backoff_s, 2),
            "last_error": self.last_error,
        }


@dataclass
class SupervisorStatus:
    running: bool
    started_at: Optional[float]
    processes: List[ProcessStatus] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "processes": [p.as_dict() for p in self.processes],
        }
