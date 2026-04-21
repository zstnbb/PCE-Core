// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Universal DOM conversation extractor (dynamically injected).
 *
 * TypeScript port of ``content_scripts/universal_extractor.js`` (P2.5
 * Phase 3b batch 5).
 *
 * Dynamically injected by the service worker's ``injector.ts`` when
 * ``detector.content.ts`` identifies an AI page that does NOT have a
 * site-specific extractor (chatgpt, claude, …). Not matched via
 * ``defineContentScript`` — this is an unlisted script emitted at the
 * output root as ``universal-extractor.js`` so the injector can pick
 * it up with ``chrome.scripting.executeScript({ files: […] })``.
 *
 * Strategy:
 *   1. Find the chat container via scored selector candidates.
 *   2. Extract message pairs via role-indicator heuristics.
 *   3. Observe DOM mutations for new messages.
 *   4. Send incremental deltas to the service worker via the shared
 *      capture runtime.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractReplyContent,
  extractThinking,
  fingerprintConversation,
  getSessionHint as sharedGetSessionHint,
  isStreaming as sharedIsStreaming,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_UNIVERSAL_EXTRACTOR_LOADED?: boolean;
    // `__PCE_DETECTOR` is declared with the canonical `DetectorHandle`
    // shape in `detector.content.ts`; we do NOT re-declare it here
    // to avoid interface-merging type conflicts.
    __PCE_CHATGPT_ACTIVE?: boolean;
    __PCE_CLAUDE_ACTIVE?: boolean;
    __PCE_DEEPSEEK_ACTIVE?: boolean;
    __PCE_GEMINI_ACTIVE?: boolean;
    __PCE_GOOGLE_AI_STUDIO_ACTIVE?: boolean;
    __PCE_MANUS_ACTIVE?: boolean;
    __PCE_PERPLEXITY_ACTIVE?: boolean;
    __PCE_GROK_ACTIVE?: boolean;
    __PCE_POE_ACTIVE?: boolean;
    __PCE_COPILOT_ACTIVE?: boolean;
    __PCE_HUGGINGFACE_ACTIVE?: boolean;
    __PCE_ZHIPU_ACTIVE?: boolean;
    __PCE_GENERIC_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Site-specific extractor flags — bail out if one of these is set
// ---------------------------------------------------------------------------

export const SITE_EXTRACTOR_FLAGS: readonly string[] = [
  "__PCE_CHATGPT_ACTIVE",
  "__PCE_CLAUDE_ACTIVE",
  "__PCE_DEEPSEEK_ACTIVE",
  "__PCE_GEMINI_ACTIVE",
  "__PCE_GOOGLE_AI_STUDIO_ACTIVE",
  "__PCE_MANUS_ACTIVE",
  "__PCE_PERPLEXITY_ACTIVE",
  "__PCE_GROK_ACTIVE",
  "__PCE_POE_ACTIVE",
  "__PCE_COPILOT_ACTIVE",
  "__PCE_HUGGINGFACE_ACTIVE",
  "__PCE_ZHIPU_ACTIVE",
  "__PCE_GENERIC_ACTIVE",
];

export function siteExtractorAlreadyActive(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  win: any = globalThis,
): string | null {
  for (const flag of SITE_EXTRACTOR_FLAGS) {
    if (win[flag]) return flag;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Scored container candidates (module-scope for testability)
// ---------------------------------------------------------------------------

export interface ContainerCandidate {
  sel: string;
  score: number;
}

export const CONTAINER_SELECTORS: readonly ContainerCandidate[] = [
  // High specificity: known chat wrapper patterns
  { sel: '[data-testid*="conversation"]', score: 10 },
  { sel: '[class*="conversation-container"]', score: 9 },
  { sel: '[class*="chat-container"]', score: 9 },
  { sel: '[class*="chat-messages"]', score: 9 },
  { sel: '[class*="message-list"]', score: 8 },
  { sel: '[class*="messages-container"]', score: 8 },
  { sel: '[class*="chat-log"]', score: 8 },
  { sel: '[class*="thread-container"]', score: 7 },
  { sel: '[role="log"]', score: 7 },
  // Medium specificity
  { sel: "main [class*='chat']", score: 6 },
  { sel: '[role="feed"]', score: 5 },
  { sel: "main [class*='message']", score: 5 },
  // Low specificity fallbacks
  { sel: "main", score: 2 },
  { sel: '[role="main"]', score: 2 },
];

export function findChatContainer(doc: Document = document): Element | null {
  let best: Element | null = null;
  let bestScore = 0;

  for (const { sel, score } of CONTAINER_SELECTORS) {
    try {
      const el = doc.querySelector(sel);
      if (!el) continue;
      // Compare adjusted scores only. The previous `score > bestScore`
      // short-circuit used the base score against an already-adjusted
      // bestScore, which hid higher-child-count containers behind a
      // lower-base candidate.
      const childCount = el.children.length;
      const adjustedScore = score + Math.min(childCount, 10) * 0.5;
      if (adjustedScore > bestScore) {
        best = el;
        bestScore = adjustedScore;
      }
    } catch {
      // Invalid selector; skip
    }
  }

  return best;
}

// ---------------------------------------------------------------------------
// Role detection — pure predicates
// ---------------------------------------------------------------------------

export const MESSAGE_SELECTORS: readonly string[] = [
  "[data-message-author-role]",
  "[class*='message'][class*='user']",
  "[class*='message'][class*='assistant']",
  "[class*='message'][class*='human']",
  "[class*='message'][class*='bot']",
  "[class*='message'][class*='ai']",
  "[class*='chat-message']",
  "[class*='msg-row']",
  "[class*='turn-']",
  "[data-role]",
  "[data-author]",
];

type RolePredicate = (el: Element) => boolean;

export const USER_INDICATORS: readonly RolePredicate[] = [
  (el) => el.getAttribute("data-message-author-role") === "user",
  (el) => el.getAttribute("data-role") === "user",
  (el) => el.getAttribute("data-author") === "user",
  (el) => /\buser\b/i.test(cls(el)),
  (el) => /\bhuman\b/i.test(cls(el)),
  (el) => /\bmy-message\b/i.test(cls(el)),
  (el) => /\bsent\b/i.test(cls(el)),
  (el) => /\bright\b/i.test(cls(el)) && /\bmessage\b/i.test(cls(el)),
];

export const ASSISTANT_INDICATORS: readonly RolePredicate[] = [
  (el) => el.getAttribute("data-message-author-role") === "assistant",
  (el) => el.getAttribute("data-role") === "assistant",
  (el) => el.getAttribute("data-role") === "bot",
  (el) => el.getAttribute("data-author") === "assistant",
  (el) => /\bassistant\b/i.test(cls(el)),
  (el) => /\bbot\b/i.test(cls(el)),
  (el) => /\bai[-_]?(message|response)\b/i.test(cls(el)),
  (el) => /\breceived\b/i.test(cls(el)),
  (el) => /\bleft\b/i.test(cls(el)) && /\bmessage\b/i.test(cls(el)),
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function cls(el: any): string {
  return (el?.className ?? "").toString();
}

export function detectRole(el: Element): "user" | "assistant" | null {
  for (const test of USER_INDICATORS) {
    try {
      if (test(el)) return "user";
    } catch {
      // Ignore predicate errors
    }
  }
  for (const test of ASSISTANT_INDICATORS) {
    try {
      if (test(el)) return "assistant";
    } catch {
      // Ignore predicate errors
    }
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function offsetHeight(el: any): number {
  return typeof el?.offsetHeight === "number" ? el.offsetHeight : 0;
}

export function extractTextContent(el: Element | null): string {
  if (!el) return "";
  const shared = extractReplyContent(el);
  if (shared) return shared;
  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll("script, style, [hidden], .sr-only")
    .forEach((e) => e.remove());
  const prose = clone.querySelector(
    ".markdown, .prose, .markdown-body, [class*='rendered']",
  );
  if (prose) return safeInnerText(prose).trim();
  return safeInnerText(clone).trim();
}

/**
 * Run Strategy A (role-marked message selectors → role detection)
 * and fall back to Strategy B (alternating container children).
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  // Strategy A: explicitly-marked message elements.
  for (const sel of MESSAGE_SELECTORS) {
    try {
      const els = doc.querySelectorAll(sel);
      if (els.length < 2) continue;

      const collected: ExtractedMessage[] = [];
      els.forEach((el) => {
        const role = detectRole(el);
        if (!role) return;
        const msg = buildMessageForRole(el, role);
        if (msg) collected.push(msg);
      });
      if (collected.length >= 2) return collected;
    } catch {
      // Skip invalid selector
    }
  }

  // Strategy B: alternating children in chat container.
  const container = findChatContainer(doc);
  if (!container) return [];

  const children = Array.from(container.children).filter((el) => {
    const text = safeInnerText(el).trim();
    return text.length > 2 && offsetHeight(el) > 0;
  });
  if (children.length < 2) return [];

  let hasRoles = false;
  const withRoles = children.map((el) => {
    const role = detectRole(el);
    if (role) hasRoles = true;
    return { el, role };
  });

  const messages: ExtractedMessage[] = [];
  if (hasRoles) {
    for (const { el, role } of withRoles) {
      if (!role) continue;
      const msg = buildMessageForRole(el, role);
      if (msg) messages.push(msg);
    }
  } else {
    children.forEach((el, i) => {
      const role = i % 2 === 0 ? "user" : "assistant";
      const text = extractTextContent(el);
      if (text && text.length > 1) {
        const msg: ExtractedMessage = { role, content: text };
        const att = extractAttachments(el);
        if (att.length > 0) msg.attachments = att;
        messages.push(msg);
      }
    });
  }

  return messages;
}

function buildMessageForRole(
  el: Element,
  role: "user" | "assistant",
): ExtractedMessage | null {
  let text = "";
  if (role === "assistant") {
    const thinking = extractThinking(el);
    const reply = extractTextContent(el);
    if (thinking) text += "<thinking>\n" + thinking + "\n</thinking>\n\n";
    if (reply) text += reply;
    text = text.trim();
  } else {
    text = extractTextContent(el);
  }
  if (!text || text.length <= 1) return null;
  const msg: ExtractedMessage = { role, content: text };
  const att = extractAttachments(el);
  if (att.length > 0) msg.attachments = att;
  return msg;
}

// ---------------------------------------------------------------------------
// Provider guessing
// ---------------------------------------------------------------------------

export function guessProvider(
  hostname: string = location.hostname,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  win: any = globalThis,
): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const detector: any = win.__PCE_DETECTOR;
  const detectorDomains: ReadonlySet<string> | undefined =
    detector?.KNOWN_AI_DOMAINS;
  const host = hostname;
  if (detectorDomains && detectorDomains.has(host)) {
    const parts = host.replace("www.", "").split(".");
    return parts[0] || "unknown";
  }
  return host.replace("www.", "").split(".")[0] || "unknown";
}

// ---------------------------------------------------------------------------
// Entry point — dynamically injected, so it's a `defineUnlistedScript`
// ---------------------------------------------------------------------------

export default defineUnlistedScript(() => {
  if (window.__PCE_UNIVERSAL_EXTRACTOR_LOADED) return;
  window.__PCE_UNIVERSAL_EXTRACTOR_LOADED = true;

  const active = siteExtractorAlreadyActive(window);
  if (active) {
    console.debug(
      `[PCE Universal] Skipping — site-specific extractor already active (${active})`,
    );
    return;
  }

  console.log(
    `[PCE Universal] Loaded for ${location.hostname}`,
  );

  const runtime = createCaptureRuntime({
    provider: guessProvider(location.hostname, window),
    sourceName: "chrome-ext-universal",
    siteName: "Universal",
    extractionStrategy: "universal-dom",
    captureMode: "incremental",

    debounceMs: 2500,
    streamCheckMs: 1500,
    pollIntervalMs: 5000,

    getContainer: () => findChatContainer(document),
    isStreaming: () => sharedIsStreaming(document),
    extractMessages: () => extractMessages(document),
    getSessionHint: () => sharedGetSessionHint() || null,
    getModelName: () => null,
    hookHistoryApi: false,
  });

  // Attach to a debug handle so callers can inspect fingerprint /
  // sentCount during development (mirrors the legacy `__PCE_UNIVERSAL_*`
  // surface).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).__PCE_UNIVERSAL_RUNTIME = runtime;

  document.addEventListener("pce-manual-capture", () => {
    console.log("[PCE Universal] Manual capture triggered");
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

  // Silence the otherwise-unused fingerprint reference.
  void fingerprintConversation;
});
