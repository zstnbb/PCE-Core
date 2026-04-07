/**
 * PCE Content Script – Perplexity (www.perplexity.ai)
 *
 * Site-specific extractor with incremental delta capture.
 * Perplexity uses a search-like UI with follow-up questions and cited answers.
 */

(function () {
  "use strict";

  const PROVIDER = "perplexity";
  const SOURCE_NAME = "perplexity-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] Perplexity content script loaded");

  // -------------------------------------------------------------------------
  // Message extraction
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // Perplexity thread: user queries + AI answers in a vertical thread
    // User queries are typically in query blocks, answers in prose blocks
    const turnSelectors = [
      // Thread-based layout
      '[class*="ThreadMessage"]',
      '[class*="thread-message"]',
      // Query/Answer pairs
      '[class*="QueryBlock"], [class*="AnswerBlock"]',
      '[class*="query-block"], [class*="answer-block"]',
      // Generic message containers
      '[data-testid*="message"]',
      '[class*="prose"]',
    ];

    for (const sel of turnSelectors) {
      try {
        const els = document.querySelectorAll(sel);
        if (els.length < 1) continue;

        els.forEach((el) => {
          const role = _detectRole(el);
          const text = _extractText(el);
          if (text && text.length > 3) {
            messages.push({ role, content: text });
          }
        });

        if (messages.length >= 2) return messages;
        messages.length = 0;
      } catch {}
    }

    // Fallback: look for the search input values as queries + answer panels
    const queries = document.querySelectorAll('[class*="query"], [class*="question"], [class*="search-query"]');
    const answers = document.querySelectorAll('[class*="answer"], [class*="prose"], [class*="response"]');

    if (queries.length > 0 && answers.length > 0) {
      const maxPairs = Math.min(queries.length, answers.length);
      for (let i = 0; i < maxPairs; i++) {
        const qText = queries[i]?.innerText?.trim();
        const aText = _extractText(answers[i]);
        if (qText && qText.length > 1) messages.push({ role: "user", content: qText });
        if (aText && aText.length > 1) messages.push({ role: "assistant", content: aText });
      }
    }

    return messages;
  }

  function _detectRole(el) {
    const cls = (el.className || "").toLowerCase();
    const testId = (el.getAttribute("data-testid") || "").toLowerCase();

    if (cls.includes("query") || cls.includes("question") || cls.includes("user") || testId.includes("query")) {
      return "user";
    }
    if (cls.includes("answer") || cls.includes("response") || cls.includes("prose") || testId.includes("answer")) {
      return "assistant";
    }
    return "unknown";
  }

  function _extractText(el) {
    if (!el) return "";
    const clone = el.cloneNode(true);
    // Remove citations, action buttons, source panels
    clone.querySelectorAll("script, style, [hidden], .sr-only, button, [class*='citation'], [class*='source'], [class*='action'], [class*='feedback'], [class*='related']").forEach((e) => e.remove());

    const markdown = clone.querySelector(".prose, .markdown, [class*='markdown'], [class*='rendered']");
    if (markdown) return markdown.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Session
  // -------------------------------------------------------------------------

  function getSessionHint() {
    // Perplexity URLs: /search/<id> or /thread/<id>
    const match = location.pathname.match(/\/(?:search|thread)\/([a-zA-Z0-9_-]+)/);
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
      model_name: null,
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
        extraction_strategy: "perplexity-dom",
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
        console.log(`[PCE] Perplexity: +${newMsgs.length} msgs captured`);
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

    const container = document.querySelector("main, [class*='thread'], [class*='search-results']") || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] Perplexity observer started");

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
