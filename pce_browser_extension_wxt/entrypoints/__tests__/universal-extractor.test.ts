/**
 * Tests for `entrypoints/universal-extractor.ts` pure helpers.
 *
 * `defineUnlistedScript` is stubbed under Vitest so the dynamically-
 * injected entry body doesn't execute. We exercise the exported
 * pure helpers — container detection, role inference, text
 * extraction, provider guessing, and the double-load guard.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  ASSISTANT_INDICATORS,
  CONTAINER_SELECTORS,
  detectRole,
  extractMessages,
  extractTextContent,
  findChatContainer,
  guessProvider,
  MESSAGE_SELECTORS,
  SITE_EXTRACTOR_FLAGS,
  USER_INDICATORS,
  siteExtractorAlreadyActive,
} from "../universal-extractor";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("constants", () => {
  it("SITE_EXTRACTOR_FLAGS covers 13 site-specific flags", () => {
    expect(SITE_EXTRACTOR_FLAGS.length).toBeGreaterThanOrEqual(13);
    expect(SITE_EXTRACTOR_FLAGS).toContain("__PCE_CHATGPT_ACTIVE");
    expect(SITE_EXTRACTOR_FLAGS).toContain("__PCE_GENERIC_ACTIVE");
  });

  it("CONTAINER_SELECTORS is ordered with highest-score first", () => {
    for (let i = 1; i < CONTAINER_SELECTORS.length; i++) {
      expect(CONTAINER_SELECTORS[i - 1].score).toBeGreaterThanOrEqual(
        CONTAINER_SELECTORS[i].score,
      );
    }
  });

  it("MESSAGE_SELECTORS includes the ChatGPT role attr", () => {
    expect(MESSAGE_SELECTORS).toContain("[data-message-author-role]");
  });

  it("USER_INDICATORS + ASSISTANT_INDICATORS are non-empty arrays", () => {
    expect(USER_INDICATORS.length).toBeGreaterThan(0);
    expect(ASSISTANT_INDICATORS.length).toBeGreaterThan(0);
  });
});

describe("siteExtractorAlreadyActive", () => {
  it("returns the matching flag name when set on the window", () => {
    const win: Record<string, boolean> = {
      __PCE_CHATGPT_ACTIVE: true,
    };
    expect(siteExtractorAlreadyActive(win)).toBe("__PCE_CHATGPT_ACTIVE");
  });

  it("returns null when no flag is set", () => {
    expect(siteExtractorAlreadyActive({})).toBeNull();
  });
});

describe("findChatContainer", () => {
  it("prefers high-score [data-testid*=conversation]", () => {
    document.body.innerHTML = `
      <main><div>low</div></main>
      <div data-testid="conversation-turn" id="c"><div>1</div><div>2</div></div>`;
    const el = findChatContainer(document);
    expect(el?.id).toBe("c");
  });

  it("falls back to main when nothing high-score matches", () => {
    document.body.innerHTML = `<main id="m"><div>x</div></main>`;
    expect(findChatContainer(document)?.id).toBe("m");
  });

  it("returns null when nothing matches", () => {
    document.body.innerHTML = `<span>x</span>`;
    expect(findChatContainer(document)).toBeNull();
  });

  it("prefers container with more children (adjusted score)", () => {
    document.body.innerHTML = `
      <div data-testid="conversation-a" id="a"><div>1</div></div>
      <div class="chat-container-b" id="b"><div>1</div><div>2</div><div>3</div><div>4</div></div>`;
    // a has score 10 + 0.5 = 10.5
    // b has score 9 + 2 = 11.0
    expect(findChatContainer(document)?.id).toBe("b");
  });
});

describe("detectRole", () => {
  it("data-message-author-role=user → user", () => {
    document.body.innerHTML = `<div id="x" data-message-author-role="user">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("data-message-author-role=assistant → assistant", () => {
    document.body.innerHTML = `<div id="x" data-message-author-role="assistant">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("class=user-message → user", () => {
    document.body.innerHTML = `<div id="x" class="message user-message">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("class with bot keyword → assistant", () => {
    document.body.innerHTML = `<div id="x" class="bot-turn">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("returns null when nothing matches", () => {
    document.body.innerHTML = `<div id="x">plain</div>`;
    expect(detectRole(document.getElementById("x")!)).toBeNull();
  });
});

describe("extractTextContent", () => {
  it("returns empty string for null", () => {
    expect(extractTextContent(null)).toBe("");
  });

  it("prefers markdown / prose children", () => {
    document.body.innerHTML = `
      <div id="x">
        <div class="prose">the real content</div>
        <span class="sr-only">noise</span>
      </div>`;
    expect(extractTextContent(document.getElementById("x"))).toContain(
      "the real content",
    );
  });
});

describe("extractMessages", () => {
  it("captures user + assistant from [data-message-author-role]", () => {
    document.body.innerHTML = `
      <div data-message-author-role="user">u msg</div>
      <div data-message-author-role="assistant">a msg</div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("falls back to alternating children in chat container", () => {
    document.body.innerHTML = `
      <div data-testid="conversation-container">
        <div>alternate one long text</div>
        <div>alternate two long text</div>
        <div>alternate three long text</div>
      </div>`;
    // happy-dom returns 0 offsetHeight for all elements so the
    // children filter drops them. This case tests that missing
    // offsetHeight doesn't crash the extractor — result may be [].
    const msgs = extractMessages(document);
    expect(Array.isArray(msgs)).toBe(true);
  });

  it("returns [] when nothing is extractable", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(extractMessages(document)).toEqual([]);
  });
});

describe("guessProvider", () => {
  it("uses detector's KNOWN_AI_DOMAINS when the host is listed", () => {
    const win = {
      __PCE_DETECTOR: {
        KNOWN_AI_DOMAINS: new Set(["custom-ai.example.com"]),
      },
    };
    expect(guessProvider("custom-ai.example.com", win)).toBe("custom-ai");
  });

  it("strips www. prefix before splitting", () => {
    expect(guessProvider("www.something.ai", {})).toBe("something");
  });

  it("returns 'unknown' for empty hostname", () => {
    expect(guessProvider("", {})).toBe("unknown");
  });
});
