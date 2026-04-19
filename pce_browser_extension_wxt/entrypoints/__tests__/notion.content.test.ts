// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/notion.content.ts` (P5.A-8).
 *
 * These tests exercise pure helpers and the *documented selectors*
 * from the adapter. They do NOT claim parity with the live Notion AI
 * product — the adapter header flags that selectors need real-page
 * validation. What the tests *do* guarantee:
 *
 *   1. ``extractMessages`` returns ``[]`` when no AI block is present
 *      (so the runtime stays silent on normal Notion pages).
 *   2. When the documented AI selectors *do* match, extraction +
 *      dedup + normalisation behave as specified.
 *   3. Session-hint regex accepts both dashed and un-dashed UUID
 *      forms Notion serves in its URLs.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  extractMessages,
  getContainer,
  getSessionHint,
  normalizeText,
} from "../notion.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips Notion-specific UI noise", () => {
    expect(normalizeText("AI is writing…\nreal content")).toBe("real content");
    expect(normalizeText("Try again\nMake longer\nreal content")).toBe(
      "real content",
    );
    expect(normalizeText("done\ncancel\nreal")).toBe("real");
  });

  it("preserves multi-line real content", () => {
    expect(normalizeText("line 1\nline 2\nline 3")).toBe("line 1\nline 2\nline 3");
  });

  it("collapses internal whitespace per line", () => {
    expect(normalizeText("  lots   of    spaces  ")).toBe("lots of spaces");
  });

  it("handles null/empty input", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText(undefined)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts un-dashed 32-hex id from page path", () => {
    expect(
      getSessionHint("/My-Workspace/Meeting-Notes-abcdef0123456789abcdef0123456789"),
    ).toBe("abcdef0123456789abcdef0123456789");
  });

  it("extracts dashed uuid from path, stripping dashes", () => {
    expect(
      getSessionHint("/Page-11111111-2222-3333-4444-555555555555"),
    ).toBe("11111111222233334444555555555555");
  });

  it("falls back to last path segment when no uuid", () => {
    expect(getSessionHint("/just-some-page")).toBe("just-some-page");
  });

  it("returns null on empty pathname", () => {
    expect(getSessionHint("")).toBeNull();
  });
});

describe("getContainer", () => {
  it("prefers .notion-page-content", () => {
    document.body.innerHTML = `
      <div class="notion-page-content" id="p">x</div>
      <main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("p");
  });

  it("falls back to main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to body when nothing matches", () => {
    document.body.innerHTML = `<p>x</p>`;
    const c = getContainer(document);
    expect(c).toBe(document.body);
  });
});

describe("extractMessages", () => {
  it("returns [] on a vanilla Notion page (no AI block)", () => {
    document.body.innerHTML = `
      <div class="notion-page-content">
        <div data-block-id="abc" data-block-type="text">just text</div>
      </div>`;
    expect(extractMessages(document)).toEqual([]);
  });

  it("captures a single AI response block", () => {
    document.body.innerHTML = `
      <div class="notion-page-content">
        <div data-block-type="ai">hello from the AI block</div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toEqual({
      role: "assistant",
      content: "hello from the AI block",
    });
  });

  it("pairs user prompt with AI response when both are present", () => {
    document.body.innerHTML = `
      <div class="notion-page-content">
        <input data-testid="ai-prompt" value="summarise this doc" />
        <div data-block-type="ai">here is a summary …</div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toEqual({ role: "user", content: "summarise this doc" });
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("here is a summary");
  });

  it("dedupes duplicate AI blocks", () => {
    document.body.innerHTML = `
      <div class="notion-page-content">
        <div data-block-type="ai">same output</div>
        <div class="notion-ai-response">same output</div>
      </div>`;
    expect(extractMessages(document)).toHaveLength(1);
  });

  it("ignores AI blocks whose text is pure UI noise", () => {
    document.body.innerHTML = `
      <div class="notion-page-content">
        <div data-block-type="ai">AI is writing…</div>
      </div>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
