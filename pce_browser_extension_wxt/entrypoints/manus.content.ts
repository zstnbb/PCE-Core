/**
 * PCE — Manus content script (manus.im).
 *
 * TypeScript port of ``content_scripts/manus.js`` (P2.5 Phase 3b
 * batch 4).
 *
 * Manus renders the active task conversation inside
 * ``#manus-chat-box``. User prompts live in right-aligned bubbles
 * (``.items-end .u-break-words`` / ``.whitespace-pre-wrap``) and
 * assistant replies render in ``.manus-markdown`` blocks. The
 * legacy extractor also grabbed code blocks + non-local citation
 * links as local attachments.
 *
 * Per the legacy JS, ``normalizeText`` strips Chinese UI noise (e.g.
 * "Lite", "分享", "升级", "任务已完成", "推荐追问"). The regex
 * table is preserved byte-for-byte.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractReplyContent,
  type PceAttachment,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_MANUS_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Manus-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const NOISE_LINE_RE = /^(?:Lite|分享|升级|任务已完成|推荐追问)$/i;
const SESSION_HINT_RE = /\/app\/([a-zA-Z0-9_-]+)/;

export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  return String(text)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => {
      if (!line) return false;
      if (NOISE_LINE_RE.test(line)) return false;
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

export function getChatBox(doc: Document = document): Element | null {
  return (
    doc.querySelector("#manus-chat-box") ||
    doc.querySelector("#manus-home-page-session-content")
  );
}

export function getContainer(doc: Document = document): Element | null {
  return getChatBox(doc) || doc.body;
}

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  return m ? m[1] : pathname || null;
}

export function getModelName(doc: Document = document): string | null {
  const selectors = [
    "#manus-chat-box [aria-haspopup='dialog'] span.truncate",
    "#manus-chat-box span.truncate",
  ];
  for (const sel of selectors) {
    try {
      const el = doc.querySelector(sel);
      const text = normalizeText(safeInnerText(el));
      if (text && text.length < 120) return text;
    } catch {
      // Skip invalid selectors
    }
  }
  return "Manus";
}

export function dedupeAttachments(
  list: PceAttachment[] | null | undefined,
): PceAttachment[] {
  const seen = new Set<string>();
  const out: PceAttachment[] = [];
  for (const att of list || []) {
    if (!att || typeof att !== "object") continue;
    const key = JSON.stringify(att);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(att);
  }
  return out;
}

/**
 * Extract Manus-specific local attachments: ``<pre>`` code blocks
 * (with the language label picked up from a sibling ``span.text-sm``)
 * plus external ``<a href>`` citation links (skips same-origin).
 */
export function extractLocalAttachments(
  el: Element | null,
  origin: string = location.hostname,
): PceAttachment[] {
  if (!el) return [];
  const attachments: PceAttachment[] = [];

  el.querySelectorAll("pre").forEach((pre) => {
    const code = safeInnerText(pre).trim();
    if (!code || code.length < 3) return;
    const langLabel =
      safeInnerText(
        pre.closest(".manus-markdown")?.querySelector("span.text-sm"),
      ).trim() || "";
    attachments.push({
      type: "code_block",
      language: langLabel,
      code: code.slice(0, 8000),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  el.querySelectorAll("a[href]").forEach((link) => {
    const href = (link as HTMLAnchorElement).href || "";
    const title = safeInnerText(link).trim();
    if (!href || !title) return;
    try {
      if (new URL(href).hostname === origin) return;
    } catch {
      return;
    }
    attachments.push({
      type: "citation",
      url: href.slice(0, 500),
      title: title.slice(0, 200),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  return dedupeAttachments(attachments);
}

/**
 * Iterate user bubbles + assistant markdown blocks inside
 * ``#manus-chat-box``, dedupe by role+content key.
 */
export function extractMessages(
  doc: Document = document,
  origin: string = location.hostname,
): ExtractedMessage[] {
  const chatBox = getChatBox(doc);
  if (!chatBox) return [];

  const messages: ExtractedMessage[] = [];
  const seen = new Set<string>();
  const nodes = chatBox.querySelectorAll(
    ".items-end .u-break-words, .items-end .whitespace-pre-wrap, .manus-markdown",
  );

  nodes.forEach((node) => {
    const isAssistant = !!node.closest(".manus-markdown");
    const role = isAssistant ? "assistant" : "user";
    const text = isAssistant
      ? normalizeText(extractReplyContent(node) || safeInnerText(node))
      : normalizeText(safeInnerText(node));

    if (!text || text.length < 2) return;

    const key = `${role}:${text}`;
    if (seen.has(key)) return;
    seen.add(key);

    const scope: Element = isAssistant
      ? node.closest(".manus-markdown") || node
      : node.closest(".items-end") || node;
    const rawAtt = [
      ...extractAttachments(scope),
      ...extractLocalAttachments(scope, origin),
    ];
    const att = dedupeAttachments(rawAtt);
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
  matches: ["https://manus.im/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_MANUS_ACTIVE) return;
    window.__PCE_MANUS_ACTIVE = true;

    console.log("[PCE] Manus content script loaded");

    const runtime = createCaptureRuntime({
      provider: "manus",
      sourceName: "manus-web",
      siteName: "Manus",
      extractionStrategy: "manus-dom",
      captureMode: "incremental",

      debounceMs: 2500,
      pollIntervalMs: 4000,

      getContainer: () => getContainer(document),
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
