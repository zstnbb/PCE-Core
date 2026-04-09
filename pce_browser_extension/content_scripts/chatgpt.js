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

  // Guard against double-injection (manifest + proactive fallback can both fire)
  if (window.__PCE_CHATGPT_ACTIVE) return;
  window.__PCE_CHATGPT_ACTIVE = true;

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
    // Delegate to shared utilities (covers stop buttons + streaming indicators)
    if (window.__PCE_EXTRACT && window.__PCE_EXTRACT.isStreaming()) return true;
    // ChatGPT-specific fallback: agent-turn streaming
    const main = document.querySelector("main");
    if (main && main.querySelector('[class*="agent-turn"] [class*="streaming"]')) return true;
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

  // Delegate to shared utilities from pce_dom_utils.js
  const _pce = () => window.__PCE_EXTRACT || {};
  function extractThinking(el) { return (_pce().extractThinking || (() => ""))(el); }
  function extractReplyContent(el) { return (_pce().extractReplyContent || ((e) => e ? e.innerText.trim() : ""))(el); }
  function extractAttachments(el) { return (_pce().extractAttachments || (() => []))(el); }

  /**
   * Extract special content from the page that may live outside normal
   * message elements: Deep Research reports, Canvas artifacts, etc.
   * Returns {role, content, attachments} or null.
   */
  function extractSpecialContent() {
    // Deep Research: look for research report cards in the conversation area
    const main = document.querySelector("main");
    if (!main) return null;

    // Strategy: find large text blocks that are not inside [data-message-author-role="user"]
    // Deep Research renders a card/article with the report
    const candidates = main.querySelectorAll(
      'article, [class*="research"], [class*="report"], [class*="deep-dive"], ' +
      '[class*="canvas-content"], [role="article"], [role="document"], ' +
      '[class*="result-content"], section'
    );

    let bestText = "";
    for (const el of candidates) {
      // Skip if inside a user message
      if (el.closest('[data-message-author-role="user"]')) continue;
      const text = el.innerText.trim();
      // Prefer the longest meaningful block
      if (text.length > bestText.length && text.length > 50) {
        bestText = text;
      }
    }

    // Fallback: look for any large scrollable content container in main
    if (!bestText) {
      const scrollables = main.querySelectorAll('[class*="scroll"], [class*="overflow"]');
      for (const el of scrollables) {
        if (el.closest('[data-message-author-role="user"]')) continue;
        // Skip the outer conversation container itself
        if (el.contains(main.querySelector('[data-message-author-role]'))) continue;
        const text = el.innerText.trim();
        if (text.length > bestText.length && text.length > 100) {
          bestText = text;
        }
      }
    }

    // Final fallback: look for conversation turn containers that have no role attr
    // but contain substantial text (Deep Research result containers)
    if (!bestText) {
      const turns = main.querySelectorAll('[data-testid^="conversation-turn"]');
      for (const turn of turns) {
        const roleEl = turn.querySelector('[data-message-author-role]');
        if (roleEl && roleEl.getAttribute('data-message-author-role') === 'user') continue;
        const text = turn.innerText.trim();
        if (text.length > bestText.length && text.length > 100) {
          bestText = text;
        }
      }
    }

    if (bestText.length > 50) {
      return { role: "assistant", content: bestText, attachments: [] };
    }
    return null;
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
          if (text) {
            const att = extractAttachments(el);
            const msg = { role: "user", content: text };
            if (att.length > 0) msg.attachments = att;
            messages.push(msg);
          }
        } else {
          // Assistant/tool: capture thinking + reply separately
          const thinking = extractThinking(el);
          const reply = extractReplyContent(el);
          if (thinking || reply) {
            let content = "";
            if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
            if (reply) content += reply;
            const att = extractAttachments(el);
            const msg = { role: role || "assistant", content: content.trim() };
            if (att.length > 0) msg.attachments = att;
            messages.push(msg);
          }
        }
      });
      // Check: if we only found user messages, try to find special content
      // (Deep Research reports, Canvas, etc.) that may lack the role attribute
      const hasNonUser = messages.some(m => m.role !== "user");
      if (!hasNonUser && messages.length > 0) {
        const special = extractSpecialContent();
        if (special) messages.push(special);
      }
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
          if (text) {
            const att = extractAttachments(turn);
            const msg = { role: "user", content: text };
            if (att.length > 0) msg.attachments = att;
            messages.push(msg);
          }
        } else {
          const thinking = extractThinking(turn);
          const reply = extractReplyContent(turn);
          if (thinking || reply) {
            let content = "";
            if (thinking) content += "<thinking>\n" + thinking + "\n</thinking>\n\n";
            if (reply) content += reply;
            const att = extractAttachments(turn);
            const msg = { role, content: content.trim() };
            if (att.length > 0) msg.attachments = att;
            messages.push(msg);
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
          const role = roleAttr || (i % 2 === 0 ? "user" : "assistant");
          const att = extractAttachments(el);
          const msg = { role, content: text };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
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
          const att = extractAttachments(el);
          const msg = { role, content: text };
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
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

    let convId = getConvId();
    if (!convId && allMsgs.length < 2) {
      return;
    }

    // New chat: URL hasn't updated to /c/... yet.
    // Use a temporary ID only after we have a full turn pair.
    if (!convId) {
      convId = "_new_" + Date.now().toString(36);
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

    const updateOnly = allMsgs.length > 0 && allMsgs.slice(sentCount).length === 0;
    const newMsgs = updateOnly ? [allMsgs[allMsgs.length - 1]] : allMsgs.slice(sentCount);

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
        capture_mode: updateOnly ? "message_update" : "message_delta",
        updated_message_index: updateOnly ? allMsgs.length - 1 : null,
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
