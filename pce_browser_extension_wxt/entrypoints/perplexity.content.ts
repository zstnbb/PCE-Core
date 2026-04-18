// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Perplexity content script (www.perplexity.ai).
 *
 * TypeScript port of ``content_scripts/perplexity.js`` (P2.5 Phase 3b
 * batch 2).
 *
 * Perplexity uses a search-like UI with follow-up questions and cited
 * answers, rendered inside thread-style containers whose class names
 * include keywords like ``query``, ``answer``, ``prose``. The legacy
 * extractor ran through 6 thread selectors in priority order, fell
 * back to pairing ``[class*="query"]`` / ``[class*="answer"]`` nodes
 * by index, then dedup'd with a 240-char prefix key.
 *
 * This TS port uses the shared capture runtime + preserves the
 * extraction ladder byte-for-byte. Incremental delta mode, 2500 ms
 * debounce, 5000 ms SPA polling, no history-API hook — identical to
 * the legacy file.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import { extractAttachments } from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_PERPLEXITY_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Perplexity-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/(?:search|thread)\/([a-zA-Z0-9_-]+)/;

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector(
      "main, [class*='thread'], [class*='search-results']",
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

export function detectRole(el: Element): "user" | "assistant" | "unknown" {
  const cls = ((el as Element).className || "").toString().toLowerCase();
  const testId = (el.getAttribute("data-testid") || "").toLowerCase();

  if (
    cls.includes("query") ||
    cls.includes("question") ||
    cls.includes("user") ||
    testId.includes("query")
  ) {
    return "user";
  }
  if (el.tagName === "H1" && cls.includes("group/query")) {
    return "user";
  }
  if (el.tagName === "PRE" || cls.includes("not-prose")) {
    return "unknown";
  }
  if (
    cls.includes("answer") ||
    cls.includes("response") ||
    cls.includes("prose") ||
    testId.includes("answer")
  ) {
    return "assistant";
  }
  return "unknown";
}

export function extractText(el: Element | null): string {
  if (!el) return "";
  const cls = ((el as Element).className || "").toString().toLowerCase();
  if (el.tagName === "PRE" || cls.includes("not-prose")) return "";

  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll(
      "script, style, [hidden], .sr-only, button, [class*='citation'], " +
        "[class*='source'], [class*='action'], [class*='feedback'], " +
        "[class*='related']",
    )
    .forEach((e) => e.remove());

  const markdown = clone.querySelector(
    ".prose, .markdown, [class*='markdown'], [class*='rendered']",
  );
  if (markdown) return safeInnerText(markdown).trim();

  return safeInnerText(clone).trim();
}

function dedupeMessages(messages: ExtractedMessage[]): ExtractedMessage[] {
  const deduped: ExtractedMessage[] = [];
  const seen = new Set<string>();
  for (const msg of messages) {
    if (!msg || !msg.role || msg.role === "unknown" || !msg.content) continue;
    const key = `${msg.role}:${msg.content.slice(0, 240)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(msg);
  }
  return deduped;
}

/**
 * Run through the 6 legacy thread selectors + index-pair fallback.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const turnSelectors = [
    '[class*="ThreadMessage"]',
    '[class*="thread-message"]',
    '[class*="QueryBlock"], [class*="AnswerBlock"]',
    '[class*="query-block"], [class*="answer-block"]',
    '[data-testid*="message"]',
    '[class*="prose"]',
  ];

  for (const sel of turnSelectors) {
    try {
      const els = doc.querySelectorAll(sel);
      if (els.length < 1) continue;

      const collected: ExtractedMessage[] = [];
      els.forEach((el) => {
        const role = detectRole(el);
        const text = extractText(el);
        if (role === "unknown" || !text || text.length <= 3) return;
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
      // Skip invalid selectors and continue
    }
  }

  // Fallback: pair queries[i] with answers[i] by index.
  const queries = doc.querySelectorAll(
    '[class*="query"], [class*="question"], [class*="search-query"]',
  );
  const answers = doc.querySelectorAll(
    '[class*="answer"], [class*="prose"], [class*="response"]',
  );

  const fallback: ExtractedMessage[] = [];
  if (queries.length > 0 && answers.length > 0) {
    const maxPairs = Math.min(queries.length, answers.length);
    for (let i = 0; i < maxPairs; i++) {
      const qEl = queries[i];
      const aEl = answers[i];
      const qText = safeInnerText(qEl).trim();
      const aText = extractText(aEl);
      if (qText && qText.length > 1) {
        const msg: ExtractedMessage = { role: "user", content: qText };
        const att = extractAttachments(qEl);
        if (att.length > 0) msg.attachments = att;
        fallback.push(msg);
      }
      if (aText && aText.length > 1) {
        const msg: ExtractedMessage = { role: "assistant", content: aText };
        const att = extractAttachments(aEl);
        if (att.length > 0) msg.attachments = att;
        fallback.push(msg);
      }
    }
  }

  return dedupeMessages(fallback);
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://www.perplexity.ai/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_PERPLEXITY_ACTIVE) return;
    window.__PCE_PERPLEXITY_ACTIVE = true;

    console.log("[PCE] Perplexity content script loaded");

    const runtime = createCaptureRuntime({
      provider: "perplexity",
      sourceName: "perplexity-web",
      siteName: "Perplexity",
      extractionStrategy: "perplexity-dom",
      captureMode: "incremental",

      debounceMs: 2500,
      pollIntervalMs: 5000,

      getContainer: () => getContainer(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
      // Perplexity legacy always reports null model_name.
      getModelName: () => null,
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
