# SPDX-License-Identifier: Apache-2.0
"""PCE Network Environment Detector — VPN / upstream-proxy awareness.

PCE depends on the system-proxy slot (mitmproxy at 127.0.0.1:8080) being
free. VPN-style tools that ALSO claim the system proxy slot
(Clash / V2Ray / Mihomo / Shadowsocks / Surge / etc.) would silently
divert most of the user's traffic AROUND PCE unless we chain mitmproxy
upstream into them.

This module is the **detector half** — it observes the local network
environment and recommends an adaptation. The **applier half** lives in
:mod:`pce_app.service_manager` (mitmproxy startup) and the **UI half**
in :mod:`pce_app.control_panel` (``NetworkEnvPanel``).
"""

from .detector import (
    NetworkEnv,
    UpstreamCandidate,
    detect,
    upstream_arg_for_mitmproxy,
)

__all__ = [
    "NetworkEnv",
    "UpstreamCandidate",
    "detect",
    "upstream_arg_for_mitmproxy",
]
