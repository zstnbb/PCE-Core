/**
 * PCE Content Script - Google AI Studio (aistudio.google.com)
 *
 * DOM extractor for AI Studio's prompt playground chat surface.
 * The page renders each turn inside <ms-chat-turn> with stable user/model
 * container classes, so this script captures the visible conversation directly
 * instead of relying on the generic interceptor path.
 */

(function () {
  "use strict";

  if (window.__PCE_GOOGLE_AI_STUDIO_ACTIVE) return;
  window.__PCE_GOOGLE_AI_STUDIO_ACTIVE = true;

  const PROVIDER = "google";
  const SOURCE_NAME = "google-ai-studio-web";
  const DEBOUNCE_MS = 2500;
  const STREAM_CHECK_MS = 1500;
  const POLL_INTERVAL_MS = 4000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let activeObserver = null;
  let lastUrl = location.href;

  const _pce = () => window.__PCE_EXTRACT || {};
  const extractAttachments = (el) => (_pce().extractAttachments || (() => []))(el);
  const extractReplyContent = (el) => (_pce().extractReplyContent || ((node) => node ? node.innerText.trim() : ""))(el);
  const extractThinking = (el) => (_pce().extractThinking || (() => ""))(el);
  const fingerprintConversation = (messages) => (_pce().fingerprintConversation || ((items) => items.map((msg) => `${msg.role}:${msg.content.slice(0, 120)}`).join("|")))(messages);

  const CONTROL_LINE_RE = /^(?:edit|more_vert|thumb_up|thumb_down|thumb_up_alt|thumb_down_alt|info|share|compare_arrows|add|close|search|settings|menu_open)$/i;
  const META_LINE_RE = /^(?:user|model)\s+\d{1,2}:\d{2}$/i;
  const DISCLAIMER_LINE_RE = /^google ai models may make mistakes/i;
  const AI_STUDIO_UI_LINE_RE = /^(?:download|content_copy|expand_less|expand_more|copy code|copy)$/i;

  console.log("[PCE] Google AI Studio content script loaded");

  function uniqueNonEmpty(list) {
    const seen = new Set();
    const out = [];
    for (const item of list) {
      const text = normalizeText(item);
      if (!text) continue;
      if (seen.has(text)) continue;
      seen.add(text);
      out.push(text);
    }
    return out;
  }

  function normalizeText(text) {
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

    const deduped = [];
    for (const line of lines) {
      if (deduped[deduped.length - 1] === line) continue;
      deduped.push(line);
    }

    return deduped.join("\n").trim();
  }

  function normalizeCitationUrl(rawUrl) {
    if (!rawUrl) return "";
    try {
      const parsed = new URL(rawUrl, location.href);
      if (/(^|\.)google\.com$/i.test(parsed.hostname) && parsed.pathname === "/url") {
        return parsed.searchParams.get("q") || parsed.searchParams.get("url") || parsed.href;
      }
      return parsed.href;
    } catch {
      return rawUrl;
    }
  }

  function dedupeAttachments(list) {
    const seen = new Set();
    const out = [];
    for (const att of list || []) {
      if (!att || typeof att !== "object") continue;
      const key = [
        att.type || "",
        att.url || "",
        att.name || "",
        att.title || "",
        att.code || "",
      ].join("|") || JSON.stringify(att);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(att);
    }
    return out;
  }

  function imageMediaType(src, name) {
    const dataMatch = String(src || "").match(/^data:([^;,]+)[;,]/i);
    if (dataMatch) return dataMatch[1];
    const lower = String(name || src || "").toLowerCase();
    if (lower.includes(".png")) return "image/png";
    if (lower.includes(".jpg") || lower.includes(".jpeg")) return "image/jpeg";
    if (lower.includes(".gif")) return "image/gif";
    if (lower.includes(".webp")) return "image/webp";
    return "";
  }

  function attachmentOnlyText(attachments) {
    const labels = [];
    for (const att of attachments || []) {
      if (att?.type === "image_url" || att?.type === "image_generation") {
        labels.push(`[Image attachment: ${att.alt || att.name || att.title || "image"}]`);
      } else if (att?.type === "file") {
        labels.push(`[File attachment: ${att.name || att.title || "file"}]`);
      }
    }
    return labels.join("\n").trim();
  }

  function extractLocalAttachments(el) {
    if (!el) return [];
    const attachments = [];

    el.querySelectorAll("ms-image-chunk img, .image-container img.loaded-image, .image-container img[alt]").forEach((img) => {
      const src = img.src || img.getAttribute("src") || "";
      if (!src || src.startsWith("data:image/svg")) return;
      const alt = (img.alt || img.getAttribute("alt") || "").trim();
      const mediaType = imageMediaType(src, alt);
      const att = {
        type: "image_url",
        url: src.startsWith("data:") ? `${src.slice(0, 200)}...[truncated]` : src.slice(0, 2000),
        source_type: "google-ai-studio-image",
      };
      if (alt) {
        att.alt = alt.slice(0, 200);
        att.name = alt.slice(0, 200);
      }
      if (mediaType) att.media_type = mediaType;
      attachments.push(att);
    });

    el.querySelectorAll("ms-file-chunk, [class*='file-chunk']").forEach((fileEl) => {
      const nameEl = fileEl.querySelector(".name, [title]");
      const name =
        (nameEl?.getAttribute?.("title") || nameEl?.innerText || fileEl.innerText || "")
          .split("\n")[0]
          .trim();
      if (!name || name.length < 3) return;
      attachments.push({
        type: "file",
        name: name.slice(0, 200),
        source_type: "google-ai-studio-file",
      });
    });

    el.querySelectorAll("pre").forEach((pre) => {
      const code = (pre.innerText || "").trim();
      if (!code || code.length < 3) return;
      attachments.push({
        type: "code_block",
        language: "",
        code: code.slice(0, 8000),
      });
    });

    el.querySelectorAll("a[href]").forEach((link) => {
      const url = normalizeCitationUrl(link.href || "");
      const title = (link.innerText || link.textContent || "").trim();
      if (!url || !title) return;
      try {
        if (new URL(url).hostname === location.hostname) return;
      } catch {
        return;
      }
      attachments.push({
        type: "citation",
        url: url.slice(0, 500),
        title: title.slice(0, 200),
      });
    });

    return dedupeAttachments(attachments);
  }

  function cleanContainerText(el, { stripLinks = false, stripCode = false } = {}) {
    if (!el) return "";
    const clone = el.cloneNode(true);
    clone.querySelectorAll(
      "button, mat-icon, .mat-icon, [role='button'], [aria-hidden='true'], " +
      ".mat-mdc-tooltip-trigger, .chat-turn-actions, .feedback-buttons, .turn-header, " +
      ".actions-container, .author-label, .timestamp, .turn-footer, ms-chat-turn-options"
    ).forEach((node) => node.remove());
    if (stripCode) {
      clone.querySelectorAll(
        "pre, code, [class*='code-block'], [class*='codeblock'], [class*='code-header'], " +
        "ms-code-block, ms-md-code-block"
      ).forEach((node) => node.remove());
    }
    if (stripLinks) {
      clone.querySelectorAll("a[href]").forEach((node) => node.remove());
    }
    return normalizeText(clone.innerText || "");
  }

  function isStreaming() {
    if ((_pce().isStreaming || (() => false))()) return true;

    for (const btn of document.querySelectorAll("button")) {
      const label = `${btn.innerText || ""} ${btn.getAttribute("aria-label") || ""}`.trim();
      if (/stop|cancel generation/i.test(label)) return true;
    }

    return false;
  }

  function getContainer() {
    return document.querySelector(".chat-view-container, ms-chat-session, .chat-session-content, main") || document.body;
  }

  function getSessionHint() {
    const match = location.pathname.match(/\/prompts\/([a-zA-Z0-9_-]+)/);
    if (match) return match[1];
    return (_pce().getSessionHint || (() => location.pathname))();
  }

  function extractModelName() {
    const selectors = [
      "ms-model-selector .mat-mdc-select-value-text",
      "ms-model-selector .model-name",
      "[data-testid*='model']",
      "[class*='model-selector'] [class*='selected']",
      "[class*='model'] [class*='name']",
    ];

    for (const sel of selectors) {
      try {
        const el = document.querySelector(sel);
        const text = normalizeText(el?.innerText || "");
        if (text && text.length < 120) return text;
      } catch {}
    }

    const bodyText = document.body?.innerText || "";
    const match = bodyText.match(/\b(gemini-[\w.-]+)\b/i);
    return match ? match[1] : null;
  }

  function extractUserText(turn) {
    const userContainer = turn.querySelector(".chat-turn-container.user, .chat-turn-container .user");
    return cleanContainerText(userContainer || turn);
  }

  function extractAssistantText(turn, attachments = []) {
    const modelContainer = turn.querySelector(".chat-turn-container.model, .model");
    if (!modelContainer) return "";

    const hasCode = attachments.some((att) => att?.type === "code_block");
    const hasCitation = attachments.some((att) => att?.type === "citation");
    let reply =
      cleanContainerText(modelContainer, {
        stripCode: hasCode,
        stripLinks: hasCitation,
      }) ||
      extractReplyContent(modelContainer) ||
      cleanContainerText(modelContainer);

    const thinking = normalizeText(extractThinking(modelContainer));
    if (thinking) {
      return `<thinking>\n${thinking}\n</thinking>\n\n${normalizeText(reply)}`.trim();
    }
    return normalizeText(reply);
  }

  function extractMessages() {
    const messages = [];
    const turns = document.querySelectorAll("ms-chat-turn");
    if (turns.length === 0) return messages;

    turns.forEach((turn) => {
      const turnContainer = turn.querySelector(".chat-turn-container");
      const cls = (turnContainer?.className || turn.className || "").toLowerCase();

      if (cls.includes("user")) {
        const content = extractUserText(turn);
        const att = dedupeAttachments([
          ...extractAttachments(turn),
          ...extractLocalAttachments(turn),
        ]).map((attachment) => {
          if (attachment?.type === "citation" && attachment.url) {
            return { ...attachment, url: normalizeCitationUrl(attachment.url) };
          }
          return attachment;
        });
        const attachmentText = attachmentOnlyText(att);
        if (content || attachmentText || att.length > 0) {
          const msg = { role: "user", content: content || attachmentText || "[Attachment]" };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
        return;
      }

      if (cls.includes("model")) {
        const att = dedupeAttachments([
          ...extractAttachments(turn),
          ...extractLocalAttachments(turn),
        ]).map((attachment) => {
          if (attachment?.type === "citation" && attachment.url) {
            return { ...attachment, url: normalizeCitationUrl(attachment.url) };
          }
          return attachment;
        });
        const content = extractAssistantText(turn, att);
        if (content) {
          const msg = { role: "assistant", content };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
        return;
      }

      const userContent = extractUserText(turn);
      const userAtt = dedupeAttachments([
        ...extractAttachments(turn),
        ...extractLocalAttachments(turn),
      ]);
      const userAttachmentText = attachmentOnlyText(userAtt);
      if (userContent || userAttachmentText || userAtt.length > 0) {
        const msg = { role: "user", content: userContent || userAttachmentText || "[Attachment]" };
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

  function fingerprint(messages) {
    return fingerprintConversation(messages);
  }

  function scheduleCapture(delay = DEBOUNCE_MS) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(captureConversation, delay);
  }

  function captureConversation() {
    if (isStreaming()) {
      scheduleCapture(STREAM_CHECK_MS);
      return;
    }

    const allMessages = extractMessages();
    if (allMessages.length === 0) return;

    const fp = fingerprint(allMessages);
    if (fp === lastFingerprint) return;

    const updateOnly = allMessages.length > 0 && allMessages.slice(sentCount).length === 0;
    const newMessages = updateOnly ? [allMessages[allMessages.length - 1]] : allMessages.slice(sentCount);

    const prevCount = sentCount;
    lastFingerprint = fp;
    sentCount = allMessages.length;

    const payload = {
      provider: PROVIDER,
      source_name: SOURCE_NAME,
      host: location.hostname,
      path: location.pathname,
      model_name: extractModelName(),
      session_hint: getSessionHint(),
      conversation: {
        messages: newMessages,
        total_messages: allMessages.length,
        url: location.href,
        title: document.title,
      },
      meta: {
        new_message_count: newMessages.length,
        total_message_count: allMessages.length,
        capture_mode: updateOnly ? "message_update" : "message_delta",
        updated_message_index: updateOnly ? allMessages.length - 1 : null,
        extraction_strategy: "google-ai-studio-dom",
        behavior: window.__PCE_BEHAVIOR
          ? window.__PCE_BEHAVIOR.getBehaviorSnapshot(true)
          : {},
      },
    };

    chrome.runtime.sendMessage({ type: "PCE_CAPTURE", payload }, (resp) => {
      if (chrome.runtime.lastError || !resp?.ok) {
        sentCount = prevCount;
        lastFingerprint = "";
        return;
      }
      console.log(`[PCE] Google AI Studio: +${newMessages.length} msgs captured`);
    });
  }

  function reconnectObserver() {
    if (activeObserver) {
      activeObserver.disconnect();
      activeObserver = null;
    }

    const container = getContainer();
    if (!container) {
      scheduleCapture();
      return;
    }

    activeObserver = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        scheduleCapture();
      }
    });

    activeObserver.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    scheduleCapture();
  }

  document.addEventListener("pce-manual-capture", () => {
    lastFingerprint = "";
    sentCount = 0;
    captureConversation();
  });

  function start() {
    reconnectObserver();

    setInterval(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        lastFingerprint = "";
        sentCount = 0;
        reconnectObserver();
      }
    }, POLL_INTERVAL_MS);
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    start();
  } else {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  }
})();
