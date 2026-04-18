// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Content Script Bridge.
 *
 * TypeScript port of ``content_scripts/bridge.js`` (P2.5 Phase 2).
 *
 * Bridges communication between the page-context interceptor scripts
 * (``interceptor-network.ts``, ``interceptor-ai-patterns.ts``) and the
 * extension's background service worker.
 *
 * Flow::
 *
 *   interceptor-network (page context)
 *     → window.postMessage("PCE_NETWORK_CAPTURE", …)
 *     → this bridge (content-script context)
 *     → chrome.runtime.sendMessage("PCE_NETWORK_CAPTURE", …)
 *     → background.ts
 *
 * This script also injects the interceptor scripts into the page context
 * (inline scripts are blocked by CSP on most AI sites, so we use
 * ``<script src=chrome.runtime.getURL(…)>`` which is CSP-safe).
 */

// All content-script URLs used by this bridge. Paths match what WXT
// emits for unlisted-script entrypoints (``entrypoints/<name>.ts`` →
// ``<output>/<name>.js``).
const INTERCEPTOR_SCRIPTS = [
  "interceptor-page-confirmed.js",
  "interceptor-ai-patterns.js",
  "interceptor-network.js",
] as const;

declare global {
  interface Window {
    __PCE_BRIDGE_ACTIVE?: boolean;
    __PCE_BRIDGE_LOADED?: boolean;
  }
}

export default defineContentScript({
  matches: [
    "https://chatgpt.com/*",
    "https://chat.openai.com/*",
    "https://claude.ai/*",
    "https://gemini.google.com/*",
    "https://aistudio.google.com/*",
    "https://manus.im/*",
    "https://chat.deepseek.com/*",
    "https://chat.z.ai/*",
    "https://www.perplexity.ai/*",
    "https://copilot.microsoft.com/*",
    "https://poe.com/*",
    "https://huggingface.co/chat/*",
    "https://grok.com/*",
    "https://chat.mistral.ai/*",
    "https://kimi.moonshot.cn/*",
    "https://www.kimi.com/*",
    "https://kimi.com/*",
  ],
  runAt: "document_start",
  main() {
    const TAG = "[PCE:bridge]";

    // Guard against double-injection
    if (window.__PCE_BRIDGE_ACTIVE) return;
    window.__PCE_BRIDGE_ACTIVE = true;
    // detector.js historically checked __PCE_BRIDGE_LOADED. Keep both
    // flags so static content-script injection is visible to all
    // older/newer callers.
    window.__PCE_BRIDGE_LOADED = true;

    // ───────────────────────────────────────────────────────────────
    // Inject interceptor scripts into page context
    // ───────────────────────────────────────────────────────────────

    function injectPageScript(fileName: string): void {
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

    // Signal to page-context scripts that this page is confirmed AI.
    // Use a DOM attribute (CSP-safe) so interceptor-network.ts can read it.
    document.documentElement.setAttribute("data-pce-ai-confirmed", "1");

    // Also inject a tiny external script to set the window-level flag
    // (external <script src> is allowed by CSP, inline is not).
    injectPageScript(INTERCEPTOR_SCRIPTS[0]);

    // ai-patterns must load first (it sets window.__PCE_AI_PATTERNS)
    injectPageScript(INTERCEPTOR_SCRIPTS[1]);

    // Small delay to ensure ai-patterns is evaluated before interceptor runs
    setTimeout(() => {
      injectPageScript(INTERCEPTOR_SCRIPTS[2]);
    }, 0);

    // ───────────────────────────────────────────────────────────────
    // Listen for messages from page context (postMessage)
    // ───────────────────────────────────────────────────────────────

    window.addEventListener("message", function (event: MessageEvent) {
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
            payload,
          },
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          (_response: unknown) => {
            if (chrome.runtime.lastError) {
              // Extension context invalidated or SW not ready — ignore
            }
          },
        );
      } catch {
        // Extension context destroyed — ignore
      }
    });
  },
});
