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

  it("dedupes turns with identical prefix", () => {
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
