// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Browser Extension — Background Service Worker.
 *
 * TypeScript port of ``background/service_worker.js`` (P2.5 Phase 1).
 *
 * Receives messages from content scripts and POSTs them to the local PCE
 * Ingest API at ``http://127.0.0.1:9800/api/v1/captures``.
 *
 * Handles three capture flavours:
 *   - ``PCE_CAPTURE`` — DOM-extracted conversations
 *   - ``PCE_NETWORK_CAPTURE`` — intercepted fetch/XHR/WS/EventSource
 *   - ``PCE_SNIPPET`` — text snippet collected by text_collector.js
 *
 * Plus the orchestration messages:
 *   - ``PCE_AI_PAGE_DETECTED`` — detector.js → dynamic injection
 *   - ``PCE_GET_STATUS`` / ``PCE_SET_ENABLED`` / ``PCE_BLACKLIST_UPDATED``
 *
 * Includes a 5-second dedup window (DOM vs network capture from the same
 * conversation is deduped into a single row) and an IndexedDB-backed
 * offline buffer (``CaptureQueue``) that kicks in when the PCE server is
 * unreachable.
 */

import { defineBackground } from "wxt/sandbox";
import { CaptureQueue } from "./background/capture-queue";
import {
  handleDynamicInjection,
  proactiveInjectFallback,
  type InjectionContext,
} from "./background/injector";
// Build-mode gated imports. Resolved to real modules in the dev
// build and no-op stubs in the production build via Vite alias.
// See ``wxt.config.ts`` for the swap.
import { observeCaptureMessage, setPipelineStateProvider } from "$probe-rpc-capture";
import { startProbeRpc } from "$probe-rpc";
import {
  DEFAULT_PCE_INGEST_URL,
  DEFAULT_PCE_SERVER_ORIGIN,
} from "../utils/pce-messages";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const PCE_INGEST_URL = DEFAULT_PCE_INGEST_URL;
const PCE_HEALTH_URL = `${DEFAULT_PCE_SERVER_ORIGIN}/api/v1/health`;
const PCE_SNIPPETS_URL = `${DEFAULT_PCE_SERVER_ORIGIN}/api/v1/snippets`;
const PCE_DOMAINS_URL = `${DEFAULT_PCE_SERVER_ORIGIN}/api/v1/domains`;

const HEALTH_CHECK_INTERVAL_MS = 30_000;
const SELF_CHECK_WINDOW_MS = 5 * 60 * 1_000;
const SELF_CHECK_INTERVAL_MS = 60_000;
const DEDUP_WINDOW_MS = 5_000;
const DEDUP_CLEANUP_INTERVAL_MS = 60_000;

// ---------------------------------------------------------------------------
// Types mirroring the legacy message contract
// ---------------------------------------------------------------------------

interface CapturePayload {
  provider?: string;
  host?: string;
  path?: string;
  source_name?: string;
  model_name?: string | null;
  session_hint?: string | null;
  layer_meta?: Record<string, unknown> | null;
  conversation?: { messages?: Array<{ role?: string; content?: string }> };
  meta?: Record<string, unknown>;
}

interface NetworkCapturePayload {
  provider?: string;
  host?: string;
  path?: string;
  method?: string;
  model?: string | null;
  status_code?: number | null;
  latency_ms?: number | null;
  url?: string;
  request_body?: string | null;
  response_body?: string | null;
  is_streaming?: boolean;
  response_content_type?: string | null;
  confidence?: string;
  capture_type?: string;
  capture_id?: string;
  timestamps?: Record<string, unknown>;
}

interface SnippetPayload {
  content_text: string;
  source_url?: string | null;
  source_domain?: string | null;
  provider?: string | null;
  category?: string;
  note?: string | null;
}

interface AIPageDetectedPayload {
  domain?: string;
  confidence?: string;
  signals?: string[];
  score?: number;
}

interface IngestResponse {
  id?: string;
  pair_id?: string;
}

interface CaptureResult {
  id?: string;
  pair_id?: string;
  error?: string;
  skipped?: boolean;
  reason?: string;
  queued?: boolean;
}

interface MinimalTab {
  id?: number;
  url?: string;
  title?: string;
}

// ---------------------------------------------------------------------------
// Runtime state (moved behind a module so tests can reset cleanly)
// ---------------------------------------------------------------------------

const injectedTabs = new Set<number>();
const reportedDomains = new Set<string>();
const recentHashes = new Map<string, number>();

interface SelfCheckEntry {
  detectedAt: number;
  tabId: number | null;
  captures: number;
}
const domainDetections = new Map<string, SelfCheckEntry>();

interface SilentDomain {
  domain: string;
  detectedAt: number;
  age_s: number;
}

let isEnabled = true;
let captureCount = 0;
let lastError: string | null = null;
let pceServerOnline = false;
let siteBlacklist: string[] = [];

// ---------------------------------------------------------------------------
// Self-check: track AI-page detections vs actual captures
// ---------------------------------------------------------------------------

function recordDetection(domain: string | null | undefined, tabId: number | null) {
  if (!domain) return;
  const prev = domainDetections.get(domain);
  domainDetections.set(domain, {
    detectedAt: Date.now(),
    tabId,
    captures: prev?.captures ?? 0,
  });
}

function recordCaptureSuccess(domain: string | null | undefined) {
  if (!domain) return;
  const entry = domainDetections.get(domain);
  if (entry) {
    entry.captures++;
  } else {
    domainDetections.set(domain, { detectedAt: Date.now(), tabId: null, captures: 1 });
  }
}

function getSilentDomains(): SilentDomain[] {
  const now = Date.now();
  const silent: SilentDomain[] = [];
  for (const [domain, info] of domainDetections) {
    const age = now - info.detectedAt;
    if (age > SELF_CHECK_WINDOW_MS) {
      domainDetections.delete(domain);
      continue;
    }
    if (age > 30_000 && info.captures === 0) {
      silent.push({
        domain,
        detectedAt: info.detectedAt,
        age_s: Math.round(age / 1_000),
      });
    }
  }
  return silent;
}

// ---------------------------------------------------------------------------
// Dedup (5-second short-time window)
// ---------------------------------------------------------------------------

function simpleHash(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    hash = ((hash << 5) - hash + ch) | 0;
  }
  return hash.toString(36);
}

function isDuplicate(
  sessionHint: string | null | undefined,
  contentFingerprint: string,
): boolean {
  const key = `${sessionHint || "global"}:${simpleHash(contentFingerprint)}`;
  const now = Date.now();
  const prev = recentHashes.get(key);
  if (prev && now - prev < DEDUP_WINDOW_MS) {
    return true;
  }
  recentHashes.set(key, now);
  return false;
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

async function checkHealth(): Promise<void> {
  try {
    const resp = await fetch(PCE_HEALTH_URL, {
      method: "GET",
      signal: AbortSignal.timeout(3_000),
    });
    pceServerOnline = resp.ok;
    lastError = null;
  } catch {
    pceServerOnline = false;
    lastError = "PCE server unreachable";
  }
}

// ---------------------------------------------------------------------------
// Blacklist helper
// ---------------------------------------------------------------------------

function isBlacklisted(domain: string | null | undefined): boolean {
  if (!domain || siteBlacklist.length === 0) return false;
  return siteBlacklist.some(
    (blocked) => domain === blocked || domain.endsWith("." + blocked),
  );
}

// ---------------------------------------------------------------------------
// Ingest API
// ---------------------------------------------------------------------------

interface IngestBody {
  source_type: string;
  source_name: string;
  direction: string;
  provider: string;
  host: string;
  path: string;
  method: string;
  model_name: string | null;
  status_code?: number | null;
  latency_ms?: number | null;
  headers_json: string;
  body_json: string;
  body_format: string;
  session_hint: string | null;
  layer_meta?: Record<string, unknown> | null;
  schema_version: number;
  meta: Record<string, unknown>;
}

async function postToIngestAPI(
  body: IngestBody,
  provider: string | undefined,
): Promise<CaptureResult> {
  const bodyStr = JSON.stringify(body);
  try {
    const resp = await fetch(PCE_INGEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: bodyStr,
      signal: AbortSignal.timeout(5_000),
    });

    if (!resp.ok) {
      const text = await resp.text();
      if (resp.status >= 400 && resp.status < 500) {
        console.error(`[PCE] Capture rejected (${resp.status}): ${text}`);
        return { error: text };
      }
      throw new Error(`Ingest API returned ${resp.status}: ${text}`);
    }

    const result = (await resp.json()) as IngestResponse;
    captureCount++;
    chrome.storage.local.set({ pce_capture_count: captureCount });
    lastError = null;
    pceServerOnline = true;

    recordCaptureSuccess(body.host);
    console.log(
      `[PCE] Captured: ${provider || "unknown"} via ${body.direction} ` +
        `from ${body.host} (${result.id?.slice(0, 8)})`,
    );
    return { id: result.id, pair_id: result.pair_id };
  } catch (err) {
    const msg = (err as Error).message;
    lastError = msg;
    pceServerOnline = false;
    console.warn("[PCE] Capture failed, queuing for retry:", msg);
    await CaptureQueue.enqueue(PCE_INGEST_URL, bodyStr);
    return { queued: true };
  }
}

// ---------------------------------------------------------------------------
// Capture handlers
// ---------------------------------------------------------------------------

function hostnameOfTab(tab: MinimalTab | null | undefined): string | null {
  if (!tab?.url) return null;
  try {
    return new URL(tab.url).hostname;
  } catch {
    return null;
  }
}

function pathOfTab(tab: MinimalTab | null | undefined): string {
  if (!tab?.url) return "/";
  try {
    return new URL(tab.url).pathname;
  } catch {
    return "/";
  }
}

async function handleCapture(
  payload: CapturePayload,
  tab: MinimalTab | undefined,
): Promise<CaptureResult> {
  if (!isEnabled) return { skipped: true, reason: "capture disabled" };

  const domain = payload.host || hostnameOfTab(tab);
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping capture from ${domain}`);
    return { skipped: true, reason: "blacklisted" };
  }

  const convObj = payload.conversation || {};
  const messages = convObj.messages || [];
  const fingerprint = messages
    .map((m) => (m.role || "") + ":" + (m.content || "").slice(0, 100))
    .join("|");

  const sessionHint = payload.session_hint || null;

  if (fingerprint && isDuplicate(sessionHint, fingerprint)) {
    console.log("[PCE] Dedup: skipping duplicate conversation capture");
    return { skipped: true, reason: "dedup" };
  }

  const body: IngestBody = {
    source_type: "browser_extension",
    source_name: payload.source_name || "chrome-ext",
    direction: "conversation",
    provider: payload.provider || "unknown",
    host: payload.host || hostnameOfTab(tab) || "unknown",
    path: payload.path || pathOfTab(tab),
    method: "GET",
    model_name: payload.model_name || null,
    headers_json: "{}",
    body_json: JSON.stringify(convObj),
    body_format: "json",
    session_hint: sessionHint,
    layer_meta: payload.layer_meta || null,
    schema_version: 2,
    meta: {
      page_url: tab?.url ?? null,
      page_title: tab?.title ?? null,
      tab_id: tab?.id ?? null,
      captured_at: new Date().toISOString(),
      capture_method: "dom_extraction",
      ...(payload.meta || {}),
    },
  };

  return postToIngestAPI(body, payload.provider);
}

async function handleNetworkCapture(
  payload: NetworkCapturePayload,
  tab: MinimalTab | undefined,
): Promise<CaptureResult> {
  if (!isEnabled) return { skipped: true, reason: "capture disabled" };

  const domain = payload.host || hostnameOfTab(tab);
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping network capture from ${domain}`);
    return { skipped: true, reason: "blacklisted" };
  }

  const reqSnippet = (payload.request_body || "").slice(0, 200);
  const respSnippet = (payload.response_body || "").slice(0, 200);
  const fingerprint = `${payload.host || ""}:${reqSnippet}:${respSnippet}`;

  // Extract conversation UUID from path so network and DOM captures merge
  // into the same session:  /c/69d723b2-... → 69d723b2-...
  let sessionHint: string | null = null;
  if (tab?.url) {
    try {
      const tabPath = new URL(tab.url).pathname;
      const convMatch = tabPath.match(
        /\/(?:c|chat|conversation|thread|t)\/([a-f0-9-]{8,})/i,
      );
      sessionHint = convMatch ? convMatch[1] : tabPath;
    } catch {
      sessionHint = null;
    }
  }

  if (isDuplicate(sessionHint, fingerprint)) {
    console.log("[PCE] Dedup: skipping duplicate network capture");
    return { skipped: true, reason: "dedup" };
  }

  const body: IngestBody = {
    source_type: "browser_extension",
    source_name: `chrome-ext-${payload.capture_type || "network"}`,
    direction: "network_intercept",
    provider: payload.provider || "unknown",
    host: payload.host || hostnameOfTab(tab) || "unknown",
    path: payload.path || "/",
    method: payload.method || "POST",
    model_name: payload.model || null,
    status_code: payload.status_code ?? null,
    latency_ms: payload.latency_ms ?? null,
    headers_json: "{}",
    body_json: JSON.stringify({
      request_body: payload.request_body,
      response_body: payload.response_body,
      url: payload.url,
      is_streaming: payload.is_streaming,
      response_content_type: payload.response_content_type,
      confidence: payload.confidence,
    }),
    body_format: "json",
    session_hint: sessionHint,
    schema_version: 2,
    meta: {
      page_url: tab?.url ?? null,
      page_title: tab?.title ?? null,
      tab_id: tab?.id ?? null,
      captured_at: new Date().toISOString(),
      capture_method: "network_intercept",
      capture_type: payload.capture_type,
      capture_id: payload.capture_id,
      behavior: payload.timestamps || {},
    },
  };

  return postToIngestAPI(body, payload.provider);
}

async function handleSnippet(
  payload: SnippetPayload,
): Promise<{ id?: string; skipped?: boolean; reason?: string }> {
  if (!isEnabled) return { skipped: true, reason: "capture disabled" };

  const body = {
    content_text: payload.content_text,
    source_url: payload.source_url || null,
    source_domain: payload.source_domain || null,
    provider: payload.provider || null,
    category: payload.category || "general",
    note: payload.note || null,
  };

  const resp = await fetch(PCE_SNIPPETS_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(5_000),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Snippets API returned ${resp.status}: ${text}`);
  }
  const result = (await resp.json()) as { id?: string };
  console.log(
    `[PCE] Snippet saved: ${result.id?.slice(0, 8)} ` +
      `(${body.category}) from ${body.source_domain}`,
  );
  return { id: result.id };
}

// ---------------------------------------------------------------------------
// Discovered-domain reporting (fire-and-forget)
// ---------------------------------------------------------------------------

async function reportDiscoveredDomain(
  domain: string | null | undefined,
  confidence: string | undefined,
  signals: string[] | undefined,
): Promise<void> {
  if (!domain || reportedDomains.has(domain)) return;
  reportedDomains.add(domain);
  try {
    await fetch(PCE_DOMAINS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain,
        source: "browser_extension",
        confidence: confidence || "unknown",
        reason: (signals || []).join(", ") || "detector.js",
      }),
      signal: AbortSignal.timeout(3_000),
    });
    console.log(`[PCE] Reported discovered domain: ${domain}`);
  } catch {
    reportedDomains.delete(domain); // allow retry later
  }
}

// ---------------------------------------------------------------------------
// Context-menu capture
// ---------------------------------------------------------------------------

async function handleContextMenuCapture(tab: MinimalTab): Promise<void> {
  if (!isEnabled || !tab.id || !tab.url) return;
  let domain: string;
  try {
    domain = new URL(tab.url).hostname;
  } catch {
    return;
  }
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Context menu: ${domain} is blacklisted, skipping`);
    return;
  }
  await handleDynamicInjection(injectionCtx, tab.id, {
    domain,
    confidence: "manual",
  });

  setTimeout(async () => {
    try {
      if (tab.id === undefined) return;
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          document.dispatchEvent(new CustomEvent("pce-manual-capture"));
        },
      });
      console.log(`[PCE] Context menu capture triggered for ${domain}`);
    } catch (err) {
      console.error("[PCE] Context menu capture failed:", (err as Error).message);
    }
  }, 1_500);
}

// ---------------------------------------------------------------------------
// Injection context shared with the injector module
// ---------------------------------------------------------------------------

const injectionCtx: InjectionContext = {
  isEnabled: () => isEnabled,
  isBlacklisted,
  injectedTabs,
};

// ---------------------------------------------------------------------------
// Main entrypoint
// ---------------------------------------------------------------------------

export default defineBackground({
  type: "module",
  persistent: false,
  main() {
    console.log("[PCE] background service worker starting");

    // -----------------------------------------------------------------------
    // Persisted state
    // -----------------------------------------------------------------------
    chrome.storage.local.get(
      ["pce_enabled", "pce_capture_count", "pce_blacklist"],
      (result: Record<string, unknown>) => {
        if (result.pce_enabled !== undefined) isEnabled = Boolean(result.pce_enabled);
        if (typeof result.pce_capture_count === "number") {
          captureCount = result.pce_capture_count;
        }
        if (Array.isArray(result.pce_blacklist)) {
          siteBlacklist = result.pce_blacklist as string[];
        }
      },
    );

    // -----------------------------------------------------------------------
    // Context menu
    // -----------------------------------------------------------------------
    chrome.runtime.onInstalled.addListener(() => {
      chrome.contextMenus.create({
        id: "pce-capture-page",
        title: "Capture This Page (PCE)",
        contexts: ["page"],
      });
    });

    chrome.contextMenus.onClicked.addListener(
      // Chrome's `OnClickData` type is wider than `{menuItemId: string}`
      // but we only read that one field; the `tab` param is OPTIONAL
      // per chrome.contextMenus typings, so declare `tab?:` instead of
      // `tab: T | undefined` to match the API signature (closes a
      // latent TS2345 that `pnpm typecheck` flagged post-resubmission
      // audit; no runtime change).
      (info: chrome.contextMenus.OnClickData, tab?: MinimalTab) => {
        if (info.menuItemId === "pce-capture-page" && tab?.id) {
          void handleContextMenuCapture(tab);
        }
      },
    );

    // -----------------------------------------------------------------------
    // Tab lifecycle — injected-set housekeeping + proactive fallback
    // -----------------------------------------------------------------------
    chrome.tabs.onRemoved.addListener((tabId: number) => {
      injectedTabs.delete(tabId);
    });

    chrome.tabs.onUpdated.addListener(
      (
        tabId: number,
        changeInfo: { status?: string; url?: string },
        tab: MinimalTab | undefined,
      ) => {
        if (changeInfo.status === "loading" && changeInfo.url) {
          injectedTabs.delete(tabId);
          return;
        }
        if (changeInfo.status === "complete" && tab?.url && isEnabled) {
          void proactiveInjectFallback(injectionCtx, tabId, tab.url);
        }
      },
    );

    // -----------------------------------------------------------------------
    // Message router
    // -----------------------------------------------------------------------
    chrome.runtime.onMessage.addListener(
      (
        message: { type?: string; payload?: unknown; enabled?: boolean; blacklist?: string[] },
        sender: { tab?: MinimalTab },
        sendResponse: (response: unknown) => void,
      ): boolean => {
        if (message.type === "PCE_CAPTURE") {
          // Probe observer tap: snapshot before forwarding to ingest
          // (non-blocking, never throws; see probe-rpc-capture.ts).
          // Vite replaces ``__PCE_PROBE_ENABLED__`` with the literal
          // ``false`` in the webstore build; Rollup then strips this
          // branch as dead code.
          if (__PCE_PROBE_ENABLED__) {
            observeCaptureMessage({
              kind: "PCE_CAPTURE",
              payload: (message.payload ?? {}) as Record<string, unknown>,
            });
          }
          handleCapture(message.payload as CapturePayload, sender.tab)
            .then((result) => sendResponse({ ok: !result.error, ...result }))
            .catch((err: Error) =>
              sendResponse({ ok: false, error: err.message }),
            );
          return true;
        }

        if (message.type === "PCE_NETWORK_CAPTURE") {
          if (__PCE_PROBE_ENABLED__) {
            observeCaptureMessage({
              kind: "PCE_NETWORK_CAPTURE",
              payload: (message.payload ?? {}) as Record<string, unknown>,
            });
          }
          handleNetworkCapture(message.payload as NetworkCapturePayload, sender.tab)
            .then((result) => sendResponse({ ok: !result.error, ...result }))
            .catch((err: Error) =>
              sendResponse({ ok: false, error: err.message }),
            );
          return true;
        }

        if (message.type === "PCE_SNIPPET") {
          if (__PCE_PROBE_ENABLED__) {
            observeCaptureMessage({
              kind: "PCE_SNIPPET",
              payload: (message.payload ?? {}) as Record<string, unknown>,
            });
          }
          handleSnippet(message.payload as SnippetPayload)
            .then((result) => sendResponse({ ok: true, ...result }))
            .catch((err: Error) =>
              sendResponse({ ok: false, error: err.message }),
            );
          return true;
        }

        if (message.type === "PCE_GET_STATUS") {
          Promise.all([checkHealth(), CaptureQueue.count()]).then(
            ([, queuedCount]) => {
              sendResponse({
                enabled: isEnabled,
                captureCount,
                serverOnline: pceServerOnline,
                lastError,
                silentDomains: getSilentDomains(),
                queuedCaptures: queuedCount,
              });
            },
          );
          return true;
        }

        if (message.type === "PCE_SET_ENABLED") {
          isEnabled = Boolean(message.enabled);
          chrome.storage.local.set({ pce_enabled: isEnabled });
          sendResponse({ enabled: isEnabled });
          return false;
        }

        if (message.type === "PCE_BLACKLIST_UPDATED") {
          siteBlacklist = message.blacklist || [];
          sendResponse({ ok: true });
          return false;
        }

        if (message.type === "PCE_AI_PAGE_DETECTED") {
          if (!isEnabled) {
            sendResponse({ injected: false, reason: "capture disabled" });
            return false;
          }
          const tabId = sender.tab?.id;
          if (!tabId) {
            sendResponse({ injected: false, reason: "no tab id" });
            return false;
          }

          const payload = (message.payload || {}) as AIPageDetectedPayload;
          const domain = payload.domain;
          if (domain) {
            void reportDiscoveredDomain(domain, payload.confidence, payload.signals);
            recordDetection(domain, tabId);
          }

          handleDynamicInjection(injectionCtx, tabId, payload)
            .then((result) => sendResponse(result))
            .catch((err: Error) =>
              sendResponse({ injected: false, error: err.message }),
            );
          return true;
        }

        return false;
      },
    );

    // -----------------------------------------------------------------------
    // Periodic tasks
    // -----------------------------------------------------------------------
    setInterval(() => {
      if (!isEnabled) return;
      const silent = getSilentDomains();
      if (silent.length > 0) {
        chrome.action.setBadgeText({ text: "!" });
        chrome.action.setBadgeBackgroundColor({ color: "#fb923c" });
        console.warn(
          `[PCE] Self-check: ${silent.length} AI page(s) detected but no captures received:`,
          silent.map((s) => `${s.domain} (${s.age_s}s ago)`).join(", "),
        );
      } else {
        chrome.action.setBadgeText({ text: "" });
      }
    }, SELF_CHECK_INTERVAL_MS);

    setInterval(() => {
      const now = Date.now();
      for (const [key, ts] of recentHashes) {
        if (now - ts > DEDUP_WINDOW_MS * 2) {
          recentHashes.delete(key);
        }
      }
    }, DEDUP_CLEANUP_INTERVAL_MS);

    void checkHealth();
    setInterval(() => void checkHealth(), HEALTH_CHECK_INTERVAL_MS);
    CaptureQueue.startRetryLoop();

    // -----------------------------------------------------------------------
    // Internal-only debug surface — gated by ``__PCE_PROBE_ENABLED__``.
    // Vite replaces the constant with the literal ``false`` for the
    // public build, and Rollup eliminates this entire block plus the
    // imports that fed it. The shipped bundle contains no trace of
    // the symbols inside.
    // -----------------------------------------------------------------------
    if (__PCE_PROBE_ENABLED__) {
      setPipelineStateProvider(() => {
        void CaptureQueue.count();
        return {
          enabled: isEnabled,
          capture_count: captureCount,
          last_error: lastError,
          server_online: pceServerOnline,
          queued_captures: 0,
        };
      });
      startProbeRpc();
    }
  },
});

// ---------------------------------------------------------------------------
// Test hooks — exported only for Vitest harnesses, not used at runtime.
// ---------------------------------------------------------------------------

export const __testing = {
  simpleHash,
  isDuplicate,
  isBlacklisted,
  recordDetection,
  recordCaptureSuccess,
  getSilentDomains,
  resetState() {
    injectedTabs.clear();
    reportedDomains.clear();
    recentHashes.clear();
    domainDetections.clear();
    isEnabled = true;
    captureCount = 0;
    lastError = null;
    pceServerOnline = false;
    siteBlacklist = [];
  },
  getState() {
    return {
      isEnabled,
      captureCount,
      lastError,
      pceServerOnline,
      siteBlacklist: [...siteBlacklist],
      injectedTabs: new Set(injectedTabs),
      recentHashesSize: recentHashes.size,
      domainDetectionsSize: domainDetections.size,
    };
  },
};
