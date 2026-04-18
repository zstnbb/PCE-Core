/**
 * Tests for `entrypoints/grok.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  getContainer,
  getModelName,
  getSessionHint,
  normalizeText,
} from "../grok.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips 'share' / 'auto' / 'copy' / 'copied' lines", () => {
    expect(normalizeText("share\nauto\ncopy\ncopied\nreal")).toBe("real");
  });

  it("strips timing lines like '3s' / '12.5 sec' / '5 seconds'", () => {
    expect(normalizeText("3s\nreal")).toBe("real");
    expect(normalizeText("12.5 sec\nreal")).toBe("real");
    expect(normalizeText("5 seconds\nreal")).toBe("real");
  });

  it("strips mode toggles", () => {
    expect(normalizeText("fast mode\ndeepsearch\nping\nreal")).toBe("real");
    expect(normalizeText("quick mode\nreal")).toBe("real");
  });

  it("preserves multi-line real content", () => {
    expect(normalizeText("line 1\nline 2\nline 3")).toBe("line 1\nline 2\nline 3");
  });

  it("handles null/empty input", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText(undefined)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts UUID from /c/<uuid>", () => {
    expect(getSessionHint("/c/abc123-def")).toBe("abc123-def");
  });

  it("is case-insensitive on /c/ prefix", () => {
    expect(getSessionHint("/C/deadbeef")).toBe("deadbeef");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/home")).toBe("/home");
  });
});

describe("getContainer", () => {
  it("prefers #last-reply-container", () => {
    document.body.innerHTML = `<div id="last-reply-container">x</div><main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("last-reply-container");
  });

  it("falls back to main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("returns null when nothing matches", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBeNull();
  });
});

describe("getModelName", () => {
  it("extracts grok-* from body text", () => {
    document.body.innerHTML = `<p>Running on grok-2-mini today</p>`;
    expect(getModelName(document)).toBe("grok-2-mini");
  });

  it("returns null when no grok-* match", () => {
    document.body.innerHTML = `<p>something else</p>`;
    expect(getModelName(document)).toBeNull();
  });
});

describe("detectRole", () => {
  it("items-end → user", () => {
    document.body.innerHTML = `<div id="x" class="flex items-end gap-2">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("items-start → assistant", () => {
    document.body.innerHTML = `<div id="x" class="flex items-start gap-2">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("unknown when no alignment class", () => {
    document.body.innerHTML = `<div id="x" class="flex">?</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

describe("extractMessages", () => {
  it("pairs items-end user + items-start assistant from [id^=response-]", () => {
    document.body.innerHTML = `
      <div id="last-reply-container">
        <div id="response-1" class="flex items-end">
          <div class="message-bubble">user question</div>
        </div>
        <div id="response-2" class="flex items-start">
          <div class="message-bubble">assistant reply</div>
        </div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toBe("user question");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("assistant reply");
  });

  it("dedupes repeated role+content pairs", () => {
    document.body.innerHTML = `
      <div id="last-reply-container">
        <div id="response-1" class="items-end">
          <div class="message-bubble">same text</div>
        </div>
        <div id="response-2" class="items-end">
          <div class="message-bubble">same text</div>
        </div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(1);
  });

  it("returns [] when no container exists", () => {
    document.body.innerHTML = `<p>no container</p>`;
    expect(extractMessages(document)).toEqual([]);
  });

  it("ignores response nodes without alignment classes", () => {
    document.body.innerHTML = `
      <div id="last-reply-container">
        <div id="response-1" class="flex">
          <div class="message-bubble">ambiguous</div>
        </div>
      </div>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
