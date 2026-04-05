"""mitmproxy entry point for PCE Proxy.

Usage:
    mitmdump -s run_proxy.py -p 8080
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so pce_proxy package is importable
_project_root = str(Path(__file__).resolve().parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from pce_proxy.addon import addons  # noqa: E402
