/**
 * PCE – Universal DOM Conversation Extractor
 *
 * A generic extractor that works across many AI chat interfaces by using
 * heuristic DOM patterns. Dynamically injected by the service worker when
 * detector.js identifies an AI page that does NOT have a site-specific
 * extractor (chatgpt.js, claude.js, etc.).
 *
 * Strategy:
 *   1. Find the chat container via scored selector candidates
 *   2. Extract message pairs by role-indicator heuristics
 *   3. Observe DOM mutations for new messages
 *   4. Send incremental deltas to service worker
 */

(() => {
  "use strict";

  // Avoid double-load
  if (window.__PCE_UNIVERSAL_EXTRACTOR_LOADED) return;
  window.__PCE_UNIVERSAL_EXTRACTOR_LOADED = true;

  // Skip if a site-specific extractor is already active (avoid duplicate captures)
  const _SITE_EXTRACTORS = [
    "__PCE_CHATGPT_ACTIVE",
    "__PCE_CLAUDE_ACTIVE",
    "__PCE_DEEPSEEK_ACTIVE",
    "__PCE_GEMINI_ACTIVE",
    "__PCE_PERPLEXITY_ACTIVE",
    "__PCE_GROK_ACTIVE",
    "__PCE_POE_ACTIVE",
    "__PCE_COPILOT_ACTIVE",
  ];
  for (const flag of _SITE_EXTRACTORS) {
    if (window[flag]) {
      console.debug(`[PCE Universal] Skipping — site-specific extractor already active (${flag})`);
      return;
    }
  }

  const PROVIDER = "universal";
  const SOURCE_NAME = "chrome-ext-universal";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let debounceTimer = null;
  let observerActive = false;
  let activeObserver = null;
  let lastFingerprint = "";
  let sentCount = 0;

  // -------------------------------------------------------------------------
  // Container detection — scored candidates
  // -------------------------------------------------------------------------

  const CONTAINER_SELECTORS = [
    // High specificity: known chat wrapper patterns
    { sel: '[data-testid*="conversation"]', score: 10 },
    { sel: '[class*="conversation-container"]', score: 9 },
    { sel: '[class*="chat-container"]', score: 9 },
    { sel: '[class*="chat-messages"]', score: 9 },
    { sel: '[class*="message-list"]', score: 8 },
    { sel: '[class*="messages-container"]', score: 8 },
    { sel: '[class*="chat-log"]', score: 8 },
    { sel: '[class*="thread-container"]', score: 7 },
    { sel: '[role="log"]', score: 7 },
    { sel: '[role="feed"]', score: 5 },
    // Medium specificity
    { sel: "main [class*='chat']", score: 6 },
    { sel: "main [class*='message']", score: 5 },
    // Low specificity fallbacks
    { sel: "main", score: 2 },
    { sel: '[role="main"]', score: 2 },
  ];

  function findChatContainer() {
    let best = null;
    let bestScore = 0;

    for (const { sel, score } of CONTAINER_SELECTORS) {
      try {
        const el = document.querySelector(sel);
        if (el && score > bestScore) {
          // Prefer containers with enough child elements (likely messages)
          const childCount = el.children.length;
          const adjustedScore = score + Math.min(childCount, 10) * 0.5;
          if (adjustedScore > bestScore) {
            best = el;
            bestScore = adjustedScore;
          }
        }
      } catch {
        // skip invalid selectors
      }
    }

    return best;
  }

  // -------------------------------------------------------------------------
  // Message extraction — heuristic role detection
  // -------------------------------------------------------------------------

  // Selectors that identify individual message elements
  const MESSAGE_SELECTORS = [
    "[data-message-author-role]",
    "[class*='message'][class*='user']",
    "[class*='message'][class*='assistant']",
    "[class*='message'][class*='human']",
    "[class*='message'][class*='bot']",
    "[class*='message'][class*='ai']",
    "[class*='chat-message']",
    "[class*='msg-row']",
    "[class*='turn-']",
    "[data-role]",
    "[data-author]",
  ];

  // Patterns to determine message role from element attributes/classes
  const USER_INDICATORS = [
    (el) => el.getAttribute("data-message-author-role") === "user",
    (el) => el.getAttribute("data-role") === "user",
    (el) => el.getAttribute("data-author") === "user",
    (el) => /\buser\b/i.test(el.className),
    (el) => /\bhuman\b/i.test(el.className),
    (el) => /\bmy-message\b/i.test(el.className),
    (el) => /\bsent\b/i.test(el.className),
    (el) => /\bright\b/i.test(el.className) && /\bmessage\b/i.test(el.className),
  ];

  const ASSISTANT_INDICATORS = [
    (el) => el.getAttribute("data-message-author-role") === "assistant",
    (el) => el.getAttribute("data-role") === "assistant",
    (el) => el.getAttribute("data-role") === "bot",
    (el) => el.getAttribute("data-author") === "assistant",
    (el) => /\bassistant\b/i.test(el.className),
    (el) => /\bbot\b/i.test(el.className),
    (el) => /\bai[-_]?(message|response)\b/i.test(el.className),
    (el) => /\breceived\b/i.test(el.className),
    (el) => /\bleft\b/i.test(el.className) && /\bmessage\b/i.test(el.className),
  ];

  function detectRole(el) {
    for (const test of USER_INDICATORS) {
      try { if (test(el)) return "user"; } catch {}
    }
    for (const test of ASSISTANT_INDICATORS) {
      try { if (test(el)) return "assistant"; } catch {}
    }
    return null;
  }

  // Shared utilities from pce_dom_utils.js
  const _pce = () => window.__PCE_EXTRACT || {};

  function extractTextContent(el) {
    // Delegate to shared utility if available
    const shared = _pce().extractReplyContent;
    if (shared) return shared(el);
    // Fallback: clone and remove hidden elements, scripts, styles
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only").forEach((e) => e.remove());
    const prose = clone.querySelector(".markdown, .prose, .markdown-body, [class*='rendered']");
    if (prose) return prose.innerText.trim();
    return clone.innerText.trim();
  }

  function extractMessages() {
    const messages = [];

    // Strategy A: Find explicitly marked message elements
    for (const sel of MESSAGE_SELECTORS) {
      try {
        const els = document.querySelectorAll(sel);
        if (els.length < 2) continue; // need at least 2 messages

        els.forEach((el) => {
          const role = detectRole(el);
          if (!role) return;
          let text = "";
          if (role === "assistant") {
            const thinking = (_pce().extractThinking || (() => ""))(el);
            const reply = extractTextContent(el);
            if (thinking) text += "<thinking>\n" + thinking + "\n</thinking>\n\n";
            if (reply) text += reply;
            text = text.trim();
          } else {
            text = extractTextContent(el);
          }
          if (text && text.length > 1) {
            const msg = { role, content: text };
            const att = (_pce().extractAttachments || (() => []))(el);
            if (att.length > 0) msg.attachments = att;
            messages.push(msg);
          }
        });

        if (messages.length >= 2) return messages;
        messages.length = 0; // reset and try next selector
      } catch {
        // skip
      }
    }

    // Strategy B: Alternating children in chat container
    const container = findChatContainer();
    if (!container) return messages;

    const children = Array.from(container.children).filter((el) => {
      const text = el.innerText?.trim();
      return text && text.length > 2 && el.offsetHeight > 0;
    });

    if (children.length < 2) return messages;

    // Try to detect roles; if can't, assume alternating user/assistant
    let hasRoles = false;
    const withRoles = children.map((el) => {
      const role = detectRole(el);
      if (role) hasRoles = true;
      return { el, role };
    });

    if (hasRoles) {
      // Use detected roles, skip undetected
      for (const { el, role } of withRoles) {
        if (!role) continue;
        let text = "";
        if (role === "assistant") {
          const thinking = (_pce().extractThinking || (() => ""))(el);
          const reply = extractTextContent(el);
          if (thinking) text += "<thinking>\n" + thinking + "\n</thinking>\n\n";
          if (reply) text += reply;
          text = text.trim();
        } else {
          text = extractTextContent(el);
        }
        if (text && text.length > 1) {
          const msg = { role, content: text };
          const att = (_pce().extractAttachments || (() => []))(el);
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
      }
    } else {
      // Alternating assumption: first visible = user, second = assistant
      children.forEach((el, i) => {
        const role = i % 2 === 0 ? "user" : "assistant";
        const text = extractTextContent(el);
        if (text && text.length > 1) {
          const msg = { role, content: text };
          const att = (_pce().extractAttachments || (() => []))(el);
          if (att.length > 0) msg.attachments = att;
          messages.push(msg);
        }
      });
    }

    return messages;
  }

  // -------------------------------------------------------------------------
  // Fingerprinting for change detection
  // -------------------------------------------------------------------------

  function fingerprint(msgs) {
    return msgs.map((m) => `${m.role}:${m.content.slice(0, 80)}`).join("|");
  }

  // -------------------------------------------------------------------------
  // Session hint extraction
  // -------------------------------------------------------------------------

  function getSessionHint() {
    // Delegate to shared utility
    const shared = _pce().getSessionHint;
    if (shared) return shared();
    // Fallback
    const path = location.pathname;
    const m = path.match(/\/(?:c|chat|conversation|thread|t)\/([a-f0-9-]{8,})/i);
    return m ? m[1] : (path.length > 1 ? path : null);
  }

  // -------------------------------------------------------------------------
  // Provider guessing
  // -------------------------------------------------------------------------

  function guessProvider() {
    const host = location.hostname;

    // Check detector's known domain map
    const detectorDomains = window.__PCE_DETECTOR?.KNOWN_AI_DOMAINS;
    if (detectorDomains && detectorDomains.has(host)) {
      // Derive from hostname
      const parts = host.replace("www.", "").split(".");
      return parts[0] || "unknown";
    }

    // Fallback: use hostname
    return host.replace("www.", "").split(".")[0] || "unknown";
  }

  // -------------------------------------------------------------------------
  // Capture logic
  // -------------------------------------------------------------------------

  function captureConversation() {
    // Wait for streaming to finish before capturing
    const streaming = _pce().isStreaming;
    if (streaming && streaming()) {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(captureConversation, 1500);
      return;
    }

    const allMsgs = extractMessages();
    if (allMsgs.length === 0) return;

    const fp = fingerprint(allMsgs);
    if (fp === lastFingerprint) return;
    lastFingerprint = fp;

    const updateOnly = allMsgs.length > 0 && allMsgs.slice(sentCount).length === 0;
    const newMsgs = updateOnly ? [allMsgs[allMsgs.length - 1]] : allMsgs.slice(sentCount);
      // Content changed but count didn't — update in last message

    const prevCount = sentCount;
    sentCount = allMsgs.length;

    const sessionHint = getSessionHint();
    const provider = guessProvider();

    console.log(
      `[PCE Universal] Sending ${newMsgs.length} new msgs (${prevCount}->${sentCount}) from ${location.hostname}`
    );

    const payload = {
      provider,
      source_name: SOURCE_NAME,
      host: location.hostname,
      path: location.pathname,
      model_name: null,
      session_hint: sessionHint,
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
        extraction_strategy: "universal-dom",
        behavior: window.__PCE_BEHAVIOR
          ? window.__PCE_BEHAVIOR.getBehaviorSnapshot(true)
          : {},
      },
    };

    chrome.runtime.sendMessage({ type: "PCE_CAPTURE", payload }, (resp) => {
      if (chrome.runtime.lastError) {
        console.error("[PCE Universal] Send failed:", chrome.runtime.lastError.message);
        sentCount = prevCount;
        lastFingerprint = "";
        return;
      }
      if (resp?.ok) {
        console.log(`[PCE Universal] +${newMsgs.length} msgs captured from ${location.hostname}`);
      } else {
        console.warn("[PCE Universal] Capture not ok:", resp);
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
  // MutationObserver
  // -------------------------------------------------------------------------

  function startObserver() {
    if (observerActive) return;

    const container = findChatContainer();
    if (!container) {
      // Retry a few times
      setTimeout(startObserver, 3000);
      return;
    }

    activeObserver = new MutationObserver((mutations) => {
      const hasNewContent = mutations.some(
        (m) => m.addedNodes.length > 0 || m.type === "characterData"
      );
      if (hasNewContent) {
        debouncedCapture();
      }
    });

    activeObserver.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    observerActive = true;
    console.log(`[PCE Universal] Observer started for ${location.hostname}`);

    // SPA navigation detection
    let lastUrl = location.href;
    setInterval(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        lastFingerprint = "";
        sentCount = 0;
        debouncedCapture();
      }
    }, POLL_INTERVAL_MS);

    // Initial capture
    debouncedCapture();
  }

  // -------------------------------------------------------------------------
  // Manual capture trigger (from popup "Capture This Page" button)
  // -------------------------------------------------------------------------

  document.addEventListener("pce-manual-capture", () => {
    console.log("[PCE Universal] Manual capture triggered");
    // Force re-capture by resetting fingerprint
    lastFingerprint = "";
    sentCount = 0;
    captureConversation();
  });

  // -------------------------------------------------------------------------
  // Boot
  // -------------------------------------------------------------------------

  if (document.readyState === "complete" || document.readyState === "interactive") {
    startObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startObserver);
  }
})();
