// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — extension-side WebSocket client + verb dispatcher.
 *
 * Tries to connect to ``ws://127.0.0.1:9888`` (or ``PROBE_STORAGE_KEY_WS_URL``
 * override). When connected, exposes the verbs declared in
 * ``utils/probe-protocol.ts``. When not connected (server not running),
 * silently retries with exponential backoff. End users with no probe
 * server running pay zero cost beyond a few periodic socket open
 * attempts (which fail instantly on a closed port).
 *
 * The dispatcher validates incoming envelopes, routes ``verb`` to the
 * right handler module, applies a per-request timeout, and serializes
 * any handler exception into a structured ``ProbeErrorResponse``.
 *
 * See ``Docs/docs/engineering/PCE-PROBE-API.md`` for the full contract.
 */

import {
  PROBE_DEFAULT_WS_URL,
  PROBE_SCHEMA_VERSION,
  PROBE_STORAGE_KEY_ENABLED,
  PROBE_STORAGE_KEY_WS_URL,
  type ProbeError,
  type ProbeErrorCode,
  type ProbeHelloFrame,
  type ProbeRequest,
  type ProbeResponse,
  type ProbeVerb,
} from "../../utils/probe-protocol";

// ---------------------------------------------------------------------------
// Shared verb-handler primitives. Lived in this file historically; moved
// to ``probe-rpc-shared.ts`` to break the cycle between this dispatcher
// and the five ``probe-rpc-*.ts`` verb modules. Re-exported here so the
// public surface (``import { ProbeException } from "./probe-rpc"``) is
// preserved for vitest unit tests and any external consumers. New code
// should import from ``./probe-rpc-shared`` directly.
// See ``probe-rpc-shared.ts`` and PCE-PROBE-API.md §10 for context.
// ---------------------------------------------------------------------------
import {
  ProbeException,
  getExtensionVersion,
  type VerbHandler,
} from "./probe-rpc-shared";
export { ProbeException, getExtensionVersion } from "./probe-rpc-shared";
export type { VerbHandler } from "./probe-rpc-shared";

import { captureVerbs } from "./probe-rpc-capture";
import { domVerbs } from "./probe-rpc-dom";
import { pageVerbs } from "./probe-rpc-page";
import { systemVerbs } from "./probe-rpc-system";
import { tabVerbs } from "./probe-rpc-tab";

// ---------------------------------------------------------------------------
// Module-private state
// ---------------------------------------------------------------------------

const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
const KEEPALIVE_ALARM_NAME = "pce-probe-keepalive";
const KEEPALIVE_PERIOD_MIN = 0.5; // 30 s — under MV3's idle ceiling

let ws: WebSocket | null = null;
let backoffMs = RECONNECT_MIN_MS;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let started = false;

// ---------------------------------------------------------------------------
// Verb registry
// ---------------------------------------------------------------------------

const verbs: Record<string, VerbHandler> = {
  ...systemVerbs,
  ...tabVerbs,
  ...domVerbs,
  ...pageVerbs,
  ...captureVerbs,
};

const knownVerbs: ProbeVerb[] = Object.keys(verbs) as ProbeVerb[];

// ---------------------------------------------------------------------------
// Manifest version helper (used in hello + system.* verbs)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------

async function getProbeUrl(): Promise<string> {
  return new Promise((resolve) => {
    chrome.storage.local.get([PROBE_STORAGE_KEY_WS_URL], (got) => {
      const stored = got[PROBE_STORAGE_KEY_WS_URL];
      if (typeof stored === "string" && stored.startsWith("ws://")) {
        resolve(stored);
      } else {
        resolve(PROBE_DEFAULT_WS_URL);
      }
    });
  });
}

async function isProbeEnabled(): Promise<boolean> {
  return new Promise((resolve) => {
    chrome.storage.local.get([PROBE_STORAGE_KEY_ENABLED], (got) => {
      resolve(got[PROBE_STORAGE_KEY_ENABLED] !== false);
    });
  });
}

function scheduleReconnect(): void {
  if (reconnectTimer !== null) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connect();
  }, backoffMs);
  backoffMs = Math.min(backoffMs * 2, RECONNECT_MAX_MS);
}

async function connect(): Promise<void> {
  if (!(await isProbeEnabled())) {
    // Probe explicitly disabled. Don't even try.
    return;
  }
  if (ws && ws.readyState === WebSocket.OPEN) return;

  const url = await getProbeUrl();
  let socket: WebSocket;
  try {
    socket = new WebSocket(url);
  } catch (err) {
    console.debug(
      `[PCE Probe] WS construction failed: ${(err as Error).message}; ` +
        `retrying in ${backoffMs}ms`,
    );
    scheduleReconnect();
    return;
  }

  ws = socket;

  socket.addEventListener("open", () => {
    backoffMs = RECONNECT_MIN_MS;
    const hello: ProbeHelloFrame = {
      v: PROBE_SCHEMA_VERSION,
      hello: {
        extension_version: getExtensionVersion(),
        schema_version: PROBE_SCHEMA_VERSION,
        capabilities: knownVerbs,
        user_agent:
          typeof navigator !== "undefined" ? navigator.userAgent : undefined,
      },
    };
    try {
      socket.send(JSON.stringify(hello));
      console.log(
        `[PCE Probe] connected to ${url} ` +
          `(${knownVerbs.length} verbs registered)`,
      );
    } catch (err) {
      console.warn(
        `[PCE Probe] failed to send hello: ${(err as Error).message}`,
      );
    }
  });

  socket.addEventListener("message", (ev) => {
    const data = typeof ev.data === "string" ? ev.data : "";
    void handleFrame(socket, data);
  });

  socket.addEventListener("close", () => {
    if (ws === socket) ws = null;
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    // Don't log noisily — the WebSocket emits an error on every failed
    // connection attempt when nothing is listening, which is the
    // baseline state. ``close`` will fire right after and trigger the
    // backoff retry.
  });
}

// ---------------------------------------------------------------------------
// Frame dispatch
// ---------------------------------------------------------------------------

interface AnyParsedFrame {
  v?: unknown;
  id?: unknown;
  verb?: unknown;
  params?: unknown;
  timeout_ms?: unknown;
  ok?: unknown;
  hello?: unknown;
}

async function handleFrame(socket: WebSocket, raw: string): Promise<void> {
  let parsed: AnyParsedFrame;
  try {
    parsed = JSON.parse(raw) as AnyParsedFrame;
  } catch {
    console.warn("[PCE Probe] ignoring non-JSON frame");
    return;
  }

  // Tolerate hello-ack frames silently — the server may send one after
  // our hello to acknowledge supported versions.
  // Also tolerate application-layer heartbeats (``{v, heartbeat: true}``):
  // the SERVER pings every 20 s so the SW's ``onmessage`` event fires,
  // resetting MV3's 30 s idle timer. We do nothing with the frame itself —
  // the act of receiving it is the keepalive.
  if (
    "hello" in parsed ||
    "heartbeat" in parsed ||
    (parsed.ok === true && parsed.id === undefined)
  ) {
    return;
  }

  if (parsed.v !== PROBE_SCHEMA_VERSION) {
    sendErrorResponse(
      socket,
      typeof parsed.id === "string" ? parsed.id : "unknown",
      Date.now(),
      "schema_mismatch",
      `extension speaks v${PROBE_SCHEMA_VERSION}, frame says v=${String(parsed.v)}`,
    );
    return;
  }

  if (
    typeof parsed.id !== "string" ||
    typeof parsed.verb !== "string" ||
    typeof parsed.params !== "object" ||
    parsed.params === null
  ) {
    sendErrorResponse(
      socket,
      typeof parsed.id === "string" ? parsed.id : "unknown",
      Date.now(),
      "params_invalid",
      "envelope missing required fields (id/verb/params)",
    );
    return;
  }

  const req = parsed as unknown as ProbeRequest;
  const start = Date.now();
  const handler = verbs[req.verb];

  if (!handler) {
    sendErrorResponse(
      socket,
      req.id,
      start,
      "unknown_verb",
      `verb '${req.verb}' is not registered`,
      { agent_hint: `extension supports: ${knownVerbs.join(", ")}` },
    );
    return;
  }

  const timeoutMs = typeof req.timeout_ms === "number" ? req.timeout_ms : 30_000;
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);

  try {
    const result = await handler(req.params, ac.signal);
    clearTimeout(timer);
    const response: ProbeResponse = {
      v: PROBE_SCHEMA_VERSION,
      id: req.id,
      ok: true,
      result,
      elapsed_ms: Date.now() - start,
    };
    safeSend(socket, response);
  } catch (err) {
    clearTimeout(timer);
    if (ac.signal.aborted) {
      sendErrorResponse(socket, req.id, start, "timeout", `verb '${req.verb}' exceeded ${timeoutMs}ms`);
      return;
    }
    if (err instanceof ProbeException) {
      sendErrorResponse(socket, req.id, start, err.code, err.message, {
        context: err.context,
        agent_hint: err.agentHint,
      });
      return;
    }
    const e = err as Error;
    sendErrorResponse(socket, req.id, start, "extension_internal", e.message, {
      context: { stack: e.stack },
      agent_hint: "Probably a bug in the probe-rpc handler. Please file with the stack.",
    });
  }
}

function sendErrorResponse(
  socket: WebSocket,
  id: string,
  start: number,
  code: ProbeErrorCode,
  message: string,
  extras?: { context?: ProbeError["context"]; agent_hint?: string },
): void {
  const response: ProbeResponse = {
    v: PROBE_SCHEMA_VERSION,
    id,
    ok: false,
    error: {
      code,
      message,
      context: extras?.context,
      agent_hint: extras?.agent_hint,
    },
    elapsed_ms: Date.now() - start,
  };
  safeSend(socket, response);
}

function safeSend(socket: WebSocket, response: ProbeResponse): void {
  try {
    socket.send(JSON.stringify(response));
  } catch (err) {
    console.warn(
      `[PCE Probe] failed to send response: ${(err as Error).message}`,
    );
  }
}

// ---------------------------------------------------------------------------
// MV3 service-worker keepalive
// ---------------------------------------------------------------------------

function ensureKeepaliveAlarm(): void {
  // chrome.alarms is only present in extension contexts; guard so
  // unit tests don't blow up.
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.get(KEEPALIVE_ALARM_NAME, (existing) => {
    if (!existing) {
      chrome.alarms.create(KEEPALIVE_ALARM_NAME, {
        periodInMinutes: KEEPALIVE_PERIOD_MIN,
      });
    }
  });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name !== KEEPALIVE_ALARM_NAME) return;
    // No-op on purpose. The alarm firing is what resets the SW idle
    // timer; we just need to be subscribed.
    if (ws && ws.readyState !== WebSocket.OPEN) {
      void connect();
    }
  });
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Boot the probe RPC channel. Idempotent — calling twice is a no-op.
 *
 * Called once from ``background.ts`` ``main()``.
 */
export function startProbeRpc(): void {
  if (started) return;
  started = true;
  ensureKeepaliveAlarm();
  void connect();
}

/**
 * For tests + diagnostics. Exposed via ``window.__pce_probe_state`` in
 * vitest harnesses.
 */
export const __probeTesting = {
  isConnected(): boolean {
    return ws !== null && ws.readyState === WebSocket.OPEN;
  },
  knownVerbs,
  reset(): void {
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    ws = null;
    backoffMs = RECONNECT_MIN_MS;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    started = false;
  },
};
