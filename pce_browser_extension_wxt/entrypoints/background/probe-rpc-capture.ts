// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — ``capture.*`` verb handlers + capture-pipeline observer.
 *
 * The agent uses these verbs to close the loop:
 *
 *   1. types a unique token into the AI tab,
 *   2. waits for the capture pipeline to flush a row whose body
 *      contains that token,
 *   3. then queries PCE Core's HTTP API to verify the row landed.
 *
 * To make step 2 work, ``background.ts`` calls
 * ``observeCaptureMessage`` on every incoming ``PCE_CAPTURE`` /
 * ``PCE_NETWORK_CAPTURE`` / ``PCE_SNIPPET`` message BEFORE forwarding
 * it to the ingest API. We never modify or block those messages — we
 * just keep a 100-entry ring buffer + check pending waiters.
 *
 * Pipeline state (``isEnabled``, ``captureCount``, ...) lives in
 * ``background.ts`` so we accept it via a single setter rather than
 * importing the global state directly (keeps the unit tests for this
 * module self-contained).
 */

import type {
  CaptureEventSummary,
  CapturePipelineStateResult,
  CaptureRecentEventsParams,
  CaptureRecentEventsResult,
  CaptureWaitForTokenParams,
  CaptureWaitForTokenResult,
} from "../../utils/probe-protocol";
import { ProbeException, type VerbHandler } from "./probe-rpc-shared";

// ---------------------------------------------------------------------------
// Module-private state
// ---------------------------------------------------------------------------

const RING_MAX = 100;
const recent: CaptureEventSummary[] = [];

interface PendingWaiter {
  token: string;
  provider?: string;
  resolve: (value: CaptureWaitForTokenResult) => void;
  reject: (err: Error) => void;
  /** Full body string captured at observation time, for substring match. */
  fullBody?: string;
}

const waiters = new Set<PendingWaiter>();

interface PipelineStateProvider {
  (): {
    enabled: boolean;
    capture_count: number;
    last_error: string | null;
    server_online: boolean;
    queued_captures: number;
  };
}

let pipelineStateProvider: PipelineStateProvider | null = null;

/**
 * Wire the pipeline-state getter from ``background.ts``. Called once
 * at boot; safe to call again to rebind in tests.
 */
export function setPipelineStateProvider(p: PipelineStateProvider): void {
  pipelineStateProvider = p;
}

// ---------------------------------------------------------------------------
// Observer (called from background.ts message router)
// ---------------------------------------------------------------------------

interface ObservableCaptureMessage {
  kind: "PCE_CAPTURE" | "PCE_NETWORK_CAPTURE" | "PCE_SNIPPET";
  payload: Record<string, unknown>;
}

/**
 * Convert any incoming capture message to a stringified body for
 * token matching. Returns the raw string we'll search for substrings.
 */
function extractMatchableBody(
  msg: ObservableCaptureMessage,
): { body: string; fingerprint: string } {
  const p = msg.payload;
  let body = "";
  if (msg.kind === "PCE_CAPTURE") {
    const conv = p.conversation as { messages?: Array<{ content?: unknown }> } | undefined;
    if (conv?.messages) {
      for (const m of conv.messages) {
        const c = m.content;
        if (typeof c === "string") body += c + "\n";
        else if (c) body += JSON.stringify(c) + "\n";
      }
    }
  } else if (msg.kind === "PCE_NETWORK_CAPTURE") {
    const req = (p.request_body ?? "") as string;
    const resp = (p.response_body ?? "") as string;
    body = `${req}\n---\n${resp}`;
  } else if (msg.kind === "PCE_SNIPPET") {
    body = (p.content_text ?? "") as string;
  }
  const fp = body.length > 200 ? body.slice(0, 200) + "..." : body;
  return { body, fingerprint: fp };
}

/**
 * Called by ``background.ts`` for every PCE_* capture message before
 * the dispatcher forwards it to the ingest API. Records a summary in
 * the ring buffer and resolves any pending waiter that matches.
 */
export function observeCaptureMessage(msg: ObservableCaptureMessage): void {
  const { body, fingerprint } = extractMatchableBody(msg);
  const summary: CaptureEventSummary = {
    ts: Date.now(),
    kind: msg.kind,
    provider: typeof msg.payload.provider === "string" ? msg.payload.provider : undefined,
    host: typeof msg.payload.host === "string" ? msg.payload.host : undefined,
    session_hint:
      typeof msg.payload.session_hint === "string"
        ? msg.payload.session_hint
        : null,
    fingerprint,
  };
  recent.push(summary);
  while (recent.length > RING_MAX) recent.shift();

  // Walk waiters; resolve those whose token appears in the body and
  // whose provider filter (if any) matches.
  if (waiters.size === 0) return;
  for (const w of [...waiters]) {
    if (w.provider && summary.provider !== w.provider) continue;
    if (body.indexOf(w.token) === -1) continue;
    waiters.delete(w);
    w.resolve({
      matched: true,
      ts: summary.ts,
      provider: summary.provider,
      host: summary.host,
      session_hint: summary.session_hint,
      fingerprint: summary.fingerprint,
    });
  }
}

// ---------------------------------------------------------------------------
// Verb implementations
// ---------------------------------------------------------------------------

function requireString(
  obj: Record<string, unknown>,
  field: string,
): string {
  const v = obj[field];
  if (typeof v !== "string" || v.length === 0) {
    throw new ProbeException("params_invalid", `field '${field}' must be a non-empty string`);
  }
  return v;
}

async function captureWaitForToken(
  rawParams: unknown,
  signal: AbortSignal,
): Promise<CaptureWaitForTokenResult> {
  const p = rawParams as Partial<CaptureWaitForTokenParams>;
  const token = requireString(p as Record<string, unknown>, "token");
  const timeoutMs = typeof p.timeout_ms === "number" ? p.timeout_ms : 60_000;
  const provider = typeof p.provider === "string" ? p.provider : undefined;

  // Fast path: token may already be in the ring buffer (rare but
  // possible on retries).
  for (let i = recent.length - 1; i >= 0; i--) {
    const ev = recent[i];
    if (provider && ev.provider !== provider) continue;
    if ((ev.fingerprint ?? "").indexOf(token) !== -1) {
      return {
        matched: true,
        ts: ev.ts,
        provider: ev.provider,
        host: ev.host,
        session_hint: ev.session_hint,
        fingerprint: ev.fingerprint,
      };
    }
  }

  return new Promise<CaptureWaitForTokenResult>((resolve, reject) => {
    const waiter: PendingWaiter = {
      token,
      provider,
      resolve,
      reject,
    };
    waiters.add(waiter);

    const timer = setTimeout(() => {
      cleanup();
      reject(
        new ProbeException(
          "capture_not_seen",
          `no capture with token '${token}' arrived within ${timeoutMs}ms`,
          {
            last_capture_events: recent.slice(-20),
          },
          "Possible causes: capture pipeline disabled, content-script not " +
            "injected, token never made it into the prompt, or session_hint " +
            "doesn't include the conversation.",
        ),
      );
    }, timeoutMs);

    const onAbort = () => {
      cleanup();
      reject(
        new ProbeException(
          "timeout",
          "capture.wait_for_token aborted by dispatcher",
        ),
      );
    };

    function cleanup() {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      waiters.delete(waiter);
    }

    signal.addEventListener("abort", onAbort);
  });
}

async function captureRecentEvents(
  rawParams: unknown,
): Promise<CaptureRecentEventsResult> {
  const p = rawParams as Partial<CaptureRecentEventsParams>;
  const lastN = typeof p.last_n === "number" && p.last_n > 0 ? p.last_n : 20;
  const provider = typeof p.provider === "string" ? p.provider : undefined;
  const filtered = provider
    ? recent.filter((e) => e.provider === provider)
    : recent;
  return { events: filtered.slice(Math.max(0, filtered.length - lastN)) };
}

async function capturePipelineState(): Promise<CapturePipelineStateResult> {
  if (!pipelineStateProvider) {
    return {
      enabled: true,
      capture_count: 0,
      last_error: "pipeline_state_provider_not_wired",
      server_online: false,
      queued_captures: 0,
    };
  }
  return pipelineStateProvider();
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const captureVerbs: Record<string, VerbHandler> = {
  "capture.wait_for_token": captureWaitForToken as VerbHandler,
  "capture.recent_events": captureRecentEvents as VerbHandler,
  "capture.pipeline_state": capturePipelineState as VerbHandler,
};

export const __captureTesting = {
  ringRef(): CaptureEventSummary[] {
    return recent;
  },
  reset(): void {
    recent.length = 0;
    for (const w of waiters) w.reject(new Error("reset"));
    waiters.clear();
  },
};
