// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Microsoft Copilot content script (copilot.microsoft.com).
 *
 * TypeScript port of ``content_scripts/copilot.js`` (P2.5 Phase 3b
 * batch 3).
 *
 * Copilot uses a React-based chat UI with CIB ("Conversational AI in
 * Bing") components. The extractor runs through 6 selector strategies
 * in priority order, detects role via class/source/data-testid
 * keywords, and prefers ``.ac-textBlock`` / markdown children inside
 * the turn node. Legacy timing: 2500 ms debounce, 5000 ms polling.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";

declare global {
  interface Window {
    __PCE_COPILOT_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Copilot-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/(?:chat|c|thread)\/([a-zA-Z0-9_-]+)/;

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector("main, [class*='chat-container'], #app") || doc.body
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

export function detectRole(el: Element): "user" | "assistant" | "unknown" {
  const cls = ((el as Element).className || "").toString().toLowerCase();
  const source = el.getAttribute("source") || "";
  const testId = el.getAttribute("data-testid") || "";

  if (source === "user" || cls.includes("user") || testId.includes("user")) {
    return "user";
  }
  if (
    source === "bot" ||
    cls.includes("bot") ||
    cls.includes("copilot") ||
    testId.includes("bot")
  ) {
    return "assistant";
  }
  return "unknown";
}

export function extractText(el: Element): string {
  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll(
      "script, style, [hidden], .sr-only, button, " +
        "[class*='action'], [class*='feedback'], [class*='citation']",
    )
    .forEach((e) => e.remove());

  const rendered = clone.querySelector(
    ".ac-textBlock, .markdown, [class*='rendered'], [class*='text-content']",
  );
  if (rendered) return safeInnerText(rendered).trim();

  return safeInnerText(clone).trim();
}

export function getModelName(doc: Document = document): string | null {
  const badge = doc.querySelector(
    '[class*="model"], [class*="badge"][class*="gpt"], [data-model]',
  );
  if (!badge) return null;
  const text = (badge.textContent || "").trim();
  return text || badge.getAttribute("data-model") || null;
}

/**
 * Iterate through the 6 legacy selector strategies; return first
 * strategy that yields ≥ 2 messages.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const turnSelectors = [
    '[class*="user-message"], [class*="bot-message"]',
    '[class*="UserMessage"], [class*="BotMessage"]',
    'cib-message-group[source="user"], cib-message-group[source="bot"]',
    '[data-testid*="message"]',
    '[class*="thread-message"]',
    '[class*="turn-"]',
  ];

  for (const sel of turnSelectors) {
    try {
      const els = doc.querySelectorAll(sel);
      if (els.length < 1) continue;

      const collected: ExtractedMessage[] = [];
      els.forEach((el) => {
        const role = detectRole(el);
        const text = extractText(el);
        if (text) {
          collected.push({ role, content: text });
        }
      });

      if (collected.length >= 2) return collected;
    } catch {
      // Skip invalid selectors
    }
  }

  return [];
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://copilot.microsoft.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_COPILOT_ACTIVE) return;
    window.__PCE_COPILOT_ACTIVE = true;

    console.log("[PCE] Copilot content script loaded");

    const runtime = createCaptureRuntime({
      provider: "microsoft",
      sourceName: "copilot-web",
      siteName: "Copilot",
      extractionStrategy: "copilot-dom",
      captureMode: "incremental",

      debounceMs: 2500,
      pollIntervalMs: 5000,

      getContainer: () => getContainer(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
      getModelName: () => getModelName(document),
      hookHistoryApi: false,
    });

    document.addEventListener("pce-manual-capture", () => {
      runtime.triggerCapture();
    });

    if (
      document.readyState === "complete" ||
      document.readyState === "interactive"
    ) {
      runtime.start();
    } else {
      document.addEventListener("DOMContentLoaded", () => runtime.start());
    }
  },
});
