/**
 * PCE Content Script – Claude (claude.ai)
 *
 * Observes the DOM for conversation messages and sends them to the
 * background service worker for capture.
 */

(function () {
  "use strict";

  // Guard against double-injection
  if (window.__PCE_CLAUDE_ACTIVE) return;
  window.__PCE_CLAUDE_ACTIVE = true;

  const PROVIDER = "anthropic";
  const SOURCE_NAME = "claude-web";
  const DEBOUNCE_MS = 2000;
  const POLL_INTERVAL_MS = 3000;

  let lastCapturedText = "";
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] Claude content script loaded");

  // Delegate to shared utilities from pce_dom_utils.js
  const _pce = () => window.__PCE_EXTRACT || {};
  function _extractAttachments(el) { return (_pce().extractAttachments || (() => []))(el); }
  function _extractThinking(el) { return (_pce().extractThinking || (() => ""))(el); }

  // ---------------------------------------------------------------------------
  // DOM selectors
  // ---------------------------------------------------------------------------

  function getConversationContainer() {
    return (
      document.querySelector('[class*="conversation-content"]') ||
      document.querySelector('[class*="chat-messages"]') ||
      document.querySelector('[class*="thread-content"]') ||
      document.querySelector('[role="log"]') ||
      document.querySelector("main .flex.flex-col") ||
      document.querySelector("main")
    );
  }

  function extractMessages() {
    const messages = [];

    // Strategy 1: Human/Assistant turn containers
    const humanTurns = document.querySelectorAll(
      '[data-testid="human-turn"], [data-testid*="user-message"], [class*="human-turn"], .font-user-message, [data-role="user"]'
    );
    const assistantTurns = document.querySelectorAll(
      '[data-testid="assistant-turn"], [data-testid*="assistant-message"], [class*="assistant-turn"], .font-claude-message, [data-role="assistant"]'
    );

    if (humanTurns.length > 0 || assistantTurns.length > 0) {
      // Collect all turns with position info for ordering
      const allTurns = [];

      humanTurns.forEach((el) => {
        const text = el.innerText.trim();
        if (text) {
          const att = _extractAttachments(el);
          const turn = {
            role: "user",
            content: text,
            top: el.getBoundingClientRect().top,
          };
          if (att.length > 0) turn.attachments = att;
          allTurns.push(turn);
        }
      });

      assistantTurns.forEach((el) => {
        const thinking = _extractThinking(el);
        const text = el.innerText.trim();
        if (text) {
          let content = text;
          if (thinking) content = "<thinking>\n" + thinking + "\n</thinking>\n\n" + content;
          const att = _extractAttachments(el);
          const turn = {
            role: "assistant",
            content,
            top: el.getBoundingClientRect().top,
          };
          if (att.length > 0) turn.attachments = att;
          allTurns.push(turn);
        }
      });

      // Sort by vertical position (top of page first)
      allTurns.sort((a, b) => a.top - b.top);
      allTurns.forEach((t) => {
        const msg = { role: t.role, content: t.content };
        if (t.attachments) msg.attachments = t.attachments;
        messages.push(msg);
      });
      return messages;
    }

    // Strategy 2: Generic message blocks with role detection
    const blocks = document.querySelectorAll(
      '[class*="message"], [class*="Message"], [class*="turn"], [class*="Turn"]'
    );
    blocks.forEach((el) => {
      const text = el.innerText.trim();
      if (!text || text.length < 3) return;

      // Detect role from class names, data attributes, or ARIA
      const classes = (el.className || "").toLowerCase();
      const dataRole = el.getAttribute("data-role") || "";
      const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase();
      let role = "unknown";
      if (classes.includes("human") || classes.includes("user") || dataRole === "user" || ariaLabel.includes("user") || ariaLabel.includes("human")) {
        role = "user";
      } else if (classes.includes("assistant") || classes.includes("claude") || classes.includes("ai") || dataRole === "assistant" || ariaLabel.includes("assistant") || ariaLabel.includes("claude")) {
        role = "assistant";
      }

      if (role !== "unknown") {
        messages.push({ role, content: text });
      }
    });
    if (messages.length >= 2) return messages;

    // Strategy 3: Alternating large text blocks in conversation area (last resort)
    messages.length = 0;
    const container = getConversationContainer();
    if (container) {
      const directChildren = container.querySelectorAll(':scope > div');
      if (directChildren.length >= 2) {
        directChildren.forEach((el, i) => {
          const text = el.innerText.trim();
          if (text && text.length > 5) {
            messages.push({ role: i % 2 === 0 ? "user" : "assistant", content: text });
          }
        });
      }
    }

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
