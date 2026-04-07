/**
 * PCE Content Script – DeepSeek Chat (chat.deepseek.com)
 *
 * Site-specific extractor with incremental delta capture.
 * DeepSeek uses a React-based chat UI with markdown rendering.
 */

(function () {
  "use strict";

  const PROVIDER = "deepseek";
  const SOURCE_NAME = "deepseek-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] DeepSeek content script loaded");

  // -------------------------------------------------------------------------
  // Message extraction
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // DeepSeek uses .ds-chat-message or similar containers
    const turnSelectors = [
      '[class*="chatMessage"], [class*="chat-message"]',
      '[class*="ChatMessage"]',
      '[data-role]',
      '.msg-item',
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

    // Fallback: look for alternating user/assistant blocks
    const userBlocks = document.querySelectorAll('[class*="user"], [class*="User"]');
    const assistantBlocks = document.querySelectorAll('[class*="assistant"], [class*="Assistant"], [class*="bot"], [class*="Bot"]');

    if (userBlocks.length > 0 && assistantBlocks.length > 0) {
      // Interleave based on DOM order
      const all = [...document.querySelectorAll('[class*="user"], [class*="assistant"], [class*="User"], [class*="Assistant"]')];
      all.forEach((el) => {
        const role = _detectRole(el);
        const text = _extractText(el);
        if (text && text.length > 2 && role !== "unknown") {
          messages.push({ role, content: text });
        }
      });
    }

    return messages;
  }

  function _detectRole(el) {
    const cls = (el.className || "").toLowerCase();
    const role = (el.getAttribute("data-role") || "").toLowerCase();

    if (role === "user" || cls.includes("user") || cls.includes("human") || cls.includes("query")) {
      return "user";
    }
    if (role === "assistant" || cls.includes("assistant") || cls.includes("bot") || cls.includes("deepseek") || cls.includes("response")) {
      return "assistant";
    }
    return "unknown";
  }

  function _extractText(el) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only, button, [class*='action'], [class*='copy']").forEach((e) => e.remove());

    const markdown = clone.querySelector(".markdown, .markdown-body, [class*='markdown'], [class*='Markdown']");
    if (markdown) return markdown.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Model + session
  // -------------------------------------------------------------------------

  function extractModelName() {
    const el = document.querySelector('[class*="model-select"] [class*="name"], [class*="modelName"], [class*="model-tag"]');
    if (el) return el.textContent.trim() || null;

    // DeepSeek may show model in title
    const title = document.title;
    if (title.includes("DeepSeek-V")) return title.match(/DeepSeek-V\S+/)?.[0] || null;
    return null;
  }

  function getSessionHint() {
    const match = location.pathname.match(/\/chat\/([a-zA-Z0-9_-]+)/);
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
        extraction_strategy: "deepseek-dom",
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
        console.log(`[PCE] DeepSeek: +${newMsgs.length} msgs captured`);
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

    const container = document.querySelector("main, [class*='chat-container']") || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] DeepSeek observer started");

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
