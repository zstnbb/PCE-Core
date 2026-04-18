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

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : null;
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
): ExtractedMessage[] {
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
    if (!text || text.length < 3) return;

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

  // Strategy 3
  const container = getContainer(doc);
  if (container) {
    const directChildren = container.querySelectorAll(":scope > div");
    if (directChildren.length >= 2) {
      const strat3: ExtractedMessage[] = [];
      directChildren.forEach((el, i) => {
        const text = safeInnerText(el).trim();
        if (text && text.length > 5) {
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
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
      hookHistoryApi: false,
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
