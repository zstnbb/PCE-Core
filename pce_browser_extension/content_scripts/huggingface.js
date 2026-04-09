/**
 * PCE Content Script – HuggingFace Chat (huggingface.co/chat)
 *
 * Site-specific extractor with incremental delta capture.
 * HuggingFace Chat hosts open-source models in a clean chat UI.
 */

(function () {
  "use strict";

  const PROVIDER = "huggingface";
  const SOURCE_NAME = "huggingface-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] HuggingFace Chat content script loaded");

  // -------------------------------------------------------------------------
  // Message extraction
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // HuggingFace Chat uses [data-message-role] or class-based role markers
    const turnSelectors = [
      '[class*="message"][class*="user"], [class*="message"][class*="assistant"]',
      '[data-message-role]',
      '[class*="ConversationMessage"]',
      '[class*="chat-message"]',
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

    // Fallback: look for alternating prose blocks inside chat container
    const container = document.querySelector('[class*="chat-container"], [class*="messages"], main');
    if (container) {
      const children = Array.from(container.children).filter((el) => {
        return el.innerText?.trim().length > 2 && el.offsetHeight > 0;
      });

      children.forEach((el) => {
        const role = _detectRole(el);
        const text = _extractText(el);
        if (text && text.length > 2) {
          messages.push({ role: role !== "unknown" ? role : (messages.length % 2 === 0 ? "user" : "assistant"), content: text });
        }
      });
    }

    return messages;
  }

  function _detectRole(el) {
    const cls = (el.className || "").toLowerCase();
    const role = (el.getAttribute("data-message-role") || "").toLowerCase();

    if (role === "user" || cls.includes("user") || cls.includes("human")) {
      return "user";
    }
    if (role === "assistant" || cls.includes("assistant") || cls.includes("bot") || cls.includes("model")) {
      return "assistant";
    }
    return "unknown";
  }

  function _extractText(el) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only, button, [class*='action'], [class*='copy'], [class*='feedback']").forEach((e) => e.remove());

    const markdown = clone.querySelector(".prose, .markdown, [class*='markdown'], [class*='rendered']");
    if (markdown) return markdown.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Model + session
  // -------------------------------------------------------------------------

  function extractModelName() {
    // HuggingFace Chat shows model name in header or URL
    const header = document.querySelector('[class*="model-name"], [class*="ModelName"], [class*="model-selector"] [class*="selected"]');
    if (header) return header.textContent.trim() || null;

    // From URL: /chat/models/<model-name>
    const match = location.pathname.match(/\/chat\/models\/([^/?]+)/);
    if (match) return decodeURIComponent(match[1]);

    // From page title
    const titleMatch = document.title.match(/^(.+?)\s*[-|]/);
    if (titleMatch && titleMatch[1].includes("/")) return titleMatch[1].trim();

    return null;
  }

  function getSessionHint() {
    // HuggingFace Chat URLs: /chat/conversation/<id>
    const match = location.pathname.match(/\/chat\/conversation\/([a-f0-9]+)/i);
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
        extraction_strategy: "huggingface-dom",
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
        console.log(`[PCE] HuggingFace: +${newMsgs.length} msgs captured`);
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

    const container = document.querySelector('[class*="chat-container"], [class*="messages"], main') || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] HuggingFace Chat observer started");

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
