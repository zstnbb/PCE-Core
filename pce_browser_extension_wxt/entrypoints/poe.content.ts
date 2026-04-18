// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Poe content script (poe.com).
 *
 * TypeScript port of ``content_scripts/poe.js`` (P2.5 Phase 3b batch
 * 3).
 *
 * Poe renders each turn in a ``ChatMessage_chatMessage_<hash>``
 * container with ``[id^='message-']``. User turns use right-side
 * bubble classes; assistant turns use left-side bubble/header classes.
 * The legacy script normalized text to strip timestamps + share/copy
 * button labels, deduped by role+content key, and deferred capture
 * when either user or assistant was missing (``requireBothRoles`` in
 * the shared runtime).
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import { extractAttachments } from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_POE_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Poe-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const TIMESTAMP_LINE_RE = /^\d{1,2}:\d{2}$/;
const UI_LINE_RE = /^(?:compare|share|copy|copied)$/i;

export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  return String(text)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => {
      if (!line) return false;
      if (TIMESTAMP_LINE_RE.test(line)) return false;
      if (UI_LINE_RE.test(line)) return false;
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

export function getMessageNodes(
  doc: Document = document,
): NodeListOf<Element> {
  return doc.querySelectorAll(
    "[id^='message-'].ChatMessage_chatMessage__xkgHx, " +
      "[id^='message-'][class*='ChatMessage_chatMessage']",
  );
}

export function detectRole(node: Element): "user" | "assistant" | "unknown" {
  const cls = ((node as Element).className || "").toString().toLowerCase();
  if (
    cls.includes("rightsidemessagewrapper") ||
    cls.includes("rightsidemessagebubble") ||
    node.querySelector(
      "[class*='rightSideMessageWrapper'], [class*='rightSideMessageBubble']",
    )
  ) {
    return "user";
  }
  if (
    cls.includes("leftsidemessagebubble") ||
    cls.includes("leftsidemessageheader") ||
    node.querySelector(
      "[class*='leftSideMessageBubble'], [class*='LeftSideMessageHeader'], [class*='BotMessageHeader']",
    )
  ) {
    return "assistant";
  }
  return "unknown";
}

export function extractText(node: Element): string {
  const textNode =
    node.querySelector("[class*='Message_messageTextContainer']") ||
    node.querySelector("[class*='Markdown_markdownContainer']") ||
    node.querySelector("[class*='Message_selectableText']") ||
    node;
  return normalizeText(safeInnerText(textNode));
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector(
      "[class*='ChatMessagesScrollWrapper_scrollableContainerWrapper']",
    ) ||
    doc.querySelector("[class*='ChatMessagesView_infiniteScroll']") ||
    doc.querySelector("main") ||
    doc.body
  );
}

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "chat" && parts.length >= 2) {
    return parts[parts.length - 1];
  }
  return pathname || null;
}

export function getModelName(doc: Document = document): string | null {
  const botName =
    doc.querySelector("[class*='BotHeader_textContainer'] p") ||
    doc.querySelector(
      "[class*='BotInfoCardHeader_botImageAndNameContainer'] p",
    );
  if (!botName) return null;
  const text = normalizeText(botName.textContent);
  return text || null;
}

/**
 * Iterate message nodes, classify role, dedupe by role+content key,
 * return ExtractedMessage[].
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();

  getMessageNodes(doc).forEach((node) => {
    const role = detectRole(node);
    if (role === "unknown") return;

    const text = extractText(node);
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
  matches: ["https://poe.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_POE_ACTIVE) return;
    window.__PCE_POE_ACTIVE = true;

    console.log("[PCE] Poe content script loaded");

    const runtime = createCaptureRuntime({
      provider: "poe",
      sourceName: "poe-web",
      siteName: "Poe",
      extractionStrategy: "poe-dom",
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
