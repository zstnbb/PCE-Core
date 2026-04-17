"""PCE Proxy Toggle — cross-platform system proxy on/off switch.

Wraps Windows registry / ``networksetup(8)`` / ``gsettings(1)`` behind one
API. Shares the "no silent elevation, dry-run everywhere, injectable
backend" contract with :mod:`pce_core.cert_wizard`.

Public API::

    from pce_core.proxy_toggle import (
        enable_system_proxy, disable_system_proxy, get_proxy_state,
        ProxyState, ProxyOpResult, Platform,
    )
"""

from __future__ import annotations

from .models import (
    DEFAULT_BYPASS,
    Platform,
    ProxyOpResult,
    ProxyState,
)
from .manager import (
    ProxyManager,
    detect_platform,
    disable_system_proxy,
    enable_system_proxy,
    get_proxy_state,
)

__all__ = [
    "DEFAULT_BYPASS",
    "Platform",
    "ProxyManager",
    "ProxyOpResult",
    "ProxyState",
    "detect_platform",
    "disable_system_proxy",
    "enable_system_proxy",
    "get_proxy_state",
]
