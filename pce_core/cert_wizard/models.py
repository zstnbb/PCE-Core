# SPDX-License-Identifier: Apache-2.0
"""Shared dataclasses for the cert wizard."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Platform(str, Enum):
    """Supported host platforms for the cert wizard."""
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


# Subject-name markers we treat as "PCE-owned" certificates. mitmproxy
# installs a CA whose CN contains ``mitmproxy`` — that's the only identity
# we manage. This keeps the wizard from touching unrelated root CAs.
MITMPROXY_SUBJECT_MARKERS: tuple[str, ...] = ("mitmproxy",)


@dataclass
class CertInfo:
    """Summary of a PCE-managed CA entry as seen in the host trust store."""

    subject: str
    issuer: str
    thumbprint_sha1: str
    # Platform-specific trust-store identifier. On Windows this is the
    # thumbprint; on macOS it's the SHA-1 hash of the DER encoding; on
    # Linux it's the installed filename under /usr/local/share/ca-certificates.
    store_id: str
    platform: Platform
    source_path: Optional[str] = None   # where on disk the cert came from
    not_after_iso: Optional[str] = None


@dataclass
class CertOpResult:
    """Result of a cert-wizard mutation (install/uninstall/regen/export).

    ``ok`` is ``True`` only if the operation *actually* completed. When
    ``needs_elevation`` is set, ``ok`` is always ``False`` and
    ``elevated_cmd`` holds the command the caller should run with admin
    rights. ``dry_run`` is True when the caller asked for a preview and
    no state was changed.
    """

    ok: bool
    message: str
    platform: Platform
    needs_elevation: bool = False
    elevated_cmd: Optional[List[str]] = None
    dry_run: bool = False
    # Populated on success where applicable:
    thumbprint_sha1: Optional[str] = None
    store_id: Optional[str] = None
    output_path: Optional[str] = None
    # Raw stdout/stderr for diagnostics (truncated to avoid dumping megabytes).
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
            "thumbprint_sha1": self.thumbprint_sha1,
            "store_id": self.store_id,
            "output_path": self.output_path,
            "stdout": self.stdout[-4000:] if self.stdout else "",
            "stderr": self.stderr[-4000:] if self.stderr else "",
            "extra": self.extra,
        }
