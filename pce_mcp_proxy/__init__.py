# SPDX-License-Identifier: Apache-2.0
"""PCE MCP middleware proxy (capture posture B / UCS layer L3f).

A transparent stdio JSON-RPC 2.0 forwarder that sits between an MCP
host (Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI /
Gemini CLI / Cascade-Windsurf) and an upstream MCP server (filesystem,
git, postgres, sequential-thinking, …), copying every frame into the
PCE capture pipeline as it passes through.

Boundary contract — what this package MUST do and MUST NOT do:

- MUST forward host ↔ upstream bytes losslessly. The relay is on the
  protocol hot path; any modification of bytes (re-encoding, header
  injection, JSON re-serialisation) breaks compatibility with strict
  MCP hosts. We forward raw bytes and observe a copy.

- MUST keep the proxy's own diagnostics off stdout. stdout is reserved
  for the upstream server's MCP frames; anything we accidentally write
  there will be parsed by the host as JSON-RPC and break the session.
  Logs go to stderr (which is forwarded to the host's stderr channel
  for visibility) and to ``PCE_DATA_DIR/mcp_proxy.log`` when
  configured.

- MUST treat capture failures as soft. If the SQLite write fails or
  the JSON parse blows up, the relay keeps forwarding. The product
  guarantee is "the host's MCP session never breaks because of PCE."

- MUST be cross-platform. Windows uses Proactor event loop which does
  not support ``loop.connect_read_pipe(sys.stdin)``, so the relay is
  thread-based rather than asyncio-based.

- MUST NOT pin to FastMCP / mcp-python-sdk. We are protocol-level
  middleware, not an SDK consumer; we should not break when MCP host
  vendors release new protocol revisions ahead of the SDK.

OSS classification: this package is Apache-2.0 (Open Core OSS) per
ADR-013, sharing the same boundary as ``pce_mcp/``: tools that
capture user-visible AI traffic sit in OSS so adoption isn't gated by
a paywall. Closed-source value-add layers (fleet ops, multi-tenant
team consoles, etc.) sit in the Pro tier above this package, never
inside it.

Cross-references:

- ``Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`` §4.1 +
  §5.2 — posture B architecture and source_type rationale.
- ``Docs/install/PCE_MCP_PROXY_INSTALL.md`` — per-host install guide.
- ``pce_core/migrations/0009_mcp_proxy_source.py`` — registers the
  ``mcp-proxy-default`` source row this package writes against.
- ``pce_core/normalizer/mcp_jsonrpc.py`` — turns paired raw JSON-RPC
  frames into normalised session+message rows.
- ``tests/e2e_mcp/test_pce_mcp_proxy_stdio.py`` — end-to-end relay
  + observer behaviour tests.
"""

__version__ = "0.1.0"
