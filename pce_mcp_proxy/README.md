# `pce_mcp_proxy/` — MCP Middleware Proxy (姿态 B / UCS L3f)

> **Status**: P5.B.1 (this slot). Foundation for transparent MCP
> capture across all hosts that speak stdio JSON-RPC 2.0.
> **License**: Apache-2.0 (Open Core OSS) per
> [ADR-013](../Docs/docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md).

## What this is

A transparent stdio JSON-RPC 2.0 forwarder that sits **between** an
MCP host and an upstream MCP server, copying every frame into the
PCE capture pipeline as it passes through.

```
┌──────────────────┐  stdio  ┌──────────────┐  stdio  ┌─────────────┐
│ MCP host (Claude │ ──────► │ pce-mcp-     │ ──────► │ upstream    │
│ Desktop / Cursor │         │ proxy (L3f)  │         │ MCP server  │
│ / Windsurf / ... │ ◄────── │ + capture    │ ◄────── │ (fs/git/...)│
└──────────────────┘         └──────┬───────┘         └─────────────┘
                                    │
                                    ▼ side-channel write
                                ┌────────┐
                                │ SQLite │
                                └────────┘
```

The host and upstream **never know** the proxy is there. No protocol
modification, no header injection, no JSON re-serialisation —
forwarded bytes are byte-for-byte identical to what the host sent.

## Capture posture A vs B

| | **Posture A** (`pce_mcp/`) | **Posture B** (`pce_mcp_proxy/`, this) |
|---|---|---|
| **PCE acts as** | the MCP server itself | a wire-tap between host and a real upstream |
| **What the host sees** | a server with `pce_capture` / `pce_query` / … tools | the upstream's normal toolset, unchanged |
| **What gets captured** | only what the agent volunteers via tool calls | every JSON-RPC frame, transparently |
| **Trust model** | depends on the agent following its system prompt | independent of agent compliance |
| **Coverage** | only PCE-aware MCP sessions | every wrapped MCP server |
| **Source ID** | `mcp-default` | `mcp-proxy-default` |

The two postures are complementary, not exclusive. A power user can
configure both: run `pce_mcp` as one MCP server (posture A,
agent-cooperative ledger) AND wrap their other MCP servers with
`pce_mcp_proxy` (posture B, transparent wire capture).

## Quick start

```powershell
# Wrap the official filesystem server
python -m pce_mcp_proxy -- npx -y @modelcontextprotocol/server-filesystem C:\projects

# Wrap a Python MCP server with a friendly label
python -m pce_mcp_proxy --upstream-name git -- python -m mcp_git

# Print frame statistics on exit
python -m pce_mcp_proxy --print-stats -- npx -y @scope/server
```

The proxy speaks stdio, so the host config just needs to point at
the proxy instead of the upstream. Per-host snippets are in
[`Docs/install/PCE_MCP_PROXY_INSTALL.md`](../Docs/install/PCE_MCP_PROXY_INSTALL.md).

## What gets recorded

Each JSON-RPC frame becomes a row in `raw_captures` with
`source_id = 'mcp-proxy-default'`, `provider = 'mcp:<upstream-name>'`,
`method = 'JSONRPC'`, `host = 'stdio'`, `path = <jsonrpc-method>`.
Direction follows the wire:

| JSON-RPC frame type | `direction` | `status_code` | Notes |
|---|---|---|---|
| Request (host → upstream, has `id`) | `request` | `null` | Stashed and paired with response. |
| Response (upstream → host, has `id`) | `response` | `200` / `500` | Latency in `latency_ms`. Errors → 500. |
| Notification (host → upstream, no `id`) | `request` | `null` | One-shot, no pair. |
| Server-initiated (upstream → host, has `method`) | `response` | `null` | Sampling / roots. One-shot. |

`meta_json` always contains:

```json
{
  "upstream": "<name>",
  "kind": "request|notification|response|response_error|server_initiated|unclassified",
  "jsonrpc_method": "tools/call",
  "jsonrpc_id": 7
}
```

Plus `jsonrpc_error_code` / `jsonrpc_error_message` for errors.

## Architecture

Threading model. See `relay.py` docstring for the rationale (Windows
ProactorEventLoop doesn't support `connect_read_pipe(stdin)`).

```
sys.stdin  ──► [T1: host→upstream]  ──► proc.stdin
                       │
proc.stdout ──► [T2: upstream→host] ──► sys.stdout
                       │
                       ├─► observer_queue ──► [T4: observer thread]
                       │                         │
                       │                         ▼
                       │                   pce_core.db.insert_capture
                       │
proc.stderr ──► [T3: stderr passthrough] ──► sys.stderr
```

Forwarding (T1/T2) is decoupled from observation (T4) so a slow
`json.loads` over a multi-megabyte response cannot stall the wire.

## Boundary contract

This package **MUST**:

- Forward host ↔ upstream bytes losslessly (no re-encoding)
- Keep its own diagnostics off stdout (stdout is protocol-only)
- Treat capture failures as soft (DB down ≠ session broken)
- Work on Windows + Linux + macOS (threading, not asyncio stdio)

This package **MUST NOT**:

- Pin to FastMCP or `mcp-python-sdk` (we are protocol-level
  middleware, not an SDK consumer)
- Inject anything into the upstream's `tools/list` / `resources/list`
  responses
- Hold per-session state that survives across upstream restarts
  (the relay is fully ephemeral; SQLite is the durable layer)

## Files

- `__init__.py` — package boundary contract (read this first)
- `__main__.py` — CLI entry, `python -m pce_mcp_proxy`
- `config.py` — argv split, `RelayConfig`, name heuristics
- `capture.py` — `JsonRpcObserver` (threadsafe, `pending` map)
- `relay.py` — `Relay` class, threading topology, lifecycle

## Tests

End-to-end coverage at
[`tests/e2e_mcp/test_pce_mcp_proxy_stdio.py`](../tests/e2e_mcp/test_pce_mcp_proxy_stdio.py)
spawns the proxy as a subprocess against a mock upstream and against
the real `pce_mcp` server, verifying:

- Lossless forwarding for `initialize` / `tools/list` / `tools/call`
- Side-channel capture lands in `raw_captures` with the right
  `source_id`, direction, latency, and meta
- Upstream crash / proxy SIGINT / host stdin close → clean shutdown
- Notifications and errors are classified correctly

## Cross-references

- [`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`](../Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md)
  §4.1 + §5.2 — posture B architecture and `source_type = mcp_proxy`.
- [`Docs/install/PCE_MCP_PROXY_INSTALL.md`](../Docs/install/PCE_MCP_PROXY_INSTALL.md)
  — per-host install guide for all 8 P5.B target hosts.
- [`Docs/docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md`](../Docs/docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md)
  — OSS classification (covers both `pce_mcp/` and this package).
- [`Docs/docs/engineering/adr/ADR-015-ucs-l3f-mcp-middleware.md`](../Docs/docs/engineering/adr/ADR-015-ucs-l3f-mcp-middleware.md)
  — UCS amendment introducing L3f as a first-class layer.
- [`pce_core/migrations/0009_mcp_proxy_source.py`](../pce_core/migrations/0009_mcp_proxy_source.py)
  — registers the `mcp-proxy-default` source row.
- [`pce_core/normalizer/mcp_jsonrpc.py`](../pce_core/normalizer/mcp_jsonrpc.py)
  — Tier 0 → Tier 1 normaliser for paired JSON-RPC frames.
