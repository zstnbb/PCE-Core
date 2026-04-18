// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/interceptor-ai-patterns.ts`.
 *
 * Pure pattern-matching — no DOM needed. These tests lock down the
 * URL / body / streaming matchers so regressions in the pattern
 * tables surface immediately. Coverage chosen to exercise:
 *   - known AI API domains (fast-path)
 *   - web UI domains with noise filtering
 *   - web UI domains with AI-specific paths
 *   - unknown domains with path-pattern fallback
 *   - body field signatures (high + medium confidence)
 *   - streaming content-type detection
 *   - combined `isAIRequest` promotion rules
 */

// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  AI_API_DOMAINS,
  AI_PATH_PATTERNS,
  AI_REQUEST_FIELDS,
  HOST_TO_PROVIDER,
  WEB_UI_DOMAINS,
  WEB_UI_AI_PATHS,
  WEB_UI_NOISE_PATHS,
  guessProviderFromHost,
  isAIRequest,
  isStreamingResponse,
  matchRequestBody,
  matchUrl,
} from "../interceptor-ai-patterns";

// --- matchUrl ----------------------------------------------------------

describe("matchUrl — pure API domains (fast path)", () => {
  it("marks api.openai.com as AI + high confidence", () => {
    const m = matchUrl("https://api.openai.com/v1/chat/completions");
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
    expect(m.provider).toBe("openai");
    expect(m.host).toBe("api.openai.com");
  });

  it("marks api.anthropic.com as AI + high confidence", () => {
    const m = matchUrl("https://api.anthropic.com/v1/messages");
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
    expect(m.provider).toBe("anthropic");
  });

  it("marks localhost as AI", () => {
    expect(matchUrl("http://localhost:8080/v1/chat/completions").isAI).toBe(
      true,
    );
  });
});

describe("matchUrl — web UI domains", () => {
  it("accepts ChatGPT backend conversation paths", () => {
    const m = matchUrl(
      "https://chatgpt.com/backend-api/conversation/abc-123",
    );
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
    expect(m.provider).toBe("openai");
  });

  it("rejects ChatGPT noise paths (sentinel, accounts, files)", () => {
    expect(matchUrl("https://chatgpt.com/sentinel/chat-requirements").isAI).toBe(
      false,
    );
    expect(matchUrl("https://chatgpt.com/backend-api/accounts").isAI).toBe(
      false,
    );
    expect(matchUrl("https://chatgpt.com/backend-api/settings").isAI).toBe(
      false,
    );
    expect(matchUrl("https://chatgpt.com/_next/chunks/main.js").isAI).toBe(
      false,
    );
  });

  it("rejects ChatGPT unknown paths (conservative)", () => {
    expect(matchUrl("https://chatgpt.com/some-random-page").isAI).toBe(false);
  });

  it("accepts Claude append-message POSTs", () => {
    const m = matchUrl("https://claude.ai/api/append-message");
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
    expect(m.provider).toBe("anthropic");
  });

  it("accepts DeepSeek /api/chat/completions", () => {
    const m = matchUrl("https://chat.deepseek.com/api/chat/completions");
    expect(m.isAI).toBe(true);
    expect(m.provider).toBe("deepseek");
  });

  it("rejects Docs/Gmail noise (ListActivities etc.)", () => {
    expect(
      matchUrl("https://docs.google.com/document/d/abc/ListActivities").isAI,
    ).toBe(false);
  });
});

describe("matchUrl — unknown domains via path patterns", () => {
  it("catches OpenAI-compatible proxies via path pattern", () => {
    const m = matchUrl("https://my-proxy.example.com/v1/chat/completions");
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("medium");
  });

  it("catches Ollama /api/generate on unknown host", () => {
    const m = matchUrl("https://my-ollama.lan:11434/api/generate");
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("medium");
  });

  it("does NOT match arbitrary unrelated URLs", () => {
    expect(matchUrl("https://example.com/about").isAI).toBe(false);
    expect(matchUrl("https://google.com/search?q=hi").isAI).toBe(false);
  });
});

describe("matchUrl — error handling", () => {
  it("returns safe defaults for garbage URLs", () => {
    const m = matchUrl("not a url at all");
    expect(m.isAI).toBe(false);
    expect(m.confidence).toBe("none");
    expect(m.host).toBeNull();
    expect(m.path).toBeNull();
  });
});

// --- matchRequestBody --------------------------------------------------

describe("matchRequestBody", () => {
  it("returns none for null / primitive / array", () => {
    expect(matchRequestBody(null).isAI).toBe(false);
    expect(matchRequestBody("string").isAI).toBe(false);
    expect(matchRequestBody(42).isAI).toBe(false);
  });

  it("accepts OpenAI-style model + messages as HIGH", () => {
    const m = matchRequestBody({
      model: "gpt-4",
      messages: [{ role: "user", content: "hi" }],
    });
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
    expect(m.model).toBe("gpt-4");
  });

  it("accepts Gemini contents field as HIGH", () => {
    const m = matchRequestBody({ contents: [{ parts: [{ text: "hi" }] }] });
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
  });

  it("accepts Notion aiSessionId as HIGH", () => {
    expect(matchRequestBody({ aiSessionId: "xyz" }).confidence).toBe("high");
  });

  it("accepts prompt+max_tokens as MEDIUM", () => {
    expect(
      matchRequestBody({ prompt: "hi", max_tokens: 500 }).confidence,
    ).toBe("medium");
  });

  it("accepts bare messages as MEDIUM", () => {
    expect(matchRequestBody({ messages: [] }).confidence).toBe("medium");
  });

  it("rejects unrelated objects", () => {
    expect(matchRequestBody({ hello: "world" }).isAI).toBe(false);
  });

  it("model defaults to null when body has no model field", () => {
    const m = matchRequestBody({ messages: [] });
    expect(m.model).toBeNull();
  });
});

// --- isStreamingResponse ----------------------------------------------

describe("isStreamingResponse", () => {
  const streamingCases: Array<[string, boolean]> = [
    ["text/event-stream", true],
    ["text/event-stream;charset=utf-8", true],
    ["application/x-ndjson", true],
    ["application/json", false],
    ["text/html", false],
    ["", false],
  ];
  it.each(streamingCases)("%s → %s", (ct, expected) => {
    expect(isStreamingResponse(ct)).toBe(expected);
  });

  it("returns false for null / undefined", () => {
    expect(isStreamingResponse(null)).toBe(false);
    expect(isStreamingResponse(undefined)).toBe(false);
  });
});

// --- isAIRequest (combined) -------------------------------------------

describe("isAIRequest", () => {
  it("high-confidence URL + high-confidence body → high", () => {
    const m = isAIRequest(
      "https://api.openai.com/v1/chat/completions",
      { model: "gpt-4", messages: [] },
    );
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
    expect(m.provider).toBe("openai");
    expect(m.model).toBe("gpt-4");
  });

  it("medium-confidence URL alone → medium", () => {
    const m = isAIRequest(
      "https://my-proxy.lan/api/chat/completions",
      null,
    );
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("medium");
  });

  it("unknown URL + high-confidence body → high", () => {
    const m = isAIRequest("https://example.com/some-path", {
      model: "x",
      messages: [],
    });
    expect(m.isAI).toBe(true);
    expect(m.confidence).toBe("high");
  });

  it("unknown URL + unrelated body → not AI", () => {
    const m = isAIRequest("https://example.com/x", { foo: "bar" });
    expect(m.isAI).toBe(false);
  });
});

// --- guessProviderFromHost --------------------------------------------

describe("guessProviderFromHost", () => {
  it("returns 'unknown' for null / empty", () => {
    expect(guessProviderFromHost(null)).toBe("unknown");
    expect(guessProviderFromHost("")).toBe("unknown");
  });

  it("resolves direct hits", () => {
    expect(guessProviderFromHost("api.openai.com")).toBe("openai");
    expect(guessProviderFromHost("claude.ai")).toBe("anthropic");
  });

  it("resolves substring matches", () => {
    // The legacy algorithm is substring `host.includes(domain)`, not
    // suffix match — a `ai.openai.com.mycompany.io` would still hit.
    expect(guessProviderFromHost("stg.api.openai.com")).toBe("openai");
  });

  it("returns 'unknown' for truly unrelated hosts", () => {
    expect(guessProviderFromHost("example.org")).toBe("unknown");
  });
});

// --- Static tables -----------------------------------------------------

describe("Static pattern tables", () => {
  it("AI_API_DOMAINS is non-empty and covers every HOST_TO_PROVIDER key", () => {
    expect(AI_API_DOMAINS.size).toBeGreaterThan(0);
    for (const host of Object.keys(HOST_TO_PROVIDER)) {
      expect(AI_API_DOMAINS.has(host)).toBe(true);
    }
  });

  it("every WEB_UI_DOMAINS entry is also in AI_API_DOMAINS", () => {
    for (const domain of WEB_UI_DOMAINS) {
      expect(AI_API_DOMAINS.has(domain)).toBe(true);
    }
  });

  it("AI_PATH_PATTERNS are all regexes", () => {
    for (const re of AI_PATH_PATTERNS) expect(re).toBeInstanceOf(RegExp);
  });

  it("WEB_UI_AI_PATHS are all regexes", () => {
    for (const re of WEB_UI_AI_PATHS) expect(re).toBeInstanceOf(RegExp);
  });

  it("WEB_UI_NOISE_PATHS are all regexes", () => {
    for (const re of WEB_UI_NOISE_PATHS) expect(re).toBeInstanceOf(RegExp);
  });

  it("AI_REQUEST_FIELDS has non-empty high + medium arrays", () => {
    expect(AI_REQUEST_FIELDS.high.length).toBeGreaterThan(0);
    expect(AI_REQUEST_FIELDS.medium.length).toBeGreaterThan(0);
  });
});
