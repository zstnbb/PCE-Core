// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/chatgpt.content.ts`.
 *
 * Exercises the pure ChatGPT-specific helpers (extraction strategies,
 * container resolution, streaming detection, conversation-id
 * resolution). The `defineContentScript` entrypoint closure is NOT
 * executed because Vitest stubs `defineContentScript` as a no-op
 * pass-through (see `test/setup.ts`).
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  extractMessages,
  extractSpecialContent,
  getContainer,
  getConvId,
  getModelName,
  isStreamingChatGPT,
  resolveConversationId,
} from "../chatgpt.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// getConvId
// ---------------------------------------------------------------------------

describe("getConvId", () => {
  it("extracts the UUID from /c/<id> paths", () => {
    expect(getConvId("/c/abcd1234-ef56")).toBe("abcd1234-ef56");
  });

  it("returns null for paths that don't match", () => {
    expect(getConvId("/settings")).toBeNull();
    expect(getConvId("/")).toBeNull();
    expect(getConvId("")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// getContainer
// ---------------------------------------------------------------------------

describe("getContainer", () => {
  it("prefers react-scroll-to-bottom", () => {
    document.body.innerHTML = `<div class="react-scroll-to-bottom"><p>x</p></div>`;
    const el = getContainer(document);
    expect(el?.className).toContain("react-scroll-to-bottom");
  });

  it("falls back to main when nothing else matches", () => {
    document.body.innerHTML = `<main id="fallback">hi</main>`;
    expect(getContainer(document)!.id).toBe("fallback");
  });

  it("returns null when there is nothing", () => {
    expect(getContainer(document)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// isStreamingChatGPT
// ---------------------------------------------------------------------------

describe("isStreamingChatGPT", () => {
  it("true when a Stop button is present", () => {
    document.body.innerHTML = `<button aria-label="Stop generating">stop</button>`;
    expect(isStreamingChatGPT(document)).toBe(true);
  });

  it("true when agent-turn streaming class is present", () => {
    document.body.innerHTML = `<main><div class="agent-turn"><div class="streaming">.</div></div></main>`;
    expect(isStreamingChatGPT(document)).toBe(true);
  });

  it("true when a generic result-streaming indicator is present", () => {
    document.body.innerHTML = `<main><div class="result-streaming">...</div></main>`;
    expect(isStreamingChatGPT(document)).toBe(true);
  });

  it("false when idle", () => {
    document.body.innerHTML = `<main><p>idle</p></main>`;
    expect(isStreamingChatGPT(document)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// getModelName
// ---------------------------------------------------------------------------

describe("getModelName", () => {
  it("returns the first [class*='model'] trimmed text", () => {
    document.body.innerHTML = `<div class="model-picker">  GPT-4o  </div>`;
    expect(getModelName(document)).toBe("GPT-4o");
  });

  it("returns null when no model element is present", () => {
    document.body.innerHTML = `<div>nothing</div>`;
    expect(getModelName(document)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// resolveConversationId
// ---------------------------------------------------------------------------

describe("resolveConversationId", () => {
  it("returns the /c/<uuid> when present", () => {
    expect(resolveConversationId([], "/c/abc-123", 0)).toBe("abc-123");
  });

  it("returns _new_<ts> when no URL but ≥ 2 messages", () => {
    const now = 100_000;
    const id = resolveConversationId(
      [
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ],
      "/",
      now,
    );
    expect(id).toBe("_new_" + now.toString(36));
  });

  it("returns null when no URL and < 2 messages", () => {
    expect(resolveConversationId([{ role: "user", content: "hi" }], "/", 0)).toBeNull();
    expect(resolveConversationId([], "/", 0)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// extractMessages
// ---------------------------------------------------------------------------

describe("extractMessages — Strategy A (data-message-author-role)", () => {
  it("captures user + assistant messages", () => {
    document.body.innerHTML = `
      <main>
        <div data-testid="conversation-turn-1">
          <div data-message-author-role="user">hi there</div>
        </div>
        <div data-testid="conversation-turn-2">
          <div data-message-author-role="assistant">
            <div class="markdown prose">here's my answer</div>
          </div>
        </div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toMatchObject({ role: "user", content: "hi there" });
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("here's my answer");
  });

  it("wraps thinking panels in <thinking> tags", () => {
    document.body.innerHTML = `
      <main>
        <div data-message-author-role="assistant">
          <details>
            <summary>Thinking…</summary>
            <div class="markdown">step 1: analyse</div>
          </details>
          <div class="markdown prose">final reply text</div>
        </div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].content).toContain("<thinking>");
    expect(msgs[0].content).toContain("step 1: analyse");
    expect(msgs[0].content).toContain("final reply text");
  });

  it("runs Deep Research fallback when only user messages are found", () => {
    document.body.innerHTML = `
      <main>
        <div data-message-author-role="user">user prompt</div>
        <article>
          ${"A".repeat(120)} deep research report content
        </article>
      </main>`;
    const msgs = extractMessages(document);
    // Strategy A collects the user msg + special-content assistant msg
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs.some((m) => m.role === "user")).toBe(true);
    expect(msgs.some((m) => m.role === "assistant")).toBe(true);
  });
});

describe("extractMessages — Strategy B (conversation-turn fallback)", () => {
  it("alternates user/assistant by index when no role attr present", () => {
    document.body.innerHTML = `
      <main>
        <div data-testid="conversation-turn-1">first turn</div>
        <div data-testid="conversation-turn-2"><div class="markdown prose">second reply</div></div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });
});

describe("extractMessages — empty DOM", () => {
  it("returns [] when there's nothing to extract", () => {
    expect(extractMessages(document)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// extractSpecialContent
// ---------------------------------------------------------------------------

describe("extractSpecialContent", () => {
  it("returns null when there's no main element", () => {
    expect(extractSpecialContent(document)).toBeNull();
  });

  it("returns null when no substantial text is found", () => {
    document.body.innerHTML = `<main><p>tiny</p></main>`;
    expect(extractSpecialContent(document)).toBeNull();
  });

  it("returns an assistant message when a long research block exists", () => {
    document.body.innerHTML = `
      <main>
        <article>${"report ".repeat(20)}</article>
      </main>`;
    const result = extractSpecialContent(document);
    expect(result).not.toBeNull();
    expect(result!.role).toBe("assistant");
    expect(result!.content.length).toBeGreaterThan(50);
  });

  it("skips content inside a user message", () => {
    document.body.innerHTML = `
      <main>
        <div data-message-author-role="user">
          <article>${"user text ".repeat(20)}</article>
        </div>
      </main>`;
    expect(extractSpecialContent(document)).toBeNull();
  });
});
