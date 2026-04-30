// SPDX-License-Identifier: Apache-2.0
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
  extractThinking,
  isStreaming as sharedIsStreaming,
  normalizeCitationUrl,
  type PceAttachment,
} from "../utils/pce-dom";

declare global {
  interface Window {
    __PCE_GOOGLE_AI_STUDIO_ACTIVE?: boolean | string;
  }
}

const CONTENT_SCRIPT_INSTANCE_ID = "google-ai-studio-20260425-system-layer-edit";

// ---------------------------------------------------------------------------
// Text-normalisation tables (lifted verbatim from legacy JS)
// ---------------------------------------------------------------------------

const CONTROL_LINE_RE =
  /^(?:edit|more_vert|thumb_up|thumb_down|thumb_up_alt|thumb_down_alt|info|share|compare_arrows|add|close|search|settings|menu_open)$/i;
const META_LINE_RE = /^(?:user|model)\s+\d{1,2}:\d{2}$/i;
const DISCLAIMER_LINE_RE = /^google ai models may make mistakes/i;
const AI_STUDIO_UI_LINE_RE =
  /^(?:download|content_copy|expand_less|expand_more|copy code|copy|fullscreen|chevron_right)$/i;
const SESSION_HINT_RE = /\/prompts\/([a-zA-Z0-9_-]+)/;
const MODEL_NAME_RE = /\b((?:gemini|imagen|veo|lyria)-[\w.-]+)\b/i;
const AI_STUDIO_ERROR_RE =
  /(failed to generate content|permission denied|an internal error has occurred|quota|rate limit|try again)/i;
const TRANSIENT_PROMPT_IDS = new Set([
  "new_chat",
  "new_freeform",
  "new_structured",
]);

let transientSessionHint: string | null = null;

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
  // Check both the provided name/alt and the source URL — a non-empty
  // alt ("a diagram") must not mask the extension hint in the URL.
  const lower = (
    String(name || "") +
    " " +
    String(src || "")
  ).toLowerCase();
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

function filterAiStudioAttachments(list: PceAttachment[]): PceAttachment[] {
  return list.filter((attachment) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const a = attachment as any;
    // `extractAttachments` has a ChatGPT-specific title fallback that turns
    // AI Studio action-bar text such as "edit" into fake file attachments.
    // GAS has its own ms-file-chunk path above, so this fallback is noise here.
    if (a?.type === "file" && a.source_type === "chatgpt-upload-title-fallback") {
      return false;
    }
    return true;
  });
}

interface CleanOptions {
  stripLinks?: boolean;
  stripCode?: boolean;
  stripThoughts?: boolean;
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
        ".actions-container, .author-label, .timestamp, .turn-footer, ms-chat-turn-options, " +
        ".role-container, .model-run-time, .model-run-time-pill, .status-message, " +
        ".header, .info-container, .feedback-container, .bottom-right-image-controls, " +
        ".chat-hallucinations-disclaimer, .error-callout, .error-icon, .link-warning, " +
        ".mat-expansion-indicator, .mat-expansion-panel-header-description, " +
        ".loading-indicator, loading-indicator, .hover-or-edit, .actions, " +
        ".material-symbols-outlined, ms-prompt-feedback",
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
  if (options.stripThoughts) {
    clone
      .querySelectorAll(
        "ms-thought-chunk, .thought-panel, [class*='thought-panel'], " +
          ".top-panel-header, .thought-summary, .thought-toggle",
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
      ".chat-view-container, ms-chat-session, .chat-session-content, " +
        "ms-prompt-renderer, ms-chunk-editor, main",
    ) || doc.body
  );
}

export function getSessionHint(
  pathname: string = location.pathname,
): string | null {
  const m = pathname.match(SESSION_HINT_RE);
  if (!m) return null;
  const id = m[1];
  if (TRANSIENT_PROMPT_IDS.has(id)) return null;
  return id;
}

function getTransientSessionHint(): string {
  if (!transientSessionHint) {
    transientSessionHint = `ai-studio-transient-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 8)}`;
  }
  return transientSessionHint;
}

export function getCaptureSessionHint(
  pathname: string = location.pathname,
  fallbackId?: string,
): string | null {
  const stable = getSessionHint(pathname);
  if (stable) return stable;

  const m = pathname.match(SESSION_HINT_RE);
  if (!m || !TRANSIENT_PROMPT_IDS.has(m[1])) return null;
  return fallbackId || getTransientSessionHint();
}

export function getModelName(doc: Document = document): string | null {
  const selectors = [
    ".model-selector-card",
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

let lastKnownSystemInstructions: string | null = null;

type SystemInstructionScan = {
  found: boolean;
  text: string | null;
};

function scanSystemInstructionsFromDom(
  doc: Document = document,
): SystemInstructionScan {
  const selectors = [
    'textarea[aria-label*="System instructions" i]',
    'textarea[aria-label*="system" i]',
    'textarea[placeholder*="instruction" i]',
    'textarea[placeholder*="system" i]',
    '[class*="system-instructions"] textarea',
    '[class*="system"] textarea',
    '[contenteditable="true"][aria-label*="system" i]',
  ];

  let found = false;
  for (const sel of selectors) {
    try {
      const fields = Array.from(doc.querySelectorAll(sel));
      for (const field of fields) {
        found = true;
        const value =
          (field as HTMLTextAreaElement).value ||
          field.getAttribute("value") ||
          safeInnerText(field);
        const text = normalizeText(value);
        if (text && !/^system instructions$/i.test(text)) {
          return { found: true, text };
        }
      }
    } catch {
      /* skip invalid selector */
    }
  }

  const cardSelectors = [
    '[class*="system-instructions"]',
    '[aria-label*="System instructions" i]',
    'ms-run-settings [class*="card"]',
  ];
  for (const sel of cardSelectors) {
    try {
      const cards = Array.from(doc.querySelectorAll(sel));
      for (const card of cards) {
        found = true;
        const rawText = normalizeText(safeInnerText(card));
        if (!rawText) continue;
        const text = rawText
          .replace(/^system instructions\s*/i, "")
          .trim();
        if (
          text &&
          !/^optional tone and style instructions for the model$/i.test(text)
        ) {
          return { found: true, text };
        }
      }
    } catch {
      /* skip invalid selector */
    }
  }

  try {
    const bodyText = normalizeText(
      [doc.body ? safeInnerText(doc.body) : "", deepTextContent(doc.body)].join(" "),
    );
    const bodyMatch = bodyText.match(
      /System instructions\s+(.+?)(?:\s+Temperature|\s+Thinking level|\s+Tools|\s+Structured outputs|\s+Code execution|\s+Function calling|\s+Grounding with|\s+URL context|$)/i,
    );
    if (bodyMatch) {
      found = true;
      const text = bodyMatch[1].trim();
      if (
        text &&
        !/^optional tone and style instructions for the model$/i.test(text)
      ) {
        return { found: true, text };
      }
    }
  } catch {
    /* body text fallback is best-effort only */
  }
  return { found, text: null };
}

function deepTextContent(node: Node | null, limit = 30000): string {
  if (!node || limit <= 0) return "";
  const parts: string[] = [];
  const visit = (current: Node) => {
    if (parts.join(" ").length > limit) return;
    if (current.nodeType === Node.TEXT_NODE) {
      const text = current.textContent || "";
      if (text.trim()) parts.push(text);
      return;
    }
    if (current.nodeType !== Node.ELEMENT_NODE && current.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) {
      return;
    }
    const element = current as Element & { shadowRoot?: ShadowRoot | null };
    if (element.shadowRoot) visit(element.shadowRoot);
    for (const child of Array.from(current.childNodes)) {
      visit(child);
      if (parts.join(" ").length > limit) break;
    }
  };
  visit(node);
  return parts.join(" ");
}

export function rememberSystemInstructions(
  doc: Document = document,
): string | null {
  const scan = scanSystemInstructionsFromDom(doc);
  if (scan.found) {
    lastKnownSystemInstructions = scan.text;
    return scan.text;
  }
  return null;
}

export function getSystemInstructions(doc: Document = document): string | null {
  const current = rememberSystemInstructions(doc);
  return current || lastKnownSystemInstructions;
}

function getLayerMeta(doc: Document = document): Record<string, unknown> | null {
  const systemInstructions = getSystemInstructions(doc);
  return systemInstructions
    ? { system_instructions: systemInstructions }
    : null;
}

function getTurnContainer(turn: Element): Element | null {
  if (
    turn.matches(
      ".chat-turn-container, .virtual-scroll-container, .user-prompt-container, " +
        ".model-prompt-container, [data-turn-role]",
    )
  ) {
    return turn;
  }
  return (
    turn.querySelector(".chat-turn-container") ||
    turn.querySelector(
      ".virtual-scroll-container, .user-prompt-container, .model-prompt-container",
    ) ||
    turn.querySelector("[data-turn-role]") ||
    null
  );
}

function querySelfOrDescendant(turn: Element, selector: string): Element | null {
  if (turn.matches(selector)) return turn;
  return turn.querySelector(selector);
}

function getTurnRole(turn: Element): "user" | "assistant" | null {
  const candidates = [
    turn,
    getTurnContainer(turn),
    turn.querySelector("[data-turn-role]"),
  ].filter(Boolean) as Element[];

  for (const candidate of candidates) {
    const classText = String(candidate.className || "").toLowerCase();
    if (/\buser\b/.test(classText)) return "user";
    if (/\bmodel\b/.test(classText)) return "assistant";
    const dataRole = (
      candidate.getAttribute("data-turn-role") || ""
    ).toLowerCase();
    if (dataRole.includes("user")) return "user";
    if (dataRole.includes("model")) return "assistant";
  }
  return null;
}

function extractAiStudioThinking(turn: Element): string {
  const parts: string[] = [];
  const selectors = [
    "ms-thought-chunk .mat-expansion-panel-body",
    "ms-thought-chunk .mat-expansion-panel-content",
    "ms-thought-chunk .markdown",
    "ms-thought-chunk .whitespace-pre-wrap",
    "details .markdown",
    "details .whitespace-pre-wrap",
  ];
  for (const sel of selectors) {
    try {
      turn.querySelectorAll(sel).forEach((node) => {
        const text = normalizeAiStudioThinking(safeInnerText(node));
        if (!text) return;
        parts.push(text);
      });
    } catch {
      /* skip */
    }
  }
  return normalizeText(parts.join("\n"));
}

function normalizeAiStudioThinking(text: string | null | undefined): string {
  const lines = normalizeText(text)
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !/^thoughts$/i.test(line))
    .filter((line) => !/expand to view model thoughts/i.test(line))
    .filter((line) => !/^chevron_right$/i.test(line));
  return lines.join("\n").trim();
}

function isErrorTurn(el: Element): boolean {
  const text = cleanContainerText(el, { stripThoughts: true });
  if (!text) return false;
  const lines = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length > 0 && lines.every((line) => AI_STUDIO_ERROR_RE.test(line));
}

export function extractUserText(turn: Element): string {
  const userContainer = querySelfOrDescendant(
    turn,
    ".chat-turn-container.user, .chat-turn-container .user, " +
      ".virtual-scroll-container.user-prompt-container, .user-prompt-container, " +
      "[data-turn-role='User']",
  );
  return cleanContainerText(userContainer || turn);
}

export function extractAssistantText(
  turn: Element,
  attachments: PceAttachment[] = [],
): string {
  const modelContainer = querySelfOrDescendant(
    turn,
    ".chat-turn-container.model, .chat-turn-container .model, " +
      ".virtual-scroll-container.model-prompt-container, .model-prompt-container, " +
      "[data-turn-role='Model']",
  ) || getTurnContainer(turn);
  if (!modelContainer) return "";
  if (getTurnRole(turn) !== "assistant" && !String(modelContainer.className).includes("model")) {
    return "";
  }
  if (isErrorTurn(modelContainer)) return "";

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasCode = attachments.some((a: any) => a?.type === "code_block");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasCitation = attachments.some((a: any) => a?.type === "citation");
  const cleaned = cleanContainerText(modelContainer, {
    stripCode: hasCode,
    stripLinks: hasCitation,
    stripThoughts: true,
  });
  const reply =
    cleaned ||
    cleanContainerText(modelContainer, {
      stripLinks: hasCitation,
      stripThoughts: true,
    }) ||
    attachmentOnlyText(attachments);

  const thinking =
    extractAiStudioThinking(modelContainer) ||
    normalizeAiStudioThinking(extractThinking(modelContainer));
  if (thinking) {
    return `<thinking>\n${thinking}\n</thinking>\n\n${normalizeText(reply)}`.trim();
  }
  return normalizeText(reply);
}

function getPromptTurns(doc: Document): Element[] {
  const chatTurns = Array.from(doc.querySelectorAll("ms-chat-turn"));
  if (chatTurns.length > 0) return chatTurns;

  const fallbackTurns = Array.from(
    doc.querySelectorAll(
      ".virtual-scroll-container.user-prompt-container, " +
        ".virtual-scroll-container.model-prompt-container, " +
        ".user-prompt-container, .model-prompt-container, " +
        "[data-turn-role='User'], [data-turn-role='Model']",
    ),
  );

  return fallbackTurns.filter(
    (turn) => !fallbackTurns.some((other) => other !== turn && other.contains(turn)),
  );
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
  const turns = getPromptTurns(doc);
  if (turns.length === 0) return messages;

  turns.forEach((turn) => {
    const role = getTurnRole(turn);

    if (role === "user") {
      const content = extractUserText(turn);
      const rawAtt = [
        ...extractAttachments(turn),
        ...extractLocalAttachments(turn, origin),
      ];
      const att = dedupeAttachments(filterAiStudioAttachments(rawAtt)).map((attachment) => {
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

    if (role === "assistant") {
      const rawAtt = [
        ...extractAttachments(turn),
        ...extractLocalAttachments(turn, origin),
      ];
      const att = dedupeAttachments(filterAiStudioAttachments(rawAtt)).map((attachment) => {
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

    // Ambiguous turn: try both extractions — but only when we can
    // distinguish user from model via explicit container classes.
    //
    // Closes P5.B gap **A3**: previously, an ambiguous turn with a
    // ``.chat-turn-container.model`` child (but no outer class marker)
    // would extract the WHOLE turn as the user message (because
    // ``extractUserText`` falls back to ``cleanContainerText(turn)``),
    // producing a ghost user turn containing the model's reply.
    //
    // We now only extract a user message if an explicit user container
    // exists, and only extract an assistant message if an explicit
    // model container exists. Turns with neither (ghost / loading /
    // structural wrappers) contribute zero messages.
    const userContainer = turn.querySelector(
      ".chat-turn-container.user, .chat-turn-container .user",
    );
    const modelContainer = turn.querySelector(
      ".chat-turn-container.model, .model",
    );
    if (!userContainer && !modelContainer) {
      return;
    }

    if (userContainer) {
      const userContent = extractUserText(turn);
      const userAtt = dedupeAttachments(filterAiStudioAttachments([
        ...extractAttachments(turn),
        ...extractLocalAttachments(turn, origin),
      ]));
      const userAttachmentText = attachmentOnlyText(userAtt);
      if (userContent || userAttachmentText || userAtt.length > 0) {
        const msg: ExtractedMessage = {
          role: "user",
          content: userContent || userAttachmentText || "[Attachment]",
        };
        if (userAtt.length > 0) msg.attachments = userAtt;
        messages.push(msg);
      }
    }

    if (modelContainer) {
      const assistantContent = extractAssistantText(turn);
      if (assistantContent) {
        messages.push({ role: "assistant", content: assistantContent });
      }
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
    if (window.__PCE_GOOGLE_AI_STUDIO_ACTIVE === CONTENT_SCRIPT_INSTANCE_ID) return;
    window.__PCE_GOOGLE_AI_STUDIO_ACTIVE = CONTENT_SCRIPT_INSTANCE_ID;

    console.log("[PCE] Google AI Studio content script loaded", CONTENT_SCRIPT_INSTANCE_ID);

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
      getSessionHint: () => getCaptureSessionHint(),
      getModelName: () => getModelName(document),
      getLayerMeta: () => getLayerMeta(document),
      resolveConversationId: () => getCaptureSessionHint(),
      hookHistoryApi: true,
      requireSessionHint: true,
      // The AI Studio router transitions ``/prompts/new_chat`` ->
      // ``/prompts/<conv-id>`` mid-turn, and the
      // ``ms-chat-turn`` web component briefly renders the model
      // turn before the user turn is reattached after the SPA
      // route swap. Without this guard the capture runtime fires
      // a partial payload with only the user turn (under the
      // transient hint) and a second partial with only the model
      // turn (under the stable hint), producing two PCE-Core
      // sessions per real conversation. This is the same DOM
      // race the Poe / Grok adapters mitigate via this flag.
      // T02 / T13 manifest the bug as ``no_assistant`` /
      // ``no_pce_message_with_token`` failures; flipping it on
      // collapses the partial captures into a single complete
      // payload at stable-URL time.
      requireBothRoles: true,
    });

    document.addEventListener("pce-manual-capture", () => {
      runtime.triggerCapture();
    });

    const rememberCurrentSystemInstructions = () => {
      try {
        rememberSystemInstructions(document);
      } catch {
        /* best-effort memory for transient settings panel */
      }
    };
    document.addEventListener("input", rememberCurrentSystemInstructions, true);
    document.addEventListener("change", rememberCurrentSystemInstructions, true);
    document.addEventListener("keyup", rememberCurrentSystemInstructions, true);
    try {
      const root = document.documentElement || document;
      new MutationObserver(rememberCurrentSystemInstructions).observe(root, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["aria-label", "placeholder", "class", "value"],
      });
    } catch {
      /* MutationObserver may be unavailable in narrow test harnesses. */
    }
    rememberCurrentSystemInstructions();

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
