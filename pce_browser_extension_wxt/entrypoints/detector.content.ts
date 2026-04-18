/**
 * PCE — Lightweight AI-page detector content script.
 *
 * TypeScript port of ``content_scripts/detector.js`` (P2.5 Phase 3b
 * batch 5).
 *
 * Runs on ALL pages (via ``<all_urls>`` match + ``exclude_matches``
 * for the 13 covered sites that already carry their own extractor).
 * Quickly decides if the current page is an AI tool / chat interface.
 * If detected, posts a ``PCE_AI_PAGE_DETECTED`` message to the
 * service worker, which then dynamically injects the full capture
 * pipeline (bridge + behavior_tracker + universal_extractor).
 *
 * Design goals:
 *   - Minimal overhead: bail out fast on non-AI pages.
 *   - No DOM mutation observers — a single quick scan, with a
 *     re-scan after SPA navigation.
 *   - No dependence on any shared util: the detector runs standalone
 *     (it's often the first PCE script on the page).
 */

declare global {
  interface Window {
    __PCE_DETECTOR_LOADED?: boolean;
    __PCE_DETECTOR?: DetectorHandle;
    __PCE_BRIDGE_ACTIVE?: boolean;
    __PCE_BRIDGE_LOADED?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Tables + types (module-scope for testability)
// ---------------------------------------------------------------------------

export const KNOWN_AI_DOMAINS: ReadonlySet<string> = new Set([
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
  "www.kimi.com",
  "kimi.com",
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
  "chat.z.ai",
  "chatglm.cn",
  "manus.im",
  "yiyan.baidu.com",
  "tongyi.aliyun.com",
  "xinghuo.xfyun.cn",
  "tiangong.kunlun.com",
]);

export const KNOWN_AI_PATH_PREFIXES: readonly string[] = [
  "/chat",
  "/c/",
  "/conversation",
  "/playground",
  "/assistant",
];

export interface DOMSignal {
  selector: string;
  weight: number;
}

export const DOM_SIGNALS: readonly DOMSignal[] = [
  // Input areas typical of chat UIs
  { selector: 'textarea[placeholder*="message" i]', weight: 3 },
  { selector: 'textarea[placeholder*="ask" i]', weight: 3 },
  { selector: 'textarea[placeholder*="chat" i]', weight: 3 },
  { selector: 'textarea[placeholder*="prompt" i]', weight: 3 },
  { selector: 'textarea[placeholder*="type" i]', weight: 1 },
  {
    selector: '[contenteditable="true"][data-placeholder*="message" i]',
    weight: 3,
  },
  {
    selector: '[contenteditable="true"][data-placeholder*="ask" i]',
    weight: 3,
  },
  { selector: '[role="textbox"][aria-label*="message" i]', weight: 3 },

  // Chat message containers
  { selector: "[data-message-author-role]", weight: 5 },
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

  // Markdown-rendered content blocks
  { selector: ".prose.markdown", weight: 2 },
  { selector: ".markdown-body", weight: 1 },
  { selector: '[class*="code-block"]', weight: 1 },
];

export const TITLE_KEYWORDS: readonly string[] = [
  "chat",
  "ai",
  "assistant",
  "copilot",
  "gpt",
  "claude",
  "gemini",
  "llm",
  "model",
  "prompt",
  "conversation",
];

export const META_KEYWORDS: readonly string[] = [
  "ai chat",
  "chatbot",
  "language model",
  "ai assistant",
  "generative ai",
];

export const HEURISTIC_THRESHOLD = 6;

export type DetectionConfidence = "known" | "heuristic" | "none";

export interface DetectionResult {
  detected: boolean;
  confidence: DetectionConfidence;
  domain: string;
  score: number;
  signals: string[];
}

export interface DetectorHandle {
  detectAIPage: () => DetectionResult;
  KNOWN_AI_DOMAINS: ReadonlySet<string>;
}

// ---------------------------------------------------------------------------
// Pure detection logic (exported for testability)
// ---------------------------------------------------------------------------

export function detectAIPage(
  doc: Document = document,
  hostname: string = location.hostname,
  pathname: string = location.pathname,
): DetectionResult {
  const lowerPath = pathname.toLowerCase();

  // Fast path: known AI domain.
  if (KNOWN_AI_DOMAINS.has(hostname)) {
    return {
      detected: true,
      confidence: "known",
      domain: hostname,
      score: 100,
      signals: ["known_domain"],
    };
  }

  let score = 0;
  const signals: string[] = [];

  // URL path hints
  for (const prefix of KNOWN_AI_PATH_PREFIXES) {
    if (lowerPath.startsWith(prefix)) {
      score += 2;
      signals.push(`path:${prefix}`);
      break;
    }
  }

  // Title keywords
  const title = (doc.title || "").toLowerCase();
  for (const kw of TITLE_KEYWORDS) {
    if (title.includes(kw)) {
      score += 1;
      signals.push(`title:${kw}`);
      break;
    }
  }

  // Meta description keywords
  const metaDesc = (
    doc
      .querySelector('meta[name="description"]')
      ?.getAttribute("content") || ""
  ).toLowerCase();
  for (const kw of META_KEYWORDS) {
    if (metaDesc.includes(kw)) {
      score += 2;
      signals.push(`meta:${kw}`);
      break;
    }
  }

  // DOM signals — early-exit once the threshold is met.
  for (const { selector, weight } of DOM_SIGNALS) {
    try {
      if (doc.querySelector(selector)) {
        score += weight;
        signals.push(`dom:${selector.slice(0, 40)}`);
        if (score >= HEURISTIC_THRESHOLD) break;
      }
    } catch {
      // Invalid selector on this document; skip.
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

  return {
    detected: false,
    confidence: "none",
    domain: hostname,
    score,
    signals,
  };
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: ["<all_urls>"],
  excludeMatches: [
    "https://chatgpt.com/*",
    "https://chat.openai.com/*",
    "https://claude.ai/*",
    "https://gemini.google.com/*",
    "https://chat.deepseek.com/*",
    "https://aistudio.google.com/*",
    "https://www.perplexity.ai/*",
    "https://copilot.microsoft.com/*",
    "https://poe.com/*",
    "https://grok.com/*",
    "https://huggingface.co/chat/*",
    "https://manus.im/*",
    "https://chat.z.ai/*",
    "https://chat.mistral.ai/*",
    "https://kimi.moonshot.cn/*",
    "https://www.kimi.com/*",
    "https://kimi.com/*",
  ],
  runAt: "document_idle",
  main() {
    if (window.__PCE_DETECTOR_LOADED) return;
    window.__PCE_DETECTOR_LOADED = true;

    function reportDetection(result: DetectionResult): void {
      if (!result.detected) return;
      if (window.__PCE_BRIDGE_ACTIVE || window.__PCE_BRIDGE_LOADED) {
        console.debug(
          "[PCE Detector] Capture scripts already loaded via manifest, skipping injection.",
        );
        return;
      }
      try {
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
          (resp: unknown) => {
            if (chrome.runtime.lastError) {
              console.debug(
                "[PCE Detector] Could not reach service worker:",
                chrome.runtime.lastError.message,
              );
              return;
            }
            const r = resp as { injected?: boolean } | null;
            if (r?.injected) {
              console.log(
                `[PCE Detector] Capture scripts injected for ${result.domain} (score=${result.score})`,
              );
            }
          },
        );
      } catch (err) {
        console.debug(
          "[PCE Detector] sendMessage threw:",
          (err as Error).message,
        );
      }
    }

    const runDetection = (): void => {
      const result = detectAIPage();
      reportDetection(result);
    };

    if (
      document.readyState === "complete" ||
      document.readyState === "interactive"
    ) {
      // Small delay to let SPA frameworks render.
      setTimeout(runDetection, 1500);
    } else {
      document.addEventListener("DOMContentLoaded", () => {
        setTimeout(runDetection, 1500);
      });
    }

    // Re-check on SPA navigation (URL changes without page reload).
    let lastUrl = location.href;
    setInterval(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        setTimeout(runDetection, 2000);
      }
    }, 3000);

    // Expose for testing / introspection.
    window.__PCE_DETECTOR = { detectAIPage, KNOWN_AI_DOMAINS };
  },
});
