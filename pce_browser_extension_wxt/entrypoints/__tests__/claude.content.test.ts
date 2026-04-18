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
  getSessionHint,
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
