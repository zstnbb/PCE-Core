/**
 * PCE – Shared DOM Extraction Utilities
 *
 * Provides generic, platform-agnostic functions for extracting rich content
 * from AI chat interfaces. Used by both site-specific extractors (chatgpt.js,
 * deepseek.js, etc.) and the universal_extractor.js.
 *
 * Exposed via window.__PCE_EXTRACT so any content script can call them.
 */

(() => {
  "use strict";

  if (window.__PCE_EXTRACT) return;

  function normalizeCitationUrl(rawUrl) {
    if (!rawUrl) return "";
    try {
      const parsed = new URL(rawUrl, location.href);
      const isGoogleRedirect =
        /(^|\.)google\.com$/i.test(parsed.hostname) &&
        parsed.pathname === "/url";
      if (isGoogleRedirect) {
        const target = parsed.searchParams.get("q") || parsed.searchParams.get("url");
        if (target) return target;
      }
      return parsed.href;
    } catch {
      return rawUrl;
    }
  }

  function fingerprintConversation(messages) {
    if (!Array.isArray(messages)) return "";
    return messages
      .map((msg) => {
        const content = String(msg?.content || "").slice(0, 160);
        const attSig = Array.isArray(msg?.attachments)
          ? msg.attachments
              .filter((att) => att && typeof att === "object")
              .map((att) => {
                const attType = att.type || "unknown";
                const detail =
                  att.file_id ||
                  att.url ||
                  att.name ||
                  att.title ||
                  att.language ||
                  (typeof att.code === "string" ? att.code.slice(0, 80) : "") ||
                  (typeof att.output === "string" ? att.output.slice(0, 80) : "");
                return `${attType}:${detail}`;
              })
              .join(",");
          : "";
        return `${msg?.role || "unknown"}:${content}:${attSig}`;
      })
      .join("|");
  }

  // -------------------------------------------------------------------------
  // Generic attachment extraction — works across AI chat UIs
  // -------------------------------------------------------------------------

  /**
   * Extract rich-content attachments from a DOM element (message turn).
   * Returns an array of attachment objects for images, code blocks,
   * file attachments, citations, canvas/artifact content, and tool output.
   *
   * @param {Element} el - The message turn DOM element
   * @returns {Array<Object>} attachments
   */
  function extractAttachments(el) {
    const attachments = [];
    if (!el) return attachments;

    // --- Images (user-uploaded, AI-generated, inline) ---
    el.querySelectorAll("img").forEach((img) => {
      const src = img.src || img.getAttribute("src") || "";
      if (!src) return;
      const alt = (img.alt || img.getAttribute("alt") || "").slice(0, 200);
      const srcHints = `${src} ${alt} ${img.className || ""}`;
      const looksLikeUserUpload =
        /uploaded|已上传|user image|attachment/i.test(srcHints) ||
        src.includes("/backend-api/estuary/content") ||
        src.startsWith("blob:");
      // Skip SVG icons, avatars, tiny UI elements
      if (src.startsWith("data:image/svg")) return;
      if (/avatar|icon|logo|favicon|emoji/i.test(src)) return;
      if (/avatar|icon|logo/i.test(img.className || "")) return;
      if (!looksLikeUserUpload) {
        if (img.naturalWidth > 0 && img.naturalWidth < 40) return;
        if (img.width > 0 && img.width < 40) return;
      }

      const isGenerated = /dall-?e|oaidalleapi|generated|creation/i.test(src + " " + alt);

      attachments.push({
        type: isGenerated ? "image_generation" : "image_url",
        url: src.startsWith("data:") ? src.slice(0, 200) + "...[truncated]" : src.slice(0, 2000),
        alt,
      });
    });

    // --- Code blocks (pre > code or standalone pre) ---
    el.querySelectorAll("pre").forEach((pre) => {
      const codeEl = pre.querySelector("code");
      const code = (codeEl || pre).innerText.trim();
      if (code.length < 5) return;

      let lang = "";
      // Try class like "language-python", "hljs language-js"
      const langSrc = codeEl || pre;
      const cls = langSrc.className || "";
      const langMatch = cls.match(/(?:language|lang|hljs)-(\w+)/);
      if (langMatch) lang = langMatch[1];

      // Try header bar (many UIs show language label above code block)
      if (!lang) {
        const prev = pre.previousElementSibling;
        if (prev) {
          const txt = prev.innerText.trim().toLowerCase();
          if (txt.length > 0 && txt.length < 30 && /^[a-z#+]+$/.test(txt)) lang = txt;
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
      '[class*="file"]', '[class*="attachment"]', '[class*="upload"]',
      '[data-testid*="file"]', '[data-testid*="attachment"]',
    ];
    for (const sel of fileSelectors) {
      try {
        el.querySelectorAll(sel).forEach((fileEl) => {
          // Skip if it contains code or images (already captured above)
          if (fileEl.querySelector("pre") || fileEl.querySelector("img")) return;
          // Skip tiny or very large text (likely not a filename)
          const nameEl = fileEl.querySelector('[class*="name"], [class*="title"], span, p');
          const name = (nameEl || fileEl).innerText.trim();
          if (!name || name.length < 2 || name.length > 200) return;
          // Skip if it looks like a button label
          if (fileEl.tagName === "BUTTON" && name.length < 15) return;
          attachments.push({ type: "file", name: name.slice(0, 200) });
        });
      } catch { /* skip invalid selector */ }
    }

    // --- File uploads (heuristic): small elements with filename-like text ---
    // Catches file chips on platforms with obfuscated class names (e.g. ChatGPT)
    const _fileExtRe = /\.(pdf|docx?|txt|md|py|js|ts|csv|json|xml|html?|xlsx?|pptx?|zip|rar|png|jpe?g|gif|svg|mp[34]|wav|webp|heic|c|cpp|rb|go|rs|java|kt|swift|sh|ipynb|sql|ya?ml)$/i;
    const _existingFileNames = new Set(attachments.filter(a => a.type === "file").map(a => a.name));
    el.querySelectorAll("button, [role='button'], a, div, span").forEach((chip) => {
      // Only consider small, leaf-ish elements (file chips are compact)
      if (chip.querySelector("pre, code, .markdown, article")) return;
      if (chip.scrollHeight > 100) return;
      const text = chip.innerText.trim();
      if (text.length < 3 || text.length > 120) return;
      // Check if the text (or its first line) looks like a filename
      const firstLine = text.split("\n")[0].trim();
      if (_fileExtRe.test(firstLine) && !_existingFileNames.has(firstLine)) {
        _existingFileNames.add(firstLine);
        attachments.push({ type: "file", name: firstLine.slice(0, 200) });
      }
    });

    // --- Citations / external links ---
    el.querySelectorAll("a[href]").forEach((a) => {
      const href = normalizeCitationUrl(a.href || "");
      if (!href || href.startsWith("javascript:") || href.startsWith("#")) return;
      // Skip internal platform links
      try {
        const linkHost = new URL(href).hostname;
        if (linkHost === location.hostname) return;
      } catch { return; }
      const title = (a.innerText.trim() || a.title || "").slice(0, 200);
      if (!title || title.length < 3) return;
      if (a.querySelector("img")) return; // image link, not citation
      attachments.push({
        type: "citation",
        url: href.slice(0, 500),
        title,
      });
    });

    // --- Canvas / Artifacts (large editable content panels) ---
    const canvasSelectors = [
      '[class*="canvas"]', '[class*="artifact"]',
      '[data-testid*="canvas"]', '[data-testid*="artifact"]',
      'iframe[title*="canvas"]', 'iframe[src*="canvas"]',
    ];
    for (const sel of canvasSelectors) {
      try {
        el.querySelectorAll(sel).forEach((canvasEl) => {
          if (canvasEl.tagName === "BUTTON" || canvasEl.tagName === "A") return;
          const text = canvasEl.innerText.trim();
          if (text.length < 20) return;
          attachments.push({ type: "canvas", content: text.slice(0, 10000) });
        });
      } catch { /* skip */ }
    }

    // --- Tool call indicators (collapsible sections, status chips) ---
    // Many AI UIs show tool usage as expandable sections like "Used X" or "Searched 3 sites"
    const _toolTextRe = /^(?:Used|Searched|Browsed|Analyzed|Generated|Ran|Running|Reading|Thinking|Creating|Executing|Querying|Looking up|Calling|使用了|搜索|浏览了?|分析|生成|运行|正在|调用|查询)\s+(.+)/i;
    const _seenToolNames = new Set();
    el.querySelectorAll("details, summary, [role='button'], button").forEach((toolEl) => {
      const text = toolEl.innerText.trim();
      if (text.length < 4 || text.length > 300) return;
      const firstLine = text.split("\n")[0].trim();
      const toolMatch = firstLine.match(_toolTextRe);
      if (toolMatch) {
        const toolName = toolMatch[1].slice(0, 200);
        if (!_seenToolNames.has(toolName)) {
          _seenToolNames.add(toolName);
          attachments.push({ type: "tool_call", name: toolName });
        }
      }
    });

    // --- Tool output / sandbox / interpreter results ---
    const outputSelectors = [
      '[class*="sandbox"]', '[class*="interpreter"]',
      '[class*="execution"]', '[class*="output"]',
      '[class*="result"]', '[class*="stdout"]',
    ];
    for (const sel of outputSelectors) {
      try {
        el.querySelectorAll(sel).forEach((outEl) => {
          if (outEl.tagName === "PRE" || outEl.querySelector("pre")) return;
          const text = outEl.innerText.trim();
          if (text.length < 10) return;
          // Skip if inside a user message
          if (outEl.closest('[data-message-author-role="user"]')) return;
          if (outEl.closest('[data-role="user"]')) return;
          if (/\buser\b/i.test(outEl.closest('[class*="message"]')?.className || "")) return;
          attachments.push({ type: "code_output", output: text.slice(0, 8000) });
        });
      } catch { /* skip */ }
    }

    return attachments;
  }

  // -------------------------------------------------------------------------
  // Generic thinking/reasoning extraction
  // -------------------------------------------------------------------------

  /**
   * Extract thinking/reasoning content from a message element.
   * Works across platforms that use <details>, collapsible panels,
   * or elements with "think"/"thought"/"reason" in class names.
   *
   * @param {Element} el - The message turn DOM element
   * @returns {string} thinking text (empty string if none found)
   */
  function extractThinking(el) {
    if (!el) return "";
    const parts = [];

    // Pattern 1: <details> containing thinking (ChatGPT, DeepSeek, etc.)
    el.querySelectorAll("details").forEach((d) => {
      const summary = d.querySelector("summary");
      const body = d.querySelector(".markdown, .whitespace-pre-wrap, div");
      if (summary && body) {
        parts.push(body.innerText.trim());
      } else {
        const txt = d.innerText.trim();
        if (txt.length > 5) parts.push(txt);
      }
    });

    // Pattern 2: elements with class containing "thought", "think", "reason"
    if (parts.length === 0) {
      const thinkSelectors = [
        '[class*="thought"]', '[class*="think"]', '[class*="reason"]',
        '[class*="internal"]', '[data-testid*="think"]',
      ];
      for (const sel of thinkSelectors) {
        try {
          el.querySelectorAll(sel).forEach((thEl) => {
            const txt = thEl.innerText.trim();
            if (txt.length > 5) parts.push(txt);
          });
        } catch { /* skip */ }
      }
    }

    return parts.join("\n").trim();
  }

  // -------------------------------------------------------------------------
  // Generic reply content extraction (excluding thinking)
  // -------------------------------------------------------------------------

  /**
   * Extract the main reply content from a message element,
   * preferring rendered markdown and excluding thinking panels.
   *
   * @param {Element} el - The message turn DOM element
   * @returns {string} reply text
   */
  function extractReplyContent(el) {
    if (!el) return "";

    // Priority-ordered content selectors (generic across platforms)
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
          const text = contentEl.innerText.trim();
          if (text.length > 10) return text;
        }
      } catch { /* skip */ }
    }

    // Fallback: clone, remove thinking panels, return rest
    const clone = el.cloneNode(true);
    clone.querySelectorAll("details, [class*='think'], [class*='thought']").forEach((d) => d.remove());
    clone.querySelectorAll("script, style, [hidden], .sr-only").forEach((e) => e.remove());
    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Generic streaming detection
  // -------------------------------------------------------------------------

  /**
   * Detect if the AI is currently generating/streaming a response.
   * Uses heuristic selectors that work across many AI chat UIs.
   *
   * @returns {boolean}
   */
  function isStreaming() {
    // Stop/cancel buttons (most platforms show these during generation)
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
        if (document.querySelector(sel)) return true;
      } catch { /* skip */ }
    }

    // Streaming indicators in the conversation area
    const streamSelectors = [
      ".result-streaming",
      '[class*="result-streaming"]',
      '[class*="streaming"]',
      '[class*="generating"]',
      '[class*="typing"]',
      '[class*="loading-dots"]',
      '[class*="cursor-blink"]',
    ];
    const main = document.querySelector("main") || document.body;
    for (const sel of streamSelectors) {
      try {
        if (main.querySelector(sel)) return true;
      } catch { /* skip */ }
    }

    return false;
  }

  // -------------------------------------------------------------------------
  // Generic session hint extraction from URL
  // -------------------------------------------------------------------------

  /**
   * Extract a conversation/session ID from the URL path.
   * Works for common patterns: /c/<id>, /chat/<id>, /conversation/<id>, /thread/<id>
   *
   * @returns {string|null}
   */
  function getSessionHint() {
    const path = location.pathname;
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

  // -------------------------------------------------------------------------
  // Expose API
  // -------------------------------------------------------------------------

  window.__PCE_EXTRACT = {
    extractAttachments,
    extractThinking,
    extractReplyContent,
    isStreaming,
    getSessionHint,
    normalizeCitationUrl,
    fingerprintConversation,
  };

  console.debug("[PCE] DOM extraction utilities loaded");
})();
