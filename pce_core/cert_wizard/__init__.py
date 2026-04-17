"""PCE Cert Wizard — cross-platform CA certificate management.

Wraps platform-native trust store tools behind one API so the Tauri shell,
HTTP API and CLI all use the same code path. Designed for the P2
"抓层工业化 + UX" phase (TASK-004 §5.2).

Key guardrails:

- **No silent elevation.** If an operation requires admin / root, we return
  a :class:`CertOpResult` with ``needs_elevation=True`` and the command
  string the caller should run. We never invoke ``sudo`` / ``runas`` /
  UAC from inside Python.
- **Dry-run everywhere.** Every mutation supports ``dry_run=True`` so the
  UI can preview the exact command and tests never touch the real trust
  store.
- **Platform dispatch inside the module.** Callers pass a :class:`Platform`
  override (used by tests); default is :func:`detect_platform`.
- **Identity via subject CN.** PCE only manages certificates whose subject
  matches :data:`MITMPROXY_SUBJECT_MARKERS`; we never enumerate or delete
  arbitrary trust anchors.

Public API::

    from pce_core.cert_wizard import (
        install_ca, uninstall_ca, export_ca, regenerate_ca, list_ca,
        default_ca_path, CertOpResult, CertInfo, Platform,
    )
"""

from __future__ import annotations

from .models import CertInfo, CertOpResult, Platform, MITMPROXY_SUBJECT_MARKERS
from .manager import (
    CertManager,
    default_ca_path,
    detect_platform,
    export_ca,
    install_ca,
    list_ca,
    regenerate_ca,
    uninstall_ca,
)

__all__ = [
    "CertInfo",
    "CertManager",
    "CertOpResult",
    "MITMPROXY_SUBJECT_MARKERS",
    "Platform",
    "default_ca_path",
    "detect_platform",
    "export_ca",
    "install_ca",
    "list_ca",
    "regenerate_ca",
    "uninstall_ca",
]
