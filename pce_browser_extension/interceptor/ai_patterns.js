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
  // Chinese AI providers
  /\/api\/paas\/.*\/chat\/completions/, // Zhipu OpenAI-compatible
  /\/chatglm\/.*\/stream/,             // ChatGLM streaming
  /\/assistant\/stream/,               // ChatGLM / generic assistant streaming
  /\/erniebot\//,                      // Baidu ERNIE Bot
  /\/rpc\/2\.0\/ai_custom\//,          // Baidu AI custom
  /\/api\/v\d+\/chat\//,               // Generic versioned chat API
  /\/compatible-mode\/v\d+\/chat/,     // Zhipu compatible mode
  /\/api\/append-message/,   // Claude web
  /\/generateContent/,       // Google Gemini API
  /\/streamGenerateContent/, // Google Gemini API streaming
  // Embedded AI paths
  /\/api\/v3\/getCompletion/,    // Notion AI
  /\/api\/v3\/ai\//,             // Notion AI
  /\/v1beta\/models\/.+:generateContent/, // Gemini beta
  /\/v1beta\/models\/.+:streamGenerateContent/, // Gemini beta streaming
  /\/_\/smart_?compose/i,        // Gmail Smart Compose
  /\/assist\//,                  // Generic AI assist endpoints
];

// ---------------------------------------------------------------------------
// Web UI domains vs pure API domains
// Web UI domains serve pages + telemetry + AI calls on the same host.
// We only capture requests whose path matches AI_PATH_PATTERNS or
// WEB_UI_AI_PATHS, and skip known noise paths.
// Pure API domains (api.openai.com etc.) capture everything.
// ---------------------------------------------------------------------------

const WEB_UI_DOMAINS = new Set([
  "chatgpt.com",
  "chat.openai.com",
  "claude.ai",
  "chat.deepseek.com",
  "gemini.google.com",
  "poe.com",
  "grok.com",
  "kimi.moonshot.cn",
  "copilot.microsoft.com",
  "huggingface.co",
  "www.perplexity.ai",
  // Zhipu / ChatGLM / Z.ai
  "chat.z.ai",
  "chatglm.cn",
  "chat.zhipuai.cn",
  // Embedded AI hosts (these serve both normal + AI traffic)
  "www.notion.so",
  "notion.so",
  "docs.google.com",
  "mail.google.com",
  "v0.dev",
]);

const WEB_UI_AI_PATHS = [
  /\/backend-api\/conversation$/,      // ChatGPT conversation POST
  /\/backend-api\/conversation\//,     // ChatGPT conversation/<id>
  /\/api\/chat\/completions/,          // DeepSeek API
  /\/api\/append-message/,             // Claude
  /\/api\/retry-message/,              // Claude
  /\/api\/organizations\/.+\/chat_conversations/, // Claude
  // Notion AI
  /\/api\/v3\/getCompletion/,          // Notion AI completion
  /\/api\/v3\/ai\//,                   // Notion AI endpoints
  /\/api\/v3\/runAIBlock/,             // Notion AI block
  // Google Docs / Gmail embedded AI
  /\/document\/d\/.+\/batchUpdate/,    // Docs batch update (may contain AI)
  /\/_\/smart_?compose/i,              // Gmail Smart Compose
  /\/assist/,                          // Gmail "Help me write"
  /\/v0\/chat/,                        // v0.dev chat
  // Zhipu / ChatGLM / Z.ai
  /\/api\/paas\/.*\/chat\/completions/,  // Zhipu OpenAI-compatible
  /\/chatglm\/.*\/stream/,               // ChatGLM streaming
  /\/assistant\/stream/,                 // ChatGLM assistant stream
  /\/api\/chat[\/-]/,                    // Generic chat endpoint
  /\/api\/assistant\//,                  // Generic assistant endpoint
];

const WEB_UI_NOISE_PATHS = [
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

const AI_REQUEST_FIELDS = {
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
      // For web UI domains, filter out noise paths
      if (WEB_UI_DOMAINS.has(host)) {
        // Check if this is a known noise path → skip
        if (WEB_UI_NOISE_PATHS.some((re) => re.test(path))) {
          return { isAI: false, confidence: "none", provider: null, host, path };
        }
        // Check if this matches a known AI path → high confidence
        if (WEB_UI_AI_PATHS.some((re) => re.test(path)) ||
            AI_PATH_PATTERNS.some((re) => re.test(path))) {
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
    WEB_UI_DOMAINS,
    HOST_TO_PROVIDER,
  };
}
