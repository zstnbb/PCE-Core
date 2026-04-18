// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — HuggingFace Chat content script (huggingface.co/chat).
 *
 * TypeScript port of ``content_scripts/huggingface.js`` (P2.5 Phase 3b
 * batch 4).
 *
 * HuggingFace Chat hosts open-source models in a clean chat UI. The
 * legacy extractor ran through 4 turn-selector strategies with role
 * inference via class keywords / ``data-message-role`` attributes,
 * then fell back to alternating children of the chat container when
 * nothing matched. Model name resolution has three strategies:
 * header-selector, ``/chat/models/<name>`` URL, and title prefix.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";

declare global {
  interface Window {
    __PCE_HUGGINGFACE_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// HuggingFace-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/chat\/conversation\/([a-f0-9]+)/i;
const MODEL_URL_RE = /\/chat\/models\/([^/?]+)/;

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector(
      "[class*='chat-container'], [class*='messages'], main",
    ) || doc.body
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function offsetHeight(el: any): number {
  return typeof el?.offsetHeight === "number" ? el.offsetHeight : 0;
}

export function detectRole(el: Element): "user" | "assistant" | "unknown" {
  const cls = ((el as Element).className || "").toString().toLowerCase();
  const role = (el.getAttribute("data-message-role") || "").toLowerCase();

  if (role === "user" || cls.includes("user") || cls.includes("human")) {
    return "user";
  }
  if (
    role === "assistant" ||
    cls.includes("assistant") ||
    cls.includes("bot") ||
    cls.includes("model")
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
        "[class*='action'], [class*='copy'], [class*='feedback']",
    )
    .forEach((e) => e.remove());

  const markdown = clone.querySelector(
    ".prose, .markdown, [class*='markdown'], [class*='rendered']",
  );
  if (markdown) return safeInnerText(markdown).trim();

  return safeInnerText(clone).trim();
}

export function getModelName(
  doc: Document = document,
  pathname: string = location.pathname,
): string | null {
  const header = doc.querySelector(
    "[class*='model-name'], [class*='ModelName'], " +
      "[class*='model-selector'] [class*='selected']",
  );
  if (header) {
    const text = (header.textContent || "").trim();
    if (text) return text;
  }

  const urlMatch = pathname.match(MODEL_URL_RE);
  if (urlMatch) {
    try {
      return decodeURIComponent(urlMatch[1]);
    } catch {
      return urlMatch[1];
    }
  }

  const titleMatch = doc.title.match(/^(.+?)\s*[-|]/);
  if (titleMatch && titleMatch[1].includes("/")) {
    return titleMatch[1].trim();
  }

  return null;
}

/**
 * Run the 4 turn-selector strategies then fall back to alternating
 * container children.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];

  const turnSelectors = [
    '[class*="message"][class*="user"], [class*="message"][class*="assistant"]',
    "[data-message-role]",
    '[class*="ConversationMessage"]',
    '[class*="chat-message"]',
  ];

  for (const sel of turnSelectors) {
    try {
      const els = doc.querySelectorAll(sel);
      if (els.length < 1) continue;

      els.forEach((el) => {
        const role = detectRole(el);
        const text = extractText(el);
        if (text && text.length > 1) {
          messages.push({ role, content: text });
        }
      });

      if (messages.length >= 2) return messages;
      messages.length = 0;
    } catch {
      // Skip invalid selectors
    }
  }

  // Fallback: alternating container direct-children.
  const container = doc.querySelector(
    "[class*='chat-container'], [class*='messages'], main",
  );
  if (container) {
    const children = Array.from(container.children).filter((el) => {
      const text = safeInnerText(el).trim();
      return text.length > 2 && offsetHeight(el) > 0;
    });

    children.forEach((el) => {
      const role = detectRole(el);
      const text = extractText(el);
      if (text && text.length > 2) {
        messages.push({
          role:
            role !== "unknown"
              ? role
              : messages.length % 2 === 0
                ? "user"
                : "assistant",
          content: text,
        });
      }
    });
  }

  return messages;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://huggingface.co/chat/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_HUGGINGFACE_ACTIVE) return;
    window.__PCE_HUGGINGFACE_ACTIVE = true;

    console.log("[PCE] HuggingFace Chat content script loaded");

    const runtime = createCaptureRuntime({
      provider: "huggingface",
      sourceName: "huggingface-web",
      siteName: "HuggingFace",
      extractionStrategy: "huggingface-dom",
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
