// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/figma.content.ts` (P5.A-9).
 *
 * Figma is canvas-heavy and DOM-opaque so the live-page selectors
 * here are best-effort scaffolding. These tests lock in the two
 * invariants that DO matter regardless of selector tuning:
 *
 *   1. Silent-by-default: on a normal canvas page with no AI panel
 *      mounted, ``extractMessages`` returns ``[]`` so the runtime
 *      does not emit captures.
 *   2. When an AI panel + response are both present, the adapter
 *      produces (user, assistant) turns, dedupes, and strips the
 *      button-label UI noise.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  extractMessages,
  getContainer,
  getSessionHint,
  normalizeText,
} from "../figma.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips Figma AI button labels", () => {
    expect(normalizeText("Generate\nReal output")).toBe("Real output");
    expect(normalizeText("Keep\nDiscard\nReal")).toBe("Real");
    expect(normalizeText("42%\nReal")).toBe("Real");
  });

  it("preserves multi-line content", () => {
    expect(normalizeText("line a\nline b")).toBe("line a\nline b");
  });

  it("handles null/empty", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts fileKey from /file/<key>/...", () => {
    expect(
      getSessionHint("/file/aBcDeFgHiJkLmNoPqRsTuV/My-Design"),
    ).toBe("aBcDeFgHiJkLmNoPqRsTuV");
  });

  it("extracts fileKey from /design/<key>/...", () => {
    expect(
      getSessionHint("/design/1234567890abcdef0000/Untitled"),
    ).toBe("1234567890abcdef0000");
  });

  it("extracts fileKey from /board/<key>/... (FigJam)", () => {
    expect(
      getSessionHint("/board/BBBBCCCCDDDDEEEE0000/Brainstorm"),
    ).toBe("BBBBCCCCDDDDEEEE0000");
  });

  it("falls back to pathname when no file URL", () => {
    expect(getSessionHint("/recent")).toBe("/recent");
  });

  it("returns null on empty", () => {
    expect(getSessionHint("")).toBeNull();
  });
});

describe("getContainer", () => {
  it("prefers an AI panel selector over body", () => {
    document.body.innerHTML = `
      <div data-testid="figma-ai-panel" id="ai">x</div>
      <div id="canvas">canvas</div>`;
    expect(getContainer(document)!.id).toBe("ai");
  });

  it("falls back to body when no AI panel", () => {
    document.body.innerHTML = `<div id="canvas">canvas</div>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("extractMessages", () => {
  it("returns [] when no AI panel is mounted", () => {
    document.body.innerHTML = `<div id="canvas">normal editing</div>`;
    expect(extractMessages(document)).toEqual([]);
  });

  it("captures user prompt + AI response when panel is open", () => {
    document.body.innerHTML = `
      <div data-testid="figma-ai-panel">
        <textarea data-testid="ai-prompt-input">design a signup screen</textarea>
        <div data-testid="ai-response">here is a draft signup screen …</div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toEqual({
      role: "user",
      content: "design a signup screen",
    });
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("draft signup screen");
  });

  it("dedupes duplicate AI response blocks", () => {
    document.body.innerHTML = `
      <div data-testid="figma-ai-panel">
        <div data-testid="ai-response">same draft</div>
        <div class="ai-response">same draft</div>
      </div>`;
    const msgs = extractMessages(document);
    const assistants = msgs.filter((m) => m.role === "assistant");
    expect(assistants).toHaveLength(1);
  });

  it("captures assistant-only turn when no prompt input found", () => {
    document.body.innerHTML = `
      <div data-testid="figma-ai-panel">
        <div data-testid="ai-response">here is the output</div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("assistant");
  });
});
