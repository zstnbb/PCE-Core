# SPDX-License-Identifier: Apache-2.0
"""PCE Test Conductor — cross-lane test orchestration & MCP agent contract.

Authority: ``Docs/docs/engineering/adr/ADR-017-test-conductor-cross-lane-agent-contract.md``
(status: Adopted as of P5.C.2, 2026-05-12).

Three-layer architecture (per ADR-017 §3.1):

    Tier A — Agent Surface (this package, OSS)
        8 MCP tools  /  FailureKind enum  /  canary store  /  patch templates
              │ MCP stdio JSON-RPC 2.0
              ▼
    Tier B — Lane Execution (existing, untouched)
        Browser:  pce_probe/  +  tests/e2e_probe/
        Desktop:  tests/e2e_desktop_ui/
        CLI:      pce_cli_wrapper/  +  tests/e2e_cli/
        MCP:      pce_mcp_proxy/    +  tests/e2e_mcp/
              │
              ▼
    Tier C — Shared verification (existing, untouched)
        tests/e2e/capture_verifier.py  +  pce_core/health.py

Conductor never rewrites any existing case or adapter — it is an
**additive orchestration layer**. It calls pytest via subprocess +
collects ``summary.json``-style output + classifies failures + suggests
patches as data (per ADR-019 §3.1 contract D — patches-as-data, not
action).

Public modules:

- ``manifest``   — load ``targets/<id>.yaml`` files, expose ``Target`` dataclass
- ``classifier`` — ``FailureKind`` 9-value enum + ``classify_run`` heuristic
- ``runner``     — ``run_case`` pytest subprocess wrapper + ``RunRecord``
- ``canary``     — JSON Schema infer + diff (severity ladder)
- ``replay``     — fixture playback (P5.C.2 stub; full impl P5.C.4)
- ``patches``    — 3 patch templates (data only, never applied)
- ``server``     — FastMCP server registering the 8 tools

Entry points:

- ``python -m pce_test_conductor``                # stdio MCP server
- ``python -m pce_test_conductor --sse``          # SSE MCP server (dev)
- ``python -m pce_test_conductor --list-targets`` # CLI introspection
"""

__version__ = "0.2.0"

# Re-export the most common dataclasses for ergonomics in tests
# and embedding scripts. The 8 MCP tool entry points live in
# ``server.py`` and are not re-exported here — callers should drive
# via MCP, not via direct Python import.

from .manifest import Target, load_manifest, list_target_ids  # noqa: F401
from .classifier import FailureKind, FailureRecord, classify_run  # noqa: F401
from .runner import RunRecord, RunStatus  # noqa: F401

__all__ = [
    "__version__",
    "Target",
    "load_manifest",
    "list_target_ids",
    "FailureKind",
    "FailureRecord",
    "classify_run",
    "RunRecord",
    "RunStatus",
]
