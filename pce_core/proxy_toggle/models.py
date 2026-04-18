# SPDX-License-Identifier: Apache-2.0
"""Shared dataclasses for the proxy toggle."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Platform(str, Enum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


# Sensible default bypass list — anything on the loopback / private net,
# plus the PCE Ingest port so the agent never proxies itself.
DEFAULT_BYPASS: tuple[str, ...] = (
    "127.0.0.1",
    "localhost",
    "::1",
    "10.*",
    "172.16.*",
    "172.17.*",
    "172.18.*",
    "172.19.*",
    "172.2*",
    "172.30.*",
    "172.31.*",
    "192.168.*",
    "*.local",
)


@dataclass
class ProxyState:
    """Snapshot of the host's current system-proxy settings."""

    platform: Platform
    enabled: bool = False
    host: Optional[str] = None
    port: Optional[int] = None
    bypass: List[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)      # platform-specific diagnostics

    def as_dict(self) -> dict:
        return {
            "platform": self.platform.value,
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "bypass": list(self.bypass),
            "raw": self.raw,
        }


@dataclass
class ProxyOpResult:
    ok: bool
    message: str
    platform: Platform
    needs_elevation: bool = False
    elevated_cmd: Optional[List[str]] = None
    dry_run: bool = False
    state_after: Optional[ProxyState] = None
    stdout: str = ""
    stderr: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "message": self.message,
            "platform": self.platform.value,
            "needs_elevation": self.needs_elevation,
            "elevated_cmd": self.elevated_cmd,
            "dry_run": self.dry_run,
            "state_after": self.state_after.as_dict() if self.state_after else None,
            "stdout": self.stdout[-4000:] if self.stdout else "",
            "stderr": self.stderr[-4000:] if self.stderr else "",
            "extra": self.extra,
        }
