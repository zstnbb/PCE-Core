/**
 * PCE – Declarative Site Selector Configurations
 *
 * Central registry of CSS selectors for all supported AI chat sites.
 * Each config declares multiple candidate selectors in priority order —
 * the selector engine tries them top-to-bottom and uses the first that
 * matches.  When a site redesigns, just update or add selectors here
 * instead of rewriting extraction logic.
 *
 * Config schema:
 *   provider:    string    — canonical provider name
 *   sourceName:  string    — source identifier for captures
 *   container:   string[]  — chat container selectors (priority order)
 *   userMsg:     string[]  — user message element selectors
 *   assistantMsg:string[]  — assistant message element selectors
 *   turnPair:    string[]  — selectors for combined user+assistant turn containers
 *   streamingIndicator: string[]  — elements that signal active streaming
 *   roleAttr:    string[]  — attributes to check for role detection
 *   roleKeywords:object    — {user: string[], assistant: string[]} class/attr substrings
 *   sessionHintPattern: RegExp — extract session ID from URL pathname
 *   modelSelector: string[]— elements containing model name
 *
 * Loaded by pce_dom_utils.js / selector_engine.js at runtime.
 */

// eslint-disable-next-line no-unused-vars
const PCE_SITE_CONFIGS = {
  // =========================================================================
  // ChatGPT (chatgpt.com, chat.openai.com)
  // =========================================================================
  "chatgpt.com": {
    provider: "openai",
    sourceName: "chatgpt-web",
    container: [
      '[class*="react-scroll-to-bottom"]',
      'main [role="presentation"]',
      'main [role="log"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[data-message-author-role="user"]',
      '[data-testid^="conversation-turn"] [data-message-author-role="user"]',
    ],
    assistantMsg: [
      '[data-message-author-role="assistant"]',
      '[data-message-author-role="tool"]',
      '[data-testid^="conversation-turn"]:nth-child(even)',
    ],
    turnPair: [
      '[data-testid^="conversation-turn"]',
      "main article",
      "[data-message-id]",
      '[role="row"]',
      '[role="listitem"]',
    ],
    streamingIndicator: [
      '[class*="result-streaming"]',
      '[class*="streaming"]',
      'button[aria-label*="Stop"]',
      'button[data-testid*="stop"]',
      '[class*="agent-turn"] [class*="streaming"]',
    ],
    roleAttr: ["data-message-author-role"],
    roleKeywords: {
      user: ["user"],
      assistant: ["assistant", "tool", "system"],
    },
    sessionHintPattern: /\/c\/([a-f0-9-]+)/i,
    modelSelector: [
      '[class*="model"]',
      '[data-testid*="model"]',
    ],
  },

  // =========================================================================
  // Claude (claude.ai)
  // =========================================================================
  "claude.ai": {
    provider: "anthropic",
    sourceName: "claude-web",
    container: [
      '[class*="conversation-content"]',
      '[class*="chat-messages"]',
      '[class*="thread-content"]',
      '[role="log"]',
      "main .flex.flex-col",
      "main",
    ],
    userMsg: [
      '[data-testid="human-turn"]',
      '[data-testid*="user-message"]',
      '[class*="human-turn"]',
      ".font-user-message",
      '[data-role="user"]',
    ],
    assistantMsg: [
      '[data-testid="assistant-turn"]',
      '[data-testid*="assistant-message"]',
      '[class*="assistant-turn"]',
      ".font-claude-message",
      '[data-role="assistant"]',
    ],
    turnPair: [],
    streamingIndicator: [
      'button[aria-label*="Stop"]',
      '[class*="streaming"]',
      '[class*="is-streaming"]',
    ],
    roleAttr: ["data-testid", "data-role"],
    roleKeywords: {
      user: ["human", "user"],
      assistant: ["assistant", "claude"],
    },
    sessionHintPattern: /\/chat\/([a-f0-9-]+)/i,
    modelSelector: [
      '[class*="model-selector"]',
      '[data-testid*="model"]',
    ],
  },

  // =========================================================================
  // Gemini (gemini.google.com)
  // =========================================================================
  "gemini.google.com": {
    provider: "google",
    sourceName: "gemini-web",
    container: [
      ".conversation-container",
      'main [role="log"]',
      "main",
    ],
    userMsg: [
      "user-query",
      '[data-turn-role="user"]',
      '[class*="query-text"]',
      '[class*="query-content"]',
    ],
    assistantMsg: [
      "model-response",
      '[data-turn-role="model"]',
      '[class*="response-container"]',
      '[class*="model-response-text"]',
      "message-content",
    ],
    turnPair: [
      '[class*="turn"]',
      '[class*="Turn"]',
      ".conversation-container .turn-content",
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="streaming"]',
      'button[aria-label*="Stop"]',
      "mat-progress-bar",
    ],
    roleAttr: ["data-turn-role"],
    roleKeywords: {
      user: ["user", "query"],
      assistant: ["model", "response"],
    },
    sessionHintPattern: null,
    modelSelector: [
      '[class*="model-picker"]',
      '[class*="selected-model"]',
    ],
  },

  // =========================================================================
  // DeepSeek (chat.deepseek.com)
  // =========================================================================
  "chat.deepseek.com": {
    provider: "deepseek",
    sourceName: "deepseek-web",
    container: [
      '[class*="chat-container"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[data-role="user"]',
      '[data-message-role="user"]',
    ],
    assistantMsg: [
      ".ds-markdown",
      '[class*="ds-markdown"]',
      '[class*="markdown-body"]',
      ".chat-markdown",
      '[data-role="assistant"]',
    ],
    turnPair: [
      '[data-role]',
      '[data-message-role]',
      '[data-testid*="message"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="streaming"]',
      'button[aria-label*="Stop"]',
      ".ds-loading",
    ],
    roleAttr: ["data-role", "data-message-role"],
    roleKeywords: {
      user: ["user"],
      assistant: ["assistant", "markdown", "ds-markdown"],
    },
    sessionHintPattern: /\/chat\/([a-f0-9-]+)/i,
    modelSelector: [],
  },

  // =========================================================================
  // Perplexity (www.perplexity.ai)
  // =========================================================================
  "www.perplexity.ai": {
    provider: "perplexity",
    sourceName: "perplexity-web",
    container: [
      '[class*="thread"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[class*="query-text"]',
      '[class*="user-question"]',
      '[class*="question-text"]',
      '[data-testid*="query"]',
    ],
    assistantMsg: [
      '[class*="prose"]',
      '[class*="answer"]',
      '[class*="response-text"]',
      '[class*="markdown-content"]',
    ],
    turnPair: [
      '[class*="query-pair"]',
      '[class*="thread-item"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="streaming"]',
      '[class*="typing"]',
    ],
    roleAttr: [],
    roleKeywords: {
      user: ["query", "question", "user"],
      assistant: ["answer", "prose", "response"],
    },
    sessionHintPattern: /\/search\/([a-f0-9-]+)/i,
    modelSelector: [],
  },

  // =========================================================================
  // Grok (grok.com)
  // =========================================================================
  "grok.com": {
    provider: "xai",
    sourceName: "grok-web",
    container: [
      '[class*="conversation"]',
      '[class*="chat-container"]',
      "main",
    ],
    userMsg: [
      '[class*="user-message"]',
      '[data-role="user"]',
      '[class*="human"]',
    ],
    assistantMsg: [
      '[class*="assistant-message"]',
      '[class*="bot-message"]',
      '[data-role="assistant"]',
      '[class*="markdown"]',
    ],
    turnPair: [
      '[class*="message"]',
      '[class*="turn"]',
    ],
    streamingIndicator: [
      '[class*="streaming"]',
      '[class*="loading"]',
      'button[aria-label*="Stop"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["user", "human"],
      assistant: ["assistant", "bot", "grok"],
    },
    sessionHintPattern: /\/chat\/([a-f0-9-]+)/i,
    modelSelector: [],
  },

  // =========================================================================
  // Copilot (copilot.microsoft.com)
  // =========================================================================
  "copilot.microsoft.com": {
    provider: "microsoft",
    sourceName: "copilot-web",
    container: [
      '[class*="conversation"]',
      '[class*="chat-container"]',
      "main",
    ],
    userMsg: [
      '[data-content="user-message"]',
      '[class*="user-message"]',
      '[class*="human-message"]',
    ],
    assistantMsg: [
      '[data-content="bot-message"]',
      '[class*="bot-message"]',
      '[class*="ai-message"]',
      '[class*="response-message"]',
    ],
    turnPair: [
      '[class*="turn"]',
      '[class*="message-pair"]',
    ],
    streamingIndicator: [
      '[class*="typing-indicator"]',
      '[class*="loading"]',
    ],
    roleAttr: ["data-content"],
    roleKeywords: {
      user: ["user", "human"],
      assistant: ["bot", "ai", "copilot", "response"],
    },
    sessionHintPattern: null,
    modelSelector: [],
  },

  // =========================================================================
  // Poe (poe.com)
  // =========================================================================
  "poe.com": {
    provider: "poe",
    sourceName: "poe-web",
    container: [
      '[class*="ChatMessages"]',
      '[class*="chat-messages"]',
      "main",
    ],
    userMsg: [
      '[class*="Message_humanMessage"]',
      '[class*="human"]',
      '[data-role="user"]',
    ],
    assistantMsg: [
      '[class*="Message_botMessage"]',
      '[class*="bot"]',
      '[data-role="assistant"]',
      '[class*="markdown"]',
    ],
    turnPair: [
      '[class*="Message_row"]',
      '[class*="message"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="typing"]',
      'button[class*="stop"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["human", "user"],
      assistant: ["bot", "assistant"],
    },
    sessionHintPattern: /\/chat\/([a-zA-Z0-9]+)/i,
    modelSelector: [
      '[class*="BotName"]',
      '[class*="bot-name"]',
    ],
  },

  // =========================================================================
  // HuggingFace Chat (huggingface.co/chat)
  // =========================================================================
  "huggingface.co": {
    provider: "huggingface",
    sourceName: "huggingface-web",
    container: [
      '[class*="chat-container"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[class*="user"]',
      '[data-role="user"]',
    ],
    assistantMsg: [
      '[class*="assistant"]',
      '[data-role="assistant"]',
      '[class*="prose"]',
    ],
    turnPair: [
      '[class*="message"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="generating"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["user"],
      assistant: ["assistant"],
    },
    sessionHintPattern: /\/chat\/conversation\/([a-f0-9-]+)/i,
    modelSelector: [],
  },

  // =========================================================================
  // Kimi (kimi.com, kimi.moonshot.cn)
  // =========================================================================
  "kimi.com": {
    provider: "moonshot",
    sourceName: "kimi-web",
    container: [
      '[class*="chat"]',
      "main",
    ],
    userMsg: [
      ".segment.segment-user",
      '[class*="user-message"]',
      '[data-role="user"]',
    ],
    assistantMsg: [
      ".segment.segment-assistant",
      '[class*="assistant-message"]',
      '[data-role="assistant"]',
      '[class*="markdown"]',
    ],
    turnPair: [
      ".segment",
      '[class*="message"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="typing"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["user", "segment-user"],
      assistant: ["assistant", "segment-assistant"],
    },
    sessionHintPattern: /\/chat\/([a-zA-Z0-9]+)/i,
    modelSelector: [],
  },

  // =========================================================================
  // Mistral (chat.mistral.ai)
  // =========================================================================
  "chat.mistral.ai": {
    provider: "mistral",
    sourceName: "mistral-web",
    container: [
      '[class*="chat"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[class*="user-message"]',
      '[data-role="user"]',
      '[class*="human"]',
    ],
    assistantMsg: [
      '[class*="assistant-message"]',
      '[data-role="assistant"]',
      '[class*="markdown"]',
      '[class*="prose"]',
    ],
    turnPair: [
      '[class*="message"]',
      '[class*="turn"]',
    ],
    streamingIndicator: [
      '[class*="streaming"]',
      '[class*="loading"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["user", "human"],
      assistant: ["assistant", "bot"],
    },
    sessionHintPattern: /\/chat\/([a-f0-9-]+)/i,
    modelSelector: [],
  },

  // =========================================================================
  // Google AI Studio (aistudio.google.com)
  // =========================================================================
  "aistudio.google.com": {
    provider: "google",
    sourceName: "ai-studio-web",
    container: [
      '[class*="chat-container"]',
      '[class*="conversation"]',
      "ms-chat-session",
      "main",
    ],
    userMsg: [
      '[data-turn-role="user"]',
      "ms-chat-turn-user",
      '[class*="user-turn"]',
    ],
    assistantMsg: [
      '[data-turn-role="model"]',
      "ms-chat-turn-model",
      '[class*="model-turn"]',
      '[class*="markdown"]',
    ],
    turnPair: [
      "ms-chat-turn",
      '[class*="chat-turn"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="streaming"]',
      "mat-progress-bar",
    ],
    roleAttr: ["data-turn-role"],
    roleKeywords: {
      user: ["user"],
      assistant: ["model"],
    },
    sessionHintPattern: null,
    modelSelector: [],
  },

  // =========================================================================
  // Manus (manus.im)
  // =========================================================================
  "manus.im": {
    provider: "manus",
    sourceName: "manus-web",
    container: [
      '[class*="chat"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[class*="user-message"]',
      '[data-role="user"]',
    ],
    assistantMsg: [
      '[class*="assistant-message"]',
      '[class*="bot-message"]',
      '[data-role="assistant"]',
    ],
    turnPair: [
      '[class*="message"]',
      '[class*="turn"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="streaming"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["user"],
      assistant: ["assistant", "bot", "manus"],
    },
    sessionHintPattern: null,
    modelSelector: [],
  },

  // =========================================================================
  // Zhipu / ChatGLM (chat.z.ai, chatglm.cn)
  // =========================================================================
  "chat.z.ai": {
    provider: "zhipu",
    sourceName: "zhipu-web",
    container: [
      '[class*="chat"]',
      '[class*="conversation"]',
      "main",
    ],
    userMsg: [
      '[class*="user-message"]',
      '[data-role="user"]',
    ],
    assistantMsg: [
      '[class*="assistant-message"]',
      '[data-role="assistant"]',
      '[class*="markdown"]',
    ],
    turnPair: [
      '[class*="message"]',
    ],
    streamingIndicator: [
      '[class*="loading"]',
      '[class*="typing"]',
    ],
    roleAttr: ["data-role"],
    roleKeywords: {
      user: ["user"],
      assistant: ["assistant", "bot"],
    },
    sessionHintPattern: null,
    modelSelector: [],
  },
};

// Aliases — sites that share a config
PCE_SITE_CONFIGS["chat.openai.com"] = PCE_SITE_CONFIGS["chatgpt.com"];
PCE_SITE_CONFIGS["www.kimi.com"] = PCE_SITE_CONFIGS["kimi.com"];
PCE_SITE_CONFIGS["kimi.moonshot.cn"] = PCE_SITE_CONFIGS["kimi.com"];
PCE_SITE_CONFIGS["chatglm.cn"] = PCE_SITE_CONFIGS["chat.z.ai"];
PCE_SITE_CONFIGS["chat.zhipuai.cn"] = PCE_SITE_CONFIGS["chat.z.ai"];

// Expose globally for content scripts
if (typeof window !== "undefined") {
  window.__PCE_SITE_CONFIGS = PCE_SITE_CONFIGS;
}
