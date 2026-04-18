/**
 * Tests for `entrypoints/detector.content.ts` pure detection logic.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectAIPage,
  DOM_SIGNALS,
  HEURISTIC_THRESHOLD,
  KNOWN_AI_DOMAINS,
  KNOWN_AI_PATH_PREFIXES,
  META_KEYWORDS,
  TITLE_KEYWORDS,
} from "../detector.content";

beforeEach(() => {
  document.body.innerHTML = "";
  document.title = "";
});

describe("KNOWN_AI_DOMAINS", () => {
  it("covers the 13 sites we ship extractors for", () => {
    const expected = [
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
      "aistudio.google.com",
      "chat.z.ai",
      "manus.im",
    ];
    for (const host of expected) {
      expect(KNOWN_AI_DOMAINS.has(host), `missing ${host}`).toBe(true);
    }
  });
});

describe("constants", () => {
  it("HEURISTIC_THRESHOLD is > 0", () => {
    expect(HEURISTIC_THRESHOLD).toBeGreaterThan(0);
  });

  it("KNOWN_AI_PATH_PREFIXES includes /chat and /c/", () => {
    expect(KNOWN_AI_PATH_PREFIXES).toContain("/chat");
    expect(KNOWN_AI_PATH_PREFIXES).toContain("/c/");
  });

  it("TITLE_KEYWORDS + META_KEYWORDS cover common AI phrases", () => {
    expect(TITLE_KEYWORDS).toContain("chat");
    expect(TITLE_KEYWORDS).toContain("claude");
    expect(META_KEYWORDS).toContain("ai chat");
    expect(META_KEYWORDS).toContain("language model");
  });

  it("DOM_SIGNALS has at least a handful of chat-UI patterns", () => {
    expect(DOM_SIGNALS.length).toBeGreaterThan(10);
    const selectors = DOM_SIGNALS.map((s) => s.selector);
    expect(selectors).toContain("[data-message-author-role]");
  });
});

describe("detectAIPage — known domain fast path", () => {
  it("returns detected=true with confidence=known for known domains", () => {
    const r = detectAIPage(document, "chatgpt.com", "/c/abc");
    expect(r.detected).toBe(true);
    expect(r.confidence).toBe("known");
    expect(r.domain).toBe("chatgpt.com");
    expect(r.score).toBe(100);
    expect(r.signals).toContain("known_domain");
  });
});

describe("detectAIPage — heuristic path", () => {
  it("detects via high-weight DOM signals (ChatGPT-like role attr)", () => {
    document.body.innerHTML = `
      <div data-message-author-role="user">q</div>
      <div data-message-author-role="assistant">a</div>`;
    const r = detectAIPage(document, "unknown.example", "/");
    expect(r.detected).toBe(true);
    expect(r.confidence).toBe("heuristic");
    expect(r.score).toBeGreaterThanOrEqual(HEURISTIC_THRESHOLD);
  });

  it("combines title + path + DOM signals to cross the threshold", () => {
    document.body.innerHTML = `
      <textarea placeholder="Ask me anything"></textarea>
      <div class="chat-message">x</div>`;
    document.title = "My AI Assistant";
    const r = detectAIPage(document, "unknown.example", "/chat/session1");
    expect(r.detected).toBe(true);
    // title(+1) + path(+2) + textarea ask(+3) + chat-message(+3) = 9
    expect(r.score).toBeGreaterThanOrEqual(HEURISTIC_THRESHOLD);
    expect(r.signals.some((s) => s.startsWith("path:"))).toBe(true);
    expect(r.signals.some((s) => s.startsWith("title:"))).toBe(true);
    expect(r.signals.some((s) => s.startsWith("dom:"))).toBe(true);
  });

  it("detects via meta description keyword", () => {
    document.head.innerHTML = `
      <meta name="description" content="The first chatbot that…" />`;
    document.body.innerHTML = `
      <textarea placeholder="Ask here"></textarea>
      <div class="chat-message">x</div>`;
    const r = detectAIPage(document, "unknown.example", "/");
    expect(r.signals.some((s) => s.startsWith("meta:"))).toBe(true);
  });

  it("returns detected=false when no signals match", () => {
    document.body.innerHTML = `<p>plain content page</p>`;
    document.title = "News article";
    const r = detectAIPage(document, "unknown.example", "/articles");
    expect(r.detected).toBe(false);
    expect(r.confidence).toBe("none");
  });
});
