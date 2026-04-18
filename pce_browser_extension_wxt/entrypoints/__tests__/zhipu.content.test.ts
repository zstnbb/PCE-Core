/**
 * Tests for `entrypoints/zhipu.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  getContainer,
  getModelName,
  getSessionHint,
  normalizeText,
} from "../zhipu.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("collapses internal whitespace", () => {
    expect(normalizeText("line   one\n\nline   two")).toBe(
      "line one\nline two",
    );
  });

  it("strips empty lines", () => {
    expect(normalizeText("\n\nreal\n\n\n")).toBe("real");
  });

  it("handles null / empty", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText(undefined)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts UUID from /c/<uuid>", () => {
    expect(getSessionHint("/c/abc-123-def")).toBe("abc-123-def");
  });

  it("is case-insensitive on /c/", () => {
    expect(getSessionHint("/C/deadbeef")).toBe("deadbeef");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/home")).toBe("/home");
  });
});

describe("getContainer", () => {
  it("prefers main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to body", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("getModelName", () => {
  it("reads GLM-* from body text", () => {
    document.body.innerHTML = `<p>Running on GLM-4.5 today</p>`;
    expect(getModelName(document)).toBe("GLM-4.5");
  });

  it("is case-insensitive", () => {
    document.body.innerHTML = `<p>Using glm-4-flash for this</p>`;
    expect(getModelName(document)).toBe("glm-4-flash");
  });

  it("returns null when no GLM-* match", () => {
    document.body.innerHTML = `<p>no model marker</p>`;
    expect(getModelName(document)).toBeNull();
  });
});

describe("detectRole", () => {
  it("chat-user / user-message class → user", () => {
    document.body.innerHTML = `
      <div id="a" class="chat-user markdown-prose">u</div>
      <div id="b" class="user-message">u</div>`;
    expect(detectRole(document.getElementById("a")!)).toBe("user");
    expect(detectRole(document.getElementById("b")!)).toBe("user");
  });

  it("chat-assistant class → assistant", () => {
    document.body.innerHTML = `
      <div id="a" class="chat-assistant markdown-prose">a</div>
      <div id="b" class="chat-assistant">a</div>`;
    expect(detectRole(document.getElementById("a")!)).toBe("assistant");
    expect(detectRole(document.getElementById("b")!)).toBe("assistant");
  });

  it("returns unknown otherwise", () => {
    document.body.innerHTML = `<div id="x" class="message-1">x</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

describe("extractMessages", () => {
  it("captures chat-user + chat-assistant pair", () => {
    document.body.innerHTML = `
      <main>
        <div class="chat-user markdown-prose">user question text</div>
        <div class="chat-assistant markdown-prose">the assistant reply</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toBe("user question text");
    expect(msgs[1].role).toBe("assistant");
  });

  it("honours .user-message as user", () => {
    document.body.innerHTML = `
      <main>
        <div class="user-message">u</div>
        <div class="chat-assistant">a</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
  });

  it("skips unknown-role nodes", () => {
    document.body.innerHTML = `
      <main>
        <div class="message-1">ambiguous</div>
        <div class="chat-user">u</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("user");
  });

  it("dedupes repeated role+content pairs", () => {
    document.body.innerHTML = `
      <main>
        <div class="chat-user">same text</div>
        <div class="chat-user">same text</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(1);
  });

  it("returns [] when nothing matches", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
