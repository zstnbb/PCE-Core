/**
 * PCE Content Script - Z.ai / Zhipu (chat.z.ai)
 *
 * Z.ai renders user and assistant turns with stable `chat-user` /
 * `chat-assistant` containers, so a dedicated extractor is more reliable than
 * the universal heuristics.
 */

(function () {
  "use strict";

  if (window.__PCE_ZHIPU_ACTIVE) return;
  window.__PCE_ZHIPU_ACTIVE = true;

  const PROVIDER = "zhipu";
  const SOURCE_NAME = "zhipu-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 4000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let activeObserver = null;
  let lastUrl = location.href;

  const _pce = () => window.__PCE_EXTRACT || {};
  const extractAttachments = (el) => (_pce().extractAttachments || (() => []))(el);
  const fingerprintConversation = (messages) => (_pce().fingerprintConversation || ((items) => items.map((msg) => `${msg.role}:${msg.content.slice(0, 120)}`).join("|")))(messages);

  console.log("[PCE] Zhipu content script loaded");

  function normalizeText(text) {
    if (!text) return "";
    return String(text)
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter(Boolean)
      .join("\n")
      .trim();
  }

  function getSessionHint() {
    const match = location.pathname.match(/\/c\/([a-f0-9-]+)/i);
    return match ? match[1] : location.pathname;
  }

  function extractModelName() {
    const bodyText = document.body?.innerText || "";
    const match = bodyText.match(/\b(GLM-[\w.-]+)\b/i);
    return match ? match[1] : null;
  }

  function extractMessages() {
    const messages = [];
    const seen = new Set();
    const nodes = document.querySelectorAll(
      ".chat-user.markdown-prose, .chat-assistant.markdown-prose, .chat-assistant, .user-message, [class*='message-']"
    );

    nodes.forEach((node) => {
      const cls = (node.className || "").toLowerCase();
      let role = null;
      if (cls.includes("chat-user") || cls.includes("user-message")) role = "user";
      else if (cls.includes("chat-assistant")) role = "assistant";
      else return;

      const text = normalizeText(node.innerText || "");
      if (!text || text.length < 2) return;

      const key = `${role}:${text}`;
      if (seen.has(key)) return;
      seen.add(key);

      const att = extractAttachments(node);
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
    if (
      allMessages.some((msg) => msg.role === "user") &&
      !allMessages.some((msg) => msg.role === "assistant")
    ) {
      scheduleCapture(1500);
      return;
    }

    const fp = fingerprint(allMessages);
    if (fp === lastFingerprint) return;

    const newMessages = allMessages.slice(sentCount);
    if (newMessages.length === 0) {
      lastFingerprint = fp;
      return;
    }

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
        extraction_strategy: "zhipu-dom",
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
      console.log(`[PCE] Zhipu: +${newMessages.length} msgs captured`);
    });
  }

  function reconnectObserver() {
    if (activeObserver) {
      activeObserver.disconnect();
      activeObserver = null;
    }

    const container = document.querySelector("main") || document.body;
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
