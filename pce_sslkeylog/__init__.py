# SPDX-License-Identifier: Apache-2.0
"""pce_sslkeylog — A2 SSLKEYLOGFILE capture path (UCS L1-alt, V-GREEN-clean).

P5.D.1 Wave 2 (rewritten 2026-05-15 for Arch B). This package implements
the independent network-capture leg that replaces L1 MITM as the
primary V-GREEN-clean path for Chromium-based AI surfaces.

Architecture (Arch B — tshark wrap)::

    Daily Chrome / Electron AI app
       │ reads SSLKEYLOGFILE env var on startup
       │
       ├──→ normal TLS handshake ──→ server  (server sees a real Chrome
       │                                       client; NO mitmproxy
       │                                       fingerprint mismatch)
       │
       └──→ Chromium writes session keys to %LOCALAPPDATA%/pce/keylog.txt

    pce_sslkeylog.daemon
       │ subprocess: tshark -i any -f "host <allowed>" \\
       │                    -o tls.keylog_file:<path> -T ek
       │
       ├──→ parses NDJSON HTTP events out of tshark
       │
       └──→ pce_core.db.insert_capture(source_id='sslkeylog-default', ...)

Compliance properties (vs. L1 MITM, see REDUNDANCY-AUDIT-MATRIX.md §1.0):

- No CA injection (server's certificate trust chain unchanged)
- No TLS handshake interference (Chromium's native handshake reaches
  server without modification — JA3/JA4 indistinguishable from any
  other Chrome user)
- Out-of-band decryption (we observe ciphertext on the NIC and decrypt
  later with keys; the server never sees us)
- Failure-mode-independent from L1 MITM (mitmproxy crash / CA trust
  drift / system proxy off do NOT affect this path; conversely
  Npcap / SSLKEYLOGFILE env var dropping do NOT affect L1 MITM)

For the failure-mode independence proof + ladder semantics see
``Docs/stability/REDUNDANCY-AUDIT-MATRIX.md`` sections 1.0 + 1.2.1.
"""

__version__ = "0.1.0"
__all__ = [
    "TsharkRunner",
    "TsharkEvent",
    "parse_ek_line",
    "EkRecord",
    "build_capture_from_pair",
]

from .parser import EkRecord, TsharkEvent, build_capture_from_pair, parse_ek_line
from .tshark_wrap import TsharkRunner
