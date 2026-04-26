// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Claude content script (claude.ai).
 *
 * TypeScript port of ``content_scripts/claude.js`` (P2.5 Phase 3b).
 *
 * Claude has a few quirks that set it apart from ChatGPT / Gemini:
 *
 * 1. **Full capture mode.** The legacy script always resent the entire
 *    conversation when the fingerprint changed, instead of sending an
 *    incremental delta. The capture runtime honours this via
 *    ``captureMode: "full"``.
 *
 * 2. **Position-sorted extraction.** Human and assistant turns are
 *    queried separately (they live in different DOM branches) and
 *    sorted by ``getBoundingClientRect().top`` before being returned,
 *    because DOM order doesn't always match visual order.
 *
 * 3. **No SPA ``pushState`` hook.** The legacy JS only polled
 *    ``location.href`` — Claude navigates with full page reloads
 *    sometimes, and the history API interception was never wired.
 *    We match that behaviour (``hookHistoryApi: false``).
 *
 * 4. **No ``pce-manual-capture`` listener.** The legacy JS never
 *    wired one. We keep the same omission to avoid introducing
 *    behaviour change in this phase.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractThinking,
  installManualCaptureBridge,
  isStreaming as sharedIsStreaming,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_CLAUDE_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Claude-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/chat\/([a-f0-9-]+)/;

// Conversation paths that PCE captures from. Anything else (root,
// /settings/*, /share/*, /recents, /organizations/*, project landing
// without a chat) is idle by definition and must produce zero
// captures (closes P5.B C19 settings-noise gap).
//
// Allowed:
//   /new                                  — transient new chat
//   /chat/<uuid>                          — vanilla chat
//   /project/<id>/chat/<uuid>             — Projects chat
const CONVERSATION_PATH_RE =
  /^\/(?:new(?:$|\/)|chat\/|project\/[^/]+\/chat\/)/i;

// Claude model families (matches "Claude 3.5 Sonnet", "Claude Opus 4",
// "Haiku 3", etc.). Case-insensitive.
const MODEL_NAME_RE = /claude\s+(?:[\d.]+\s+)?(haiku|sonnet|opus)(?:\s+[\d.]+)?/i;

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : null;
}

/**
 * Returns true when the current path is a Claude conversation surface
 * (chat / projects chat / transient /new). Anything else — including
 * /settings/*, /share/*, /recents, /organizations/*, project landing
 * pages, and root — is idle and must NOT produce captures.
 *
 * Closes P5.B C19 (settings-noise) at the extractor layer; the
 * runtime-level ``requireSessionHint: true`` is defence in depth.
 */
export function isConversationPath(
  pathname: string = typeof location !== "undefined" ? location.pathname : "/",
): boolean {
  return CONVERSATION_PATH_RE.test(pathname);
}

/**
 * Resolve the active model name (closes P5.B gap **C5**).
 *
 * Claude's top-right model selector renders as a button with a
 * data-testid and the full model name as its text content. Falls
 * back to any element with a ``model``-containing class, then to a
 * body-text regex matching the Claude Haiku/Sonnet/Opus family.
 *
 * Matches the pattern in ``chatgpt.content.ts`` and ``gemini.content.ts``.
 */
export function getModelName(doc: Document = document): string | null {
  const selectorEl = doc.querySelector(
    '[data-testid="model-selector-button"], [data-testid*="model-selector"], [aria-label*="Model" i]',
  );
  if (selectorEl) {
    const text = safeInnerText(selectorEl).trim();
    if (text && text.length < 120) return text;
  }

  const classEl = doc.querySelector('[class*="model" i]');
  if (classEl) {
    const text = safeInnerText(classEl).trim();
    if (text && text.length < 120 && MODEL_NAME_RE.test(text)) {
      return text;
    }
  }

  const bodyText = doc.body ? safeInnerText(doc.body) : "";
  const match = bodyText.match(MODEL_NAME_RE);
  return match ? match[0] : null;
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector('[class*="conversation-content"]') ||
    doc.querySelector('[class*="chat-messages"]') ||
    doc.querySelector('[class*="thread-content"]') ||
    doc.querySelector('[role="log"]') ||
    doc.querySelector("main .flex.flex-col") ||
    doc.querySelector("main")
  );
}

/**
 * Streaming check (closes P5.B gap **C2**): shared DOM helper OR a
 * dedicated `[data-testid="stop-button"]` OR an aria-label/text regex
 * on any button.
 *
 * Passed to ``createCaptureRuntime`` as the ``isStreaming`` gate so
 * mid-stream debounce ticks DON'T fire a partial capture — matches
 * the pattern in ``chatgpt.content.ts`` and ``google-ai-studio.content.ts``.
 */
export function isStreaming(doc: Document = document): boolean {
  if (sharedIsStreaming(doc)) return true;
  // Dedicated Claude UI testid (2024+ layout)
  if (doc.querySelector('[data-testid="stop-button"]')) return true;
  const buttons = doc.querySelectorAll("button");
  for (const btn of Array.from(buttons)) {
    const label = `${safeInnerText(btn) || ""} ${
      btn.getAttribute("aria-label") || ""
    }`.trim();
    if (/stop response|stop generating|cancel/i.test(label)) return true;
  }
  return false;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function elementTop(el: any): number {
  if (!el || typeof el.getBoundingClientRect !== "function") return 0;
  try {
    const rect = el.getBoundingClientRect();
    return typeof rect?.top === "number" ? rect.top : 0;
  } catch {
    return 0;
  }
}

interface PositionedMessage extends ExtractedMessage {
  _top: number;
}

const HUMAN_TURN_SELECTORS = [
  '[data-testid="human-turn"]',
  '[data-testid*="user-message"]',
  '[class*="human-turn"]',
  ".font-user-message",
  '[data-role="user"]',
].join(", ");

const ASSISTANT_TURN_SELECTORS = [
  '[data-testid="assistant-turn"]',
  '[data-testid*="assistant-message"]',
  '[class*="assistant-turn"]',
  ".font-claude-message",
  '[data-role="assistant"]',
].join(", ");

/**
 * Run through the 3 legacy extraction strategies. Returns the first
 * non-empty list.
 *
 * Strategy 1 — dedicated human/assistant selectors, sorted by visual
 * top position.
 * Strategy 2 — generic ``[class*="message"]`` blocks with role
 * detected from class / data-role / ARIA.
 * Strategy 3 — alternating direct children of the conversation
 * container (last resort).
 */
export function extractMessages(
  doc: Document = document,
  pathname: string = typeof location !== "undefined" ? location.pathname : "/",
): ExtractedMessage[] {
  // Closes P5.B C19 (settings-noise) and C9 (shared-chat skip):
  // restrict capture to conversation surfaces. /share/, /settings/*,
  // /recents, /organizations/*, project landing pages, and root all
  // fail this guard and produce zero captures.
  if (!isConversationPath(pathname)) return [];

  // Strategy 1
  const humanTurns = doc.querySelectorAll(HUMAN_TURN_SELECTORS);
  const assistantTurns = doc.querySelectorAll(ASSISTANT_TURN_SELECTORS);

  if (humanTurns.length > 0 || assistantTurns.length > 0) {
    const collected: PositionedMessage[] = [];

    humanTurns.forEach((el) => {
      const text = safeInnerText(el).trim();
      if (!text) return;
      const att = extractAttachments(el);
      const turn: PositionedMessage = {
        role: "user",
        content: text,
        _top: elementTop(el),
      };
      if (att.length > 0) turn.attachments = att;
      collected.push(turn);
    });

    assistantTurns.forEach((el) => {
      const text = safeInnerText(el).trim();
      if (!text) return;
      const thinking = extractThinking(el);
      const content = thinking
        ? "<thinking>\n" + thinking + "\n</thinking>\n\n" + text
        : text;
      const att = extractAttachments(el);
      const turn: PositionedMessage = {
        role: "assistant",
        content,
        _top: elementTop(el),
      };
      if (att.length > 0) turn.attachments = att;
      collected.push(turn);
    });

    collected.sort((a, b) => a._top - b._top);
    return collected.map((t) => {
      const msg: ExtractedMessage = { role: t.role, content: t.content };
      if (t.attachments) msg.attachments = t.attachments;
      return msg;
    });
  }

  // Strategy 2
  const blocks = doc.querySelectorAll(
    '[class*="message"], [class*="Message"], [class*="turn"], [class*="Turn"]',
  );
  const strat2: ExtractedMessage[] = [];
  blocks.forEach((el) => {
    const text = safeInnerText(el).trim();
    if (!text) return;

    const classes = ((el as Element).className || "").toString().toLowerCase();
    const dataRole = el.getAttribute("data-role") || "";
    const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase();
    let role = "unknown";
    if (
      classes.includes("human") ||
      classes.includes("user") ||
      dataRole === "user" ||
      ariaLabel.includes("user") ||
      ariaLabel.includes("human")
    ) {
      role = "user";
    } else if (
      classes.includes("assistant") ||
      classes.includes("claude") ||
      classes.includes("ai") ||
      dataRole === "assistant" ||
      ariaLabel.includes("assistant") ||
      ariaLabel.includes("claude")
    ) {
      role = "assistant";
    }

    if (role !== "unknown") {
      strat2.push({ role, content: text });
    }
  });
  if (strat2.length >= 2) return strat2;

  // Strategy 3 — use Element.children instead of `:scope > div` because
  // some test DOMs (happy-dom) and non-browser environments don't support
  // `:scope`. Keep behaviour otherwise identical (direct-child divs only,
  // alternate user/assistant by visual order).
  const container = getContainer(doc);
  if (container) {
    const directChildren = Array.from(container.children).filter(
      (el) => (el.tagName || "").toUpperCase() === "DIV",
    );
    if (directChildren.length >= 2) {
      const strat3: ExtractedMessage[] = [];
      directChildren.forEach((el, i) => {
        const text = safeInnerText(el).trim();
        if (text) {
          strat3.push({
            role: i % 2 === 0 ? "user" : "assistant",
            content: text,
          });
        }
      });
      if (strat3.length > 0) return strat3;
    }
  }

  return [];
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://claude.ai/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_CLAUDE_ACTIVE) return;
    window.__PCE_CLAUDE_ACTIVE = true;

    console.log("[PCE] Claude content script loaded");

    const runtime = createCaptureRuntime({
      provider: "anthropic",
      sourceName: "claude-web",
      siteName: "Claude",
      extractionStrategy: "dom",
      captureMode: "full",

      debounceMs: 2000,
      pollIntervalMs: 3000,

      getContainer: () => getContainer(document),
      isStreaming: () => isStreaming(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
      getModelName: () => getModelName(document),
      hookHistoryApi: false,
      // Defence in depth (closes P5.B C19): even if extractMessages
      // somehow returns content on a non-conversation path (e.g. a
      // future DOM-extractor regression), the runtime defers capture
      // until getSessionHint() returns a real /chat/<uuid>.
      requireSessionHint: true,
    });

    // Closes P5.B gap **C6**: wire the shared ``pce-manual-capture`` DOM
    // event so the tray / shortcut can force a capture. Matches the
    // pattern in ``chatgpt.content.ts`` / ``gemini.content.ts`` /
    // ``google-ai-studio.content.ts``.
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
