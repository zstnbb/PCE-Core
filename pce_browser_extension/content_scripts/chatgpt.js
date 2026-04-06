/**
 * PCE Content Script – ChatGPT (chatgpt.com / chat.openai.com)
 *
 * Observes the DOM for new assistant messages and sends them to the
 * background service worker for capture.
 */

(function () {
  "use strict";

  const PROVIDER = "openai";
  const SOURCE_NAME = "chatgpt-web";
  const DEBOUNCE_MS = 2000;
  const POLL_INTERVAL_MS = 3000;

  let lastCapturedText = "";
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] ChatGPT content script loaded");

  // ---------------------------------------------------------------------------
  // DOM selectors – ChatGPT's DOM changes frequently, so we use multiple
  // fallback strategies
  // ---------------------------------------------------------------------------

  function getConversationContainer() {
    // Main chat thread container
    return (
      document.querySelector('[class*="react-scroll-to-bottom"]') ||
      document.querySelector("main .flex.flex-col") ||
      document.querySelector("main")
    );
  }

  function extractMessages() {
    const messages = [];

    // Strategy 1: data-message-author-role attribute
    const msgElements = document.querySelectorAll("[data-message-author-role]");
    if (msgElements.length > 0) {
      msgElements.forEach((el) => {
        const role = el.getAttribute("data-message-author-role");
        const contentEl = el.querySelector(".markdown, .whitespace-pre-wrap, p");
        const text = contentEl ? contentEl.innerText.trim() : el.innerText.trim();
        if (text) {
          messages.push({ role: role || "unknown", content: text });
        }
      });
      return messages;
    }

    // Strategy 2: alternating user/assistant message groups
    const groups = document.querySelectorAll(
      'main [data-testid^="conversation-turn"]'
    );
    if (groups.length > 0) {
      groups.forEach((group, i) => {
        const text = group.innerText.trim();
        if (text) {
          // Even indices tend to be user, odd are assistant (heuristic)
          const role = group.querySelector('[data-message-author-role]')
            ?.getAttribute("data-message-author-role")
            || (i % 2 === 0 ? "user" : "assistant");
          messages.push({ role, content: text });
        }
      });
      return messages;
    }

    // Strategy 3: fallback – look for agent turns
    const agentTurns = document.querySelectorAll(
      '.agent-turn, [class*="message"]'
    );
    agentTurns.forEach((el) => {
      const text = el.innerText.trim();
      if (text && text.length > 5) {
        const isUser = el.closest('[data-message-author-role="user"]') !== null;
        messages.push({ role: isUser ? "user" : "assistant", content: text });
      }
    });

    return messages;
  }

  function getSessionHint() {
    // Try to extract conversation ID from URL
    const match = window.location.pathname.match(/\/c\/([a-f0-9-]+)/);
    return match ? match[1] : null;
  }

  function getModelName() {
    // Try to find model selector/indicator in DOM
    const modelEl = document.querySelector(
      '[class*="model"], [data-testid*="model"]'
    );
    return modelEl ? modelEl.innerText.trim() : null;
  }

  // ---------------------------------------------------------------------------
  // Capture logic
  // ---------------------------------------------------------------------------

  function captureConversation() {
    const messages = extractMessages();
    if (messages.length === 0) return;

    // Create a text fingerprint to avoid duplicate captures
    const fingerprint = messages.map((m) => `${m.role}:${m.content.slice(0, 50)}`).join("|");
    if (fingerprint === lastCapturedText) return;
    lastCapturedText = fingerprint;

    const payload = {
      provider: PROVIDER,
      source_name: SOURCE_NAME,
      host: window.location.hostname,
      path: window.location.pathname,
      model_name: getModelName(),
      session_hint: getSessionHint(),
      conversation: {
        messages,
        url: window.location.href,
        title: document.title,
      },
      meta: {
        message_count: messages.length,
        extraction_strategy: "dom",
      },
    };

    chrome.runtime.sendMessage(
      { type: "PCE_CAPTURE", payload },
      (response) => {
        if (chrome.runtime.lastError) {
          console.debug("[PCE] Send failed:", chrome.runtime.lastError.message);
          return;
        }
        if (response?.ok) {
          console.log(`[PCE] Captured ${messages.length} messages from ChatGPT`);
        }
      }
    );
  }

  function debouncedCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(captureConversation, DEBOUNCE_MS);
  }

  // ---------------------------------------------------------------------------
  // MutationObserver – watch for new messages
  // ---------------------------------------------------------------------------

  function startObserver() {
    if (observerActive) return;

    const container = getConversationContainer();
    if (!container) {
      // Retry after a short delay (page might still be loading)
      setTimeout(startObserver, 2000);
      return;
    }

    const observer = new MutationObserver((mutations) => {
      // Only trigger on meaningful changes (new child nodes)
      const hasNewContent = mutations.some(
        (m) => m.addedNodes.length > 0 || m.type === "characterData"
      );
      if (hasNewContent) {
        debouncedCapture();
      }
    });

    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    observerActive = true;
    console.log("[PCE] ChatGPT observer started");

    // Also capture on URL change (navigation between conversations)
    let lastUrl = window.location.href;
    setInterval(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        lastCapturedText = ""; // reset fingerprint
        debouncedCapture();
      }
    }, POLL_INTERVAL_MS);
  }

  // Start when DOM is ready
  if (document.readyState === "complete" || document.readyState === "interactive") {
    startObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startObserver);
  }
})();
