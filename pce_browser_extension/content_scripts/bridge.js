/**
 * PCE – Content Script Bridge
 *
 * Bridges communication between the page-context interceptor scripts
 * (network_interceptor.js, ai_patterns.js) and the extension's
 * background service worker.
 *
 * Flow:
 *   network_interceptor.js (page context)
 *     → window.postMessage("PCE_NETWORK_CAPTURE", ...)
 *     → this bridge (content script context)
 *     → chrome.runtime.sendMessage("PCE_NETWORK_CAPTURE", ...)
 *     → service_worker.js
 *
 * This script also injects the interceptor scripts into the page context.
 */

(function () {
  "use strict";

  const TAG = "[PCE:bridge]";

  // Guard against double-injection
  if (window.__PCE_BRIDGE_ACTIVE) return;
  window.__PCE_BRIDGE_ACTIVE = true;

  // -----------------------------------------------------------------------
  // Inject interceptor scripts into page context
  // -----------------------------------------------------------------------

  function injectPageScript(fileName) {
    try {
      const url = chrome.runtime.getURL(fileName);
      const script = document.createElement("script");
      script.src = url;
      script.onload = function () {
        script.remove();
      };
      (document.head || document.documentElement).appendChild(script);
    } catch (e) {
      console.warn(TAG, "Failed to inject", fileName, e);
    }
  }

  // ai_patterns.js must load first (it sets window.__PCE_AI_PATTERNS)
  injectPageScript("interceptor/ai_patterns.js");

  // Small delay to ensure ai_patterns.js is evaluated before interceptor runs
  setTimeout(() => {
    injectPageScript("interceptor/network_interceptor.js");
  }, 0);

  // -----------------------------------------------------------------------
  // Listen for messages from page context (postMessage)
  // -----------------------------------------------------------------------

  window.addEventListener("message", function (event) {
    // Only accept messages from the same page
    if (event.source !== window) return;
    if (!event.data || event.data.type !== "PCE_NETWORK_CAPTURE") return;

    const payload = event.data.payload;
    if (!payload) return;

    // Forward to service worker
    try {
      chrome.runtime.sendMessage(
        {
          type: "PCE_NETWORK_CAPTURE",
          payload: payload,
        },
        (response) => {
          if (chrome.runtime.lastError) {
            // Extension context invalidated or SW not ready – ignore
          }
        }
      );
    } catch {
      // Extension context destroyed – ignore
    }
  });
})();
