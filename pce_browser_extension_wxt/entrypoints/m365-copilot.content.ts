// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Microsoft 365 Copilot content script (embedded in Office Web).
 *
 * P5.A-8 — F2 batch P1 scaffolding. This adapter targets the Copilot
 * side-panel that Microsoft embeds into the Office-Web surface
 * (``m365.cloud.microsoft``, ``*.cloud.microsoft/copilot/*`` and the
 * legacy ``*.officeapps.live.com`` WordViewer/ExcelViewer chrome).
 * It is *not* the standalone ``copilot.microsoft.com`` site — that
 * already has its own adapter (``copilot.content.ts``).
 *
 * The side panel renders turns in a React tree with ChatGPT-style
 * ``[data-message-author-role]`` attributes in several recent builds.
 * When that attribute is absent we fall back to class-name keywords
 * (``user`` / ``assistant`` / ``bot``). Model name is typically not
 * surfaced in the DOM — MITM captures the ``substrate.office.com``
 * POST for the canonical model identity.
 *
 * Live-page validation is still required — the M365 Copilot surface
 * evolves rapidly and the selectors below may need refinement.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import { extractReplyContent } from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_M365_COPILOT_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// M365 Copilot-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

// Office Web document URLs embed a per-document GUID in one of these
// shapes:
//   /personal/<user>/_layouts/.../WopiFrame.aspx?sourcedoc=%7B<GUID>%7D
//   /:w:/g/personal/<user>/<encoded-doc-id>
// The GUID braces are URL-encoded (``%7B`` / ``%7D``) inside query
// strings, so the regex accepts both forms. The fallback path-segment
// pattern requires 16+ alphanumeric chars which rules out short
// segments like ``_layouts`` or ``personal``.
const DOC_ID_RE = /(?:\{|%7B)([0-9A-F-]{32,38})(?:\}|%7D)|\/([A-Za-z0-9_-]{16,})(?:\?|$)/i;
const COPILOT_CHAT_RE = /\/copilot(?:\/chat)?\/([^/?]+)/i;

// Copilot uses ``complementary`` / ``region`` landmarks for the panel.
const PANEL_SELECTORS = [
  "[data-testid*='copilot-chat']",
  "[data-automation-id*='copilot']",
  "[aria-label*='Copilot']",
  "aside[aria-labelledby*='copilot']",
  "[role='complementary'][aria-label*='Copilot']",
];

const TURN_SELECTORS = [
  "[data-message-author-role]",
  "[data-testid='copilot-message']",
  "[class*='MessageItem']",
  "[class*='chat-message']",
];

const UI_NOISE_RE =
  /^(?:copy|copied|like|dislike|try again|regenerate|show sources|hide sources|show reasoning|hide reasoning|\d+\s+(?:of|\/)\s+\d+)$/i;

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
  search: string = typeof location !== "undefined" ? location.search : "",
): string | null {
  if (!pathname && !search) return null;
  // Prefer an explicit copilot chat id when present.
  const chatMatch = pathname.match(COPILOT_CHAT_RE);
  if (chatMatch) return chatMatch[1];

  // Fall back to any recognisable document GUID in the URL query.
  const both = `${pathname} ${search}`;
  const doc = both.match(DOC_ID_RE);
  if (doc) return (doc[1] || doc[2] || "").replace(/[{}]/g, "");
  return pathname || null;
}

export function getContainer(doc: Document = document): Element | null {
  for (const sel of PANEL_SELECTORS) {
    const el = doc.querySelector(sel);
    if (el) return el;
  }
  return doc.querySelector("main") || doc.body || null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

export function detectRole(el: Element): "user" | "assistant" | "unknown" {
  const role = (el.getAttribute("data-message-author-role") || "").toLowerCase();
  if (role === "user" || role === "assistant") return role;

  const cls = ((el as Element).className || "").toString().toLowerCase();
  if (cls.includes("user") || cls.includes("human")) return "user";
  if (cls.includes("assistant") || cls.includes("bot") || cls.includes("ai")) {
    return "assistant";
  }

  const label = (el.getAttribute("aria-label") || "").toLowerCase();
  if (label.includes("your message") || label.includes("you said")) {
    return "user";
  }
  if (label.includes("copilot said") || label.includes("assistant")) {
    return "assistant";
  }
  return "unknown";
}

/**
 * Try ``TURN_SELECTORS`` against each known Copilot panel until one
 * yields at least two turns. Returns ``[]`` when no panel is mounted
 * (user hasn't opened Copilot yet) — runtime then stays silent.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const container = getContainer(doc);
  if (!container) return [];

  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();

  for (const sel of TURN_SELECTORS) {
    let nodes: NodeListOf<Element>;
    try {
      nodes = container.querySelectorAll(sel);
    } catch {
      continue;
    }
    if (nodes.length === 0) continue;

    messages.length = 0;
    seen.clear();
    nodes.forEach((node) => {
      const role = detectRole(node);
      if (role === "unknown") return;
      const raw =
        role === "assistant"
          ? extractReplyContent(node) || safeInnerText(node)
          : safeInnerText(node);
      const text = normalizeText(raw);
      if (!text || text.length < 2) return;
      const key = `${role}:${text}`;
      if (seen.has(key)) return;
      seen.add(key);
      messages.push({ role, content: text });
    });

    if (messages.length >= 1) return messages;
  }

  return messages;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: [
    "https://m365.cloud.microsoft/*",
    "https://*.cloud.microsoft/*",
    "https://*.officeapps.live.com/*",
  ],
  runAt: "document_start",
  main() {
    if (window.__PCE_M365_COPILOT_ACTIVE) return;
    window.__PCE_M365_COPILOT_ACTIVE = true;

    console.log("[PCE] Microsoft 365 Copilot content script loaded");

    const runtime = createCaptureRuntime({
      provider: "microsoft",
      sourceName: "m365-copilot-web",
      siteName: "Microsoft 365 Copilot",
      extractionStrategy: "m365-copilot-dom",
      // Chat-panel flow — incremental keeps bandwidth down during
      // long conversations.
      captureMode: "incremental",

      debounceMs: 2500,
      pollIntervalMs: 5000,

      getContainer: () => getContainer(document),
      extractMessages: () => extractMessages(document),
      getSessionHint: () => getSessionHint(),
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
