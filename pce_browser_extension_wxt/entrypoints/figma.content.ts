// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Figma AI content script (www.figma.com).
 *
 * P5.A-9 — F2 batch P2 scaffolding. Figma (and FigJam) exposes AI
 * through modal panels attached to the canvas: "First Draft", Figma
 * Make, "Jambot" (FigJam). The page is overwhelmingly a WebGL canvas
 * and extremely DOM-opaque; realistic extraction only works when an
 * AI panel is actually mounted. MITM still captures the
 * ``ai-api.figma.com`` POST regardless, so this adapter's primary job
 * is to attach a ``figma-ai-web`` provider fingerprint to those
 * flows and surface DOM context when we can.
 *
 * Live-page validation is required — Figma ships UI changes weekly
 * and the selectors below are best-effort starting points derived
 * from public class-name conventions. ``extractMessages`` is
 * intentionally conservative: returns ``[]`` when no AI modal is
 * mounted so the runtime stays silent on normal canvas editing.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";

declare global {
  interface Window {
    __PCE_FIGMA_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Figma-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

// Figma file URLs take one of these shapes:
//   /file/<22-char-key>/<Title>        (legacy Design files)
//   /design/<22-char-key>/<Title>      (new Design files)
//   /board/<22-char-key>/<Title>       (FigJam)
//   /slides/<22-char-key>/<Title>
//   /proto/<22-char-key>/<Title>
const FILE_KEY_RE = /\/(?:file|design|board|slides|proto)\/([A-Za-z0-9]{15,32})/i;

const AI_PANEL_SELECTORS = [
  "[data-testid*='first-draft']",
  "[data-testid*='figma-ai']",
  "[data-testid*='jambot']",
  "[data-testid*='ai-prompt']",
  "[aria-label*='Figma AI']",
  "[aria-label*='Jambot']",
  "[aria-label*='First Draft']",
  "[class*='ai-panel']",
  "[class*='jambot']",
];

const AI_PROMPT_SELECTORS = [
  "[data-testid*='ai-prompt-input']",
  "textarea[placeholder*='prompt']",
  "textarea[placeholder*='Describe']",
  "textarea[aria-label*='prompt' i]",
];

const AI_RESPONSE_SELECTORS = [
  "[data-testid*='ai-response']",
  "[data-testid*='ai-output']",
  "[class*='ai-response']",
  "[class*='generation-preview']",
];

const UI_NOISE_RE =
  /^(?:generate|regenerate|cancel|close|dismiss|try again|keep|discard|use this|\d+%|\d+\s*\/\s*\d+)$/i;

export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  return String(text)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line && !UI_NOISE_RE.test(line))
    .join("\n")
    .trim();
}

export function getSessionHint(
  pathname: string = typeof location !== "undefined" ? location.pathname : "",
): string | null {
  if (!pathname) return null;
  const m = pathname.match(FILE_KEY_RE);
  return m ? m[1] : pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  for (const sel of AI_PANEL_SELECTORS) {
    const el = doc.querySelector(sel);
    if (el) return el;
  }
  // Fall back to body so the MutationObserver is attached and notices
  // the panel when the user eventually opens it.
  return doc.body || null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

/**
 * Pair the visible AI prompt input (if any) with every visible AI
 * response region. Figma AI is single-shot per invocation so each
 * capture typically has 1 user + 1 assistant turn. Returns ``[]``
 * when no AI panel is mounted.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  // Only extract when an AI panel is actually open.
  let panelOpen = false;
  for (const sel of AI_PANEL_SELECTORS) {
    if (doc.querySelector(sel)) {
      panelOpen = true;
      break;
    }
  }
  if (!panelOpen) return [];

  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();

  for (const sel of AI_PROMPT_SELECTORS) {
    const el = doc.querySelector(sel);
    if (!el) continue;
    const raw =
      (el as HTMLTextAreaElement).value ||
      el.getAttribute("data-value") ||
      safeInnerText(el);
    const text = normalizeText(raw);
    if (text && text.length > 1) {
      const key = `user:${text}`;
      if (!seen.has(key)) {
        seen.add(key);
        messages.push({ role: "user", content: text });
      }
      break;
    }
  }

  for (const sel of AI_RESPONSE_SELECTORS) {
    const nodes = doc.querySelectorAll(sel);
    if (nodes.length === 0) continue;
    nodes.forEach((node) => {
      const text = normalizeText(safeInnerText(node));
      if (!text || text.length < 2) return;
      const key = `assistant:${text}`;
      if (seen.has(key)) return;
      seen.add(key);
      messages.push({ role: "assistant", content: text });
    });
  }

  return messages;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: [
    "https://www.figma.com/*",
    "https://figma.com/*",
  ],
  runAt: "document_start",
  main() {
    if (window.__PCE_FIGMA_ACTIVE) return;
    window.__PCE_FIGMA_ACTIVE = true;

    console.log("[PCE] Figma AI content script loaded");

    const runtime = createCaptureRuntime({
      provider: "figma",
      sourceName: "figma-ai-web",
      siteName: "Figma AI",
      extractionStrategy: "figma-ai-dom",
      captureMode: "full",

      debounceMs: 2500,
      pollIntervalMs: 6000,

      getContainer: () => getContainer(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
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
