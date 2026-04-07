/**
 * PCE Content Script – Claude (claude.ai)
 *
 * Observes the DOM for conversation messages and sends them to the
 * background service worker for capture.
 */

(function () {
  "use strict";

  const PROVIDER = "anthropic";
  const SOURCE_NAME = "claude-web";
  const DEBOUNCE_MS = 2000;
  const POLL_INTERVAL_MS = 3000;

  let lastCapturedText = "";
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] Claude content script loaded");

  // ---------------------------------------------------------------------------
  // DOM selectors
  // ---------------------------------------------------------------------------

  function getConversationContainer() {
    return (
      document.querySelector('[class*="conversation-content"]') ||
      document.querySelector('[class*="chat-messages"]') ||
      document.querySelector("main .flex.flex-col") ||
      document.querySelector("main")
    );
  }

  function extractMessages() {
    const messages = [];

    // Strategy 1: Human/Assistant turn containers
    const humanTurns = document.querySelectorAll(
      '[data-testid="human-turn"], [class*="human-turn"], .font-user-message'
    );
    const assistantTurns = document.querySelectorAll(
      '[data-testid="assistant-turn"], [class*="assistant-turn"], .font-claude-message'
    );

    if (humanTurns.length > 0 || assistantTurns.length > 0) {
      // Collect all turns with position info for ordering
      const allTurns = [];

      humanTurns.forEach((el) => {
        const text = el.innerText.trim();
        if (text) {
          allTurns.push({
            role: "user",
            content: text,
            top: el.getBoundingClientRect().top,
          });
        }
      });

      assistantTurns.forEach((el) => {
        const text = el.innerText.trim();
        if (text) {
          allTurns.push({
            role: "assistant",
            content: text,
            top: el.getBoundingClientRect().top,
          });
        }
      });

      // Sort by vertical position (top of page first)
      allTurns.sort((a, b) => a.top - b.top);
      allTurns.forEach((t) => messages.push({ role: t.role, content: t.content }));
      return messages;
    }

    // Strategy 2: Generic message blocks with role detection
    const blocks = document.querySelectorAll(
      '[class*="message"], [class*="Message"], [class*="turn"]'
    );
    blocks.forEach((el) => {
      const text = el.innerText.trim();
      if (!text || text.length < 3) return;

      // Detect role from class names or attributes
      const classes = el.className.toLowerCase();
      let role = "unknown";
      if (classes.includes("human") || classes.includes("user")) {
        role = "user";
      } else if (classes.includes("assistant") || classes.includes("claude") || classes.includes("ai")) {
        role = "assistant";
      }

      if (role !== "unknown") {
        messages.push({ role, content: text });
      }
    });

    return messages;
  }

  function getSessionHint() {
    // Extract conversation ID from URL: /chat/{uuid}
    const match = window.location.pathname.match(/\/chat\/([a-f0-9-]+)/);
    return match ? match[1] : null;
  }

  // ---------------------------------------------------------------------------
  // Capture logic
  // ---------------------------------------------------------------------------

  function captureConversation() {
    const messages = extractMessages();
    if (messages.length === 0) return;

    const fingerprint = messages
      .map((m) => `${m.role}:${m.content.slice(0, 50)}`)
      .join("|");
    if (fingerprint === lastCapturedText) return;
    lastCapturedText = fingerprint;

    const payload = {
      provider: PROVIDER,
      source_name: SOURCE_NAME,
      host: window.location.hostname,
      path: window.location.pathname,
      model_name: null,
      session_hint: getSessionHint(),
      conversation: {
        messages,
        url: window.location.href,
        title: document.title,
      },
      meta: {
        message_count: messages.length,
        extraction_strategy: "dom",
        behavior: window.__PCE_BEHAVIOR
          ? window.__PCE_BEHAVIOR.getBehaviorSnapshot(true)
          : {},
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
          console.log(`[PCE] Captured ${messages.length} messages from Claude`);
        }
      }
    );
  }

  function debouncedCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(captureConversation, DEBOUNCE_MS);
  }

  // ---------------------------------------------------------------------------
  // MutationObserver
  // ---------------------------------------------------------------------------

  function startObserver() {
    if (observerActive) return;

    const container = getConversationContainer();
    if (!container) {
      setTimeout(startObserver, 2000);
      return;
    }

    const observer = new MutationObserver((mutations) => {
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
    console.log("[PCE] Claude observer started");

    let lastUrl = window.location.href;
    setInterval(() => {
      if (window.location.href !== lastUrl) {
        lastUrl = window.location.href;
        lastCapturedText = "";
        debouncedCapture();
      }
    }, POLL_INTERVAL_MS);
  }

  if (document.readyState === "complete" || document.readyState === "interactive") {
    startObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startObserver);
  }
})();
