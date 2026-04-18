/**
 * PCE — Grok content script (grok.com).
 *
 * TypeScript port of ``content_scripts/grok.js`` (P2.5 Phase 3b batch
 * 3).
 *
 * Grok renders each turn in ``#last-reply-container`` with stable
 * ``items-end`` / ``items-start`` alignment classes distinguishing
 * user vs assistant turns. The message bubble lives under
 * ``.message-bubble``. The legacy script normalized text to strip
 * share/copy/auto button labels + timing strings + fast-mode /
 * deepsearch / ping toggles, then dedup'd by role+content key, and
 * deferred capture when either role was missing (``requireBothRoles``
 * in the shared runtime).
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractReplyContent,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_GROK_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Grok-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/c\/([a-f0-9-]+)/i;
const MODEL_NAME_RE = /\b(grok-[\w.-]+)\b/i;
const UI_LINE_RE = /^(?:share|auto|copy|copied)$/i;
const TIMING_LINE_RE = /^\d+(?:\.\d+)?\s*(?:s|sec|seconds)$/i;
const MODE_LINE_RE = /^(?:fast mode|quick mode|deepsearch|ping)$/i;

export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  return String(text)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => {
      if (!line) return false;
      if (UI_LINE_RE.test(line)) return false;
      if (TIMING_LINE_RE.test(line)) return false;
      if (MODE_LINE_RE.test(line)) return false;
      return true;
    })
    .join("\n")
    .trim();
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  return doc.querySelector("#last-reply-container") || doc.querySelector("main");
}

export function getModelName(doc: Document = document): string | null {
  const bodyText = doc.body ? safeInnerText(doc.body) : "";
  const m = bodyText.match(MODEL_NAME_RE);
  return m ? m[1] : null;
}

export function detectRole(node: Element): "user" | "assistant" | "unknown" {
  const cls = ((node as Element).className || "").toString().toLowerCase();
  if (cls.includes("items-end")) return "user";
  if (cls.includes("items-start")) return "assistant";
  return "unknown";
}

/**
 * Iterate ``[id^='response-']`` children of ``#last-reply-container``
 * (falling back to ``main``), classify each by alignment class,
 * extract text from ``.message-bubble``, dedupe by role+content key.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const container = getContainer(doc);
  if (!container) return [];

  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();
  const nodes = container.querySelectorAll("[id^='response-']");

  nodes.forEach((node) => {
    const role = detectRole(node);
    if (role === "unknown") return;

    const bubble = node.querySelector(".message-bubble") || node;
    const text =
      role === "assistant"
        ? normalizeText(extractReplyContent(bubble) || safeInnerText(bubble))
        : normalizeText(safeInnerText(bubble));
    if (!text || text.length < 2) return;

    const key = `${role}:${text}`;
    if (seen.has(key)) return;
    seen.add(key);

    const att = extractAttachments(bubble);
    const msg: ExtractedMessage = { role, content: text };
    if (att.length > 0) msg.attachments = att;
    messages.push(msg);
  });

  return messages;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://grok.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_GROK_ACTIVE) return;
    window.__PCE_GROK_ACTIVE = true;

    console.log("[PCE] Grok content script loaded");

    const runtime = createCaptureRuntime({
      provider: "xai",
      sourceName: "grok-web",
      siteName: "Grok",
      extractionStrategy: "grok-dom",
      captureMode: "incremental",

      debounceMs: 2500,
      streamCheckMs: 1500,
      pollIntervalMs: 4000,

      getContainer: () => getContainer(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
      getModelName: () => getModelName(document),
      hookHistoryApi: false,
      requireBothRoles: true,
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
