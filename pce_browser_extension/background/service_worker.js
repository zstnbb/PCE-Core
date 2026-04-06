/**
 * PCE Browser Extension – Background Service Worker
 *
 * Receives messages from content scripts and POSTs them to the local
 * PCE Ingest API at http://127.0.0.1:9800/api/v1/captures.
 */

const PCE_INGEST_URL = "http://127.0.0.1:9800/api/v1/captures";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let isEnabled = true;
let captureCount = 0;
let lastError = null;
let pceServerOnline = false;

// Load persisted state
chrome.storage.local.get(["pce_enabled", "pce_capture_count"], (result) => {
  if (result.pce_enabled !== undefined) isEnabled = result.pce_enabled;
  if (result.pce_capture_count !== undefined) captureCount = result.pce_capture_count;
});

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
  if (message.type === "PCE_CAPTURE") {
    handleCapture(message.payload, sender.tab)
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true; // async response
  }

  if (message.type === "PCE_GET_STATUS") {
    sendResponse({
      enabled: isEnabled,
      captureCount,
      serverOnline: pceServerOnline,
      lastError,
    });
    return false;
  }

  if (message.type === "PCE_SET_ENABLED") {
    isEnabled = message.enabled;
    chrome.storage.local.set({ pce_enabled: isEnabled });
    sendResponse({ enabled: isEnabled });
    return false;
  }
});

// ---------------------------------------------------------------------------
// Core: send capture to PCE Ingest API
// ---------------------------------------------------------------------------
async function handleCapture(payload, tab) {
  if (!isEnabled) {
    return { skipped: true, reason: "capture disabled" };
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
    body_json: JSON.stringify(payload.conversation || {}),
    body_format: "json",
    session_hint: payload.session_hint || null,
    meta: {
      page_url: tab ? tab.url : null,
      page_title: tab ? tab.title : null,
      tab_id: tab ? tab.id : null,
      captured_at: new Date().toISOString(),
      ...(payload.meta || {}),
    },
  };

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

    console.log(`[PCE] Captured: ${payload.provider} from ${body.host} (${result.id?.slice(0, 8)})`);
    return { id: result.id, pair_id: result.pair_id };
  } catch (err) {
    lastError = err.message;
    console.error("[PCE] Capture failed:", err.message);
    throw err;
  }
}
