/**
 * PCE Browser Extension – Background Service Worker
 *
 * Receives messages from content scripts and POSTs them to the local
 * PCE Ingest API at http://127.0.0.1:9800/api/v1/captures.
 *
 * Handles three capture types:
 *   - PCE_CAPTURE: DOM-extracted conversations (from chatgpt.js, claude.js, etc.)
 *   - PCE_NETWORK_CAPTURE: intercepted fetch/XHR/WS/EventSource (from bridge.js)
 *   - PCE_AI_PAGE_DETECTED: detector.js found an AI page → dynamic injection
 *
 * Includes a 5-second short-time dedup window to avoid storing near-identical
 * captures from both DOM extraction and network interception simultaneously.
 */

const PCE_INGEST_URL = "http://127.0.0.1:9800/api/v1/captures";

// ---------------------------------------------------------------------------
// Dynamic injection tracking — prevent double-injecting into the same tab
// ---------------------------------------------------------------------------
const injectedTabs = new Set();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let isEnabled = true;
let captureCount = 0;
let lastError = null;
let pceServerOnline = false;
let siteBlacklist = [];

// ---------------------------------------------------------------------------
// Capture self-check: track detections vs actual captures per domain
// ---------------------------------------------------------------------------
const SELF_CHECK_WINDOW_MS = 5 * 60 * 1000; // 5 minutes
const SELF_CHECK_INTERVAL_MS = 60 * 1000;    // check every 60s
const domainDetections = new Map();  // domain -> { detectedAt, tabId, captures: 0 }

function recordDetection(domain, tabId) {
  if (!domain) return;
  domainDetections.set(domain, {
    detectedAt: Date.now(),
    tabId,
    captures: domainDetections.get(domain)?.captures || 0,
  });
}

function recordCaptureSuccess(domain) {
  if (!domain) return;
  const entry = domainDetections.get(domain);
  if (entry) {
    entry.captures++;
  } else {
    domainDetections.set(domain, { detectedAt: Date.now(), tabId: null, captures: 1 });
  }
}

function getSilentDomains() {
  const now = Date.now();
  const silent = [];
  for (const [domain, info] of domainDetections) {
    const age = now - info.detectedAt;
    if (age > SELF_CHECK_WINDOW_MS) {
      domainDetections.delete(domain);
      continue;
    }
    // Detected > 30s ago but zero captures → likely broken
    if (age > 30000 && info.captures === 0) {
      silent.push({ domain, detectedAt: info.detectedAt, age_s: Math.round(age / 1000) });
    }
  }
  return silent;
}

// Periodic self-check: update badge if silent domains found
setInterval(() => {
  if (!isEnabled) return;
  const silent = getSilentDomains();
  if (silent.length > 0) {
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#fb923c" });
    console.warn(`[PCE] Self-check: ${silent.length} AI page(s) detected but no captures received:`,
      silent.map((s) => `${s.domain} (${s.age_s}s ago)`).join(", "));
  } else {
    chrome.action.setBadgeText({ text: "" });
  }
}, SELF_CHECK_INTERVAL_MS);

// Load persisted state
chrome.storage.local.get(["pce_enabled", "pce_capture_count", "pce_blacklist"], (result) => {
  if (result.pce_enabled !== undefined) isEnabled = result.pce_enabled;
  if (result.pce_capture_count !== undefined) captureCount = result.pce_capture_count;
  if (result.pce_blacklist !== undefined) siteBlacklist = result.pce_blacklist;
});

// ---------------------------------------------------------------------------
// Context menu setup
// ---------------------------------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "pce-capture-page",
    title: "Capture This Page (PCE)",
    contexts: ["page"],
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "pce-capture-page" && tab?.id) {
    handleContextMenuCapture(tab);
  }
});

async function handleContextMenuCapture(tab) {
  if (!isEnabled) return;

  const domain = new URL(tab.url).hostname;
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Context menu: ${domain} is blacklisted, skipping`);
    return;
  }

  // Ensure scripts are injected, then trigger manual capture
  await handleDynamicInjection(tab.id, {
    domain,
    confidence: "manual",
    score: 999,
    signals: ["context_menu"],
  });

  // Wait for scripts to initialize, then trigger
  setTimeout(async () => {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          document.dispatchEvent(new CustomEvent("pce-manual-capture"));
        },
      });
      console.log(`[PCE] Context menu capture triggered for ${domain}`);
    } catch (err) {
      console.error(`[PCE] Context menu capture failed:`, err.message);
    }
  }, 1500);
}

// ---------------------------------------------------------------------------
// Short-time dedup (5-second window)
// ---------------------------------------------------------------------------
const DEDUP_WINDOW_MS = 5000;
const DEDUP_CLEANUP_INTERVAL_MS = 60000;
const recentHashes = new Map(); // hash -> timestamp

function simpleHash(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const ch = str.charCodeAt(i);
    hash = ((hash << 5) - hash + ch) | 0;
  }
  return hash.toString(36);
}

function isDuplicate(sessionHint, contentFingerprint) {
  const key = `${sessionHint || "global"}:${simpleHash(contentFingerprint)}`;
  const now = Date.now();
  const prev = recentHashes.get(key);
  if (prev && (now - prev) < DEDUP_WINDOW_MS) {
    return true;
  }
  recentHashes.set(key, now);
  return false;
}

// Periodic cleanup of expired dedup entries
setInterval(() => {
  const now = Date.now();
  for (const [key, ts] of recentHashes) {
    if (now - ts > DEDUP_WINDOW_MS * 2) {
      recentHashes.delete(key);
    }
  }
}, DEDUP_CLEANUP_INTERVAL_MS);

// ---------------------------------------------------------------------------
// Health check – periodically verify the PCE server is reachable
// ---------------------------------------------------------------------------
async function checkHealth() {
  try {
    const resp = await fetch("http://127.0.0.1:9800/api/v1/health", {
      method: "GET",
      signal: AbortSignal.timeout(3000),
    });
    pceServerOnline = resp.ok;
    lastError = null;
  } catch (e) {
    pceServerOnline = false;
    lastError = "PCE server unreachable";
  }
}

// Check on startup and every 30s
checkHealth();
setInterval(checkHealth, 30000);

// ---------------------------------------------------------------------------
// Message handler – receives captures from content scripts
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // DOM-extracted conversation capture (existing path)
  if (message.type === "PCE_CAPTURE") {
    handleCapture(message.payload, sender.tab)
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true; // async response
  }

  // Network-intercepted capture (new path from bridge.js)
  if (message.type === "PCE_NETWORK_CAPTURE") {
    handleNetworkCapture(message.payload, sender.tab)
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }

  if (message.type === "PCE_GET_STATUS") {
    // Do a fresh health check before responding so popup always shows current state
    checkHealth().then(() => {
      sendResponse({
        enabled: isEnabled,
        captureCount,
        serverOnline: pceServerOnline,
        lastError,
        silentDomains: getSilentDomains(),
      });
    });
    return true; // async response
  }

  if (message.type === "PCE_SET_ENABLED") {
    isEnabled = message.enabled;
    chrome.storage.local.set({ pce_enabled: isEnabled });
    sendResponse({ enabled: isEnabled });
    return false;
  }

  // Blacklist updated from popup
  if (message.type === "PCE_BLACKLIST_UPDATED") {
    siteBlacklist = message.blacklist || [];
    sendResponse({ ok: true });
    return false;
  }

  // AI page detected by detector.js → dynamically inject capture scripts
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

    // Report discovered domain to Core API (fire-and-forget)
    const domain = message.payload?.domain;
    if (domain) {
      reportDiscoveredDomain(domain, message.payload?.confidence, message.payload?.signals);
      recordDetection(domain, tabId);
    }

    handleDynamicInjection(tabId, message.payload)
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ injected: false, error: err.message }));
    return true;
  }
});

// Clean up injectedTabs when a tab is closed or navigated
chrome.tabs.onRemoved.addListener((tabId) => {
  injectedTabs.delete(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  // Reset on full navigation (not SPA hash changes)
  if (changeInfo.status === "loading" && changeInfo.url) {
    injectedTabs.delete(tabId);
  }
});

// ---------------------------------------------------------------------------
// Core: send DOM-extracted conversation to PCE Ingest API
// ---------------------------------------------------------------------------
async function handleCapture(payload, tab) {
  if (!isEnabled) {
    return { skipped: true, reason: "capture disabled" };
  }

  // Check blacklist
  const domain = payload.host || (tab ? new URL(tab.url).hostname : null);
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping capture from ${domain}`);
    return { skipped: true, reason: "blacklisted" };
  }

  // Build dedup fingerprint from conversation messages
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

  const body = {
    source_type: "browser_extension",
    source_name: payload.source_name || "chrome-ext",
    direction: "conversation",
    provider: payload.provider || "unknown",
    host: payload.host || (tab ? new URL(tab.url).hostname : "unknown"),
    path: payload.path || (tab ? new URL(tab.url).pathname : "/"),
    method: "GET",
    model_name: payload.model_name || null,
    headers_json: "{}",
    body_json: JSON.stringify(convObj),
    body_format: "json",
    session_hint: sessionHint,
    meta: {
      page_url: tab ? tab.url : null,
      page_title: tab ? tab.title : null,
      tab_id: tab ? tab.id : null,
      captured_at: new Date().toISOString(),
      capture_method: "dom_extraction",
      ...(payload.meta || {}),
    },
  };

  return postToIngestAPI(body, payload.provider);
}

// ---------------------------------------------------------------------------
// Core: send network-intercepted capture to PCE Ingest API
// ---------------------------------------------------------------------------
async function handleNetworkCapture(payload, tab) {
  if (!isEnabled) {
    return { skipped: true, reason: "capture disabled" };
  }

  // Check blacklist
  const domain = payload.host || (tab ? new URL(tab.url).hostname : null);
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping network capture from ${domain}`);
    return { skipped: true, reason: "blacklisted" };
  }

  // Build dedup fingerprint from request+response body
  const reqSnippet = (payload.request_body || "").slice(0, 200);
  const respSnippet = (payload.response_body || "").slice(0, 200);
  const fingerprint = `${payload.host || ""}:${reqSnippet}:${respSnippet}`;
  const sessionHint = tab ? new URL(tab.url).pathname : null;

  if (isDuplicate(sessionHint, fingerprint)) {
    console.log("[PCE] Dedup: skipping duplicate network capture");
    return { skipped: true, reason: "dedup" };
  }

  const body = {
    source_type: "browser_extension",
    source_name: `chrome-ext-${payload.capture_type || "network"}`,
    direction: "network_intercept",
    provider: payload.provider || "unknown",
    host: payload.host || (tab ? new URL(tab.url).hostname : "unknown"),
    path: payload.path || "/",
    method: payload.method || "POST",
    model_name: payload.model || null,
    status_code: payload.status_code || null,
    latency_ms: payload.latency_ms || null,
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
    meta: {
      page_url: tab ? tab.url : null,
      page_title: tab ? tab.title : null,
      tab_id: tab ? tab.id : null,
      captured_at: new Date().toISOString(),
      capture_method: "network_intercept",
      capture_type: payload.capture_type,
      capture_id: payload.capture_id,
      behavior: payload.timestamps || {},
    },
  };

  return postToIngestAPI(body, payload.provider);
}

// ---------------------------------------------------------------------------
// Dynamic injection: inject capture pipeline into detected AI pages
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Blacklist helper
// ---------------------------------------------------------------------------
function isBlacklisted(domain) {
  if (!domain || siteBlacklist.length === 0) return false;
  return siteBlacklist.some((blocked) => domain === blocked || domain.endsWith("." + blocked));
}

async function handleDynamicInjection(tabId, payload) {
  // Skip if already injected in this tab
  if (injectedTabs.has(tabId)) {
    console.debug(`[PCE] Tab ${tabId} already injected, skipping`);
    return { injected: false, reason: "already_injected" };
  }

  const domain = payload?.domain || "unknown";

  // Check blacklist before injecting
  if (isBlacklisted(domain)) {
    console.log(`[PCE] Blacklisted: skipping injection for ${domain}`);
    return { injected: false, reason: "blacklisted" };
  }

  injectedTabs.add(tabId);
  const confidence = payload?.confidence || "unknown";

  console.log(
    `[PCE] Injecting capture scripts into tab ${tabId} (${domain}, confidence=${confidence})`
  );

  try {
    // Inject behavior tracker + bridge + universal extractor
    await chrome.scripting.executeScript({
      target: { tabId },
      files: [
        "content_scripts/behavior_tracker.js",
        "content_scripts/bridge.js",
        "content_scripts/universal_extractor.js",
      ],
    });

    console.log(`[PCE] Successfully injected capture scripts into tab ${tabId} (${domain})`);
    return { injected: true, domain, confidence };
  } catch (err) {
    // Remove from set so retry is possible
    injectedTabs.delete(tabId);
    console.error(`[PCE] Injection failed for tab ${tabId}:`, err.message);
    return { injected: false, error: err.message };
  }
}

// ---------------------------------------------------------------------------
// Shared: POST to PCE Ingest API
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Report discovered AI domains to Core API (dynamic allowlist)
// ---------------------------------------------------------------------------
const reportedDomains = new Set();

async function reportDiscoveredDomain(domain, confidence, signals) {
  if (!domain || reportedDomains.has(domain)) return;
  reportedDomains.add(domain);

  try {
    await fetch("http://127.0.0.1:9800/api/v1/domains", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        domain,
        source: "browser_extension",
        confidence: confidence || "unknown",
        reason: (signals || []).join(", ") || "detector.js",
      }),
      signal: AbortSignal.timeout(3000),
    });
    console.log(`[PCE] Reported discovered domain: ${domain}`);
  } catch {
    // Non-fatal: server may be offline
    reportedDomains.delete(domain);
  }
}

async function postToIngestAPI(body, provider) {
  try {
    const resp = await fetch(PCE_INGEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Ingest API returned ${resp.status}: ${text}`);
    }

    const result = await resp.json();
    captureCount++;
    chrome.storage.local.set({ pce_capture_count: captureCount });
    lastError = null;
    pceServerOnline = true;

    recordCaptureSuccess(body.host);
    console.log(`[PCE] Captured: ${provider || "unknown"} via ${body.direction} from ${body.host} (${result.id?.slice(0, 8)})`);
    return { id: result.id, pair_id: result.pair_id };
  } catch (err) {
    lastError = err.message;
    console.error("[PCE] Capture failed:", err.message);
    throw err;
  }
}
