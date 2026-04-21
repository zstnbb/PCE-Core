// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/gemini.content.ts`.
 *
 * Exercises Gemini's extraction ladder — web-component tag detection
 * (``<user-query>`` / ``<model-response>``), data-turn-role
 * attributes, class-keyword inference — and the dedup pass that
 * collapses duplicated turns.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  extractText,
  getContainer,
  getModelName,
  getSessionHint,
  isStreaming,
} from "../gemini.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// getSessionHint
// ---------------------------------------------------------------------------

describe("getSessionHint", () => {
  it("extracts IDs from /app/<hex>", () => {
    expect(getSessionHint("/app/abcdef123456")).toBe("abcdef123456");
  });

  it("extracts IDs from /chat/<hex>", () => {
    expect(getSessionHint("/chat/abc123")).toBe("abc123");
  });

  it("falls back to the whole pathname when no match", () => {
    expect(getSessionHint("/settings")).toBe("/settings");
  });

  // P5.B spec G6 clarification: the unanchored regex matches the
  // `/chat/<hex>` substring anywhere in the path. Gem conversations
  // therefore ALREADY work correctly and don't need a regex change.
  // Lock in that behaviour so a future "tightening" doesn't regress it.
  it("extracts chat hex from /gem/<id>/chat/<hex> (Gem conversations)", () => {
    expect(getSessionHint("/gem/gem-slug/chat/abc123def")).toBe("abc123def");
  });

  it("falls back to pathname for /gem/<id> alone — overview page", () => {
    // Acceptable: the Gem overview has no chat content to capture,
    // so the session_hint string just feeds the fingerprint.
    expect(getSessionHint("/gem/gem-slug")).toBe("/gem/gem-slug");
  });
});

// ---------------------------------------------------------------------------
// getContainer / getModelName
// ---------------------------------------------------------------------------

describe("getContainer + getModelName", () => {
  it("prefers main, falls back to body", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("reads model name from .model-badge", () => {
    document.body.innerHTML = `<div class="model-badge">Gemini 2.5</div>`;
    expect(getModelName(document)).toBe("Gemini 2.5");
  });

  it("returns null model when nothing matches", () => {
    document.body.innerHTML = `<div>nothing</div>`;
    expect(getModelName(document)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// detectRole
// ---------------------------------------------------------------------------

describe("detectRole", () => {
  it("detects user-query tag", () => {
    document.body.innerHTML = `<user-query id="u">hi</user-query>`;
    expect(detectRole(document.getElementById("u")!)).toBe("user");
  });

  it("detects model-response tag", () => {
    document.body.innerHTML = `<model-response id="m">hello</model-response>`;
    expect(detectRole(document.getElementById("m")!)).toBe("assistant");
  });

  it("detects data-turn-role=user", () => {
    document.body.innerHTML = `<div id="u" data-turn-role="user">x</div>`;
    expect(detectRole(document.getElementById("u")!)).toBe("user");
  });

  it("detects data-turn-role=model", () => {
    document.body.innerHTML = `<div id="m" data-turn-role="model">x</div>`;
    expect(detectRole(document.getElementById("m")!)).toBe("assistant");
  });

  it("returns unknown when nothing matches", () => {
    document.body.innerHTML = `<div id="x">ambiguous</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

// ---------------------------------------------------------------------------
// extractText
// ---------------------------------------------------------------------------

describe("extractText", () => {
  it("uses extractReplyContent for model-response", () => {
    document.body.innerHTML = `
      <model-response id="m">
        <div class="markdown">the markdown reply body</div>
      </model-response>`;
    expect(extractText(document.getElementById("m")!)).toContain(
      "the markdown reply body",
    );
  });

  // P5.B gap G3 regression: Gemini 2.5 Pro Thinking wraps reasoning in a
  // <details> panel above the reply. The extractor must surface that as
  // <thinking>...</thinking> before the reply body.
  it("wraps 2.5 Pro Thinking panels around the reply text", () => {
    document.body.innerHTML = `
      <model-response id="m">
        <details>
          <summary>Thinking</summary>
          <div class="markdown">step-by-step reasoning</div>
        </details>
        <div class="markdown">the final reply body</div>
      </model-response>`;
    const text = extractText(document.getElementById("m")!);
    expect(text).toContain("<thinking>");
    expect(text).toContain("step-by-step reasoning");
    expect(text).toContain("</thinking>");
    expect(text).toContain("the final reply body");
    // Thinking must precede the reply
    expect(text.indexOf("<thinking>")).toBeLessThan(
      text.indexOf("the final reply body"),
    );
  });

  it("does not wrap in <thinking> when no Thinking panel is present", () => {
    document.body.innerHTML = `
      <model-response id="m">
        <div class="markdown">just a plain reply</div>
      </model-response>`;
    const text = extractText(document.getElementById("m")!);
    expect(text).not.toContain("<thinking>");
    expect(text).toContain("just a plain reply");
  });

  it("strips hidden / chip / action elements in the generic branch", () => {
    document.body.innerHTML = `
      <div id="u">
        <span class="sr-only">screen reader only</span>
        <span class="chip-container">chip</span>
        <span class="action-button">action</span>
        visible content here
      </div>`;
    const text = extractText(document.getElementById("u")!);
    expect(text).toContain("visible content here");
    expect(text).not.toContain("screen reader only");
    expect(text).not.toContain("chip");
    expect(text).not.toContain("action");
  });
});

// ---------------------------------------------------------------------------
// isStreaming (P5.B gap G2 regression)
// ---------------------------------------------------------------------------

describe("isStreaming", () => {
  it("true when a Stop-generating button is present", () => {
    document.body.innerHTML = `<button aria-label="Stop generating">X</button>`;
    expect(isStreaming(document)).toBe(true);
  });

  it("true when a Cancel button text is present", () => {
    document.body.innerHTML = `<button>Cancel</button>`;
    expect(isStreaming(document)).toBe(true);
  });

  it("false when the page is idle", () => {
    document.body.innerHTML = `<p>a quiet page</p>`;
    expect(isStreaming(document)).toBe(false);
  });

  it("false when buttons are present but none match stop/cancel", () => {
    document.body.innerHTML = `<button aria-label="Send">Send</button>`;
    expect(isStreaming(document)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// extractMessages
// ---------------------------------------------------------------------------

describe("extractMessages — Gemini turn selectors", () => {
  it("captures user-query + model-response pair via Strategy 1", () => {
    document.body.innerHTML = `
      <main>
        <user-query>what is gemini?</user-query>
        <model-response>
          <div class="markdown">I am Gemini, a Google assistant.</div>
        </model-response>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toBe("what is gemini?");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("I am Gemini");
  });

  it("captures data-turn-role-based pairs", () => {
    document.body.innerHTML = `
      <main>
        <div data-turn-role="user">user turn</div>
        <div data-turn-role="model">model response</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("dedupes turns with identical content", () => {
    document.body.innerHTML = `
      <main>
        <user-query>same question</user-query>
        <user-query>same question</user-query>
        <model-response><div class="markdown">reply</div></model-response>
      </main>`;
    const msgs = extractMessages(document);
    const userMsgs = msgs.filter((m) => m.role === "user");
    expect(userMsgs).toHaveLength(1);
  });

  // P5.B gap G10 regression: two messages that share a long common
  // preamble but differ in the tail must NOT be deduped. Previously
  // the key was `role:content.slice(0,200)` which collapsed them.
  it("does NOT dedupe turns with 200+ char common prefix but different tail (G10)", () => {
    const commonPrefix = "Please help me with the following task: ".repeat(8);
    // commonPrefix is ~320 chars, well over the old 200-char slice.
    document.body.innerHTML = `
      <main>
        <user-query>${commonPrefix} First actual question.</user-query>
        <model-response><div class="markdown">reply 1</div></model-response>
        <user-query>${commonPrefix} Second actual question.</user-query>
        <model-response><div class="markdown">reply 2</div></model-response>
      </main>`;
    const msgs = extractMessages(document);
    const userMsgs = msgs.filter((m) => m.role === "user");
    expect(userMsgs).toHaveLength(2);
    expect(userMsgs[0].content).toContain("First actual question");
    expect(userMsgs[1].content).toContain("Second actual question");
  });

  it("falls back to [class*='turn'] when dedicated selectors miss", () => {
    document.body.innerHTML = `
      <main>
        <div class="turn user-turn"><div class="markdown">first turn</div></div>
        <div class="turn model-turn"><div class="markdown">second turn</div></div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs.some((m) => m.role === "user")).toBe(true);
    expect(msgs.some((m) => m.role === "assistant")).toBe(true);
  });

  it("returns [] when there's nothing parseable", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// extractMessages strategy-ladder coverage (P5.B gap G9 regression)
//
// Strategies 2, 4, 5 had zero test coverage before v1.0.1. These tests
// force each strategy to fire in isolation by constructing DOM that only
// matches that strategy's selectors. Locks in fallback behaviour against
// future Angular DOM churn.
// ---------------------------------------------------------------------------

// P5.B gap G8 regression: shared URLs must return [] even when the
// DOM has valid-looking turns (because the current user didn't author
// them). Additive safety check; no regression on non-share paths.
describe("extractMessages — /share/ URL skip (G8)", () => {
  it("returns [] on /share/<hex> paths regardless of DOM content", () => {
    document.body.innerHTML = `
      <main>
        <user-query>someone else's question</user-query>
        <model-response>
          <div class="markdown">someone else's answer</div>
        </model-response>
      </main>`;
    expect(extractMessages(document, "/share/abc123def")).toEqual([]);
  });

  it("still captures on non-share paths with the same DOM", () => {
    document.body.innerHTML = `
      <main>
        <user-query>my question</user-query>
        <model-response>
          <div class="markdown">my answer</div>
        </model-response>
      </main>`;
    expect(extractMessages(document, "/app/xyz").length).toBeGreaterThan(0);
  });
});

describe("extractMessages — Strategy 2 (class keyword fallback)", () => {
  it("fires for [class*='query-text'] / [class*='response-container']", () => {
    // No <user-query>/<model-response> tags + no data-turn-role, so
    // Strategy 1 / 3 are skipped. Classes force Strategy 2.
    document.body.innerHTML = `
      <main>
        <div class="query-text user-query">user via strategy 2</div>
        <div class="response-container model-response">assistant via strategy 2</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.some((m) => m.role === "user" && m.content.includes("strategy 2"))).toBe(true);
    expect(msgs.some((m) => m.role === "assistant" && m.content.includes("strategy 2"))).toBe(true);
  });
});

describe("extractMessages — Strategy 4 (legacy conversation-container)", () => {
  it("fires for .conversation-container .turn-content", () => {
    // No web-component tags, no class*="query-text"/"response-container",
    // no data-turn-role — forces fall-through to Strategy 4.
    document.body.innerHTML = `
      <main>
        <div class="conversation-container">
          <div class="turn-content user-turn">user via strategy 4</div>
          <div class="turn-content model-turn">assistant via strategy 4</div>
        </div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.some((m) => m.role === "user" && m.content.includes("strategy 4"))).toBe(true);
    expect(msgs.some((m) => m.role === "assistant" && m.content.includes("strategy 4"))).toBe(true);
  });
});

describe("extractMessages — Strategy 5 (message-content custom element)", () => {
  it("fires for message-content tags with class-based role keywords", () => {
    // No <user-query>/<model-response>, no query-text/response-container
    // class, no data-turn-role, no .conversation-container wrapper —
    // only the message-content tag selector matches.
    document.body.innerHTML = `
      <main>
        <message-content class="user-query">user via strategy 5</message-content>
        <message-content class="model-response">assistant via strategy 5</message-content>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.some((m) => m.role === "user" && m.content.includes("strategy 5"))).toBe(true);
    expect(msgs.some((m) => m.role === "assistant" && m.content.includes("strategy 5"))).toBe(true);
  });
});
