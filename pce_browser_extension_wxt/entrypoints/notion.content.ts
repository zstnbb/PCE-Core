// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Notion AI content script (www.notion.so / notion.so).
 *
 * P5.A-8 — F2 batch P1 scaffolding. Notion AI is not a chat UI: AI
 * blocks are invoked inline inside a page, the user's prompt appears
 * briefly in an input affordance, and the AI response is rendered as a
 * replaceable block. Each invocation is therefore captured as a single
 * ``full``-mode payload with one assistant turn (and optionally the
 * user's triggering prompt when we can recover it from ``aria-label``
 * / adjacent input state).
 *
 * Selectors below are based on public Notion semantic-HTML conventions
 * (``data-block-id``, ``.notion-*`` block classes, the 32-hex page-id
 * URL pattern). They are intentionally conservative: every extractor
 * falls back to an empty array when nothing matches so the capture
 * runtime no-ops gracefully on non-AI Notion pages. Live-page
 * validation is still required — refine selectors as real DOM samples
 * come in.
 *
 * Provider fingerprint (``notion-ai-web``) is the key deliverable: it
 * lets the MITM layer correlate ``/api/v3/runInferenceTranscript``
 * requests with the correct site even when DOM extraction is silent.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";

declare global {
  interface Window {
    __PCE_NOTION_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Notion-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

// Matches any of the page-id forms Notion serves today:
//   /<Workspace-Title-UUID32>
//   /<UUID32>
//   /<Workspace>/<Page-Title-UUID32>
// The UUID is 32 hex chars (possibly dashed) at the tail of a path
// segment.
const SESSION_HINT_RE = /([a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})(?!.*[a-f0-9]{32})/i;

// Selectors that are *known* to belong to AI-generated content in
// Notion's public block markup. Order matters — specific wins.
const AI_BLOCK_SELECTORS = [
  "[data-block-type='ai']",
  "[data-block-type='ai_block']",
  "[data-testid='ai-block']",
  "[data-pendo-tag*='ai-response']",
  ".notion-ai-block",
  ".notion-ai-response",
];

const AI_PROMPT_SELECTORS = [
  "[data-testid='ai-input']",
  "[data-testid='ai-prompt']",
  "[aria-label*='Ask AI']",
  "[placeholder*='Ask AI']",
];

// Strip Notion-injected affordance labels that sometimes end up in
// ``innerText`` (toolbar hints, "AI is writing…" spinner copy).
const UI_NOISE_RE = /^(?:ai is writing.*|done|cancel|try again|make longer|make shorter|improve writing|fix spelling|retry)$/i;

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
  const last = pathname.split("/").filter(Boolean).pop() || "";
  const m = last.match(SESSION_HINT_RE);
  return m ? m[1].replace(/-/g, "") : last || pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector(".notion-page-content") ||
    doc.querySelector("[class*='notion-page']") ||
    doc.querySelector("main") ||
    doc.body ||
    null
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

/**
 * Walk ``AI_BLOCK_SELECTORS`` in order and gather every matching
 * element's text. The accompanying ``user`` turn is best-effort:
 * Notion doesn't render the submitted prompt as a stable block, so
 * we look at ``AI_PROMPT_SELECTORS`` for an in-flight input value.
 *
 * Returns ``[]`` when no AI block is found — the capture runtime
 * then simply doesn't emit a capture, which is the desired behaviour
 * on vanilla (non-AI) Notion pages.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();

  // Recover the user prompt first (at most one — Notion AI is
  // single-shot per block). Missing prompt is fine.
  for (const sel of AI_PROMPT_SELECTORS) {
    const el = doc.querySelector(sel);
    if (!el) continue;
    const raw =
      (el as HTMLInputElement).value ||
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

  for (const sel of AI_BLOCK_SELECTORS) {
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
    "https://www.notion.so/*",
    "https://notion.so/*",
  ],
  runAt: "document_start",
  main() {
    if (window.__PCE_NOTION_ACTIVE) return;
    window.__PCE_NOTION_ACTIVE = true;

    console.log("[PCE] Notion AI content script loaded");

    const runtime = createCaptureRuntime({
      provider: "notion",
      sourceName: "notion-ai-web",
      siteName: "Notion AI",
      extractionStrategy: "notion-ai-dom",
      // AI blocks are single-shot; full resend on every fingerprint
      // change is simpler than delta-tracking.
      captureMode: "full",

      debounceMs: 2500,
      pollIntervalMs: 5000,

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
