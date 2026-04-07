/**
 * PCE – Lightweight AI Page Detector
 *
 * Runs on ALL pages (via <all_urls> in manifest). Quickly determines if the
 * current page is an AI tool/chat interface. If detected, signals the service
 * worker to dynamically inject the full capture pipeline (bridge.js,
 * behavior_tracker.js, universal_extractor.js).
 *
 * Design goals:
 *   - Minimal overhead: bail out fast on non-AI pages
 *   - No DOM mutation observers — just a quick scan
 *   - Re-checks after SPA navigation
 */

(() => {
  "use strict";

  // Avoid double-injection
  if (window.__PCE_DETECTOR_LOADED) return;
  window.__PCE_DETECTOR_LOADED = true;

  // -------------------------------------------------------------------------
  // Known AI site domains (fast-path: skip heuristic scan)
  // -------------------------------------------------------------------------

  const KNOWN_AI_DOMAINS = new Set([
    "chatgpt.com",
    "chat.openai.com",
    "claude.ai",
    "gemini.google.com",
    "chat.deepseek.com",
    "www.perplexity.ai",
    "grok.com",
    "poe.com",
    "copilot.microsoft.com",
    "huggingface.co",
    "chat.mistral.ai",
    "kimi.moonshot.cn",
    "you.com",
    "pi.ai",
    "chat.lmsys.org",
    "open-assistant.io",
    "beta.character.ai",
    "character.ai",
    "phind.com",
    "devv.ai",
    "aistudio.google.com",
    "labs.perplexity.ai",
    "notebooklm.google.com",
    "coral.cohere.com",
    "chat.zhipuai.cn",
    "yiyan.baidu.com",
    "tongyi.aliyun.com",
    "xinghuo.xfyun.cn",
    "tiangong.kunlun.com",
    "chatglm.cn",
  ]);

  // Subdomains that may host AI chat (e.g., foo.vercel.app AI demos)
  const KNOWN_AI_PATH_PREFIXES = [
    "/chat",
    "/c/",
    "/conversation",
    "/playground",
    "/assistant",
  ];

  // -------------------------------------------------------------------------
  // DOM heuristic signals (scored)
  // -------------------------------------------------------------------------

  const DOM_SIGNALS = [
    // Input areas typical of chat UIs
    { selector: 'textarea[placeholder*="message" i]', weight: 3 },
    { selector: 'textarea[placeholder*="ask" i]', weight: 3 },
    { selector: 'textarea[placeholder*="chat" i]', weight: 3 },
    { selector: 'textarea[placeholder*="prompt" i]', weight: 3 },
    { selector: 'textarea[placeholder*="type" i]', weight: 1 },
    { selector: '[contenteditable="true"][data-placeholder*="message" i]', weight: 3 },
    { selector: '[contenteditable="true"][data-placeholder*="ask" i]', weight: 3 },
    { selector: '[role="textbox"][aria-label*="message" i]', weight: 3 },

    // Chat message containers
    { selector: '[data-message-author-role]', weight: 5 },
    { selector: '[data-testid*="conversation"]', weight: 4 },
    { selector: '[class*="chat-message"]', weight: 3 },
    { selector: '[class*="message-bubble"]', weight: 3 },
    { selector: '[class*="assistant-message"]', weight: 4 },
    { selector: '[class*="ai-response"]', weight: 4 },
    { selector: '[class*="bot-message"]', weight: 3 },
    { selector: '[class*="human-message"]', weight: 3 },
    { selector: '[class*="user-message"]', weight: 3 },

    // Streaming / thinking indicators
    { selector: '[class*="streaming"]', weight: 2 },
    { selector: '[class*="thinking"]', weight: 2 },
    { selector: '[class*="typing-indicator"]', weight: 2 },

    // Model selector elements
    { selector: '[class*="model-select"]', weight: 3 },
    { selector: '[aria-label*="model" i]', weight: 2 },
    { selector: 'select[class*="model"]', weight: 3 },

    // Markdown-rendered content blocks (common in AI responses)
    { selector: '.prose.markdown', weight: 2 },
    { selector: '.markdown-body', weight: 1 },
    { selector: '[class*="code-block"]', weight: 1 },
  ];

  // Title / meta heuristics
  const TITLE_KEYWORDS = [
    "chat", "ai", "assistant", "copilot", "gpt", "claude", "gemini",
    "llm", "model", "prompt", "conversation",
  ];

  const META_KEYWORDS = [
    "ai chat", "chatbot", "language model", "ai assistant", "generative ai",
  ];

  // -------------------------------------------------------------------------
  // Detection logic
  // -------------------------------------------------------------------------

  const KNOWN_THRESHOLD = 0;   // known domains always qualify
  const HEURISTIC_THRESHOLD = 6; // score needed for unknown domains

  function detectAIPage() {
    const hostname = location.hostname;
    const pathname = location.pathname.toLowerCase();

    // Fast path: known AI domain
    if (KNOWN_AI_DOMAINS.has(hostname)) {
      return {
        detected: true,
        confidence: "known",
        domain: hostname,
        score: 100,
        signals: ["known_domain"],
      };
    }

    // Score-based heuristic for unknown domains
    let score = 0;
    const signals = [];

    // URL path hints
    for (const prefix of KNOWN_AI_PATH_PREFIXES) {
      if (pathname.startsWith(prefix)) {
        score += 2;
        signals.push(`path:${prefix}`);
        break;
      }
    }

    // Title keywords
    const title = (document.title || "").toLowerCase();
    for (const kw of TITLE_KEYWORDS) {
      if (title.includes(kw)) {
        score += 1;
        signals.push(`title:${kw}`);
        break; // only count once
      }
    }

    // Meta description keywords
    const metaDesc = document.querySelector('meta[name="description"]')
      ?.getAttribute("content")?.toLowerCase() || "";
    for (const kw of META_KEYWORDS) {
      if (metaDesc.includes(kw)) {
        score += 2;
        signals.push(`meta:${kw}`);
        break;
      }
    }

    // DOM signals (query up to threshold, then stop)
    for (const { selector, weight } of DOM_SIGNALS) {
      try {
        if (document.querySelector(selector)) {
          score += weight;
          signals.push(`dom:${selector.slice(0, 40)}`);
          if (score >= HEURISTIC_THRESHOLD) break; // early exit
        }
      } catch {
        // invalid selector, skip
      }
    }

    if (score >= HEURISTIC_THRESHOLD) {
      return {
        detected: true,
        confidence: "heuristic",
        domain: hostname,
        score,
        signals,
      };
    }

    return { detected: false, confidence: "none", domain: hostname, score, signals };
  }

  // -------------------------------------------------------------------------
  // Report to service worker
  // -------------------------------------------------------------------------

  function reportDetection(result) {
    if (!result.detected) return;

    // Check if capture scripts are already loaded (from manifest content_scripts)
    if (window.__PCE_BRIDGE_LOADED) {
      console.debug("[PCE Detector] Capture scripts already loaded via manifest, skipping injection.");
      return;
    }

    chrome.runtime.sendMessage(
      {
        type: "PCE_AI_PAGE_DETECTED",
        payload: {
          url: location.href,
          domain: result.domain,
          confidence: result.confidence,
          score: result.score,
          signals: result.signals,
        },
      },
      (resp) => {
        if (chrome.runtime.lastError) {
          console.debug("[PCE Detector] Could not reach service worker:", chrome.runtime.lastError.message);
          return;
        }
        if (resp?.injected) {
          console.log(`[PCE Detector] Capture scripts injected for ${result.domain} (score=${result.score})`);
        }
      }
    );
  }

  // -------------------------------------------------------------------------
  // Run detection
  // -------------------------------------------------------------------------

  function runDetection() {
    const result = detectAIPage();
    reportDetection(result);
  }

  // Initial scan after DOM is ready
  if (document.readyState === "complete" || document.readyState === "interactive") {
    // Small delay to let SPA frameworks render
    setTimeout(runDetection, 1500);
  } else {
    document.addEventListener("DOMContentLoaded", () => {
      setTimeout(runDetection, 1500);
    });
  }

  // Re-check on SPA navigation (URL changes without page reload)
  let lastUrl = location.href;
  const navCheck = setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      // Re-scan after navigation settles
      setTimeout(runDetection, 2000);
    }
  }, 3000);

  // Expose for testing
  window.__PCE_DETECTOR = { detectAIPage, KNOWN_AI_DOMAINS };
})();
