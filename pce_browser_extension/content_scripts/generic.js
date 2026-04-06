/**
 * PCE Content Script – Generic AI Chat Sites
 *
 * A best-effort content script for AI chat sites that don't have a
 * dedicated extractor. Uses heuristics to detect conversation patterns.
 *
 * Covers: Gemini, DeepSeek, Perplexity, Grok, Poe, and other chat UIs.
 */

(function () {
  "use strict";

  const DEBOUNCE_MS = 3000;
  const POLL_INTERVAL_MS = 5000;

  let lastCapturedText = "";
  let debounceTimer = null;
  let observerActive = false;

  // Map hostname → provider
  const HOST_PROVIDER_MAP = {
    "gemini.google.com": "google",
    "chat.deepseek.com": "deepseek",
    "www.perplexity.ai": "perplexity",
    "grok.com": "xai",
    "poe.com": "poe",
  };

  const hostname = window.location.hostname;
  const provider = HOST_PROVIDER_MAP[hostname] || hostname.split(".")[0];
  const sourceName = `${provider}-web`;

  console.log(`[PCE] Generic content script loaded for ${hostname} (provider: ${provider})`);

  // ---------------------------------------------------------------------------
  // Heuristic message extraction
  // ---------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // Strategy: Look for common conversation patterns in the DOM
    // Many chat UIs use alternating containers for user/assistant

    // Common selectors across AI chat UIs
    const selectors = [
      // Gemini
      'message-content[class*="user"], message-content[class*="model"]',
      '[data-message-author], [data-author-role]',
      // DeepSeek
      '[class*="chat-message"], [class*="ChatMessage"]',
      // Perplexity
      '[class*="prose"], [class*="answer"]',
      // Generic patterns
      '[role="presentation"] [class*="message"]',
      '[class*="user-message"], [class*="bot-message"]',
      '[class*="human"], [class*="assistant"]',
      '[class*="query"], [class*="response"]',
    ];

    for (const selector of selectors) {
      try {
        const elements = document.querySelectorAll(selector);
        if (elements.length === 0) continue;

        elements.forEach((el) => {
          const text = el.innerText.trim();
          if (!text || text.length < 3) return;

          const role = detectRole(el);
          messages.push({ role, content: text });
        });

        if (messages.length > 0) return messages;
      } catch (e) {
        // Selector might be invalid for this site, skip
      }
    }

    // Last resort: look for any message-like containers
    const containers = document.querySelectorAll(
      '[class*="turn"], [class*="message-pair"], [class*="conversation-item"]'
    );
    containers.forEach((el) => {
      const text = el.innerText.trim();
      if (text && text.length > 10) {
        messages.push({ role: detectRole(el), content: text });
      }
    });

    return messages;
  }

  function detectRole(el) {
    // Check attributes and class names for role hints
    const attrs = [
      el.getAttribute("data-message-author"),
      el.getAttribute("data-author-role"),
      el.getAttribute("data-role"),
      el.className,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    if (
      attrs.includes("user") ||
      attrs.includes("human") ||
      attrs.includes("query")
    ) {
      return "user";
    }
    if (
      attrs.includes("assistant") ||
      attrs.includes("bot") ||
      attrs.includes("model") ||
      attrs.includes("ai") ||
      attrs.includes("response") ||
      attrs.includes("answer")
    ) {
      return "assistant";
    }
    return "unknown";
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
      provider,
      source_name: sourceName,
      host: hostname,
      path: window.location.pathname,
      model_name: null,
      session_hint: null,
      conversation: {
        messages,
        url: window.location.href,
        title: document.title,
      },
      meta: {
        message_count: messages.length,
        extraction_strategy: "generic-dom",
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
          console.log(`[PCE] Captured ${messages.length} messages from ${hostname}`);
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

    // For generic sites, observe the whole main/body
    const container =
      document.querySelector("main") || document.body;

    const observer = new MutationObserver((mutations) => {
      const hasNewContent = mutations.some(
        (m) => m.addedNodes.length > 0
      );
      if (hasNewContent) {
        debouncedCapture();
      }
    });

    observer.observe(container, {
      childList: true,
      subtree: true,
    });

    observerActive = true;
    console.log(`[PCE] Generic observer started for ${hostname}`);

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
