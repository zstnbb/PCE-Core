/**
 * PCE — Generic content script (Mistral + Kimi).
 *
 * TypeScript port of ``content_scripts/generic.js`` (P2.5 Phase 3b
 * batch 5).
 *
 * A best-effort content script for AI chat sites that don't have a
 * dedicated TS extractor. Currently matches ``chat.mistral.ai`` and
 * the three Kimi hosts. Heuristic DOM extraction with a 9-selector
 * priority ladder + a last-resort ``[class*=turn]`` fallback.
 *
 * ``captureMode: "full"`` — the legacy script always resent the whole
 * conversation when the fingerprint changed (matches the behaviour of
 * ``claude.content.ts``). The provider label is derived from the
 * hostname via ``HOST_PROVIDER_MAP`` so captures carry a meaningful
 * identifier.
 */

import {
  createCaptureRuntime,
  type ExtractedMessage,
} from "../utils/capture-runtime";

declare global {
  interface Window {
    __PCE_GENERIC_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Generic-specific helpers (module-scope for testability)
// ---------------------------------------------------------------------------

/**
 * Map hostname → canonical provider slug. Falls back to
 * ``hostname.split(".")[0]`` when unknown.
 */
export const HOST_PROVIDER_MAP: Readonly<Record<string, string>> = {
  "gemini.google.com": "google",
  "aistudio.google.com": "google",
  "chat.deepseek.com": "deepseek",
  "www.perplexity.ai": "perplexity",
  "grok.com": "xai",
  "poe.com": "poe",
  "manus.im": "manus",
  "chat.z.ai": "zhipu",
  "chatglm.cn": "zhipu",
  "chat.zhipuai.cn": "zhipu",
  "kimi.moonshot.cn": "moonshot",
  "www.kimi.com": "moonshot",
  "kimi.com": "moonshot",
  "chat.mistral.ai": "mistral",
};

/**
 * Hosts that have a dedicated TS extractor — the generic extractor
 * should bail out immediately when loaded on these (defensive guard
 * that mirrors legacy behaviour).
 */
const DEDICATED_HOSTS = new Set<string>([
  "grok.com",
  "chat.z.ai",
  "aistudio.google.com",
  "manus.im",
  "chatgpt.com",
  "chat.openai.com",
  "claude.ai",
  "gemini.google.com",
  "chat.deepseek.com",
  "www.perplexity.ai",
  "poe.com",
  "copilot.microsoft.com",
  "huggingface.co",
]);

export function isDedicatedHost(hostname: string): boolean {
  return DEDICATED_HOSTS.has(hostname);
}

export function resolveProvider(
  hostname: string = location.hostname,
): string {
  const direct = HOST_PROVIDER_MAP[hostname];
  if (direct) return direct;
  return hostname.split(".")[0] || "unknown";
}

export function resolveSourceName(
  hostname: string = location.hostname,
): string {
  return `${resolveProvider(hostname)}-web`;
}

export function getContainer(doc: Document = document): Element | null {
  return doc.querySelector("main") || doc.body;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeInnerText(el: any): string {
  if (!el) return "";
  if (typeof el.innerText === "string") return el.innerText;
  if (typeof el.textContent === "string") return el.textContent;
  return "";
}

/**
 * Determine role from element attributes + class names. Matches the
 * legacy ``detectRole`` byte-for-byte.
 */
export function detectRole(el: Element): "user" | "assistant" | "unknown" {
  const parts: string[] = [];
  const dataMessageAuthor = el.getAttribute("data-message-author");
  const dataAuthorRole = el.getAttribute("data-author-role");
  const dataRole = el.getAttribute("data-role");
  const cls = (el as Element).className;
  if (dataMessageAuthor) parts.push(dataMessageAuthor);
  if (dataAuthorRole) parts.push(dataAuthorRole);
  if (dataRole) parts.push(dataRole);
  if (cls) parts.push(cls.toString());
  const attrs = parts.join(" ").toLowerCase();

  if (
    attrs.includes("user") ||
    attrs.includes("human") ||
    attrs.includes("query")
  ) {
    return "user";
  }
  if (
    attrs.includes("assistant") ||
    attrs.includes("bot") ||
    attrs.includes("model") ||
    attrs.includes("ai") ||
    attrs.includes("response") ||
    attrs.includes("answer")
  ) {
    return "assistant";
  }
  return "unknown";
}

/**
 * Run through 9 selector strategies + 1 fallback. The selectors are
 * grouped by the sites they target (Kimi segment / Gemini / DeepSeek /
 * Perplexity / generic chat UIs). Returns the first non-empty list.
 */
export function extractMessages(
  doc: Document = document,
): ExtractedMessage[] {
  const selectors = [
    // Kimi segment-based layout
    ".segment.segment-user, .segment.segment-assistant",
    // Gemini message-content
    'message-content[class*="user"], message-content[class*="model"]',
    "[data-message-author], [data-author-role]",
    // DeepSeek
    '[class*="chat-message"], [class*="ChatMessage"]',
    // Perplexity
    '[class*="prose"], [class*="answer"]',
    // Generic patterns
    '[role="presentation"] [class*="message"]',
    '[class*="user-message"], [class*="bot-message"]',
    '[class*="human"], [class*="assistant"]',
    '[class*="query"], [class*="response"]',
  ];

  for (const selector of selectors) {
    try {
      const elements = doc.querySelectorAll(selector);
      if (elements.length === 0) continue;

      const collected: ExtractedMessage[] = [];
      elements.forEach((el) => {
        const text = safeInnerText(el).trim();
        if (!text || text.length < 3) return;
        const role = detectRole(el);
        collected.push({ role, content: text });
      });

      if (collected.length > 0) return collected;
    } catch {
      // Invalid selector on this site; skip
    }
  }

  // Last resort: any [class*=turn] / [class*=message-pair] / etc.
  const containers = doc.querySelectorAll(
    '[class*="turn"], [class*="message-pair"], [class*="conversation-item"]',
  );
  const fallback: ExtractedMessage[] = [];
  containers.forEach((el) => {
    const text = safeInnerText(el).trim();
    if (text && text.length > 10) {
      fallback.push({ role: detectRole(el), content: text });
    }
  });
  return fallback;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: [
    "https://chat.mistral.ai/*",
    "https://kimi.moonshot.cn/*",
    "https://www.kimi.com/*",
    "https://kimi.com/*",
  ],
  runAt: "document_start",
  main() {
    if (window.__PCE_GENERIC_ACTIVE) return;

    const hostname = window.location.hostname;
    if (isDedicatedHost(hostname)) {
      console.debug(
        `[PCE] Generic extractor disabled on dedicated host ${hostname}`,
      );
      return;
    }

    window.__PCE_GENERIC_ACTIVE = true;

    const provider = resolveProvider(hostname);
    const sourceName = resolveSourceName(hostname);
    console.log(
      `[PCE] Generic content script loaded for ${hostname} (provider: ${provider})`,
    );

    const runtime = createCaptureRuntime({
      provider,
      sourceName,
      siteName: `Generic:${provider}`,
      extractionStrategy: "generic-dom",
      captureMode: "full",

      debounceMs: 3000,
      pollIntervalMs: 5000,

      getContainer: () => getContainer(document),
      extractMessages: () => extractMessages(document),
      // Generic sites don't have a consistent URL structure for
      // conversations; defer to the shared `getSessionHint` from
      // utils/pce-dom via the `window.__PCE_EXTRACT` global injected
      // by the legacy helpers (until Phase 4), falling back to null.
      getSessionHint: () => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const extract: any = (window as any).__PCE_EXTRACT;
        if (extract && typeof extract.getSessionHint === "function") {
          try {
            return extract.getSessionHint() as string | null;
          } catch {
            return null;
          }
        }
        return null;
      },
      getModelName: () => null,
      hookHistoryApi: false,
    });

    document.addEventListener("pce-manual-capture", () => {
      runtime.triggerCapture();
    });

    if (
      document.readyState === "complete" ||
      document.readyState === "interactive"
    ) {
      runtime.start();
    } else {
      document.addEventListener("DOMContentLoaded", () => runtime.start());
    }
  },
});
