#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * PCE MCP Node Proxy — .mcpb bundle entry point.
 *
 * Exposes the 6 PCE MCP tools (pce_capture, pce_query, pce_stats,
 * pce_sessions, pce_session_messages, pce_capture_pair) over the
 * Model Context Protocol stdio transport, and forwards each tool
 * call to the locally-running pce_core daemon over HTTP.
 *
 * See Docs/docs/engineering/adr/ADR-016-cdp-launcher-and-mcpb-packaging.md
 * §3.3 for why this proxy exists in Node instead of Python.
 *
 * Failure modes:
 *  - pce_core daemon unreachable → tool returns { isError: true } with a
 *    human-readable message; the MCP session stays alive.
 *  - Malformed args → tool returns { isError: true }.
 *  - Unknown tool name → same.
 *
 * The proxy never swallows stderr. pce_core errors are surfaced as-is.
 */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import {
  formatCapturesList,
  formatStats,
  formatSessions,
  formatMessages,
  formatPair,
} from "./formatters.js";

const DEFAULT_PCE_CORE_URL = "http://127.0.0.1:9800";
const PCE_CORE_URL = (process.env.PCE_CORE_URL || DEFAULT_PCE_CORE_URL).replace(
  /\/+$/,
  "",
);
const USER_AGENT = `pce-mcp.mcpb/${process.env.npm_package_version || "0.1.0"}`;

// ---------------------------------------------------------------------------
// Tool definitions — mirrors pce_mcp/server.py signatures.
// ---------------------------------------------------------------------------

const TOOLS = [
  {
    name: "pce_capture",
    description:
      "Record an AI interaction into the local PCE database. Provide either conversation_json (full conversation) or request_body/response_body (paired request/response).",
    inputSchema: {
      type: "object",
      properties: {
        provider: {
          type: "string",
          description:
            "AI provider name (e.g. 'openai', 'anthropic', 'google').",
        },
        direction: {
          type: "string",
          enum: ["request", "response", "conversation"],
          default: "conversation",
        },
        host: { type: "string", default: "" },
        path: { type: "string", default: "" },
        method: { type: "string", default: "POST" },
        model_name: { type: "string", default: "" },
        request_body: { type: "string", default: "" },
        response_body: { type: "string", default: "" },
        conversation_json: { type: "string", default: "" },
        status_code: { type: ["integer", "null"], default: null },
        latency_ms: { type: ["number", "null"], default: null },
        meta: { type: ["object", "null"], default: null },
      },
      required: ["provider"],
    },
  },
  {
    name: "pce_query",
    description:
      "Query recent AI interaction captures from the local PCE database.",
    inputSchema: {
      type: "object",
      properties: {
        last: { type: "integer", default: 10, minimum: 1, maximum: 100 },
        provider: { type: ["string", "null"], default: null },
        host: { type: ["string", "null"], default: null },
        direction: { type: ["string", "null"], default: null },
      },
    },
  },
  {
    name: "pce_stats",
    description:
      "Get summary statistics from the local PCE database: total captures, by provider, by direction, by source.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "pce_sessions",
    description:
      "List normalized conversation sessions from the local PCE database.",
    inputSchema: {
      type: "object",
      properties: {
        last: { type: "integer", default: 10, minimum: 1, maximum: 50 },
        provider: { type: ["string", "null"], default: null },
      },
    },
  },
  {
    name: "pce_session_messages",
    description: "Get all messages from a specific PCE conversation session.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: {
          type: "string",
          description: "Full session ID or prefix (at least 8 chars).",
        },
      },
      required: ["session_id"],
    },
  },
  {
    name: "pce_capture_pair",
    description:
      "Get the full request/response pair for a given pair_id from the PCE database.",
    inputSchema: {
      type: "object",
      properties: {
        pair_id: {
          type: "string",
          description: "The pair ID linking a request and response capture.",
        },
      },
      required: ["pair_id"],
    },
  },
];

// ---------------------------------------------------------------------------
// HTTP client — talks to pce_core daemon.
// ---------------------------------------------------------------------------

/**
 * Issue a JSON HTTP request against pce_core.
 * Throws on non-2xx with a human-readable message. Caller is responsible
 * for surfacing the error through the MCP tool response.
 */
async function callPceCore(method, path, bodyObj) {
  const url = PCE_CORE_URL + path;
  const init = {
    method,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "User-Agent": USER_AGENT,
    },
  };
  if (bodyObj != null) {
    init.body = JSON.stringify(bodyObj);
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    throw new Error(
      `Cannot reach pce_core at ${PCE_CORE_URL}. Is the daemon running? ` +
        `(${err.message || err})`,
    );
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    throw new Error(
      `pce_core ${method} ${path} → HTTP ${res.status} ${res.statusText}. ${detail}`.trim(),
    );
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return await res.json();
  }
  return await res.text();
}

/** Build a URLSearchParams, skipping null/undefined/empty-string values. */
function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === null || v === undefined || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Tool dispatch.
// ---------------------------------------------------------------------------

async function handlePceCapture(args) {
  // Server-side mirror of pce_mcp/server.py:pce_capture — a dedicated
  // endpoint keeps the Node proxy a thin shim (see ADR-016 §3.3).
  const payload = {
    provider: args.provider,
    direction: args.direction || "conversation",
    host: args.host || "",
    path: args.path || "",
    method: args.method || "POST",
    model_name: args.model_name || "",
    request_body: args.request_body || "",
    response_body: args.response_body || "",
    conversation_json: args.conversation_json || "",
    status_code: args.status_code ?? null,
    latency_ms: args.latency_ms ?? null,
    meta: args.meta ?? null,
  };
  const result = await callPceCore("POST", "/api/v1/mcp/capture", payload);
  if (!result.ok) {
    throw new Error(result.error || "capture failed");
  }
  const parts = [];
  if (result.pair_id) parts.push(`pair_id=${result.pair_id}`);
  if (result.summary) parts.push(result.summary);
  return parts.join(". ") || "OK";
}

async function handlePceQuery(args) {
  const rows = await callPceCore(
    "GET",
    `/api/v1/captures${qs({
      last: args.last ?? 10,
      provider: args.provider,
      host: args.host,
      direction: args.direction,
    })}`,
  );
  return formatCapturesList(rows);
}

async function handlePceStats() {
  const stats = await callPceCore("GET", "/api/v1/stats");
  return formatStats(stats);
}

async function handlePceSessions(args) {
  const rows = await callPceCore(
    "GET",
    `/api/v1/sessions${qs({
      last: args.last ?? 10,
      provider: args.provider,
    })}`,
  );
  return formatSessions(rows);
}

async function handlePceSessionMessages(args) {
  if (!args.session_id) {
    throw new Error("session_id is required");
  }
  const msgs = await callPceCore(
    "GET",
    `/api/v1/sessions/${encodeURIComponent(args.session_id)}/messages`,
  );
  return formatMessages(args.session_id, msgs);
}

async function handlePceCapturePair(args) {
  if (!args.pair_id) {
    throw new Error("pair_id is required");
  }
  const rows = await callPceCore(
    "GET",
    `/api/v1/captures/pair/${encodeURIComponent(args.pair_id)}`,
  );
  return formatPair(args.pair_id, rows);
}

const HANDLERS = {
  pce_capture: handlePceCapture,
  pce_query: handlePceQuery,
  pce_stats: handlePceStats,
  pce_sessions: handlePceSessions,
  pce_session_messages: handlePceSessionMessages,
  pce_capture_pair: handlePceCapturePair,
};

// ---------------------------------------------------------------------------
// MCP server wiring.
// ---------------------------------------------------------------------------

const server = new Server(
  {
    name: "pce-mcp",
    version: "0.1.0",
  },
  {
    capabilities: { tools: {} },
  },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS,
}));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args } = req.params;
  const handler = HANDLERS[name];
  if (!handler) {
    return {
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
      isError: true,
    };
  }
  try {
    const text = await handler(args || {});
    return { content: [{ type: "text", text }] };
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    return {
      content: [{ type: "text", text: `Error: ${msg}` }],
      isError: true,
    };
  }
});

// ---------------------------------------------------------------------------
// Bootstrap.
// ---------------------------------------------------------------------------

async function main() {
  process.stderr.write(
    `pce-mcp.mcpb: starting Node MCP proxy (pce_core_url=${PCE_CORE_URL})\n`,
  );
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  process.stderr.write(`pce-mcp.mcpb: fatal ${err && err.stack ? err.stack : err}\n`);
  process.exit(1);
});

export { TOOLS, HANDLERS, PCE_CORE_URL };
