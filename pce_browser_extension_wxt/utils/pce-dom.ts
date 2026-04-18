// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Shared DOM extraction utilities.
 *
 * TypeScript port of ``content_scripts/pce_dom_utils.js`` (P2.5 Phase 3a).
 *
 * Generic, platform-agnostic functions for extracting rich content from
 * AI chat interfaces. Used by both site-specific extractors
 * (``chatgpt.ts`` etc., landing in Phase 3b) and the universal
 * extractor.
 *
 * Design: pure, dependency-free helpers. Each function accepts its
 * DOM handles / element references explicitly so the module can be
 * imported and unit-tested against ``happy-dom`` / ``jsdom`` without
 * any browser globals. When called from a content script, callers
 * pass ``document.body`` or a turn-container element just like the
 * legacy JS.
 *
 * Parity note: legacy ``pce_dom_utils.js`` also attached a
 * ``MutationObserver`` on ``document.documentElement`` to bridge the
 * ``data-pce-manual-capture`` attribute into a ``pce-manual-capture``
 * CustomEvent. That side-effect belongs to a runtime bootstrap, not to
 * a utility module — it's preserved here as ``installManualCaptureBridge()``
 * which callers invoke explicitly.
 */

/** Attachment extracted from a message turn. */
export interface PceAttachment {
  type:
    | "image_url"
    | "image_generation"
    | "code_block"
    | "file"
    | "citation"
    | "canvas"
    | "tool_call"
    | "code_output";
  name?: string;
  url?: string;
  alt?: string;
  language?: string;
  code?: string;
  media_type?: string;
  title?: string;
  source_type?: string;
  content?: string;
  output?: string;
}

/** Shape of a fingerprintable conversation message. */
export interface FingerprintMessage {
  role?: string;
  content?: string | null;
  attachments?: Array<Record<string, unknown>> | null;
}

/**
 * Normalise a URL that might be a Google ``/url`` redirect wrapper.
 * Returns the un-wrapped target when applicable, or the input
 * resolved against the current document when possible.
 */
export function normalizeCitationUrl(
  rawUrl: string | null | undefined,
  base?: string,
): string {
  if (!rawUrl) return "";
  try {
    const resolvedBase =
      base ??
      (typeof location !== "undefined" ? location.href : "http://localhost/");
    const parsed = new URL(rawUrl, resolvedBase);
    const isGoogleRedirect =
      /(^|\.)google\.com$/i.test(parsed.hostname) && parsed.pathname === "/url";
    if (isGoogleRedirect) {
      const target =
        parsed.searchParams.get("q") || parsed.searchParams.get("url");
      if (target) return target;
    }
    return parsed.href;
  } catch {
    return rawUrl;
  }
}

/**
 * Build a deterministic fingerprint string for a conversation so
 * dedup can detect "same conversation already captured" without
 * comparing whole bodies. Preserves the legacy algorithm byte-for-byte.
 */
export function fingerprintConversation(
  messages: FingerprintMessage[] | null | undefined,
): string {
  if (!Array.isArray(messages)) return "";
  return messages
    .map((msg) => {
      const content = String(msg?.content || "").slice(0, 160);
      const attSig = Array.isArray(msg?.attachments)
        ? (msg!.attachments as Array<Record<string, unknown>>)
            .filter((att) => att && typeof att === "object")
            .map((att) => {
              const attType = (att.type as string) || "unknown";
              const code = att.code;
              const output = att.output;
              const detail =
                (att.file_id as string) ||
                (att.url as string) ||
                (att.name as string) ||
                (att.title as string) ||
                (att.language as string) ||
                (typeof code === "string" ? code.slice(0, 80) : "") ||
                (typeof output === "string" ? output.slice(0, 80) : "");
              return `${attType}:${detail}`;
            })
            .join(",")
        : "";
      return `${msg?.role || "unknown"}:${content}:${attSig}`;
    })
    .join("|");
}

const _MEDIA_TYPE_MAP: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  pdf: "application/pdf",
  txt: "text/plain",
  md: "text/markdown",
  csv: "text/csv",
  json: "application/json",
  py: "text/x-python",
  js: "text/javascript",
  ts: "text/typescript",
  html: "text/html",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ppt: "application/vnd.ms-powerpoint",
  pptx:
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  zip: "application/zip",
};

/** Guess an RFC-2046 media type from a filename (extension-based). */
export function inferMediaTypeFromName(name: string | null | undefined): string {
  const lower = String(name || "").toLowerCase();
  if (!lower || !lower.includes(".")) return "";
  const ext = lower.split(".").pop() || "";
  return _MEDIA_TYPE_MAP[ext] || "";
}

// ---------------------------------------------------------------------------
// Attachment extraction — works across AI chat UIs
// ---------------------------------------------------------------------------

const _FILE_EXT_RE =
  /\.(pdf|docx?|txt|md|py|js|ts|csv|json|xml|html?|xlsx?|pptx?|zip|rar|png|jpe?g|gif|svg|mp[34]|wav|webp|heic|c|cpp|rb|go|rs|java|kt|swift|sh|ipynb|sql|ya?ml)$/i;

const _TOOL_TEXT_RE =
  /^(?:Used|Searched|Browsed|Analyzed|Generated|Ran|Running|Reading|Thinking|Creating|Executing|Querying|Looking up|Calling|使用了|搜索|浏览了?|分析|生成|运行|正在|调用|查询)\s+(.+)/i;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

/**
 * Extract rich-content attachments from a DOM element (message turn).
 * Returns an array of attachment objects for images, code blocks,
 * file attachments, citations, canvas/artifact content, and tool
 * output. Byte-for-byte port of the legacy heuristics.
 */
export function extractAttachments(el: Element | null): PceAttachment[] {
  const attachments: PceAttachment[] = [];
  if (!el) return attachments;

  const locHost =
    typeof location !== "undefined" ? location.hostname : "";

  const upsertFileAttachment = (next: PceAttachment | null): void => {
    if (!next || !next.name) return;
    const nameKey = next.name.toLowerCase();
    const existing = attachments.find(
      (att) =>
        att.type === "file" && String(att.name || "").toLowerCase() === nameKey,
    );
    if (!existing) {
      attachments.push(next);
      return;
    }
    if (!existing.url && next.url) existing.url = next.url;
    if (!existing.media_type && next.media_type)
      existing.media_type = next.media_type;
    if (!existing.title && next.title) existing.title = next.title;
    if (!existing.source_type && next.source_type)
      existing.source_type = next.source_type;
  };

  const buildFileAttachment = (
    fileEl: Element,
    name: string,
  ): PceAttachment => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const fe = fileEl as any;
    const anchor: Element | null =
      (typeof fe.matches === "function" &&
      fe.matches("a[href]")
        ? fileEl
        : null) ||
      (fe.querySelector ? fe.querySelector("a[href]") : null) ||
      (fe.closest ? fe.closest("a[href]") : null) ||
      null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const anchorAny = anchor as any;
    const rawUrl = anchor
      ? normalizeCitationUrl(
          (anchorAny.href as string) ||
            anchor.getAttribute("href") ||
            "",
        )
      : "";
    const mediaType =
      fileEl.getAttribute?.("type") ||
      fileEl.getAttribute?.("data-mime-type") ||
      (fe.dataset ? fe.dataset.mimeType : "") ||
      inferMediaTypeFromName(name);
    const title =
      (anchor &&
        (anchor.getAttribute("download") ||
          (anchorAny.title as string) ||
          "")) ||
      fileEl.getAttribute?.("title") ||
      "";
    const att: PceAttachment = {
      type: "file",
      name: name.slice(0, 200),
    };
    if (rawUrl && !rawUrl.startsWith("javascript:") && !rawUrl.startsWith("#")) {
      att.url = rawUrl.slice(0, 1000);
    }
    if (mediaType) att.media_type = mediaType;
    if (title && title !== name) att.title = title.slice(0, 200);
    return att;
  };

  // --- Images (user-uploaded, AI-generated, inline) ---
  el.querySelectorAll("img").forEach((img) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const imgAny = img as any;
    const src: string = imgAny.src || img.getAttribute("src") || "";
    if (!src) return;
    const alt = (
      imgAny.alt ||
      img.getAttribute("alt") ||
      ""
    )
      .toString()
      .slice(0, 200);
    const srcHints = `${src} ${alt} ${img.className || ""}`;
    const looksLikeUserUpload =
      /uploaded|已上传|user image|attachment/i.test(srcHints) ||
      src.includes("/backend-api/estuary/content") ||
      src.startsWith("blob:");
    if (src.startsWith("data:image/svg")) return;
    if (/avatar|icon|logo|favicon|emoji/i.test(src)) return;
    if (/avatar|icon|logo/i.test(String(img.className || ""))) return;
    if (!looksLikeUserUpload) {
      if (imgAny.naturalWidth > 0 && imgAny.naturalWidth < 40) return;
      if (imgAny.width > 0 && imgAny.width < 40) return;
    }

    const isGenerated = /dall-?e|oaidalleapi|generated|creation/i.test(
      src + " " + alt,
    );

    attachments.push({
      type: isGenerated ? "image_generation" : "image_url",
      url: src.startsWith("data:")
        ? src.slice(0, 200) + "...[truncated]"
        : src.slice(0, 2000),
      alt,
    });
  });

  // --- Code blocks (pre > code or standalone pre) ---
  el.querySelectorAll("pre").forEach((pre) => {
    const codeEl = pre.querySelector("code");
    const code = safeText(codeEl || pre).trim();
    if (code.length < 5) return;

    let lang = "";
    const langSrc = codeEl || pre;
    const cls = String(langSrc.className || "");
    const langMatch = cls.match(/(?:language|lang|hljs)-(\w+)/);
    if (langMatch) lang = langMatch[1];

    if (!lang) {
      const prev = pre.previousElementSibling;
      if (prev) {
        const txt = safeText(prev).trim().toLowerCase();
        if (txt.length > 0 && txt.length < 30 && /^[a-z#+]+$/.test(txt))
          lang = txt;
      }
    }

    attachments.push({
      type: "code_block",
      language: lang,
      code: code.slice(0, 8000),
    });
  });

  // --- File attachments (chips, cards, upload indicators) ---
  const fileSelectors = [
    '[class*="file"]',
    '[class*="attachment"]',
    '[class*="upload"]',
    '[data-testid*="file"]',
    '[data-testid*="attachment"]',
  ];
  for (const sel of fileSelectors) {
    try {
      el.querySelectorAll(sel).forEach((fileEl) => {
        if (fileEl.querySelector("pre") || fileEl.querySelector("img")) return;
        const nameEl = fileEl.querySelector(
          '[class*="name"], [class*="title"], span, p',
        );
        const name = safeText(nameEl || fileEl).trim();
        if (!name || name.length < 2 || name.length > 200) return;
        if (fileEl.tagName === "BUTTON" && name.length < 15) return;
        upsertFileAttachment(buildFileAttachment(fileEl, name));
      });
    } catch {
      /* skip invalid selector */
    }
  }

  // --- File uploads (heuristic): small elements with filename-like text ---
  const existingFileNames = new Set(
    attachments.filter((a) => a.type === "file").map((a) => a.name || ""),
  );
  el.querySelectorAll("button, [role='button'], a, div, span").forEach(
    (chip) => {
      if (chip.querySelector("pre, code, .markdown, article")) return;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((chip as any).scrollHeight > 100) return;
      const text = safeText(chip).trim();
      if (text.length < 3 || text.length > 120) return;
      const firstLine = text.split("\n")[0].trim();
      if (_FILE_EXT_RE.test(firstLine) && !existingFileNames.has(firstLine)) {
        existingFileNames.add(firstLine);
        upsertFileAttachment(buildFileAttachment(chip, firstLine));
      }
    },
  );

  if (!attachments.some((att) => att.type === "file")) {
    const lines = safeText(el)
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter(Boolean);
    const firstLine = lines[0] || "";
    if (_FILE_EXT_RE.test(firstLine)) {
      upsertFileAttachment({
        type: "file",
        name: firstLine.slice(0, 200),
        source_type: "text-fallback",
        media_type: inferMediaTypeFromName(firstLine),
      });
    }
  }

  // --- Citations / external links ---
  el.querySelectorAll("a[href]").forEach((a) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const aAny = a as any;
    const href = normalizeCitationUrl(aAny.href || "");
    if (!href || href.startsWith("javascript:") || href.startsWith("#")) return;
    try {
      const linkHost = new URL(href).hostname;
      if (linkHost === locHost) return;
    } catch {
      return;
    }
    const title = (safeText(a).trim() || aAny.title || "").slice(0, 200);
    if (!title || title.length < 3) return;
    if (a.querySelector("img")) return;
    attachments.push({
      type: "citation",
      url: href.slice(0, 500),
      title,
    });
  });

  // --- Canvas / Artifacts (large editable content panels) ---
  const canvasSelectors = [
    '[class*="canvas"]',
    '[class*="artifact"]',
    '[data-testid*="canvas"]',
    '[data-testid*="artifact"]',
    'iframe[title*="canvas"]',
    'iframe[src*="canvas"]',
  ];
  for (const sel of canvasSelectors) {
    try {
      el.querySelectorAll(sel).forEach((canvasEl) => {
        if (canvasEl.tagName === "BUTTON" || canvasEl.tagName === "A") return;
        const text = safeText(canvasEl).trim();
        if (text.length < 20) return;
        attachments.push({ type: "canvas", content: text.slice(0, 10000) });
      });
    } catch {
      /* skip */
    }
  }

  // --- Tool call indicators ---
  const seenToolNames = new Set<string>();
  el.querySelectorAll("details, summary, [role='button'], button").forEach(
    (toolEl) => {
      const text = safeText(toolEl).trim();
      if (text.length < 4 || text.length > 300) return;
      const firstLine = text.split("\n")[0].trim();
      const toolMatch = firstLine.match(_TOOL_TEXT_RE);
      if (toolMatch) {
        const toolName = toolMatch[1].slice(0, 200);
        if (!seenToolNames.has(toolName)) {
          seenToolNames.add(toolName);
          attachments.push({ type: "tool_call", name: toolName });
        }
      }
    },
  );

  // --- Tool output / sandbox / interpreter results ---
  const outputSelectors = [
    '[class*="sandbox"]',
    '[class*="interpreter"]',
    '[class*="execution"]',
    '[class*="output"]',
    '[class*="result"]',
    '[class*="stdout"]',
  ];
  for (const sel of outputSelectors) {
    try {
      el.querySelectorAll(sel).forEach((outEl) => {
        if (outEl.tagName === "PRE" || outEl.querySelector("pre")) return;
        const text = safeText(outEl).trim();
        if (text.length < 10) return;
        if (outEl.closest('[data-message-author-role="user"]')) return;
        if (outEl.closest('[data-role="user"]')) return;
        const parentMsgClass = String(
          outEl.closest('[class*="message"]')?.className || "",
        );
        if (/\buser\b/i.test(parentMsgClass)) return;
        attachments.push({ type: "code_output", output: text.slice(0, 8000) });
      });
    } catch {
      /* skip */
    }
  }

  return attachments;
}

// ---------------------------------------------------------------------------
// Thinking / reasoning extraction
// ---------------------------------------------------------------------------

/** Pull thinking/reasoning text out of a message turn, or empty string. */
export function extractThinking(el: Element | null): string {
  if (!el) return "";
  const parts: string[] = [];

  el.querySelectorAll("details").forEach((d) => {
    const summary = d.querySelector("summary");
    const body = d.querySelector(".markdown, .whitespace-pre-wrap, div");
    if (summary && body) {
      parts.push(safeText(body).trim());
    } else {
      const txt = safeText(d).trim();
      if (txt.length > 5) parts.push(txt);
    }
  });

  if (parts.length === 0) {
    const thinkSelectors = [
      '[class*="thought"]',
      '[class*="think"]',
      '[class*="reason"]',
      '[class*="internal"]',
      '[data-testid*="think"]',
    ];
    for (const sel of thinkSelectors) {
      try {
        el.querySelectorAll(sel).forEach((thEl) => {
          const txt = safeText(thEl).trim();
          if (txt.length > 5) parts.push(txt);
        });
      } catch {
        /* skip */
      }
    }
  }

  return parts.join("\n").trim();
}

// ---------------------------------------------------------------------------
// Reply content extraction (excluding thinking)
// ---------------------------------------------------------------------------

/**
 * Extract the main reply content from a message element, preferring
 * rendered markdown and excluding thinking panels.
 */
export function extractReplyContent(el: Element | null): string {
  if (!el) return "";

  const contentSelectors = [
    ".markdown.prose",
    ".markdown",
    ".markdown-body",
    ".whitespace-pre-wrap",
    ".text-message",
    '[class*="message-content"]',
    '[class*="response-content"]',
    '[data-testid="message-content"]',
    "article",
    '[class*="research"]',
    '[class*="report"]',
    '[class*="canvas"]',
    '[role="article"]',
    '[role="document"]',
  ];

  for (const sel of contentSelectors) {
    try {
      const contentEl = el.querySelector(sel);
      if (contentEl) {
        const text = safeText(contentEl).trim();
        if (text.length > 10) return text;
      }
    } catch {
      /* skip */
    }
  }

  // Fallback: clone, remove thinking panels, return rest
  const clone = el.cloneNode(true) as Element;
  clone
    .querySelectorAll("details, [class*='think'], [class*='thought']")
    .forEach((d) => d.remove());
  clone
    .querySelectorAll("script, style, [hidden], .sr-only")
    .forEach((e) => e.remove());
  return safeText(clone).trim();
}

// ---------------------------------------------------------------------------
// Generic streaming detection
// ---------------------------------------------------------------------------

/**
 * Detect if the AI is currently generating/streaming a response.
 * Uses heuristic selectors that work across many AI chat UIs.
 */
export function isStreaming(doc: Document = globalThis.document): boolean {
  if (!doc) return false;

  const stopSelectors = [
    'button[aria-label*="Stop"]',
    'button[aria-label*="stop"]',
    'button[aria-label*="Cancel"]',
    'button[data-testid="stop-button"]',
    'button[class*="stop"]',
    'button[class*="cancel-generation"]',
  ];
  for (const sel of stopSelectors) {
    try {
      if (doc.querySelector(sel)) return true;
    } catch {
      /* skip */
    }
  }

  const streamSelectors = [
    ".result-streaming",
    '[class*="result-streaming"]',
    '[class*="streaming"]',
    '[class*="generating"]',
    '[class*="typing"]',
    '[class*="loading-dots"]',
    '[class*="cursor-blink"]',
  ];
  const main = doc.querySelector("main") || doc.body;
  if (!main) return false;
  for (const sel of streamSelectors) {
    try {
      if (main.querySelector(sel)) return true;
    } catch {
      /* skip */
    }
  }

  return false;
}

// ---------------------------------------------------------------------------
// Session-hint extraction from URL
// ---------------------------------------------------------------------------

/**
 * Extract a conversation/session ID from the URL path. Works for the
 * common ``/c/<id>``, ``/chat/<id>``, ``/conversation/<id>``,
 * ``/thread/<id>``, ``/t/<id>`` patterns. Falls back to the whole path.
 */
export function getSessionHint(pathname?: string): string | null {
  const path =
    pathname ??
    (typeof location !== "undefined" ? location.pathname : "") ??
    "";
  const patterns = [
    /\/c\/([a-f0-9-]{8,})/i,
    /\/chat\/([a-f0-9-]{8,})/i,
    /\/conversation\/([a-f0-9-]{8,})/i,
    /\/thread\/([a-f0-9-]{8,})/i,
    /\/t\/([a-f0-9-]{8,})/i,
  ];
  for (const re of patterns) {
    const m = path.match(re);
    if (m) return m[1];
  }
  return path.length > 1 ? path : null;
}

// ---------------------------------------------------------------------------
// Manual-capture bridge (MAIN ↔ content world)
// ---------------------------------------------------------------------------

/**
 * Install a MutationObserver that bridges the
 * ``data-pce-manual-capture`` attribute on ``<html>`` into a
 * ``pce-manual-capture`` CustomEvent. Called from the content-script
 * entrypoint; separated from the utility module to keep pure
 * functions pure. Returns a teardown function (mostly for tests).
 */
export function installManualCaptureBridge(
  doc: Document = globalThis.document,
): () => void {
  if (!doc) return () => {};
  let lastManualCaptureToken = "";

  const fire = (): void => {
    const root = doc.documentElement;
    if (!root) return;
    const token = root.getAttribute("data-pce-manual-capture") || "";
    if (!token || token === lastManualCaptureToken) return;
    lastManualCaptureToken = token;
    doc.dispatchEvent(new CustomEvent("pce-manual-capture"));
  };

  let observer: MutationObserver | null = null;
  try {
    const root = doc.documentElement;
    if (root && typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(() => fire());
      observer.observe(root, {
        attributes: true,
        attributeFilter: ["data-pce-manual-capture"],
      });
      fire();
    }
  } catch {
    // Non-fatal: manual capture still works when triggered inside the
    // extension world.
  }

  return () => {
    if (observer) observer.disconnect();
    observer = null;
  };
}
