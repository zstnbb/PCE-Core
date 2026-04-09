/**
 * PCE Content Script - Grok (grok.com)
 *
 * Grok renders each turn in a `#last-reply-container` thread with stable
 * `items-end` / `items-start` alignment for user vs assistant turns. The
 * generic extractor tends to over-capture surrounding UI, so this script reads
 * only the message bubbles themselves.
 */

(function () {
  "use strict";

  if (window.__PCE_GROK_ACTIVE) return;
  window.__PCE_GROK_ACTIVE = true;

  const PROVIDER = "xai";
  const SOURCE_NAME = "grok-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 4000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let activeObserver = null;
  let lastUrl = location.href;

  const _pce = () => window.__PCE_EXTRACT || {};
  const extractAttachments = (el) => (_pce().extractAttachments || (() => []))(el);
  const extractReplyContent = (el) => (_pce().extractReplyContent || ((node) => (node ? node.innerText.trim() : "")))(el);
  const fingerprintConversation = (messages) => (_pce().fingerprintConversation || ((items) => items.map((msg) => `${msg.role}:${msg.content.slice(0, 120)}`).join("|")))(messages);

  console.log("[PCE] Grok content script loaded");

  function normalizeText(text) {
    if (!text) return "";
    return String(text)
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter((line) => {
        if (!line) return false;
        if (/^(?:share|auto|copy|copied)$/i.test(line)) return false;
        if (/^\d+(?:\.\d+)?\s*(?:s|sec|seconds)$/i.test(line)) return false;
        if (/^(?:fast mode|quick mode|deepsearch|ping)$/i.test(line)) return false;
        return true;
      })
      .join("\n")
      .trim();
  }

  function getSessionHint() {
    const match = location.pathname.match(/\/c\/([a-f0-9-]+)/i);
    return match ? match[1] : location.pathname;
  }

  function getConversationContainer() {
    return document.querySelector("#last-reply-container") || document.querySelector("main");
  }

  function extractModelName() {
    const bodyText = document.body?.innerText || "";
    const match = bodyText.match(/\b(grok-[\w.-]+)\b/i);
    return match ? match[1] : null;
  }

  function extractMessages() {
    const container = getConversationContainer();
    if (!container) return [];

    const messages = [];
    const seen = new Set();
    const nodes = container.querySelectorAll("[id^='response-']");

    nodes.forEach((node) => {
      const cls = (node.className || "").toLowerCase();
      let role = null;
      if (cls.includes("items-end")) role = "user";
      else if (cls.includes("items-start")) role = "assistant";
      else return;

      const bubble = node.querySelector(".message-bubble") || node;
      const text = role === "assistant"
        ? normalizeText(extractReplyContent(bubble) || bubble.innerText || "")
        : normalizeText(bubble.innerText || "");
      if (!text || text.length < 2) return;

      const key = `${role}:${text}`;
      if (seen.has(key)) return;
      seen.add(key);

      const att = extractAttachments(bubble);
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

    const hasUser = allMessages.some((msg) => msg.role === "user");
    const hasAssistant = allMessages.some((msg) => msg.role === "assistant");
    if (!hasUser || !hasAssistant) {
      scheduleCapture(1500);
      return;
    }

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
        extraction_strategy: "grok-dom",
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
      console.log(`[PCE] Grok: +${newMessages.length} msgs captured`);
    });
  }

  function reconnectObserver() {
    if (activeObserver) {
      activeObserver.disconnect();
      activeObserver = null;
    }

    const container = getConversationContainer() || document.body;
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
