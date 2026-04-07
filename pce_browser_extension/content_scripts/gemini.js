/**
 * PCE Content Script – Google Gemini (gemini.google.com)
 *
 * Site-specific extractor with incremental delta capture.
 * Gemini uses web components and shadow DOM in some areas.
 */

(function () {
  "use strict";

  const PROVIDER = "google";
  const SOURCE_NAME = "gemini-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] Gemini content script loaded");

  // -------------------------------------------------------------------------
  // Message extraction
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // Gemini uses turn-based containers
    // User turns: .query-content, [data-turn-role="user"], .user-query
    // Model turns: .model-response-text, [data-turn-role="model"], .response-content

    const turnSelectors = [
      // Modern Gemini layout
      "model-response, user-query",
      // Alternative selectors
      '[class*="query-text"], [class*="response-container"]',
      '[data-turn-role]',
      // Conversation turns
      '.conversation-container .turn-content',
      'message-content',
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

    // Fallback: alternating .turn containers
    const turns = document.querySelectorAll('[class*="turn"], [class*="Turn"]');
    turns.forEach((el) => {
      const role = _detectRole(el);
      const text = _extractText(el);
      if (text && text.length > 2) {
        messages.push({ role, content: text });
      }
    });

    return messages;
  }

  function _detectRole(el) {
    const tag = el.tagName?.toLowerCase() || "";
    const cls = (el.className || "").toLowerCase();
    const role = el.getAttribute("data-turn-role") || "";

    if (tag === "user-query" || role === "user" || cls.includes("user") || cls.includes("query")) {
      return "user";
    }
    if (tag === "model-response" || role === "model" || cls.includes("model") || cls.includes("response") || cls.includes("answer")) {
      return "assistant";
    }
    return "unknown";
  }

  function _extractText(el) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only, .chip-container, .action-button").forEach((e) => e.remove());

    const markdown = clone.querySelector(".markdown, .markdown-main-panel, message-content");
    if (markdown) return markdown.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Model name extraction
  // -------------------------------------------------------------------------

  function extractModelName() {
    // Gemini shows model selector in UI
    const modelEl = document.querySelector(
      '[class*="model-selector"] [class*="selected"], [data-model-name], .model-badge'
    );
    if (modelEl) {
      const text = modelEl.textContent.trim();
      if (text) return text;
    }
    return null;
  }

  // -------------------------------------------------------------------------
  // Session hint
  // -------------------------------------------------------------------------

  function getSessionHint() {
    // Gemini URLs: /app/<conversation_id> or /chat/<id>
    const match = location.pathname.match(/\/(?:app|chat)\/([a-f0-9]+)/i);
    return match ? match[1] : location.pathname;
  }

  // -------------------------------------------------------------------------
  // Fingerprint + capture
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

    const newMsgs = allMsgs.slice(sentCount);
    if (newMsgs.length === 0) return;

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
        extraction_strategy: "gemini-dom",
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
        console.log(`[PCE] Gemini: +${newMsgs.length} msgs captured`);
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

    const container = document.querySelector("main") || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] Gemini observer started");

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
