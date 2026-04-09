/**
 * PCE Content Script – Microsoft Copilot (copilot.microsoft.com)
 *
 * Site-specific extractor with incremental delta capture.
 * Copilot uses a React-based chat UI with CIB (Conversational AI in Bing) components.
 */

(function () {
  "use strict";

  const PROVIDER = "microsoft";
  const SOURCE_NAME = "copilot-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] Copilot content script loaded");

  // -------------------------------------------------------------------------
  // Message extraction
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // Copilot uses cib-message-group / cib-message or similar web components
    // Also uses [data-content] attributes and .ac-textBlock for card-based responses
    const turnSelectors = [
      // Modern Copilot layout
      '[class*="user-message"], [class*="bot-message"]',
      '[class*="UserMessage"], [class*="BotMessage"]',
      // CIB-based layout
      'cib-message-group[source="user"], cib-message-group[source="bot"]',
      // Thread-based layout
      '[data-testid*="message"]',
      '[class*="thread-message"]',
      // Copilot chat turns
      '[class*="turn-"]',
    ];

    for (const sel of turnSelectors) {
      try {
        const els = document.querySelectorAll(sel);
        if (els.length < 1) continue;

        els.forEach((el) => {
          const role = _detectRole(el);
          const text = _extractText(el);
          if (text && text.length > 1) {
            messages.push({ role, content: text });
          }
        });

        if (messages.length >= 2) return messages;
        messages.length = 0;
      } catch {}
    }

    return messages;
  }

  function _detectRole(el) {
    const tag = el.tagName?.toLowerCase() || "";
    const cls = (el.className || "").toLowerCase();
    const source = el.getAttribute("source") || "";
    const testId = el.getAttribute("data-testid") || "";

    if (source === "user" || cls.includes("user") || testId.includes("user")) {
      return "user";
    }
    if (source === "bot" || cls.includes("bot") || cls.includes("copilot") || testId.includes("bot")) {
      return "assistant";
    }
    return "unknown";
  }

  function _extractText(el) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only, button, [class*='action'], [class*='feedback'], [class*='citation']").forEach((e) => e.remove());

    // Copilot renders responses in .ac-textBlock or markdown containers
    const rendered = clone.querySelector(".ac-textBlock, .markdown, [class*='rendered'], [class*='text-content']");
    if (rendered) return rendered.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Model + session
  // -------------------------------------------------------------------------

  function extractModelName() {
    // Copilot sometimes shows GPT-4 / GPT-4o badge
    const badge = document.querySelector('[class*="model"], [class*="badge"][class*="gpt"], [data-model]');
    if (badge) return badge.textContent.trim() || badge.getAttribute("data-model") || null;
    return null;
  }

  function getSessionHint() {
    const match = location.pathname.match(/\/(?:chat|c|thread)\/([a-zA-Z0-9_-]+)/);
    return match ? match[1] : location.pathname;
  }

  // -------------------------------------------------------------------------
  // Capture
  // -------------------------------------------------------------------------

  function fingerprint(msgs) {
    return msgs.map((m) => `${m.role}:${m.content.slice(0, 80)}`).join("|");
  }

  function captureConversation() {
    const allMsgs = extractMessages();
    if (allMsgs.length === 0) return;

    const fp = fingerprint(allMsgs);
    if (fp === lastFingerprint) return;
    lastFingerprint = fp;

    const updateOnly = allMsgs.length > 0 && allMsgs.slice(sentCount).length === 0;
    const newMsgs = updateOnly ? [allMsgs[allMsgs.length - 1]] : allMsgs.slice(sentCount);

    const prevCount = sentCount;
    sentCount = allMsgs.length;

    const payload = {
      provider: PROVIDER,
      source_name: SOURCE_NAME,
      host: location.hostname,
      path: location.pathname,
      model_name: extractModelName(),
      session_hint: getSessionHint(),
      conversation: {
        messages: newMsgs,
        total_messages: allMsgs.length,
        url: location.href,
        title: document.title,
      },
      meta: {
        new_message_count: newMsgs.length,
        total_message_count: allMsgs.length,
        capture_mode: updateOnly ? "message_update" : "message_delta",
        updated_message_index: updateOnly ? allMsgs.length - 1 : null,
        extraction_strategy: "copilot-dom",
        behavior: window.__PCE_BEHAVIOR
          ? window.__PCE_BEHAVIOR.getBehaviorSnapshot(true)
          : {},
      },
    };

    chrome.runtime.sendMessage({ type: "PCE_CAPTURE", payload }, (resp) => {
      if (chrome.runtime.lastError) {
        sentCount = prevCount;
        lastFingerprint = "";
        return;
      }
      if (resp?.ok) {
        console.log(`[PCE] Copilot: +${newMsgs.length} msgs captured`);
      } else {
        sentCount = prevCount;
        lastFingerprint = "";
      }
    });
  }

  function debouncedCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(captureConversation, DEBOUNCE_MS);
  }

  // -------------------------------------------------------------------------
  // Observer
  // -------------------------------------------------------------------------

  function startObserver() {
    if (observerActive) return;

    const container = document.querySelector("main, [class*='chat-container'], #app") || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] Copilot observer started");

    let lastUrl = location.href;
    setInterval(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        lastFingerprint = "";
        sentCount = 0;
        debouncedCapture();
      }
    }, POLL_INTERVAL_MS);

    debouncedCapture();
  }

  document.addEventListener("pce-manual-capture", () => {
    lastFingerprint = "";
    sentCount = 0;
    captureConversation();
  });

  if (document.readyState === "complete" || document.readyState === "interactive") {
    startObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startObserver);
  }
})();
