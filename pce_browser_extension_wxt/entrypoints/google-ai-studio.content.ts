/**
 * PCE — Google AI Studio content script (aistudio.google.com).
 *
 * TypeScript port of ``content_scripts/google_ai_studio.js`` (P2.5
 * Phase 3b batch 2).
 *
 * AI Studio renders each turn inside a ``<ms-chat-turn>`` web component
 * with ``chat-turn-container.user`` / ``chat-turn-container.model``
 * class markers. The legacy extractor had elaborate text normalization
 * (stripping control lines like ``edit`` / ``more_vert``, user/model
 * timestamp headers, the disclaimer footer, and generic UI buttons),
 * plus a rich local-attachment extractor that understands the
 * AI-Studio-specific ``<ms-image-chunk>`` / ``<ms-file-chunk>``
 * elements, ``<pre>`` code blocks, and citation links. All of that
 * behaviour is preserved here byte-for-byte.
 *
 * Incremental delta mode, 2500 ms debounce, 4000 ms SPA polling,
 * streaming-aware (defers capture while a Stop/Cancel button is
 * visible), no history-API hook.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";
import {
  extractAttachments,
  extractReplyContent,
  extractThinking,
  isStreaming as sharedIsStreaming,
  normalizeCitationUrl,
  type PceAttachment,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_GOOGLE_AI_STUDIO_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Text-normalisation tables (lifted verbatim from legacy JS)
// ---------------------------------------------------------------------------

const CONTROL_LINE_RE =
  /^(?:edit|more_vert|thumb_up|thumb_down|thumb_up_alt|thumb_down_alt|info|share|compare_arrows|add|close|search|settings|menu_open)$/i;
const META_LINE_RE = /^(?:user|model)\s+\d{1,2}:\d{2}$/i;
const DISCLAIMER_LINE_RE = /^google ai models may make mistakes/i;
const AI_STUDIO_UI_LINE_RE =
  /^(?:download|content_copy|expand_less|expand_more|copy code|copy)$/i;
const SESSION_HINT_RE = /\/prompts\/([a-zA-Z0-9_-]+)/;
const MODEL_NAME_RE = /\b(gemini-[\w.-]+)\b/i;

// ---------------------------------------------------------------------------
// Helpers (module-scope for testability)
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

/**
 * Collapse internal whitespace, strip AI-Studio UI noise lines,
 * collapse consecutive duplicate lines. Matches the legacy
 * ``normalizeText`` exactly.
 */
export function normalizeText(text: string | null | undefined): string {
  if (!text) return "";
  const lines = String(text)
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => {
      if (!line) return false;
      if (CONTROL_LINE_RE.test(line)) return false;
      if (META_LINE_RE.test(line)) return false;
      if (DISCLAIMER_LINE_RE.test(line)) return false;
      if (AI_STUDIO_UI_LINE_RE.test(line)) return false;
      return true;
    });

  const deduped: string[] = [];
  for (const line of lines) {
    if (deduped[deduped.length - 1] === line) continue;
    deduped.push(line);
  }
  return deduped.join("\n").trim();
}

/**
 * Dedupe attachments by a composite key of type + url + name + title
 * + code. Matches the legacy ``dedupeAttachments``.
 */
export function dedupeAttachments(
  list: PceAttachment[] | null | undefined,
): PceAttachment[] {
  const seen = new Set<string>();
  const out: PceAttachment[] = [];
  for (const att of list || []) {
    if (!att || typeof att !== "object") continue;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const a = att as any;
    const key =
      [a.type || "", a.url || "", a.name || "", a.title || "", a.code || ""].join(
        "|",
      ) || JSON.stringify(att);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(att);
  }
  return out;
}

/**
 * Derive an image media type from a data URL or filename. Returns
 * empty string when unknown.
 */
export function imageMediaType(
  src: string | null | undefined,
  name: string | null | undefined,
): string {
  const dataMatch = String(src || "").match(/^data:([^;,]+)[;,]/i);
  if (dataMatch) return dataMatch[1];
  const lower = String(name || src || "").toLowerCase();
  if (lower.includes(".png")) return "image/png";
  if (lower.includes(".jpg") || lower.includes(".jpeg")) return "image/jpeg";
  if (lower.includes(".gif")) return "image/gif";
  if (lower.includes(".webp")) return "image/webp";
  return "";
}

/**
 * Build a placeholder text body for messages that contain only
 * attachments (image / file). Matches the legacy
 * ``attachmentOnlyText``.
 */
export function attachmentOnlyText(
  attachments: PceAttachment[] | null | undefined,
): string {
  const labels: string[] = [];
  for (const att of attachments || []) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const a = att as any;
    if (a?.type === "image_url" || a?.type === "image_generation") {
      labels.push(
        `[Image attachment: ${a.alt || a.name || a.title || "image"}]`,
      );
    } else if (a?.type === "file") {
      labels.push(`[File attachment: ${a.name || a.title || "file"}]`);
    }
  }
  return labels.join("\n").trim();
}

/**
 * Extract AI-Studio-specific local attachments: ``<ms-image-chunk>``
 * images, ``<ms-file-chunk>`` files, ``<pre>`` code blocks, and
 * non-local ``<a href>`` citations.
 */
export function extractLocalAttachments(
  el: Element | null,
  origin: string = location.hostname,
): PceAttachment[] {
  if (!el) return [];
  const attachments: PceAttachment[] = [];

  el.querySelectorAll(
    "ms-image-chunk img, .image-container img.loaded-image, .image-container img[alt]",
  ).forEach((img) => {
    const src =
      (img as HTMLImageElement).src || img.getAttribute("src") || "";
    if (!src || src.startsWith("data:image/svg")) return;
    const alt = (
      (img as HTMLImageElement).alt ||
      img.getAttribute("alt") ||
      ""
    ).trim();
    const mediaType = imageMediaType(src, alt);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const att: any = {
      type: "image_url",
      url: src.startsWith("data:")
        ? `${src.slice(0, 200)}...[truncated]`
        : src.slice(0, 2000),
      source_type: "google-ai-studio-image",
    };
    if (alt) {
      att.alt = alt.slice(0, 200);
      att.name = alt.slice(0, 200);
    }
    if (mediaType) att.media_type = mediaType;
    attachments.push(att);
  });

  el.querySelectorAll("ms-file-chunk, [class*='file-chunk']").forEach(
    (fileEl) => {
      const nameEl = fileEl.querySelector(".name, [title]");
      const name = (
        nameEl?.getAttribute?.("title") ||
        safeInnerText(nameEl) ||
        safeInnerText(fileEl) ||
        ""
      )
        .split("\n")[0]
        .trim();
      if (!name || name.length < 3) return;
      attachments.push({
        type: "file",
        name: name.slice(0, 200),
        source_type: "google-ai-studio-file",
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any);
    },
  );

  el.querySelectorAll("pre").forEach((pre) => {
    const code = safeInnerText(pre).trim();
    if (!code || code.length < 3) return;
    attachments.push({
      type: "code_block",
      language: "",
      code: code.slice(0, 8000),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  el.querySelectorAll("a[href]").forEach((link) => {
    const url = normalizeCitationUrl((link as HTMLAnchorElement).href || "");
    const title = safeInnerText(link).trim();
    if (!url || !title) return;
    try {
      if (new URL(url).hostname === origin) return;
    } catch {
      return;
    }
    attachments.push({
      type: "citation",
      url: url.slice(0, 500),
      title: title.slice(0, 200),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
  });

  return dedupeAttachments(attachments);
}

interface CleanOptions {
  stripLinks?: boolean;
  stripCode?: boolean;
}

/**
 * Clone + strip AI-Studio chrome (buttons, mat-icons, tooltip
 * triggers, feedback rows, author labels, timestamps, turn footers),
 * optionally strip links / code blocks, then pass the result through
 * ``normalizeText``.
 */
export function cleanContainerText(
  el: Element | null,
  options: CleanOptions = {},
): string {
  if (!el) return "";
  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll(
      "button, mat-icon, .mat-icon, [role='button'], [aria-hidden='true'], " +
        ".mat-mdc-tooltip-trigger, .chat-turn-actions, .feedback-buttons, .turn-header, " +
        ".actions-container, .author-label, .timestamp, .turn-footer, ms-chat-turn-options",
    )
    .forEach((node) => node.remove());
  if (options.stripCode) {
    clone
      .querySelectorAll(
        "pre, code, [class*='code-block'], [class*='codeblock'], [class*='code-header'], " +
          "ms-code-block, ms-md-code-block",
      )
      .forEach((node) => node.remove());
  }
  if (options.stripLinks) {
    clone.querySelectorAll("a[href]").forEach((node) => node.remove());
  }
  return normalizeText(safeInnerText(clone));
}

/**
 * Streaming check: shared DOM helper OR a Stop / Cancel button.
 */
export function isStreaming(doc: Document = document): boolean {
  if (sharedIsStreaming(doc)) return true;
  const buttons = doc.querySelectorAll("button");
  for (const btn of Array.from(buttons)) {
    const label = `${safeInnerText(btn) || ""} ${
      btn.getAttribute("aria-label") || ""
    }`.trim();
    if (/stop|cancel generation/i.test(label)) return true;
  }
  return false;
}

export function getContainer(doc: Document = document): Element | null {
  return (
    doc.querySelector(
      ".chat-view-container, ms-chat-session, .chat-session-content, main",
    ) || doc.body
  );
}

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  if (m) return m[1];
  return pathname || null;
}

export function getModelName(doc: Document = document): string | null {
  const selectors = [
    "ms-model-selector .mat-mdc-select-value-text",
    "ms-model-selector .model-name",
    "[data-testid*='model']",
    "[class*='model-selector'] [class*='selected']",
    "[class*='model'] [class*='name']",
  ];
  for (const sel of selectors) {
    try {
      const el = doc.querySelector(sel);
      const text = normalizeText(safeInnerText(el));
      if (text && text.length < 120) return text;
    } catch {
      // Invalid selector; skip
    }
  }
  const bodyText = doc.body ? safeInnerText(doc.body) : "";
  const match = bodyText.match(MODEL_NAME_RE);
  return match ? match[1] : null;
}

export function extractUserText(turn: Element): string {
  const userContainer = turn.querySelector(
    ".chat-turn-container.user, .chat-turn-container .user",
  );
  return cleanContainerText(userContainer || turn);
}

export function extractAssistantText(
  turn: Element,
  attachments: PceAttachment[] = [],
): string {
  const modelContainer = turn.querySelector(
    ".chat-turn-container.model, .model",
  );
  if (!modelContainer) return "";

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasCode = attachments.some((a: any) => a?.type === "code_block");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasCitation = attachments.some((a: any) => a?.type === "citation");
  const cleaned = cleanContainerText(modelContainer, {
    stripCode: hasCode,
    stripLinks: hasCitation,
  });
  const reply =
    cleaned || extractReplyContent(modelContainer) || cleanContainerText(modelContainer);

  const thinking = normalizeText(extractThinking(modelContainer));
  if (thinking) {
    return `<thinking>\n${thinking}\n</thinking>\n\n${normalizeText(reply)}`.trim();
  }
  return normalizeText(reply);
}

/**
 * Iterate ``<ms-chat-turn>`` web components, classify each as user
 * vs model, extract content + attachments, fall back to "both"
 * extraction when the class marker is missing.
 */
export function extractMessages(
  doc: Document = document,
  origin: string = location.hostname,
): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];
  const turns = doc.querySelectorAll("ms-chat-turn");
  if (turns.length === 0) return messages;

  turns.forEach((turn) => {
    const turnContainer = turn.querySelector(".chat-turn-container");
    const cls = (
      ((turnContainer as Element)?.className ||
        (turn as Element).className ||
        "") as string
    ).toString().toLowerCase();

    if (cls.includes("user")) {
      const content = extractUserText(turn);
      const rawAtt = [
        ...extractAttachments(turn),
        ...extractLocalAttachments(turn, origin),
      ];
      const att = dedupeAttachments(rawAtt).map((attachment) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const a = attachment as any;
        if (a?.type === "citation" && a.url) {
          return { ...a, url: normalizeCitationUrl(a.url) } as PceAttachment;
        }
        return attachment;
      });
      const attachmentText = attachmentOnlyText(att);
      if (content || attachmentText || att.length > 0) {
        const msg: ExtractedMessage = {
          role: "user",
          content: content || attachmentText || "[Attachment]",
        };
        if (att.length > 0) msg.attachments = att;
        messages.push(msg);
      }
      return;
    }

    if (cls.includes("model")) {
      const rawAtt = [
        ...extractAttachments(turn),
        ...extractLocalAttachments(turn, origin),
      ];
      const att = dedupeAttachments(rawAtt).map((attachment) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const a = attachment as any;
        if (a?.type === "citation" && a.url) {
          return { ...a, url: normalizeCitationUrl(a.url) } as PceAttachment;
        }
        return attachment;
      });
      const content = extractAssistantText(turn, att);
      if (content) {
        const msg: ExtractedMessage = { role: "assistant", content };
        if (att.length > 0) msg.attachments = att;
        messages.push(msg);
      }
      return;
    }

    // Ambiguous turn: try both extractions.
    const userContent = extractUserText(turn);
    const userAtt = dedupeAttachments([
      ...extractAttachments(turn),
      ...extractLocalAttachments(turn, origin),
    ]);
    const userAttachmentText = attachmentOnlyText(userAtt);
    if (userContent || userAttachmentText || userAtt.length > 0) {
      const msg: ExtractedMessage = {
        role: "user",
        content: userContent || userAttachmentText || "[Attachment]",
      };
      if (userAtt.length > 0) msg.attachments = userAtt;
      messages.push(msg);
    }
    const assistantContent = extractAssistantText(turn);
    if (assistantContent) {
      messages.push({ role: "assistant", content: assistantContent });
    }
  });

  return messages;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["https://aistudio.google.com/*"],
  runAt: "document_start",
  main() {
    if (window.__PCE_GOOGLE_AI_STUDIO_ACTIVE) return;
    window.__PCE_GOOGLE_AI_STUDIO_ACTIVE = true;

    console.log("[PCE] Google AI Studio content script loaded");

    const runtime = createCaptureRuntime({
      provider: "google",
      sourceName: "google-ai-studio-web",
      siteName: "GoogleAIStudio",
      extractionStrategy: "google-ai-studio-dom",
      captureMode: "incremental",

      debounceMs: 2500,
      streamCheckMs: 1500,
      pollIntervalMs: 4000,

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
