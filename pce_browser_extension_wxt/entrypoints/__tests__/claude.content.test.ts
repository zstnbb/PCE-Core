// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/claude.content.ts`.
 *
 * Exercises Claude's extraction ladder — dedicated human/assistant
 * selectors sorted by visual top position, generic message-block
 * role detection, and the direct-children-of-container fallback.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  extractMessages,
  getContainer,
  getModelName,
  getSessionHint,
  isStreaming,
} from "../claude.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// getSessionHint
// ---------------------------------------------------------------------------

describe("getSessionHint", () => {
  it("extracts the UUID from /chat/<id>", () => {
    expect(getSessionHint("/chat/abcd1234-ef56-7890")).toBe(
      "abcd1234-ef56-7890",
    );
  });

  it("returns null for non-matching paths", () => {
    expect(getSessionHint("/projects")).toBeNull();
    expect(getSessionHint("/")).toBeNull();
  });

  // P5.B spec C3 clarification: the unanchored regex matches the
  // `/chat/<uuid>` substring anywhere in the path. Projects chats
  // therefore ALREADY work correctly and don't need a regex change.
  // Lock in that behaviour so a future "tightening" doesn't regress it.
  it("extracts chat UUID from /project/<id>/chat/<uuid> (Projects chats)", () => {
    expect(
      getSessionHint("/project/proj-xyz/chat/abcd1234-ef56-7890"),
    ).toBe("abcd1234-ef56-7890");
  });

  it("returns null for /project/<id> alone — not a chat surface", () => {
    expect(getSessionHint("/project/proj-xyz")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// getContainer
// ---------------------------------------------------------------------------

describe("getContainer", () => {
  it("prefers conversation-content", () => {
    document.body.innerHTML = `<div class="conversation-content" id="win"><p>x</p></div><main>m</main>`;
    expect(getContainer(document)!.id).toBe("win");
  });

  it("falls back to role=log", () => {
    document.body.innerHTML = `<div role="log" id="log">hi</div>`;
    expect(getContainer(document)!.id).toBe("log");
  });

  it("falls back to main when nothing else matches", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });
});

// ---------------------------------------------------------------------------
// getModelName (P5.B gap C5 regression)
// ---------------------------------------------------------------------------

describe("getModelName", () => {
  it("reads from the dedicated model-selector testid", () => {
    document.body.innerHTML = `
      <button data-testid="model-selector-button">
        <span>Claude 3.5 Sonnet</span>
      </button>`;
    expect(getModelName(document)).toBe("Claude 3.5 Sonnet");
  });

  it("reads from a partial testid match", () => {
    document.body.innerHTML = `
      <div data-testid="header-model-selector-btn">
        Claude Opus 4
      </div>`;
    expect(getModelName(document)).toBe("Claude Opus 4");
  });

  it("reads from aria-label", () => {
    document.body.innerHTML = `
      <button aria-label="Model selector">Claude Haiku 3</button>`;
    expect(getModelName(document)).toBe("Claude Haiku 3");
  });

  it("falls back to [class*=model] + family regex", () => {
    document.body.innerHTML = `
      <div class="model-badge">Claude 3.7 Sonnet</div>`;
    expect(getModelName(document)).toBe("Claude 3.7 Sonnet");
  });

  it("falls back to body-text regex when no dedicated element present", () => {
    document.body.innerHTML = `<p>running on Claude 3.5 Sonnet today</p>`;
    expect(getModelName(document)).toBe("Claude 3.5 Sonnet");
  });

  it("returns null when no model reference exists", () => {
    document.body.innerHTML = `<p>nothing about models here</p>`;
    expect(getModelName(document)).toBeNull();
  });

  it("skips model-named classes that contain no valid family", () => {
    document.body.innerHTML = `<div class="model-icon">icon</div>`;
    expect(getModelName(document)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// isStreaming (P5.B gap C2 regression)
// ---------------------------------------------------------------------------

describe("isStreaming", () => {
  it("true when the dedicated stop-button testid is present", () => {
    document.body.innerHTML = `<button data-testid="stop-button">Stop</button>`;
    expect(isStreaming(document)).toBe(true);
  });

  it("true when a Stop-response button aria-label is present", () => {
    document.body.innerHTML = `<button aria-label="Stop response">X</button>`;
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
    document.body.innerHTML = `<button aria-label="Send message">Send</button>`;
    expect(isStreaming(document)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// extractMessages — /share/ URL skip (P5.B gap C9 regression)
// ---------------------------------------------------------------------------

describe("extractMessages — /share/ URL skip (C9)", () => {
  it("returns [] on /share/<uuid> paths regardless of DOM content", () => {
    document.body.innerHTML = `
      <main>
        <div data-testid="human-turn">someone else's question</div>
        <div data-testid="assistant-turn">someone else's answer</div>
      </main>`;
    expect(extractMessages(document, "/share/abcd1234-uuid")).toEqual([]);
  });

  it("still captures on normal /chat/<uuid> with the same DOM", () => {
    document.body.innerHTML = `
      <main>
        <div data-testid="human-turn">my question</div>
        <div data-testid="assistant-turn">my answer</div>
      </main>`;
    expect(
      extractMessages(document, "/chat/abcd1234-uuid").length,
    ).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// extractMessages — Strategy 1 (dedicated selectors, sorted)
// ---------------------------------------------------------------------------

describe("extractMessages — dedicated selectors", () => {
  it("captures a human + assistant pair", () => {
    document.body.innerHTML = `
      <main>
        <div data-testid="human-turn">hi Claude</div>
        <div data-testid="assistant-turn">hello, I'm Claude</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toBe("hi Claude");
    expect(msgs[1].role).toBe("assistant");
  });

  it("wraps thinking panels around the reply text", () => {
    document.body.innerHTML = `
      <main>
        <div data-testid="human-turn">question</div>
        <div data-testid="assistant-turn">
          <details>
            <summary>Reasoning</summary>
            <div class="markdown">reasoning body</div>
          </details>
          answer text
        </div>
      </main>`;
    const msgs = extractMessages(document);
    const assistant = msgs.find((m) => m.role === "assistant")!;
    expect(assistant.content).toContain("<thinking>");
    expect(assistant.content).toContain("reasoning body");
    expect(assistant.content).toContain("answer text");
  });

  it("honours .font-user-message / .font-claude-message", () => {
    document.body.innerHTML = `
      <main>
        <div class="font-user-message">user via font class</div>
        <div class="font-claude-message">claude via font class</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });
});

// ---------------------------------------------------------------------------
// Strategy 2 — generic message-block role detection
// ---------------------------------------------------------------------------

describe("extractMessages — generic message blocks", () => {
  it("detects role from class keyword", () => {
    document.body.innerHTML = `
      <main>
        <div class="some-human-message">hi</div>
        <div class="some-assistant-message">hello</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("detects role from data-role attribute", () => {
    document.body.innerHTML = `
      <main>
        <div class="message" data-role="user">u</div>
        <div class="message" data-role="assistant">a</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("detects role from aria-label", () => {
    document.body.innerHTML = `
      <main>
        <div class="message" aria-label="Human message">u</div>
        <div class="message" aria-label="Claude response">a</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs.some((m) => m.role === "user")).toBe(true);
    expect(msgs.some((m) => m.role === "assistant")).toBe(true);
  });

  it("drops blocks with unknown role", () => {
    document.body.innerHTML = `
      <main>
        <div class="message">ambiguous</div>
      </main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Strategy 3 — direct children of container (last resort)
// ---------------------------------------------------------------------------

describe("extractMessages — container direct-children fallback", () => {
  it("alternates user/assistant by index", () => {
    document.body.innerHTML = `
      <main class="flex flex-col">
        <div>long enough first turn</div>
        <div>long enough second turn</div>
        <div>long enough third turn</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(3);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[2].role).toBe("user");
  });

  it("returns [] when nothing matches any strategy", () => {
    document.body.innerHTML = `<main><span>x</span></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
