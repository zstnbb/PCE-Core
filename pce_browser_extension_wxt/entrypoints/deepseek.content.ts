// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — DeepSeek content script (chat.deepseek.com).
 *
 * TypeScript port of ``content_scripts/deepseek.js`` (P2.5 Phase 3b
 * batch 2).
 *
 * DeepSeek uses a React-based chat UI with hashed CSS class names.
 * The ONLY stable selector is ``.ds-markdown`` for assistant responses,
 * so the extractor anchors on that and walks up the DOM to find
 * conversation turns. Fallbacks cover ``[data-role]`` /
 * ``[data-message-role]`` patterns and a last-resort large-text-block
 * heuristic.
 *
 * Incremental delta mode, 2500 ms debounce, 5000 ms SPA polling, no
 * history-API hook — identical to the legacy file. The initial
 * capture is delayed by 1000 ms on first observer attach to let the
 * hydrated DOM settle; we preserve that via the runtime's built-in
 * initial ``schedule()`` (debounceMs = 2500 ms ≥ 1000 ms, so the
 * DeepSeek-specific 1s delay becomes a no-op under the shared
 * runtime — any observer-initiated capture is debounced by 2500 ms
 * anyway).
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractThinking,
  isStreaming as sharedIsStreaming,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_DEEPSEEK_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// DeepSeek-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/chat(?:\/s)?\/([a-zA-Z0-9_-]+)/;

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
 * Streaming check (closes P5.B gap **DS1**, mirrors G2/C2/MCP1/PX2):
 * shared DOM helper OR a Stop/Cancel button by text/aria-label.
 * Passed as ``isStreaming`` to ``createCaptureRuntime`` so mid-stream
 * debounce ticks DON'T fire a partial capture.
 */
export function isStreaming(doc: Document = document): boolean {
  if (sharedIsStreaming(doc)) return true;
  const buttons = doc.querySelectorAll("button");
  for (const btn of Array.from(buttons)) {
    const label = `${safeInnerText(btn) || ""} ${
      btn.getAttribute("aria-label") || ""
    }`.trim();
    if (/stop generating|stop response|cancel|停止|取消/i.test(label)) return true;
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

/**
 * Walk up from ``.ds-markdown`` to find a container whose parent has
 * ≥ 2 children, where one subtree contains ``.ds-markdown`` and
 * another holds visible user text (no nested markdown block). Returns
 * the parent container or null when no suitable ancestor found within
 * 6 levels.
 */
export function findTurnContainer(mdElement: Element): Element | null {
  let el: Element | null = mdElement.parentElement;
  for (let i = 0; i < 6 && el; i++) {
    const parent: Element | null = el.parentElement;
    if (!parent) break;

    const siblings = Array.from(parent.children);
    if (siblings.length >= 2) {
      const mdSibling = siblings.find((s) => s.contains(mdElement));
      const otherSiblings = siblings.filter((s) => s !== mdSibling);
      const hasUserContent = otherSiblings.some((s) => {
        const text = safeInnerText(s).trim();
        return (
          !!text && text.length > 0 && !s.querySelector(".ds-markdown")
        );
      });
      if (hasUserContent) return parent;
    }
    el = parent;
  }
  return null;
}

/**
 * Strip noise (icons, buttons, avatars, toolbars) and return the
 * cleaned text. If the element contains a ``.ds-markdown`` block,
 * return that block's text directly.
 */
export function cleanText(el: Element | null): string {
  if (!el) return "";
  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll(
      "script, style, [hidden], .sr-only, button, svg, img, " +
        "[class*='icon'], [class*='avatar'], [class*='action'], " +
        "[class*='copy'], [class*='toolbar']",
    )
    .forEach((e) => e.remove());

  const markdown = clone.querySelector(".ds-markdown");
  if (markdown) return safeInnerText(markdown).trim();

  return safeInnerText(clone).trim();
}

/**
 * Find the child of ``turnEl`` that does NOT contain the given
 * ``.ds-markdown`` element and return its cleaned text as the user
 * message content.
 */
export function extractUserFromTurn(
  turnEl: Element,
  mdElement: Element,
): string | null {
  const children = Array.from(turnEl.children);
  for (const child of children) {
    if (child.contains(mdElement)) continue;
    const text = cleanText(child);
    if (text && text.length > 0 && !child.querySelector(".ds-markdown")) {
      return text;
    }
  }
  return null;
}

/**
 * Run through the 3 legacy extraction strategies in priority order.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];

  // Strategy 1: anchor on .ds-markdown blocks
  const mdBlocks = doc.querySelectorAll(
    ".ds-markdown, [class*='ds-markdown'], [class*='markdown-body'], .chat-markdown",
  );
  if (mdBlocks.length > 0) {
    mdBlocks.forEach((md) => {
      const assistantText = safeInnerText(md).trim();
      if (!assistantText || assistantText.length < 2) return;

      const turnEl = findTurnContainer(md);
      if (turnEl) {
        const userText = extractUserFromTurn(turnEl, md);
        if (userText && userText.length > 0) {
          const userAtt = extractAttachments(turnEl);
          const userMsg: ExtractedMessage = { role: "user", content: userText };
          if (userAtt.length > 0) userMsg.attachments = userAtt;
          messages.push(userMsg);
        }
      }

      const assistantScope: Element =
        md.closest("[class]")?.parentElement || (md as Element);
      const thinking = extractThinking(assistantScope);
      const att = extractAttachments(assistantScope);
      let content = assistantText;
      if (thinking) {
        content = "<thinking>\n" + thinking + "\n</thinking>\n\n" + content;
      }
      const assistantMsg: ExtractedMessage = { role: "assistant", content };
      if (att.length > 0) assistantMsg.attachments = att;
      messages.push(assistantMsg);
    });

    if (messages.length >= 2) return messages;
  }

  // Strategy 2: role-attributed elements
  messages.length = 0;
  const roleEls = doc.querySelectorAll(
    '[data-role], [data-message-role], [data-testid*="message"]',
  );
  if (roleEls.length >= 2) {
    roleEls.forEach((el) => {
      const role =
        el.getAttribute("data-role") ||
        el.getAttribute("data-message-role") ||
        (el.getAttribute("data-testid") || "").replace(/.*message-/, "");
      const text = cleanText(el);
      if (
        text &&
        text.length > 1 &&
        (role === "user" || role === "assistant")
      ) {
        messages.push({ role, content: text });
      }
    });
    if (messages.length >= 2) return messages;
    messages.length = 0;
  }

  // Strategy 3: scan for large text blocks
  const main = doc.querySelector("main") || doc.body;
  if (!main) return [];
  const allTextBlocks = main.querySelectorAll("div > div > div");
  const candidates: Array<{ role: string; content: string }> = [];
  allTextBlocks.forEach((el) => {
    const text = safeInnerText(el).trim();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const anyEl = el as any;
    const offsetHeight =
      typeof anyEl.offsetHeight === "number" ? anyEl.offsetHeight : 0;
    if (
      text &&
      text.length > 10 &&
      el.children.length < 20 &&
      offsetHeight > 20
    ) {
      const nested = el.querySelector(".ds-markdown");
      if (nested) {
        candidates.push({
          role: "assistant",
          content: safeInnerText(nested).trim(),
        });
      } else if (!el.closest(".ds-markdown") && text.length < 2000) {
        candidates.push({ role: "user", content: text });
      }
    }
  });

  const seen = new Set<string>();
  candidates.forEach((c) => {
    const key = c.content.slice(0, 100);
    if (!seen.has(key)) {
      seen.add(key);
      messages.push({ role: c.role, content: c.content });
    }
  });

  return messages;
}

/**
 * DeepSeek's multi-strategy model name detector: 7 selectors → page
 * title → body text heuristics → "DeepSeek" fallback.
 */
export function getModelName(doc: Document = document): string | null {
  const selectors = [
    '[class*="model"] [class*="name"]',
    '[class*="model-select"]',
    '[class*="modelName"]',
    '[class*="model-tag"]',
    '[data-testid*="model"]',
    '[class*="model-info"]',
    '[class*="ModelSelector"]',
  ];
  for (const sel of selectors) {
    try {
      const el = doc.querySelector(sel);
      if (el) {
        const text = (el.textContent || "").trim();
        if (text && text.length > 2 && text.length < 50) return text;
      }
    } catch {
      // Ignore invalid selectors
    }
  }

  const title = doc.title;
  if (title.includes("DeepSeek")) {
    const match = title.match(/DeepSeek[-\s]?\w+/i);
    if (match) return match[0];
  }

  const body = doc.body ? safeInnerText(doc.body) : "";
  if (body.includes("DeepSeek-R1")) return "DeepSeek-R1";
  if (body.includes("DeepSeek-V3")) return "DeepSeek-V3";

  return "DeepSeek";
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://chat.deepseek.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_DEEPSEEK_ACTIVE) return;
    window.__PCE_DEEPSEEK_ACTIVE = true;

    console.log("[PCE] DeepSeek content script loaded");

    const runtime = createCaptureRuntime({
      provider: "deepseek",
      sourceName: "deepseek-web",
      siteName: "DeepSeek",
      extractionStrategy: "deepseek-dom",
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
