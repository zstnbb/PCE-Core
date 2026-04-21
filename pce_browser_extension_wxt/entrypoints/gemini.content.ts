// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Gemini content script (gemini.google.com).
 *
 * TypeScript port of ``content_scripts/gemini.js`` (P2.5 Phase 3b).
 *
 * Gemini uses Angular web components (``<user-query>``, ``<model-response>``)
 * interleaved with more conventional ``[data-turn-role]`` attributes and
 * class-based markers. The legacy script ran through 4 extraction
 * strategies in priority order and deduplicated the result before
 * returning. All of that behaviour is preserved here.
 *
 * Like ChatGPT, Gemini uses incremental delta capture; unlike ChatGPT,
 * it does NOT intercept ``pushState`` — only URL polling.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractReplyContent,
  extractThinking,
  isStreaming as sharedIsStreaming,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_GEMINI_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Gemini-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/(?:app|chat)\/([a-f0-9]+)/i;

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  return doc.querySelector("main") || doc.body;
}

/**
 * Streaming check (closes P5.B gap **G2**): shared DOM helper OR a
 * Stop/Cancel-generation button by text/aria-label.
 *
 * Passed to ``createCaptureRuntime`` as the ``isStreaming`` gate so
 * mid-stream debounce ticks DON'T fire a partial capture — matches
 * the pattern in ``chatgpt.content.ts`` and ``google-ai-studio.content.ts``.
 */
export function isStreaming(doc: Document = document): boolean {
  if (sharedIsStreaming(doc)) return true;
  const buttons = doc.querySelectorAll("button");
  for (const btn of Array.from(buttons)) {
    const label = `${safeInnerText(btn) || ""} ${
      btn.getAttribute("aria-label") || ""
    }`.trim();
    if (/stop generating|stop response|cancel/i.test(label)) return true;
  }
  return false;
}

export function getModelName(doc: Document = document): string | null {
  const el = doc.querySelector(
    '[class*="model-selector"] [class*="selected"], [data-model-name], .model-badge',
  );
  if (!el) return null;
  const t = (el.textContent || "").trim();
  return t || null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

export function detectRole(el: Element): "user" | "assistant" | "unknown" {
  const tag = (el.tagName || "").toLowerCase();
  const cls = ((el as Element).className || "").toString().toLowerCase();
  const role = el.getAttribute("data-turn-role") || "";

  if (
    tag === "user-query" ||
    role === "user" ||
    cls.includes("user") ||
    cls.includes("query")
  ) {
    return "user";
  }
  if (
    tag === "model-response" ||
    role === "model" ||
    cls.includes("model") ||
    cls.includes("response") ||
    cls.includes("answer")
  ) {
    return "assistant";
  }
  return "unknown";
}

export function extractText(el: Element): string {
  const tag = (el.tagName || "").toLowerCase();
  const cls = ((el as Element).className || "").toString().toLowerCase();
  const isAssistantTurn =
    tag === "model-response" ||
    tag === "message-content" ||
    cls.includes("model-response-text") ||
    cls.includes("response-container");

  if (isAssistantTurn) {
    // Closes P5.B gap **G3**: wrap the Gemini 2.5 Pro "Thinking" panel
    // (which renders inside a <details> collapsible) in a <thinking> block
    // ahead of the reply body. Matches the pattern in ChatGPT / Claude / GAS.
    const thinking = extractThinking(el);
    // Clone + strip <details> subtrees so ``extractReplyContent`` doesn't
    // pick the first <.markdown> *inside* the Thinking panel as the reply.
    const replyRoot = el.cloneNode(true) as Element;
    replyRoot.querySelectorAll("details").forEach((d) => d.remove());
    const reply = extractReplyContent(replyRoot);
    if (thinking || reply) {
      let content = "";
      if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
      if (reply) content += reply;
      return content.trim();
    }
  }

  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll(
      "script, style, [hidden], .sr-only, .chip-container, .action-button",
    )
    .forEach((e) => e.remove());

  const markdown = clone.querySelector(
    ".markdown, .markdown-main-panel, message-content",
  );
  if (markdown) return safeInnerText(markdown).trim();

  return safeInnerText(clone).trim();
}

function dedupeMessages(messages: ExtractedMessage[]): ExtractedMessage[] {
  const deduped: ExtractedMessage[] = [];
  const seen = new Set<string>();
  for (const msg of messages) {
    if (!msg || !msg.role || !msg.content) continue;
    const key = `${msg.role}:${msg.content.slice(0, 200)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(msg);
  }
  return deduped;
}

/**
 * Run through the 4 legacy extraction strategies, returning the first
 * that produces messages containing both user AND assistant roles.
 *
 * Strategy 1-4 are Gemini-specific turn selectors. The fallback is
 * ``[class*="turn"]`` generic role detection + dedup.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const turnSelectors = [
    "model-response, user-query",
    '[class*="query-text"], [class*="response-container"]',
    "[data-turn-role]",
    ".conversation-container .turn-content",
    "message-content",
  ];

  for (const sel of turnSelectors) {
    try {
      const els = doc.querySelectorAll(sel);
      if (els.length < 1) continue;

      const collected: ExtractedMessage[] = [];
      els.forEach((el) => {
        const role = detectRole(el);
        const text = extractText(el);
        if (role === "unknown" || !text || text.length <= 1) return;
        const msg: ExtractedMessage = { role, content: text };
        const att = extractAttachments(el);
        if (att.length > 0) msg.attachments = att;
        collected.push(msg);
      });

      const roles = new Set(collected.map((m) => m.role));
      if (collected.length >= 2 && roles.has("user") && roles.has("assistant")) {
        return dedupeMessages(collected);
      }
    } catch {
      // Skip invalid selectors (unknown custom elements) and continue
    }
  }

  // Fallback: alternating .turn containers
  const turns = doc.querySelectorAll('[class*="turn"], [class*="Turn"]');
  const fallback: ExtractedMessage[] = [];
  turns.forEach((el) => {
    const role = detectRole(el);
    const text = extractText(el);
    if (role === "unknown" || !text || text.length <= 2) return;
    const msg: ExtractedMessage = { role, content: text };
    const att = extractAttachments(el);
    if (att.length > 0) msg.attachments = att;
    fallback.push(msg);
  });

  return dedupeMessages(fallback);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://gemini.google.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_GEMINI_ACTIVE) return;
    window.__PCE_GEMINI_ACTIVE = true;

    console.log("[PCE] Gemini content script loaded");

    const runtime = createCaptureRuntime({
      provider: "google",
      sourceName: "gemini-web",
      siteName: "Gemini",
      extractionStrategy: "gemini-dom",
      captureMode: "incremental",

      debounceMs: 2500,
      pollIntervalMs: 5000,

      getContainer: () => getContainer(document),
      isStreaming: () => isStreaming(document),
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
