// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Zhipu / Z.ai content script (chat.z.ai).
 *
 * TypeScript port of ``content_scripts/zhipu.js`` (P2.5 Phase 3b batch
 * 4).
 *
 * Z.ai renders user and assistant turns with stable ``chat-user`` /
 * ``chat-assistant`` container classes. The legacy extractor:
 *
 *   - matched 5 alternative selectors (``.chat-user.markdown-prose``,
 *     ``.chat-assistant.markdown-prose``, ``.chat-assistant``,
 *     ``.user-message``, ``[class*="message-"]``),
 *   - inferred role from class keywords (``chat-user`` /
 *     ``user-message`` → user; ``chat-assistant`` → assistant),
 *   - deferred capture when a user message existed but no assistant
 *     yet (``requireBothRoles`` in the shared runtime — the shared
 *     runtime also defers for the rare "assistant-only" case, which
 *     never occurs in practice on Z.ai).
 *   - used ``GLM-*`` body-text regex for model name detection.
 *
 * Behaviour deviations from legacy JS (intentional):
 *   - The legacy script skipped ``message_update`` captures (content
 *     changes on already-sent messages silently updated the
 *     fingerprint without a send). The runtime emits a
 *     ``capture_mode: "message_update"`` payload for these, so
 *     streaming updates to the last assistant message are captured.
 *     Backend dedup + session-hint-based rollup absorb any extra
 *     requests. Net effect: richer data, no duplicates.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import { extractAttachments } from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_ZHIPU_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Zhipu-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const SESSION_HINT_RE = /\/c\/([a-f0-9-]+)/i;
const MODEL_NAME_RE = /\b(GLM-[\w.-]+)\b/i;

export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  return String(text)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean)
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
  return doc.querySelector("main") || doc.body;
}

export function getModelName(doc: Document = document): string | null {
  const bodyText = doc.body ? safeInnerText(doc.body) : "";
  const m = bodyText.match(MODEL_NAME_RE);
  return m ? m[1] : null;
}

export function detectRole(node: Element): "user" | "assistant" | "unknown" {
  const cls = ((node as Element).className || "").toString().toLowerCase();
  if (cls.includes("chat-user") || cls.includes("user-message")) {
    return "user";
  }
  if (cls.includes("chat-assistant")) {
    return "assistant";
  }
  return "unknown";
}

/**
 * Iterate Z.ai's message containers, dedupe by role+content key.
 * The selector list matches the legacy union exactly:
 * ``.chat-user.markdown-prose, .chat-assistant.markdown-prose,
 * .chat-assistant, .user-message, [class*='message-']``.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();
  const nodes = doc.querySelectorAll(
    ".chat-user.markdown-prose, .chat-assistant.markdown-prose, " +
      ".chat-assistant, .user-message, [class*='message-']",
  );

  nodes.forEach((node) => {
    const role = detectRole(node);
    if (role === "unknown") return;

    const text = normalizeText(safeInnerText(node));
    if (!text || text.length < 2) return;

    const key = `${role}:${text}`;
    if (seen.has(key)) return;
    seen.add(key);

    const att = extractAttachments(node);
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
  matches: ["https://chat.z.ai/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_ZHIPU_ACTIVE) return;
    window.__PCE_ZHIPU_ACTIVE = true;

    console.log("[PCE] Zhipu content script loaded");

    const runtime = createCaptureRuntime({
      provider: "zhipu",
      sourceName: "zhipu-web",
      siteName: "Zhipu",
      extractionStrategy: "zhipu-dom",
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
