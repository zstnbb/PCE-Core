/**
 * PCE Content Script – ChatGPT (chatgpt.com / chat.openai.com)
 *
 * Captures conversation messages from the DOM and sends deltas to the
 * background service worker.  Handles:
 *   - Incremental capture (only new messages sent)
 *   - Streaming detection (waits for generation to finish)
 *   - Thinking/reasoning panel capture
 *   - SPA navigation via pushState/replaceState interception
 *   - Observer lifecycle (disconnect old before creating new)
 */

(function () {
  "use strict";

  const PROVIDER = "openai";
  const SOURCE_NAME = "chatgpt-web";
  const DEBOUNCE_MS = 2000;
  const STREAM_CHECK_MS = 1500;
  const POLL_INTERVAL_MS = 3000;
  const MAX_RETRIES = 15;

  // ---- State ----
  let currentConvId = null;
  let sentFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let deferTimer = null;
  let activeObserver = null;
  let retries = 0;
  let navHooked = false;

  console.log("[PCE] ChatGPT content script loaded on", window.location.href);

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function getConvId() {
    const m = window.location.pathname.match(/\/c\/([a-f0-9-]+)/);
    return m ? m[1] : null;
  }

  function isStreaming() {
    // ChatGPT shows a stop button or streaming indicator while generating
    if (document.querySelector('button[aria-label="Stop generating"]')) return true;
    if (document.querySelector('button[aria-label="Stop reasoning"]')) return true;
    if (document.querySelector('button[data-testid="stop-button"]')) return true;
    if (document.querySelector(".result-streaming")) return true;
    if (document.querySelector('[class*="stop-"]')) return true;
    // Streaming cursor / animated dots
    if (document.querySelector('[class*="streaming"]')) return true;
    if (document.querySelector('[class*="typing"]')) return true;
    if (document.querySelector('[class*="cursor"]')) return true;
    return false;
  }

  function fingerprint(msgs) {
    // Content-based fingerprint so we detect when message text changes
    return msgs.map((m) => m.role + ":" + m.content.slice(0, 120)).join("|");
  }

  // ---------------------------------------------------------------------------
  // SPA navigation – intercept pushState / replaceState for instant detection
  // ---------------------------------------------------------------------------

  function onNavChange() {
    const id = getConvId();
    if (id === currentConvId) return;
    console.log("[PCE] Nav:", currentConvId, "->", id);
    currentConvId = id;
    sentCount = 0;
    sentFingerprint = "";
    reconnectObserver();
  }

  function hookNav() {
    if (navHooked) return;
    navHooked = true;

    const origPush = history.pushState;
    history.pushState = function (...a) { origPush.apply(this, a); onNavChange(); };

    const origReplace = history.replaceState;
    history.replaceState = function (...a) { origReplace.apply(this, a); onNavChange(); };

    window.addEventListener("popstate", onNavChange);

    let lastHref = location.href;
    setInterval(() => {
      if (location.href !== lastHref) { lastHref = location.href; onNavChange(); }
    }, POLL_INTERVAL_MS);
  }

  // ---------------------------------------------------------------------------
  // DOM extraction
  // ---------------------------------------------------------------------------

  function getContainer() {
    for (const sel of [
      '[class*="react-scroll-to-bottom"]',
      'main [role="presentation"]',
      'main [role="log"]',
      '[class*="conversation"]',
      "main",
    ]) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  /** Extract thinking/reasoning content from a turn element */
  function extractThinking(turnEl) {
    // ChatGPT thinking panel: <details> or elements with "思考"/"Thought"
    const parts = [];

    // Pattern 1: <details> containing thinking summary
    turnEl.querySelectorAll("details").forEach((d) => {
      const summary = d.querySelector("summary");
      const body = d.querySelector(".markdown, .whitespace-pre-wrap, div");
      if (summary && body) {
        parts.push(body.innerText.trim());
      } else {
        const txt = d.innerText.trim();
        if (txt.length > 5) parts.push(txt);
      }
    });

    // Pattern 2: elements with class containing "thought" or "think"
    if (parts.length === 0) {
      turnEl.querySelectorAll('[class*="thought"], [class*="think"]').forEach((el) => {
        const txt = el.innerText.trim();
        if (txt.length > 5) parts.push(txt);
      });
    }

    return parts.join("\n").trim();
  }

  /** Extract the main reply content from a turn element (excluding thinking) */
  function extractReplyContent(turnEl) {
    // First try specific content selectors (ordered by specificity)
    for (const sel of [
      ".markdown.prose",
      ".markdown",
      ".whitespace-pre-wrap",
      ".text-message",
      '[class*="message-content"]',
      '[class*="response-content"]',
      '[data-testid="message-content"]',
    ]) {
      const contentEl = turnEl.querySelector(sel);
      if (contentEl) return contentEl.innerText.trim();
    }
    // Fallback: direct inner text, but exclude <details> (thinking)
    const clone = turnEl.cloneNode(true);
    clone.querySelectorAll("details").forEach((d) => d.remove());
    return clone.innerText.trim();
  }

  function extractMessages() {
    const messages = [];

    // Strategy A: [data-message-author-role] elements (current ChatGPT 2024-2025)
    const roleEls = document.querySelectorAll("[data-message-author-role]");
    if (roleEls.length > 0) {
      roleEls.forEach((el) => {
        const role = el.getAttribute("data-message-author-role");
        if (role === "user") {
          const text = el.innerText.trim();
          if (text) messages.push({ role: "user", content: text });
        } else {
          // Assistant: capture thinking + reply separately
          const thinking = extractThinking(el);
          const reply = extractReplyContent(el);
          if (thinking || reply) {
            let content = "";
            if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
            if (reply) content += reply;
            messages.push({ role: role || "assistant", content: content.trim() });
          }
        }
      });
      if (messages.length > 0) return messages;
    }

    // Strategy B: conversation-turn testids
    const turns = document.querySelectorAll('[data-testid^="conversation-turn"]');
    if (turns.length > 0) {
      turns.forEach((turn, i) => {
        const roleAttr = turn.querySelector("[data-message-author-role]")
          ?.getAttribute("data-message-author-role");
        const role = roleAttr || (i % 2 === 0 ? "user" : "assistant");
        if (role === "user") {
          const text = turn.innerText.trim();
          if (text) messages.push({ role: "user", content: text });
        } else {
          const thinking = extractThinking(turn);
          const reply = extractReplyContent(turn);
          if (thinking || reply) {
            let content = "";
            if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
            if (reply) content += reply;
            messages.push({ role, content: content.trim() });
          }
        }
      });
      if (messages.length > 0) return messages;
    }

    // Strategy C: article elements
    const articles = document.querySelectorAll("main article");
    if (articles.length > 0) {
      articles.forEach((el, i) => {
        const text = el.innerText.trim();
        if (text && text.length > 3) {
          const roleAttr = el.querySelector("[data-message-author-role]")
            ?.getAttribute("data-message-author-role");
          messages.push({ role: roleAttr || (i % 2 === 0 ? "user" : "assistant"), content: text });
        }
      });
      if (messages.length > 0) return messages;
    }

    // Strategy D: data-message-id elements (another stable ChatGPT attribute)
    const msgIdEls = document.querySelectorAll("[data-message-id]");
    if (msgIdEls.length > 0) {
      msgIdEls.forEach((el, i) => {
        const roleAttr = el.getAttribute("data-message-author-role")
          || el.closest("[data-message-author-role]")?.getAttribute("data-message-author-role");
        const role = roleAttr || (i % 2 === 0 ? "user" : "assistant");
        const text = role === "user" ? el.innerText.trim() : extractReplyContent(el);
        if (text && text.length > 1) {
          messages.push({ role, content: text });
        }
      });
      if (messages.length > 0) return messages;
    }

    // Strategy E: ARIA role-based generic (role="row" or role="listitem" in conversation)
    const container = getContainer();
    if (container) {
      const rows = container.querySelectorAll('[role="row"], [role="listitem"], [role="group"]');
      if (rows.length >= 2) {
        rows.forEach((el, i) => {
          const text = el.innerText.trim();
          if (text && text.length > 3) {
            messages.push({ role: i % 2 === 0 ? "user" : "assistant", content: text });
          }
        });
      }
    }

    return messages;
  }

  function getModelName() {
    const el = document.querySelector('[class*="model"], [data-testid*="model"]');
    return el ? el.innerText.trim() : null;
  }

  // ---------------------------------------------------------------------------
  // Capture logic
  // ---------------------------------------------------------------------------

  function captureConversation() {
    // If still streaming, wait and retry
    if (isStreaming()) {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(captureConversation, STREAM_CHECK_MS);
      return;
    }

    const allMsgs = extractMessages();
    if (allMsgs.length === 0) return;

    const convId = getConvId();

    // No conversation ID yet (new chat) → retry until URL updates
    if (!convId) {
      clearTimeout(deferTimer);
      deferTimer = setTimeout(captureConversation, 1000);
      return;
    }

    // Conversation switch
    if (convId !== currentConvId) {
      currentConvId = convId;
      sentCount = 0;
      sentFingerprint = "";
    }

    // Content-based change detection (not just count)
    const fp = fingerprint(allMsgs);
    if (fp === sentFingerprint) return;

    const newMsgs = allMsgs.slice(sentCount);
    if (newMsgs.length === 0) {
      // Count didn't change but fingerprint did → content update of last msg
      // Re-send the last assistant message as an update
      sentFingerprint = fp;
      return;
    }

    const prevCount = sentCount;
    const prevFp = sentFingerprint;
    sentCount = allMsgs.length;
    sentFingerprint = fp;

    console.log(
      `[PCE] Sending ${newMsgs.length} new msgs (${prevCount}->${sentCount}) conv=${convId.slice(0, 8)}`
    );

    const payload = {
      provider: PROVIDER,
      source_name: SOURCE_NAME,
      host: location.hostname,
      path: location.pathname,
      model_name: getModelName(),
      session_hint: convId,
      conversation: {
        conversation_id: convId,
        messages: newMsgs,
        total_messages: allMsgs.length,
        url: location.href,
        title: document.title,
      },
      meta: {
        new_message_count: newMsgs.length,
        total_message_count: allMsgs.length,
        extraction_strategy: "dom-incremental",
        behavior: window.__PCE_BEHAVIOR
          ? window.__PCE_BEHAVIOR.getBehaviorSnapshot(true)
          : {},
      },
    };

    chrome.runtime.sendMessage({ type: "PCE_CAPTURE", payload }, (resp) => {
      if (chrome.runtime.lastError) {
        console.error("[PCE] Send failed:", chrome.runtime.lastError.message);
        sentCount = prevCount;
        sentFingerprint = prevFp;
        return;
      }
      if (resp?.ok) {
        console.log(`[PCE] +${newMsgs.length} msgs captured for ${convId.slice(0, 8)}`);
      } else {
        console.warn("[PCE] Capture not ok:", resp);
        sentCount = prevCount;
        sentFingerprint = prevFp;
      }
    });
  }

  function scheduledCapture() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(captureConversation, DEBOUNCE_MS);
  }

  // ---------------------------------------------------------------------------
  // Observer lifecycle – always disconnect old before creating new
  // ---------------------------------------------------------------------------

  function disconnectObserver() {
    if (activeObserver) {
      activeObserver.disconnect();
      activeObserver = null;
    }
  }

  function reconnectObserver() {
    disconnectObserver();
    retries = 0;
    startObserver();
  }

  function startObserver() {
    if (activeObserver) return; // already active

    const container = getContainer();
    if (!container) {
      retries++;
      if (retries <= MAX_RETRIES) setTimeout(startObserver, 2000);
      return;
    }

    activeObserver = new MutationObserver((mutations) => {
      const relevant = mutations.some(
        (m) => m.addedNodes.length > 0 || m.type === "characterData"
      );
      if (relevant) scheduledCapture();
    });

    activeObserver.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    currentConvId = getConvId();
    console.log("[PCE] Observer started, conv:", currentConvId || "new-chat");

    hookNav();
    scheduledCapture();
  }

  // Boot
  if (document.readyState === "complete" || document.readyState === "interactive") {
    startObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startObserver);
  }
})();
