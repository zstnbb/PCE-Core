/**
 * PCE Content Script – Poe (poe.com)
 *
 * Site-specific extractor with incremental delta capture.
 * Poe hosts multiple AI models in a unified chat interface.
 */

(function () {
  "use strict";

  const PROVIDER = "poe";
  const SOURCE_NAME = "poe-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] Poe content script loaded");

  // -------------------------------------------------------------------------
  // Message extraction
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // Poe uses [class*="Message"] containers with human/bot distinction
    const turnSelectors = [
      '[class*="Message_humanMessage"], [class*="Message_botMessage"]',
      '[class*="humanMessage"], [class*="botMessage"]',
      '[class*="ChatMessage"]',
      '[data-message-author]',
      '[class*="message_row"]',
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
    const cls = (el.className || "").toLowerCase();
    const author = (el.getAttribute("data-message-author") || "").toLowerCase();

    if (cls.includes("human") || author === "human" || author === "user") {
      return "user";
    }
    if (cls.includes("bot") || author === "bot" || author === "assistant") {
      return "assistant";
    }
    return "unknown";
  }

  function _extractText(el) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only, button, [class*='action'], [class*='feedback'], [class*='Suggested']").forEach((e) => e.remove());

    const markdown = clone.querySelector(".markdown, [class*='Markdown'], [class*='prose'], [class*='message_content']");
    if (markdown) return markdown.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Model + session
  // -------------------------------------------------------------------------

  function extractModelName() {
    // Poe shows the bot name prominently
    const botName = document.querySelector('[class*="BotName"], [class*="botName"], [class*="ChatHeader"] [class*="name"]');
    if (botName) return botName.textContent.trim() || null;

    // From URL: /chat/<botname>
    const match = location.pathname.match(/\/chat\/([^/?]+)/);
    return match ? match[1] : null;
  }

  function getSessionHint() {
    // Poe URLs: /chat/<botname>/<thread_id> or /chat/<botname>
    const match = location.pathname.match(/\/chat\/[^/]+\/(\d+)/);
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
        extraction_strategy: "poe-dom",
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
        console.log(`[PCE] Poe: +${newMsgs.length} msgs captured`);
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

    const container = document.querySelector('[class*="ChatMessages"], [class*="chatMessages"], main') || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
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
