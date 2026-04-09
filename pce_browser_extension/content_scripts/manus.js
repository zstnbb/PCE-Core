/**
 * PCE Content Script - Manus (manus.im)
 *
 * Manus renders the active task conversation inside #manus-chat-box.
 * User prompts live in right-aligned bubbles and assistant replies render in
 * .manus-markdown blocks, which are stable enough for a dedicated extractor.
 */

(function () {
  "use strict";

  if (window.__PCE_MANUS_ACTIVE) return;
  window.__PCE_MANUS_ACTIVE = true;

  const PROVIDER = "manus";
  const SOURCE_NAME = "manus-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 4000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let activeObserver = null;
  let lastUrl = location.href;

  const _pce = () => window.__PCE_EXTRACT || {};
  const extractAttachments = (el) => (_pce().extractAttachments || (() => []))(el);
  const extractReplyContent = (el) => (_pce().extractReplyContent || ((node) => node ? node.innerText.trim() : ""))(el);
  const fingerprintConversation = (messages) => (_pce().fingerprintConversation || ((items) => items.map((msg) => `${msg.role}:${msg.content.slice(0, 120)}`).join("|")))(messages);

  console.log("[PCE] Manus content script loaded");

  function normalizeText(text) {
    if (!text) return "";
    return String(text)
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter((line) => {
        if (!line) return false;
        if (/^(?:Lite|分享|升级|任务已完成|推荐追问)$/i.test(line)) return false;
        return true;
      })
      .join("\n")
      .trim();
  }

  function getChatBox() {
    return document.querySelector("#manus-chat-box") || document.querySelector("#manus-home-page-session-content");
  }

  function getSessionHint() {
    const match = location.pathname.match(/\/app\/([a-zA-Z0-9_-]+)/);
    return match ? match[1] : location.pathname;
  }

  function extractModelName() {
    const selectors = [
      "#manus-chat-box [aria-haspopup='dialog'] span.truncate",
      "#manus-chat-box span.truncate",
    ];
    for (const sel of selectors) {
      try {
        const el = document.querySelector(sel);
        const text = normalizeText(el?.innerText || "");
        if (text && text.length < 120) return text;
      } catch {}
    }
    return "Manus";
  }

  function dedupeAttachments(list) {
    const seen = new Set();
    const out = [];
    for (const att of list || []) {
      if (!att || typeof att !== "object") continue;
      const key = JSON.stringify(att);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(att);
    }
    return out;
  }

  function extractLocalAttachments(el) {
    if (!el) return [];
    const attachments = [];

    el.querySelectorAll("pre").forEach((pre) => {
      const code = (pre.innerText || "").trim();
      if (!code || code.length < 3) return;
      const langLabel =
        pre.closest(".manus-markdown")?.querySelector("span.text-sm")?.innerText?.trim() || "";
      attachments.push({
        type: "code_block",
        language: langLabel,
        code: code.slice(0, 8000),
      });
    });

    el.querySelectorAll("a[href]").forEach((link) => {
      const href = link.href || "";
      const title = (link.innerText || link.textContent || "").trim();
      if (!href || !title) return;
      try {
        if (new URL(href).hostname === location.hostname) return;
      } catch {
        return;
      }
      attachments.push({
        type: "citation",
        url: href.slice(0, 500),
        title: title.slice(0, 200),
      });
    });

    return dedupeAttachments(attachments);
  }

  function extractMessages() {
    const chatBox = getChatBox();
    if (!chatBox) return [];

    const messages = [];
    const seen = new Set();
    const nodes = chatBox.querySelectorAll(
      ".items-end .u-break-words, .items-end .whitespace-pre-wrap, .manus-markdown"
    );

    nodes.forEach((node) => {
      const role = node.closest(".manus-markdown") ? "assistant" : "user";
      const text = role === "assistant"
        ? normalizeText(extractReplyContent(node) || node.innerText || "")
        : normalizeText(node.innerText || "");

      if (!text || text.length < 2) return;

      const key = `${role}:${text}`;
      if (seen.has(key)) return;
      seen.add(key);

      const scope = role === "assistant" ? node.closest(".manus-markdown") || node : node.closest(".items-end") || node;
      const att = dedupeAttachments([
        ...extractAttachments(scope),
        ...extractLocalAttachments(scope),
      ]);
      const msg = { role, content: text };
      if (att.length > 0) msg.attachments = att;
      messages.push(msg);
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
        extraction_strategy: "manus-dom",
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
      console.log(`[PCE] Manus: +${newMessages.length} msgs captured`);
    });
  }

  function reconnectObserver() {
    if (activeObserver) {
      activeObserver.disconnect();
      activeObserver = null;
    }

    const container = getChatBox();
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
