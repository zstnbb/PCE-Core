/**
 * PCE Content Script – DeepSeek Chat (chat.deepseek.com)
 *
 * Site-specific extractor with incremental delta capture.
 * DeepSeek uses a React-based chat UI with hashed CSS class names.
 * The ONLY stable selector is `.ds-markdown` for assistant responses.
 * We anchor on that and walk up the DOM to find conversation turns.
 */

(function () {
  "use strict";

  // Guard against double-injection
  if (window.__PCE_DEEPSEEK_ACTIVE) return;
  window.__PCE_DEEPSEEK_ACTIVE = true;

  const PROVIDER = "deepseek";
  const SOURCE_NAME = "deepseek-web";
  const DEBOUNCE_MS = 2500;
  const POLL_INTERVAL_MS = 5000;

  let lastFingerprint = "";
  let sentCount = 0;
  let debounceTimer = null;
  let observerActive = false;

  console.log("[PCE] DeepSeek content script loaded");

  // Delegate to shared utilities from pce_dom_utils.js
  const _pce = () => window.__PCE_EXTRACT || {};
  function _extractAttachments(el) { return (_pce().extractAttachments || (() => []))(el); }
  function _extractThinking(el) { return (_pce().extractThinking || (() => ""))(el); }

  // -------------------------------------------------------------------------
  // Message extraction – anchored on .ds-markdown
  // -------------------------------------------------------------------------

  function extractMessages() {
    const messages = [];

    // Strategy 1: Use .ds-markdown blocks as anchors for assistant messages
    const mdBlocks = document.querySelectorAll(".ds-markdown, [class*='ds-markdown'], [class*='markdown-body'], .chat-markdown");
    if (mdBlocks.length > 0) {
      console.debug(`[PCE] DeepSeek: found ${mdBlocks.length} .ds-markdown blocks`);

      mdBlocks.forEach((md) => {
        // The assistant text is inside the .ds-markdown block
        const assistantText = md.innerText.trim();
        if (!assistantText || assistantText.length < 2) return;

        // Walk up to find the "turn" container — the common ancestor
        // that holds both the user message and this assistant response.
        // DeepSeek typically nests: turn > [user-block, assistant-block > .ds-markdown]
        const turnEl = _findTurnContainer(md);
        if (turnEl) {
          const userText = _extractUserFromTurn(turnEl, md);
          if (userText && userText.length > 0) {
            const userEl = turnEl; // best available scope for user attachments
            const userAtt = _extractAttachments(userEl);
            const userMsg = { role: "user", content: userText };
            if (userAtt.length > 0) userMsg.attachments = userAtt;
            messages.push(userMsg);
          }
        }

        // Assistant: extract thinking + attachments from the md block's parent turn
        const assistantScope = md.closest('[class]')?.parentElement || md;
        const thinking = _extractThinking(assistantScope);
        const att = _extractAttachments(assistantScope);
        let content = assistantText;
        if (thinking) content = "<thinking>\n" + thinking + "\n</thinking>\n\n" + content;
        const assistantMsg = { role: "assistant", content };
        if (att.length > 0) assistantMsg.attachments = att;
        messages.push(assistantMsg);
      });

      if (messages.length >= 2) return messages;
    }

    // Strategy 2: Look for common role-attributed elements
    const roleEls = document.querySelectorAll('[data-role], [data-message-role], [data-testid*="message"]');
    if (roleEls.length >= 2) {
      roleEls.forEach((el) => {
        const role = el.getAttribute("data-role") || el.getAttribute("data-message-role")
          || (el.getAttribute("data-testid") || "").replace(/.*message-/, "");
        const text = _cleanText(el);
        if (text && text.length > 1 && (role === "user" || role === "assistant")) {
          messages.push({ role, content: text });
        }
      });
      if (messages.length >= 2) return messages;
      messages.length = 0;
    }

    // Strategy 3: Fallback — find any large text blocks in the conversation area
    const main = document.querySelector("main") || document.body;
    const allTextBlocks = main.querySelectorAll("div > div > div");
    const candidates = [];
    allTextBlocks.forEach((el) => {
      const text = el.innerText?.trim();
      if (text && text.length > 10 && el.children.length < 20 && el.offsetHeight > 20) {
        // Check if this element contains a .ds-markdown (assistant) or is plain text (user)
        if (el.querySelector(".ds-markdown")) {
          candidates.push({ role: "assistant", content: el.querySelector(".ds-markdown").innerText.trim(), el });
        } else if (!el.closest(".ds-markdown") && text.length < 2000) {
          candidates.push({ role: "user", content: text, el });
        }
      }
    });

    // Filter out duplicate/nested candidates
    const seen = new Set();
    candidates.forEach((c) => {
      const key = c.content.slice(0, 100);
      if (!seen.has(key)) {
        seen.add(key);
        messages.push({ role: c.role, content: c.content });
      }
    });

    return messages;
  }

  function _findTurnContainer(mdElement) {
    // Walk up from .ds-markdown to find a container that has siblings
    // (the sibling typically holds the user message).
    // Usually 2-4 levels up from .ds-markdown.
    let el = mdElement.parentElement;
    for (let i = 0; i < 6 && el; i++) {
      const parent = el.parentElement;
      if (!parent) break;

      // A turn container typically has 2+ direct children where one
      // subtree contains .ds-markdown and another holds user text.
      const siblings = Array.from(parent.children);
      if (siblings.length >= 2) {
        // Check if one sibling contains .ds-markdown and another doesn't
        const mdSibling = siblings.find((s) => s.contains(mdElement));
        const otherSiblings = siblings.filter((s) => s !== mdSibling);
        const hasUserContent = otherSiblings.some((s) => {
          const text = s.innerText?.trim();
          return text && text.length > 0 && !s.querySelector(".ds-markdown");
        });
        if (hasUserContent) {
          return parent;
        }
      }
      el = parent;
    }
    return null;
  }

  function _extractUserFromTurn(turnEl, mdElement) {
    // Find the child of turnEl that does NOT contain the .ds-markdown
    const children = Array.from(turnEl.children);
    for (const child of children) {
      if (child.contains(mdElement)) continue;
      // This child might be the user message area
      const text = _cleanText(child);
      // Filter out very short text (buttons, icons) and very long text (might be another assistant)
      if (text && text.length > 0 && !child.querySelector(".ds-markdown")) {
        return text;
      }
    }
    return null;
  }

  function _cleanText(el) {
    const clone = el.cloneNode(true);
    clone.querySelectorAll("script, style, [hidden], .sr-only, button, svg, img, [class*='icon'], [class*='avatar'], [class*='action'], [class*='copy'], [class*='toolbar']").forEach((e) => e.remove());

    const markdown = clone.querySelector(".ds-markdown");
    if (markdown) return markdown.innerText.trim();

    return clone.innerText.trim();
  }

  // -------------------------------------------------------------------------
  // Model + session
  // -------------------------------------------------------------------------

  function extractModelName() {
    // Try multiple strategies to find the model name
    // 1. Look for model selector UI elements
    const selectors = [
      '[class*="model"] [class*="name"]',
      '[class*="model-select"]',
      '[class*="modelName"]',
      '[class*="model-tag"]',
      '[data-testid*="model"]',
      '[class*="model-info"]',
      '[class*="ModelSelector"]',
    ];
    for (const sel of selectors) {
      try {
        const el = document.querySelector(sel);
        if (el) {
          const text = el.textContent.trim();
          if (text && text.length > 2 && text.length < 50) return text;
        }
      } catch {}
    }

    // 2. Check page title
    const title = document.title;
    if (title.includes("DeepSeek")) {
      const match = title.match(/DeepSeek[-\s]?\w+/i);
      if (match) return match[0];
    }

    // 3. Look for "R1" or "V3" indicators in the UI
    const body = document.body.innerText;
    if (body.includes("DeepSeek-R1")) return "DeepSeek-R1";
    if (body.includes("DeepSeek-V3")) return "DeepSeek-V3";

    return "DeepSeek";
  }

  function getSessionHint() {
    // DeepSeek uses /chat/s/<session_id> or /chat/<id>
    const match = location.pathname.match(/\/chat(?:\/s)?\/([a-zA-Z0-9_-]+)/);
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
    if (allMsgs.length === 0) {
      console.debug("[PCE] DeepSeek: no messages extracted");
      return;
    }

    const fp = fingerprint(allMsgs);
    if (fp === lastFingerprint) return;
    lastFingerprint = fp;

    const updateOnly = allMsgs.length > 0 && allMsgs.slice(sentCount).length === 0;
    const newMsgs = updateOnly ? [allMsgs[allMsgs.length - 1]] : allMsgs.slice(sentCount);

    const prevCount = sentCount;
    sentCount = allMsgs.length;

    console.log(`[PCE] DeepSeek: sending ${newMsgs.length} new msgs (${prevCount}→${sentCount})`);

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

    // DeepSeek's chat area — try main first, then body
    const container = document.querySelector("main") || document.body;

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((m) => m.addedNodes.length > 0 || m.type === "characterData")) {
        debouncedCapture();
      }
    });

    observer.observe(container, { childList: true, subtree: true, characterData: true });
    observerActive = true;
    console.log("[PCE] DeepSeek observer started");

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

    // Initial capture attempt (with short delay for DOM to settle)
    setTimeout(debouncedCapture, 1000);
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
