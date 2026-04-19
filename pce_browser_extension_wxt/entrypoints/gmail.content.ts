// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Gmail AI content script (mail.google.com).
 *
 * P5.A-9 — F2 batch P2 scaffolding. Gmail surfaces AI through two
 * affordances:
 *   - **Help me write** — a dialog inside the compose window where
 *     the user types a prompt and Gmail generates a draft (Gemini).
 *   - **Smart Compose / Smart Reply** — inline grey-text suggestions
 *     inside the compose body.
 *
 * This adapter targets the Help-me-write flow (explicit prompts have
 * clear provenance); Smart Compose is not captured by design since
 * it triggers on every keystroke and the payload would be noisy.
 * Each Help-me-write invocation is captured as a ``full``-mode
 * payload with one user turn (the prompt) and one assistant turn
 * (the generated draft).
 *
 * Gmail's DOM is heavily obfuscated (generated class names, no
 * stable ``data-*`` attributes in most places) so the selectors rely
 * on ``role`` / ``aria-label`` anchors that tend to survive
 * cosmetic re-renders. Live-page validation is still required —
 * refine as real DOM samples come in.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";

declare global {
  interface Window {
    __PCE_GMAIL_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Gmail-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

// Gmail URLs put the active thread in the URL hash:
//   https://mail.google.com/mail/u/0/#inbox/<16-char-threadId>
//   https://mail.google.com/mail/u/1/#label/<label>/<threadId>
//   https://mail.google.com/mail/u/0/#search/q/<threadId>
// When no thread is open the hash is a view tag (``#inbox``, ``#sent``).
const THREAD_ID_RE = /#[^/]+\/(?:[^/]+\/)*([A-Za-z0-9_-]{12,})/;

const HELP_ME_WRITE_SELECTORS = [
  "[aria-label*='Help me write']",
  "[data-tooltip*='Help me write']",
  "[role='dialog'][aria-label*='Help me write']",
  "[aria-label*='Polish']",
  "[aria-label*='Refine']",
];

const COMPOSE_DIALOG_SELECTORS = [
  "[role='dialog'][aria-label*='ew Message']", // 'New Message' / 'Reply' dialogs
  "[role='dialog'][aria-labelledby]",
  ".compose-dialog",
];

const PROMPT_INPUT_SELECTORS = [
  "[aria-label*='Help me write'] textarea",
  "[aria-label*='prompt' i] textarea",
  "textarea[aria-label*='Enter a prompt' i]",
];

const DRAFT_RESULT_SELECTORS = [
  "[aria-label*='Generated draft']",
  "[aria-label*='Draft preview']",
  "[aria-live='polite'][role='region']",
];

const UI_NOISE_RE =
  /^(?:send|discard|save|cancel|close|insert|replace|refine|shorten|formalize|elaborate|try again|\d+\s*\/\s*\d+)$/i;

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
  hash: string = typeof location !== "undefined" ? location.hash : "",
  pathname: string = typeof location !== "undefined" ? location.pathname : "",
): string | null {
  if (hash) {
    const m = hash.match(THREAD_ID_RE);
    if (m) return m[1];
  }
  return pathname || hash || null;
}

export function getContainer(doc: Document = document): Element | null {
  for (const sel of HELP_ME_WRITE_SELECTORS) {
    const el = doc.querySelector(sel);
    if (el) {
      // Prefer the enclosing dialog so the MutationObserver sees
      // prompt-edit + draft-render mutations in one subtree.
      return el.closest("[role='dialog']") || el;
    }
  }
  for (const sel of COMPOSE_DIALOG_SELECTORS) {
    const el = doc.querySelector(sel);
    if (el) return el;
  }
  return doc.body || null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

export function isHelpMeWriteOpen(doc: Document = document): boolean {
  for (const sel of HELP_ME_WRITE_SELECTORS) {
    if (doc.querySelector(sel)) return true;
  }
  return false;
}

/**
 * When Help-me-write is open, pair the prompt input value with the
 * generated draft preview. Returns ``[]`` otherwise — plain compose
 * windows, inbox browsing, etc. are not captured.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  if (!isHelpMeWriteOpen(doc)) return [];

  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();

  for (const sel of PROMPT_INPUT_SELECTORS) {
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

  for (const sel of DRAFT_RESULT_SELECTORS) {
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
    if (messages.some((m) => m.role === "assistant")) break;
  }

  return messages;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://mail.google.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_GMAIL_ACTIVE) return;
    window.__PCE_GMAIL_ACTIVE = true;

    console.log("[PCE] Gmail Help-me-write content script loaded");

    const runtime = createCaptureRuntime({
      provider: "google",
      sourceName: "gmail-ai-web",
      siteName: "Gmail AI",
      extractionStrategy: "gmail-help-me-write-dom",
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
