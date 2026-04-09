/**
 * PCE Content Script - Poe (poe.com)
 *
 * Poe's current chat UI renders each turn in a `ChatMessage_chatMessage`
 * container. User turns use right-side bubble classes and assistant turns use
 * left-side bubble/header classes.
 */

(function () {
  "use strict";

  if (window.__PCE_POE_ACTIVE) return;
  window.__PCE_POE_ACTIVE = true;

  const PROVIDER = "poe";
  const SOURCE_NAME = "poe-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 4000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  const _pce = () => window.__PCE_EXTRACT || {};
  const extractAttachments = (el) => (_pce().extractAttachments || (() => []))(el);
  const fingerprintConversation = (messages) => (_pce().fingerprintConversation || ((items) => items.map((msg) => `${msg.role}:${msg.content.slice(0, 120)}`).join("|")))(messages);

  console.log("[PCE] Poe content script loaded");

  function normalizeText(text) {
    if (!text) return "";
    return String(text)
      .split(/\n+/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter((line) => {
        if (!line) return false;
        if (/^\d{1,2}:\d{2}$/.test(line)) return false;
        if (/^(?:compare|share|copy|copied)$/i.test(line)) return false;
        return true;
      })
      .join("\n")
      .trim();
  }

  function getMessageNodes() {
    return document.querySelectorAll("[id^='message-'].ChatMessage_chatMessage__xkgHx, [id^='message-'][class*='ChatMessage_chatMessage']");
  }

  function detectRole(node) {
    const cls = (node.className || "").toLowerCase();
    if (
      cls.includes("rightsidemessagewrapper") ||
      cls.includes("rightsidemessagebubble") ||
      node.querySelector("[class*='rightSideMessageWrapper'], [class*='rightSideMessageBubble']")
    ) {
      return "user";
    }
    if (
      cls.includes("leftsidemessagebubble") ||
      cls.includes("leftsidemessageheader") ||
      node.querySelector("[class*='leftSideMessageBubble'], [class*='LeftSideMessageHeader'], [class*='BotMessageHeader']")
    ) {
      return "assistant";
    }
    return "unknown";
  }

  function extractText(node) {
    const textNode =
      node.querySelector("[class*='Message_messageTextContainer']") ||
      node.querySelector("[class*='Markdown_markdownContainer']") ||
      node.querySelector("[class*='Message_selectableText']") ||
      node;
    return normalizeText(textNode.innerText || "");
  }

  function extractMessages() {
    const messages = [];
    const seen = new Set();

    getMessageNodes().forEach((node) => {
      const role = detectRole(node);
      if (role === "unknown") return;

      const text = extractText(node);
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

  function extractModelName() {
    const botName =
      document.querySelector("[class*='BotHeader_textContainer'] p") ||
      document.querySelector("[class*='BotInfoCardHeader_botImageAndNameContainer'] p");
    if (botName) return normalizeText(botName.textContent || "");
    return null;
  }

  function getSessionHint() {
    const parts = location.pathname.split("/").filter(Boolean);
    if (parts[0] === "chat" && parts.length >= 2) {
      return parts[parts.length - 1];
    }
    return location.pathname;
  }

  function fingerprint(messages) {
    return fingerprintConversation(messages);
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
        extraction_strategy: "poe-dom",
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
      console.log(`[PCE] Poe: +${newMessages.length} msgs captured`);
    });
  }

  function scheduleCapture(delay = DEBOUNCE_MS) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(captureConversation, delay);
  }

  function startObserver() {
    if (observerActive) return;

    const container =
      document.querySelector("[class*='ChatMessagesScrollWrapper_scrollableContainerWrapper']") ||
      document.querySelector("[class*='ChatMessagesView_infiniteScroll']") ||
      document.querySelector("main") ||
      document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        scheduleCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] Poe observer started");

    let lastUrl = location.href;
    setInterval(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        lastFingerprint = "";
        sentCount = 0;
        scheduleCapture();
      }
    }, POLL_INTERVAL_MS);

    scheduleCapture();
  }

  document.addEventListener("pce-manual-capture", () => {
    lastFingerprint = "";
    sentCount = 0;
    captureConversation();
  });

  if (document.readyState === "complete" || document.readyState === "interactive") {
    startObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startObserver, { once: true });
  }
})();
