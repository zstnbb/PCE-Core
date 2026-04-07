/**
 * PCE – AI Traffic Pattern Matching
 *
 * Identifies whether a network request/response is AI-related based on:
 *   - Known AI API domain list
 *   - URL path patterns
 *   - Request body field signatures
 *   - Response content-type and body patterns
 *
 * Designed to run in the PAGE context (injected via <script>).
 * Communicates results back via window.postMessage.
 */

// ---------------------------------------------------------------------------
// Known AI API domains
// ---------------------------------------------------------------------------

const AI_API_DOMAINS = new Set([
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
  // Zhipu / ChatGLM
  "open.bigmodel.cn",
  // Baidu / ERNIE
  "aip.baidubce.com",
  // Alibaba / Qwen
  "dashscope.aliyuncs.com",
  // Microsoft / Copilot
  "copilot.microsoft.com",
  "api.githubcopilot.com",
  "github.com",
  // Local
  "localhost",
  "127.0.0.1",
]);

// ---------------------------------------------------------------------------
// URL path patterns that strongly suggest AI API calls
// ---------------------------------------------------------------------------

const AI_PATH_PATTERNS = [
  /\/v1\/chat\/completions/,
  /\/v1\/completions/,
  /\/v1\/embeddings/,
  /\/v1\/images/,
  /\/v1\/audio/,
  /\/v1\/messages/,          // Anthropic
  /\/chat\/completions/,
  /\/api\/conversation/,     // ChatGPT web
  /\/api\/generate/,         // Ollama
  /\/api\/chat/,             // Ollama
  /\/backend-api\/conversation/, // ChatGPT internal
  /\/api\/append-message/,   // Claude web
  /\/generateContent/,       // Google Gemini API
  /\/streamGenerateContent/, // Google Gemini API streaming
];

// ---------------------------------------------------------------------------
// Request body field signatures
// ---------------------------------------------------------------------------

const AI_REQUEST_FIELDS = {
  high: [
    // High confidence: these field combos almost certainly mean AI
    ["model", "messages"],
    ["model", "prompt"],
    ["model", "input"],
    ["contents"],             // Google Gemini format
  ],
  medium: [
    ["prompt", "max_tokens"],
    ["prompt", "temperature"],
    ["messages"],
  ],
};

// ---------------------------------------------------------------------------
// Provider extraction from host
// ---------------------------------------------------------------------------

const HOST_TO_PROVIDER = {
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
  "open.bigmodel.cn": "zhipu",
  "aip.baidubce.com": "baidu",
  "dashscope.aliyuncs.com": "alibaba",
  "copilot.microsoft.com": "microsoft",
  "api.githubcopilot.com": "github",
  "localhost": "local",
  "127.0.0.1": "local",
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Check if a URL is likely an AI API endpoint.
 * Returns { isAI: boolean, confidence: 'high'|'medium'|'low'|'none', provider: string|null }
 */
function matchUrl(url) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname;
    const path = parsed.pathname;

    // Fast path: known domain
    if (AI_API_DOMAINS.has(host)) {
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
          provider: _guessProviderFromHost(host),
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

/**
 * Check if a request body looks like an AI API call.
 * @param {object|null} bodyObj - Parsed JSON body (or null if not JSON)
 * Returns { isAI: boolean, confidence: 'high'|'medium'|'none', model: string|null }
 */
function matchRequestBody(bodyObj) {
  if (!bodyObj || typeof bodyObj !== "object") {
    return { isAI: false, confidence: "none", model: null };
  }

  const keys = Object.keys(bodyObj);

  // High confidence
  for (const combo of AI_REQUEST_FIELDS.high) {
    if (combo.every((k) => keys.includes(k))) {
      return {
        isAI: true,
        confidence: "high",
        model: bodyObj.model || null,
      };
    }
  }

  // Medium confidence
  for (const combo of AI_REQUEST_FIELDS.medium) {
    if (combo.every((k) => keys.includes(k))) {
      return {
        isAI: true,
        confidence: "medium",
        model: bodyObj.model || null,
      };
    }
  }

  return { isAI: false, confidence: "none", model: null };
}

/**
 * Check if a response content-type suggests AI streaming.
 */
function isStreamingResponse(contentType) {
  if (!contentType) return false;
  return (
    contentType.includes("text/event-stream") ||
    contentType.includes("application/x-ndjson")
  );
}

/**
 * Combined check: is this request AI-related?
 * @param {string} url
 * @param {object|null} bodyObj - parsed request body
 * Returns { isAI, confidence, provider, model, host, path }
 */
function isAIRequest(url, bodyObj) {
  const urlMatch = matchUrl(url);
  const bodyMatch = matchRequestBody(bodyObj);

  // Either URL or body match is enough
  if (urlMatch.isAI || bodyMatch.isAI) {
    return {
      isAI: true,
      confidence: urlMatch.confidence === "high" || bodyMatch.confidence === "high"
        ? "high"
        : "medium",
      provider: urlMatch.provider || "unknown",
      model: bodyMatch.model || null,
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

// Best-effort provider guess from hostname
function _guessProviderFromHost(host) {
  if (!host) return "unknown";
  for (const [domain, provider] of Object.entries(HOST_TO_PROVIDER)) {
    if (host.includes(domain)) return provider;
  }
  return "unknown";
}

// Export for use by network_interceptor.js (both run in page context)
if (typeof window !== "undefined") {
  window.__PCE_AI_PATTERNS = {
    matchUrl,
    matchRequestBody,
    isStreamingResponse,
    isAIRequest,
    AI_API_DOMAINS,
    HOST_TO_PROVIDER,
  };
}
