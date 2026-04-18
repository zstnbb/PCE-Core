// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — ChatGPT content script (chatgpt.com / chat.openai.com).
 *
 * TypeScript port of ``content_scripts/chatgpt.js`` (P2.5 Phase 3b).
 *
 * All the generic plumbing — MutationObserver lifecycle, fingerprint
 * diffing, incremental delta computation, debounce, SPA nav hooks,
 * ``chrome.runtime.sendMessage`` with rollback — lives in
 * ``utils/capture-runtime.ts``. This file is responsible for:
 *
 *   1. Declaring the match list via ``defineContentScript``.
 *   2. The 5 ChatGPT-specific extraction strategies.
 *   3. The Deep Research / Canvas fallback that looks for large
 *      text blocks outside the normal message structure.
 *   4. ChatGPT's synthetic new-chat ID (``"_new_" + timestamp``)
 *      behaviour for conversations that haven't received their
 *      ``/c/<uuid>`` URL yet.
 *   5. Wiring up the ``pce-manual-capture`` DOM event (listened to via
 *      ``installManualCaptureBridge`` in ``utils/pce-dom.ts``).
 *
 * Every extraction heuristic and regex is lifted byte-for-byte from
 * the legacy JS to preserve capture parity. Type annotations and
 * imports from ``@/utils/*`` are the only additions.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractReplyContent,
  extractThinking,
  installManualCaptureBridge,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_CHATGPT_ACTIVE?: boolean;
    __PCE_BEHAVIOR?: {
      getBehaviorSnapshot?: (reset?: boolean) => Record<string, unknown>;
    };
  }
}

// ---------------------------------------------------------------------------
// ChatGPT-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const CONV_URL_RE = /\/c\/([a-f0-9-]+)/;

export function getConvId(pathname: string = location.pathname): string | null {
  const m = pathname.match(CONV_URL_RE);
  return m ? m[1] : null;
}

export function getContainer(doc: Document = document): Element | null {
  for (const sel of [
    '[class*="react-scroll-to-bottom"]',
    'main [role="presentation"]',
    'main [role="log"]',
    '[class*="conversation"]',
    "main",
  ]) {
    const el = doc.querySelector(sel);
    if (el) return el;
  }
  return null;
}

export function isStreamingChatGPT(doc: Document = document): boolean {
  // The generic stop-button / streaming-class test is covered by
  // utils/pce-dom.ts, but the runtime only invokes our isStreaming
  // hook — so check both here.
  const stopBtn = doc.querySelector(
    'button[aria-label*="Stop"], button[data-testid*="stop"]',
  );
  if (stopBtn) return true;
  const main = doc.querySelector("main");
  if (main && main.querySelector('[class*="agent-turn"] [class*="streaming"]')) {
    return true;
  }
  if (main && main.querySelector('[class*="result-streaming"], [class*="streaming"]')) {
    return true;
  }
  return false;
}

/**
 * Look for Deep Research reports / Canvas artifacts that live outside
 * the normal ``[data-message-author-role]`` structure. Returns an
 * assistant-role message, or ``null`` if nothing substantial found.
 */
export function extractSpecialContent(
  doc: Document = document,
): ExtractedMessage | null {
  const main = doc.querySelector("main");
  if (!main) return null;

  const candidates = main.querySelectorAll(
    'article, [class*="research"], [class*="report"], [class*="deep-dive"], ' +
      '[class*="canvas-content"], [role="article"], [role="document"], ' +
      '[class*="result-content"], section',
  );

  let bestText = "";
  for (const el of Array.from(candidates)) {
    if (el.closest('[data-message-author-role="user"]')) continue;
    const text = safeInnerText(el).trim();
    if (text.length > bestText.length && text.length > 50) bestText = text;
  }

  if (!bestText) {
    const scrollables = main.querySelectorAll(
      '[class*="scroll"], [class*="overflow"]',
    );
    const roleRoot = main.querySelector("[data-message-author-role]");
    for (const el of Array.from(scrollables)) {
      if (el.closest('[data-message-author-role="user"]')) continue;
      if (roleRoot && el.contains(roleRoot)) continue;
      const text = safeInnerText(el).trim();
      if (text.length > bestText.length && text.length > 100) bestText = text;
    }
  }

  if (!bestText) {
    const turns = main.querySelectorAll(
      '[data-testid^="conversation-turn"]',
    );
    for (const turn of Array.from(turns)) {
      const roleEl = turn.querySelector("[data-message-author-role]");
      if (
        roleEl &&
        roleEl.getAttribute("data-message-author-role") === "user"
      ) {
        continue;
      }
      const text = safeInnerText(turn).trim();
      if (text.length > bestText.length && text.length > 100) bestText = text;
    }
  }

  if (bestText.length > 50) {
    return { role: "assistant", content: bestText, attachments: [] };
  }
  return null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

/**
 * Run through the 5 legacy extraction strategies in priority order.
 * Returns the first non-empty list.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  // Strategy A: [data-message-author-role] elements (current 2024-25 layout)
  const roleEls = doc.querySelectorAll("[data-message-author-role]");
  if (roleEls.length > 0) {
    const messages: ExtractedMessage[] = [];
    roleEls.forEach((el) => {
      const role = el.getAttribute("data-message-author-role");
      if (role === "user") {
        const text = safeInnerText(el).trim();
        if (text) {
          const attSource =
            el.closest('[data-testid^="conversation-turn"]') ||
            el.parentElement ||
            el;
          const att = extractAttachments(attSource);
          const msg: ExtractedMessage = { role: "user", content: text };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
      } else {
        const thinking = extractThinking(el);
        const reply = extractReplyContent(el);
        if (thinking || reply) {
          let content = "";
          if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
          if (reply) content += reply;
          const attSource =
            el.closest('[data-testid^="conversation-turn"]') ||
            el.parentElement ||
            el;
          const att = extractAttachments(attSource);
          const msg: ExtractedMessage = {
            role: role || "assistant",
            content: content.trim(),
          };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
      }
    });
    const hasNonUser = messages.some((m) => m.role !== "user");
    if (!hasNonUser && messages.length > 0) {
      const special = extractSpecialContent(doc);
      if (special) messages.push(special);
    }
    if (messages.length > 0) return messages;
  }

  // Strategy B: conversation-turn testids
  const turns = doc.querySelectorAll('[data-testid^="conversation-turn"]');
  if (turns.length > 0) {
    const messages: ExtractedMessage[] = [];
    turns.forEach((turn, i) => {
      const roleAttr = turn
        .querySelector("[data-message-author-role]")
        ?.getAttribute("data-message-author-role");
      const role = roleAttr || (i % 2 === 0 ? "user" : "assistant");
      if (role === "user") {
        const text = safeInnerText(turn).trim();
        if (text) {
          const att = extractAttachments(turn);
          const msg: ExtractedMessage = { role: "user", content: text };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
      } else {
        const thinking = extractThinking(turn);
        const reply = extractReplyContent(turn);
        if (thinking || reply) {
          let content = "";
          if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
          if (reply) content += reply;
          const att = extractAttachments(turn);
          const msg: ExtractedMessage = { role, content: content.trim() };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
      }
    });
    if (messages.length > 0) return messages;
  }

  // Strategy C: article elements
  const articles = doc.querySelectorAll("main article");
  if (articles.length > 0) {
    const messages: ExtractedMessage[] = [];
    articles.forEach((el, i) => {
      const text = safeInnerText(el).trim();
      if (text && text.length > 3) {
        const roleAttr = el
          .querySelector("[data-message-author-role]")
          ?.getAttribute("data-message-author-role");
        const role = roleAttr || (i % 2 === 0 ? "user" : "assistant");
        const att = extractAttachments(el);
        const msg: ExtractedMessage = { role, content: text };
        if (att.length > 0) msg.attachments = att;
        messages.push(msg);
      }
    });
    if (messages.length > 0) return messages;
  }

  // Strategy D: data-message-id elements
  const msgIdEls = doc.querySelectorAll("[data-message-id]");
  if (msgIdEls.length > 0) {
    const messages: ExtractedMessage[] = [];
    msgIdEls.forEach((el, i) => {
      const roleAttr =
        el.getAttribute("data-message-author-role") ||
        el
          .closest("[data-message-author-role]")
          ?.getAttribute("data-message-author-role");
      const role = roleAttr || (i % 2 === 0 ? "user" : "assistant");
      const text =
        role === "user" ? safeInnerText(el).trim() : extractReplyContent(el);
      if (text && text.length > 1) {
        const att = extractAttachments(el);
        const msg: ExtractedMessage = { role, content: text };
        if (att.length > 0) msg.attachments = att;
        messages.push(msg);
      }
    });
    if (messages.length > 0) return messages;
  }

  // Strategy E: ARIA role-based generic
  const container = getContainer(doc);
  if (container) {
    const rows = container.querySelectorAll(
      '[role="row"], [role="listitem"], [role="group"]',
    );
    if (rows.length >= 2) {
      const messages: ExtractedMessage[] = [];
      rows.forEach((el, i) => {
        const text = safeInnerText(el).trim();
        if (text && text.length > 3) {
          messages.push({
            role: i % 2 === 0 ? "user" : "assistant",
            content: text,
          });
        }
      });
      return messages;
    }
  }

  return [];
}

/**
 * ChatGPT's getModelName: first element matching ``[class*="model"]``
 * or ``[data-testid*="model"]``.
 */
export function getModelName(doc: Document = document): string | null {
  const el = doc.querySelector(
    '[class*="model"], [data-testid*="model"]',
  );
  if (!el) return null;
  const t = safeInnerText(el).trim();
  return t || null;
}

/**
 * Resolve the conversation-id for the capture payload:
 *   - /c/<uuid>            → <uuid>
 *   - otherwise + ≥2 msgs  → "_new_" + timestamp
 *   - otherwise            → null (runtime falls back to session_hint)
 */
export function resolveConversationId(
  messages: ExtractedMessage[],
  pathname: string = location.pathname,
  now: number = Date.now(),
): string | null {
  const existing = getConvId(pathname);
  if (existing) return existing;
  if (messages.length >= 2) return "_new_" + now.toString(36);
  return null;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://chatgpt.com/*", "https://chat.openai.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_CHATGPT_ACTIVE) return;
    window.__PCE_CHATGPT_ACTIVE = true;

    console.log("[PCE] ChatGPT content script loaded on", window.location.href);

    let currentConvId = getConvId();

    const runtime = createCaptureRuntime({
      provider: "openai",
      sourceName: "chatgpt-web",
      siteName: "ChatGPT",
      extractionStrategy: "dom-incremental",
      captureMode: "incremental",

      debounceMs: 2000,
      streamCheckMs: 1500,
      pollIntervalMs: 3000,
      maxContainerRetries: 15,

      getContainer: () => getContainer(document),
      isStreaming: () => isStreamingChatGPT(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getConvId(),
      getModelName: () => getModelName(document),
      resolveConversationId: (messages: ExtractedMessage[]) =>
        resolveConversationId(messages, location.pathname, Date.now()),

      hookHistoryApi: true,
      onNavChange: () => {
        const id = getConvId();
        if (id !== currentConvId) {
          console.log("[PCE] Nav:", currentConvId, "->", id);
          currentConvId = id;
        }
      },
    });

    // Manual-capture bridge from utils/pce-dom.ts emits a
    // ``pce-manual-capture`` CustomEvent when the user triggers a
    // capture from the popup / shortcut.
    installManualCaptureBridge(document);
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
