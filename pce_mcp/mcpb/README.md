# `pce_mcp/mcpb/` — Claude Desktop one-click bundle for PCE MCP

This directory is the **source tree of the `.mcpb` Desktop Extension**
that ships `pce_mcp` into Claude Desktop (and any other host that
implements the [MCPB spec](https://github.com/anthropics/mcpb)).

> **Why a Node bundle when `pce_mcp/` is Python?** Claude Desktop ships
> its own bundled Node runtime but **not** Python — packaging as Node
> gives every user a zero-dependency one-click install. The Node proxy
> here speaks MCP over stdio to Claude Desktop and turns each tool call
> into a local HTTP request against the `pce_core` daemon
> (`127.0.0.1:9800` by default). The Python implementation in
> `pce_mcp/server.py` is preserved as an advanced-user path.
>
> See [ADR-016 §3.3](../../Docs/docs/engineering/adr/ADR-016-cdp-launcher-and-mcpb-packaging.md)
> for the full rationale.

## Layout

```
pce_mcp/mcpb/
├── manifest.json             ← .mcpb spec 0.2 manifest
├── package.json              ← npm dependencies
├── icon.png                  ← bundle icon (128×128)
├── server/
│   ├── index.js              ← Node MCP stdio server — tool dispatch
│   └── formatters.js         ← pure formatting helpers (mirror of Python)
├── README.md                 ← this file
└── .gitignore                ← excludes node_modules/ and *.mcpb
```

## How the bundle talks to `pce_core`

```
┌─────────────────────────────┐
│ Claude Desktop (host)       │
│        │ stdio MCP          │
│        ▼                    │
│  pce-mcp.mcpb (Node proxy)  │   ← this bundle
│        │ HTTP               │
│        ▼                    │
│  pce_core (Python daemon)   │   ← already running on 127.0.0.1:9800
│        │                    │
│        ▼                    │
│  local SQLite               │
└─────────────────────────────┘
```

The six MCP tools (`pce_capture`, `pce_query`, `pce_stats`,
`pce_sessions`, `pce_session_messages`, `pce_capture_pair`) are
defined with identical signatures to the Python implementation. Tool
calls are translated into REST requests against the `pce_core`
endpoints documented in [`pce_core/server.py`](../../pce_core/server.py).

## Build & pack

Requires Node 18+ and the official `@anthropic-ai/mcpb` CLI.

```powershell
# From the repo root:
pwsh -File scripts/build_mcpb.ps1
# produces pce_mcp/mcpb/pack-output/pce-mcp-<version>.mcpb
```

The build script:
1. Runs `npm install --production` inside `pce_mcp/mcpb/`.
2. Invokes `mcpb pack` to zip the directory into a valid
   `.mcpb` archive with an embedded manifest signature.
3. Emits the artifact to `pack-output/`.

## Install paths

**1. Self-hosted (v1.1 shipping method)**

Attach the produced `.mcpb` file to each git tag on the GitHub release
page. Users install via *Claude Desktop → Settings → Extensions →
Advanced settings → "Install Extension…"* and pick the downloaded
file.

**2. Anthropic directory submission (nice-to-have)**

Submit via the [Desktop Extensions interest form](https://docs.google.com/forms/d/14_Dmcig4z8NeRMB_e7TOyrKzuZ88-BLYdLvS6LPhiZU/viewform).
If accepted, users get one-click search-and-install from the in-app
directory, plus automatic updates.

## Local development

```powershell
# From pce_mcp/mcpb/
npm install
node server/index.js           # drops into stdio MCP mode
```

To hand-test the proxy without a real MCP client, pipe a JSON-RPC frame
on stdin:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | node server/index.js
```

You should see a `tools/list` response containing the six PCE tools.

## Testing

Python-side tests (validate the manifest and drive the Node proxy as a
subprocess) live under `tests/e2e_mcpb/`. Run:

```powershell
python -m pytest tests/e2e_mcpb/ -v
```

## User configuration

The manifest declares one `user_config` field:

| Key            | Type   | Default                   | Meaning                                            |
| -------------- | ------ | ------------------------- | -------------------------------------------------- |
| `pce_core_url` | string | `http://127.0.0.1:9800`   | Where the `pce_core` daemon is reachable.          |

`pce_core_url` is wired into the bundle as `PCE_CORE_URL` env var via
`${user_config.pce_core_url}` in `manifest.json#server.mcp_config.env`.

## Privacy posture

This bundle only makes **local HTTP requests** on the loopback
interface. It does **not** connect to any remote server. **The five
statements below are the canonical privacy posture for this
extension** and the manifest's `privacy_policies` array points at
this section's URL anchor as required by `.mcpb` spec ≥0.2 (which
mandates URLs for that field). Keep the two in sync with
[`Docs/docs/engineering/adr/ADR-016`](../../Docs/docs/engineering/adr/ADR-016-cdp-launcher-and-mcpb-packaging.md)
§3.3 and the `pce_core` retention policy.

1. **What is captured**: the content of your AI conversations
   (prompts, responses, tool calls) gets written into a local SQLite
   database at a platform-default PCE data directory:
   - Windows: `%LOCALAPPDATA%\PCE`
   - macOS: `~/Library/Application Support/PCE`
   - Linux: `~/.local/share/PCE`
2. **Where it stays**: all data is stored on your computer. Nothing is
   transmitted to PCE developers, Anthropic, or any third party by
   this extension.
3. **How it talks to PCE Core**: the extension communicates exclusively
   with a locally-running `pce_core` daemon over HTTP on `127.0.0.1`
   (default port `9800`, configurable via the `pce_core_url` user
   setting). No external network connections are made.
4. **How to delete**: stop the `pce_core` daemon and remove the PCE
   data directory listed above. You may also use `pce_core`'s built-in
   retention sweep endpoint.
5. **Source code**: Apache-2.0, published at
   <https://github.com/zstnbb/PCE-Core> — auditable end-to-end.

## License

Apache-2.0. Bundled files inherit the repo-wide SPDX header.
