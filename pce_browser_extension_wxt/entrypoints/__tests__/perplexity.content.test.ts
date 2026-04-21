// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/perplexity.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  extractText,
  getContainer,
  getSessionHint,
  isStreaming,
} from "../perplexity.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("getSessionHint", () => {
  it("extracts IDs from /search/<id>", () => {
    expect(getSessionHint("/search/abc123")).toBe("abc123");
  });

  it("extracts IDs from /thread/<id>", () => {
    expect(getSessionHint("/thread/xyz-789")).toBe("xyz-789");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/library")).toBe("/library");
  });
});

describe("getContainer", () => {
  it("prefers main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to a [class*='thread'] element", () => {
    document.body.innerHTML = `<div class="thread-container" id="t">hi</div>`;
    expect(getContainer(document)!.id).toBe("t");
  });

  it("falls back to body when nothing matches", () => {
    document.body.innerHTML = `<p>content</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("detectRole", () => {
  it("flags query/question/user classes as user", () => {
    document.body.innerHTML = `
      <div id="q" class="query-block">q</div>
      <div id="u" class="user-msg">u</div>
      <div id="qu" class="question">?</div>`;
    expect(detectRole(document.getElementById("q")!)).toBe("user");
    expect(detectRole(document.getElementById("u")!)).toBe("user");
    expect(detectRole(document.getElementById("qu")!)).toBe("user");
  });

  it("handles H1.group/query as user", () => {
    document.body.innerHTML = `<h1 id="h" class="group/query">q</h1>`;
    expect(detectRole(document.getElementById("h")!)).toBe("user");
  });

  it("flags answer/response/prose classes as assistant", () => {
    document.body.innerHTML = `
      <div id="a" class="answer-block">a</div>
      <div id="r" class="response">r</div>
      <div id="p" class="prose">p</div>`;
    expect(detectRole(document.getElementById("a")!)).toBe("assistant");
    expect(detectRole(document.getElementById("r")!)).toBe("assistant");
    expect(detectRole(document.getElementById("p")!)).toBe("assistant");
  });

  it("flags PRE and not-prose as unknown", () => {
    document.body.innerHTML = `
      <pre id="pre" class="answer">code</pre>
      <div id="np" class="not-prose answer">?</div>`;
    expect(detectRole(document.getElementById("pre")!)).toBe("unknown");
    expect(detectRole(document.getElementById("np")!)).toBe("unknown");
  });

  it("returns unknown for uncategorised elements", () => {
    document.body.innerHTML = `<div id="u">x</div>`;
    expect(detectRole(document.getElementById("u")!)).toBe("unknown");
  });

  it("honours data-testid keyword match", () => {
    document.body.innerHTML = `
      <div id="q" data-testid="query-1">q</div>
      <div id="a" data-testid="answer-2">a</div>`;
    expect(detectRole(document.getElementById("q")!)).toBe("user");
    expect(detectRole(document.getElementById("a")!)).toBe("assistant");
  });
});

describe("extractText", () => {
  it("returns empty string for PRE / not-prose", () => {
    document.body.innerHTML = `
      <pre id="p" class="answer">code</pre>
      <div id="np" class="not-prose answer">skipped</div>`;
    expect(extractText(document.getElementById("p")!)).toBe("");
    expect(extractText(document.getElementById("np")!)).toBe("");
  });

  it("strips buttons / citations / related panels", () => {
    document.body.innerHTML = `
      <div id="x">
        <button>copy</button>
        <span class="citation">1</span>
        <span class="related-question">related</span>
        visible answer
      </div>`;
    const t = extractText(document.getElementById("x")!);
    expect(t).toContain("visible answer");
    expect(t).not.toContain("copy");
    expect(t).not.toContain("related");
  });

  it("prefers .prose / .markdown child when present", () => {
    document.body.innerHTML = `
      <div id="x">
        header
        <div class="prose">the rendered answer body</div>
      </div>`;
    expect(extractText(document.getElementById("x")!)).toBe(
      "the rendered answer body",
    );
  });
});

// P5.B gap PX2 regression: isStreaming gate must detect Perplexity's
// Stop/Cancel button so mid-stream capture is deferred.
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
    document.body.innerHTML = `<p>quiet page</p>`;
    expect(isStreaming(document)).toBe(false);
  });

  it("false when buttons are present but none match stop/cancel", () => {
    document.body.innerHTML = `<button aria-label="Search">Search</button>`;
    expect(isStreaming(document)).toBe(false);
  });
});

describe("extractMessages", () => {
  it("captures user + assistant from ThreadMessage selectors", () => {
    document.body.innerHTML = `
      <main>
        <div class="ThreadMessage query-1">user question text</div>
        <div class="ThreadMessage answer-1">
          <div class="prose">the assistant reply</div>
        </div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toContain("user question text");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toBe("the assistant reply");
  });

  it("dedupes messages with identical content", () => {
    document.body.innerHTML = `
      <main>
        <div class="ThreadMessage query-1">dup question</div>
        <div class="ThreadMessage query-2">dup question</div>
        <div class="ThreadMessage answer-1"><div class="prose">reply</div></div>
      </main>`;
    const msgs = extractMessages(document);
    const users = msgs.filter((m) => m.role === "user");
    expect(users).toHaveLength(1);
  });

  // P5.B gap PX1 regression (mirrors Gemini G10): long common
  // preamble with different tail must NOT collapse.
  it("does NOT dedupe queries with 240+ char common prefix but different tail (PX1)", () => {
    const commonPrefix = "Please research the following topic in detail: ".repeat(6);
    // commonPrefix is ~288 chars, well over the old 240-char slice.
    document.body.innerHTML = `
      <main>
        <div class="ThreadMessage query-1">${commonPrefix} First question.</div>
        <div class="ThreadMessage answer-1"><div class="prose">reply 1</div></div>
        <div class="ThreadMessage query-2">${commonPrefix} Second question.</div>
        <div class="ThreadMessage answer-2"><div class="prose">reply 2</div></div>
      </main>`;
    const msgs = extractMessages(document);
    const users = msgs.filter((m) => m.role === "user");
    expect(users.length).toBe(2);
    expect(users[0].content).toContain("First question");
    expect(users[1].content).toContain("Second question");
  });

  it("returns empty when no parseable turns exist", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
