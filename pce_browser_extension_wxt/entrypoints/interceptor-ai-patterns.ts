// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — AI Traffic Pattern Matching (page-context).
 *
 * TypeScript port of ``interceptor/ai_patterns.js`` (P2.5 Phase 2).
 * Phase 3a refactor: the pattern tables and matcher functions are
 * exported at module scope so they can be imported by Vitest unit
 * tests. The ``defineUnlistedScript`` entry point still runs inside
 * the page context and wires the same functions onto
 * ``window.__PCE_AI_PATTERNS`` exactly as before — behaviour is
 * byte-identical, just reshuffled for testability.
 *
 * Identifies whether a network request/response is AI-related based on:
 *   - Known AI API domain list
 *   - URL path patterns
 *   - Request body field signatures
 *   - Response content-type and body patterns
 *
 * Runs in the **page context** (injected via ``<script src>`` by
 * ``bridge.content.ts``), exposes its API as ``window.__PCE_AI_PATTERNS``
 * for ``network-interceptor.ts`` to consume at runtime.
 *
 * Intentionally self-contained — **no ESM imports** (at runtime) — so
 * this file remains a standalone bundle that loads before
 * ``network-interceptor``. Vitest-only imports from this file work
 * because they only pull the module-level exports, not the unlisted
 * script default export.
 */

// ---------------------------------------------------------------------------
// Public shapes (exported so network-interceptor.ts can type its runtime use)
// ---------------------------------------------------------------------------

export type Confidence = "high" | "medium" | "low" | "aggressive" | "aggressive-stream" | "none";

export interface UrlMatch {
  isAI: boolean;
  confidence: Confidence;
  provider: string | null;
  host: string | null;
  path: string | null;
}

export interface BodyMatch {
  isAI: boolean;
  confidence: Confidence;
  model: string | null;
}

export interface CombinedMatch {
  isAI: boolean;
  confidence: Confidence;
  provider: string | null;
  model: string | null;
  host: string | null;
  path: string | null;
}

export interface AIPatternsAPI {
  matchUrl(url: string): UrlMatch;
  matchRequestBody(bodyObj: unknown): BodyMatch;
  isStreamingResponse(contentType: string | null | undefined): boolean;
  isAIRequest(url: string, bodyObj: unknown): CombinedMatch;
  AI_API_DOMAINS: Set<string>;
  WEB_UI_DOMAINS: Set<string>;
  HOST_TO_PROVIDER: Record<string, string>;
}

declare global {
  interface Window {
    __PCE_AI_PATTERNS?: AIPatternsAPI;
  }
}

// ---------------------------------------------------------------------------
// Known AI API domains
// ---------------------------------------------------------------------------

export const AI_API_DOMAINS: Set<string> = new Set([
  // OpenAI
  "api.openai.com",
  "chatgpt.com",
  "chat.openai.com",
  // Anthropic
  "api.anthropic.com",
  "claude.ai",
  // Google
  "generativelanguage.googleapis.com",
  "gemini.google.com",
  // DeepSeek
  "api.deepseek.com",
  "chat.deepseek.com",
  // Groq
  "api.groq.com",
  // Together
  "api.together.xyz",
  // Fireworks
  "api.fireworks.ai",
  // OpenRouter
  "openrouter.ai",
  // Perplexity
  "api.perplexity.ai",
  "www.perplexity.ai",
  // Mistral
  "api.mistral.ai",
  // Cohere
  "api.cohere.ai",
  "api.cohere.com",
  // xAI / Grok
  "api.x.ai",
  "grok.com",
  // Poe
  "poe.com",
  // HuggingFace
  "api-inference.huggingface.co",
  "huggingface.co",
  // Moonshot / Kimi
  "api.moonshot.cn",
  "kimi.moonshot.cn",
  "www.kimi.com",
  "kimi.com",
  // Zhipu / ChatGLM / Z.ai
  "open.bigmodel.cn",
  "chat.z.ai",
  "chatglm.cn",
  "chat.zhipuai.cn",
  "maas.aminer.cn",
  // Baidu / ERNIE
  "aip.baidubce.com",
  // Alibaba / Qwen
  "dashscope.aliyuncs.com",
  // Microsoft / Copilot
  "copilot.microsoft.com",
  "api.githubcopilot.com",
  "github.com",
  // Notion AI
  "www.notion.so",
  "notion.so",
  // Google embedded AI (Docs, Gmail, Sheets)
  "docs.google.com",
  "mail.google.com",
  "alkali-pa.clients6.google.com",
  "smartcompose-pa.googleapis.com",
  "content-push.googleapis.com",
  // Cursor / AI code editors
  "api2.cursor.sh",
  // Vercel AI
  "v0.dev",
  // Local
  "localhost",
  "127.0.0.1",
]);

// ---------------------------------------------------------------------------
// URL path patterns that strongly suggest AI API calls
// ---------------------------------------------------------------------------

export const AI_PATH_PATTERNS: RegExp[] = [
  /\/v1\/chat\/completions/,
  /\/v1\/completions/,
  /\/v1\/embeddings/,
  /\/v1\/images/,
  /\/v1\/audio/,
  /\/v1\/messages/,                // Anthropic
  /\/chat\/completions/,
  /\/api\/conversation/,            // ChatGPT web
  /\/api\/generate/,                // Ollama
  /\/api\/chat/,                    // Ollama
  /\/backend-api\/conversation/,    // ChatGPT internal
  // Chinese AI providers
  /\/api\/paas\/.*\/chat\/completions/, // Zhipu OpenAI-compatible
  /\/chatglm\/.*\/stream/,              // ChatGLM streaming
  /\/assistant\/stream/,                // ChatGLM / generic assistant streaming
  /\/erniebot\//,                       // Baidu ERNIE Bot
  /\/rpc\/2\.0\/ai_custom\//,           // Baidu AI custom
  /\/api\/v\d+\/chat\//,                // Generic versioned chat API
  /\/compatible-mode\/v\d+\/chat/,      // Zhipu compatible mode
  /\/api\/append-message/,              // Claude web
  /\/generateContent/,                  // Google Gemini API
  /\/streamGenerateContent/,            // Google Gemini API streaming
  // Embedded AI paths
  /\/api\/v3\/getCompletion/,           // Notion AI
  /\/api\/v3\/ai\//,                    // Notion AI
  /\/v1beta\/models\/.+:generateContent/,       // Gemini beta
  /\/v1beta\/models\/.+:streamGenerateContent/, // Gemini beta streaming
  /\/_\/smart_?compose/i,               // Gmail Smart Compose
  /\/assist\//,                         // Generic AI assist endpoints
];

// ---------------------------------------------------------------------------
// Web UI domains vs pure API domains.
// Web UI domains serve pages + telemetry + AI calls on the same host.
// We only capture requests whose path matches AI_PATH_PATTERNS or
// WEB_UI_AI_PATHS, and skip known noise paths.
// Pure API domains (api.openai.com etc.) capture everything.
// ---------------------------------------------------------------------------

export const WEB_UI_DOMAINS: Set<string> = new Set([
  "chatgpt.com",
  "chat.openai.com",
  "claude.ai",
  "chat.deepseek.com",
  "gemini.google.com",
  "poe.com",
  "grok.com",
  "kimi.moonshot.cn",
  "www.kimi.com",
  "kimi.com",
  "copilot.microsoft.com",
  "huggingface.co",
  "www.perplexity.ai",
  // Zhipu / ChatGLM / Z.ai
  "chat.z.ai",
  "chatglm.cn",
  "chat.zhipuai.cn",
  // Embedded AI hosts
  "www.notion.so",
  "notion.so",
  "docs.google.com",
  "mail.google.com",
  "v0.dev",
]);

export const WEB_UI_AI_PATHS: RegExp[] = [
  /\/backend-api\/conversation$/,              // ChatGPT conversation POST
  /\/backend-api\/conversation\//,             // ChatGPT conversation/<id>
  /\/api\/chat\/completions/,                  // DeepSeek API
  /\/api\/append-message/,                     // Claude
  /\/api\/retry-message/,                      // Claude
  /\/api\/organizations\/.+\/chat_conversations/, // Claude
  // Notion AI
  /\/api\/v3\/getCompletion/,
  /\/api\/v3\/ai\//,
  /\/api\/v3\/runAIBlock/,
  // Google Docs / Gmail embedded AI
  /\/document\/d\/.+\/batchUpdate/,
  /\/_\/smart_?compose/i,
  /\/assist/,
  /\/v0\/chat/,                                // v0.dev chat
  // Zhipu / ChatGLM / Z.ai
  /\/api\/paas\/.*\/chat\/completions/,
  /\/chatglm\/.*\/stream/,
  /\/assistant\/stream/,
  /\/api\/chat[\/-]/,
  /\/api\/assistant\//,
];

export const WEB_UI_NOISE_PATHS: RegExp[] = [
  /\/sentinel\//,
  /\/ces\//,
  /\/statsc\//,
  /\/analytics/,
  /\/telemetry/,
  /\/lat\//,
  /\/register-websocket/,
  /\/backend-api\/f\//,
  /\/backend-api\/me/,
  /\/backend-api\/settings/,
  /\/backend-api\/accounts/,
  /\/backend-api\/models/,
  /\/backend-api\/prompt_library/,
  /\/backend-api\/compliance/,
  /\/backend-api\/connectors/,
  /\/backend-api\/gizmos/,
  /\/backend-api\/files/,
  /\/backend-api\/share/,
  // Notion noise
  /\/api\/v3\/getUploadFileUrl/,
  /\/api\/v3\/syncRecordValues/,
  /\/api\/v3\/getAssetsJson/,
  /\/api\/v3\/getUserAnalyticsSettings/,
  /\/api\/v3\/getSpaces/,
  /\/api\/v3\/loadPageChunk/,
  // Google Docs/Gmail noise
  /\/ListActivities/,
  /\/GetDocument/,
  /\/GetComments/,
  /\/logImpressions/,
  /\/v1\/initialize/,
  /\/v1\/t$/,
  /\/v1\/m$/,
  /\/_next\//,
  /\/assets\//,
  /\/fe-static\//,
  /\.js$/,
  /\.css$/,
  /\.woff/,
  /\.png$/,
  /\.svg$/,
  /\.ico$/,
];

// ---------------------------------------------------------------------------
// Request body field signatures
// ---------------------------------------------------------------------------

export const AI_REQUEST_FIELDS: { high: string[][]; medium: string[][] } = {
  high: [
    // High confidence: these field combos almost certainly mean AI
    ["model", "messages"],
    ["model", "prompt"],
    ["model", "input"],
    ["contents"],             // Google Gemini format
    ["aiSessionId"],          // Notion AI session
  ],
  medium: [
    ["prompt", "max_tokens"],
    ["prompt", "temperature"],
    ["messages"],
    ["context", "prompt"],    // Notion AI context+prompt
    ["candidatesCount"],      // Gmail Smart Compose
  ],
};

// ---------------------------------------------------------------------------
// Provider extraction from host
// ---------------------------------------------------------------------------

export const HOST_TO_PROVIDER: Record<string, string> = {
  "api.openai.com": "openai",
  "chatgpt.com": "openai",
  "chat.openai.com": "openai",
  "api.anthropic.com": "anthropic",
  "claude.ai": "anthropic",
  "generativelanguage.googleapis.com": "google",
  "gemini.google.com": "google",
  "api.deepseek.com": "deepseek",
  "chat.deepseek.com": "deepseek",
  "api.groq.com": "groq",
  "api.together.xyz": "together",
  "api.fireworks.ai": "fireworks",
  "openrouter.ai": "openrouter",
  "api.perplexity.ai": "perplexity",
  "www.perplexity.ai": "perplexity",
  "api.mistral.ai": "mistral",
  "api.cohere.ai": "cohere",
  "api.cohere.com": "cohere",
  "api.x.ai": "xai",
  "grok.com": "xai",
  "poe.com": "poe",
  "api-inference.huggingface.co": "huggingface",
  "huggingface.co": "huggingface",
  "api.moonshot.cn": "moonshot",
  "kimi.moonshot.cn": "moonshot",
  "www.kimi.com": "moonshot",
  "kimi.com": "moonshot",
  "open.bigmodel.cn": "zhipu",
  "chat.z.ai": "zhipu",
  "chatglm.cn": "zhipu",
  "chat.zhipuai.cn": "zhipu",
  "maas.aminer.cn": "zhipu",
  "aip.baidubce.com": "baidu",
  "dashscope.aliyuncs.com": "alibaba",
  "copilot.microsoft.com": "microsoft",
  "api.githubcopilot.com": "github",
  "www.notion.so": "notion",
  "notion.so": "notion",
  "docs.google.com": "google",
  "mail.google.com": "google",
  "alkali-pa.clients6.google.com": "google",
  "smartcompose-pa.googleapis.com": "google",
  "content-push.googleapis.com": "google",
  "api2.cursor.sh": "cursor",
  "v0.dev": "vercel",
  "localhost": "local",
  "127.0.0.1": "local",
};

// ---------------------------------------------------------------------------
// Best-effort provider guess from hostname (substring match)
// ---------------------------------------------------------------------------

export function guessProviderFromHost(host: string | null): string {
  if (!host) return "unknown";
  for (const [domain, provider] of Object.entries(HOST_TO_PROVIDER)) {
    if (host.includes(domain)) return provider;
  }
  return "unknown";
}

// ---------------------------------------------------------------------------
// Public matchers
// ---------------------------------------------------------------------------

export function matchUrl(url: string): UrlMatch {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname;
    const path = parsed.pathname;

    // Fast path: known domain
    if (AI_API_DOMAINS.has(host)) {
      // For web UI domains, filter out noise paths
      if (WEB_UI_DOMAINS.has(host)) {
        if (WEB_UI_NOISE_PATHS.some((re) => re.test(path))) {
          return { isAI: false, confidence: "none", provider: null, host, path };
        }
        if (
          WEB_UI_AI_PATHS.some((re) => re.test(path)) ||
          AI_PATH_PATTERNS.some((re) => re.test(path))
        ) {
          return {
            isAI: true,
            confidence: "high",
            provider: HOST_TO_PROVIDER[host] || "unknown",
            host,
            path,
          };
        }
        // Unknown path on web UI domain → skip (conservative)
        return { isAI: false, confidence: "none", provider: null, host, path };
      }

      // Pure API domain → everything is AI-related
      return {
        isAI: true,
        confidence: "high",
        provider: HOST_TO_PROVIDER[host] || "unknown",
        host,
        path,
      };
    }

    // Path pattern match on unknown domain
    for (const pattern of AI_PATH_PATTERNS) {
      if (pattern.test(path)) {
        return {
          isAI: true,
          confidence: "medium",
          provider: guessProviderFromHost(host),
          host,
          path,
        };
      }
    }

    return { isAI: false, confidence: "none", provider: null, host, path };
  } catch {
    return { isAI: false, confidence: "none", provider: null, host: null, path: null };
  }
}

export function matchRequestBody(bodyObj: unknown): BodyMatch {
  if (!bodyObj || typeof bodyObj !== "object") {
    return { isAI: false, confidence: "none", model: null };
  }

  const keys = Object.keys(bodyObj as Record<string, unknown>);

  // High confidence
  for (const combo of AI_REQUEST_FIELDS.high) {
    if (combo.every((k) => keys.includes(k))) {
      return {
        isAI: true,
        confidence: "high",
        model: (bodyObj as Record<string, unknown>).model as string | null || null,
      };
    }
  }

  // Medium confidence
  for (const combo of AI_REQUEST_FIELDS.medium) {
    if (combo.every((k) => keys.includes(k))) {
      return {
        isAI: true,
        confidence: "medium",
        model: (bodyObj as Record<string, unknown>).model as string | null || null,
      };
    }
  }

  return { isAI: false, confidence: "none", model: null };
}

export function isStreamingResponse(contentType: string | null | undefined): boolean {
  if (!contentType) return false;
  return (
    contentType.includes("text/event-stream") ||
    contentType.includes("application/x-ndjson")
  );
}

export function isAIRequest(url: string, bodyObj: unknown): CombinedMatch {
  const urlMatch = matchUrl(url);
  const bodyMatch = matchRequestBody(bodyObj);

  // Either URL or body match is enough
  if (urlMatch.isAI || bodyMatch.isAI) {
    const confidence: Confidence =
      urlMatch.confidence === "high" || bodyMatch.confidence === "high"
        ? "high"
        : "medium";
    return {
      isAI: true,
      confidence,
      provider: urlMatch.provider || "unknown",
      model: bodyMatch.model,
      host: urlMatch.host,
      path: urlMatch.path,
    };
  }

  return {
    isAI: false,
    confidence: "none",
    provider: null,
    model: null,
    host: urlMatch.host,
    path: urlMatch.path,
  };
}

// ---------------------------------------------------------------------------
// WXT unlisted script entry point
//
// Runs in the page context. Publishes the module-level API to
// `window.__PCE_AI_PATTERNS` so `interceptor-network.ts` can consume
// it without needing an ESM import across scripts.
// ---------------------------------------------------------------------------

export default defineUnlistedScript(() => {
  if (typeof window !== "undefined") {
    window.__PCE_AI_PATTERNS = {
      matchUrl,
      matchRequestBody,
      isStreamingResponse,
      isAIRequest,
      AI_API_DOMAINS,
      WEB_UI_DOMAINS,
      HOST_TO_PROVIDER,
    };
  }
});
