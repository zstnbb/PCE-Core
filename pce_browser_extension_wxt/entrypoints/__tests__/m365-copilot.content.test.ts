// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/m365-copilot.content.ts` (P5.A-8).
 *
 * Covers pure helpers and the documented selectors against happy-dom
 * fixtures. Does NOT claim parity with live M365 Copilot UI — the
 * adapter header flags live validation as still-required. What these
 * tests *do* guarantee is:
 *
 *   1. ``extractMessages`` returns ``[]`` when no Copilot panel is
 *      mounted (silent-on-normal-Office behaviour).
 *   2. ``detectRole`` honours ``data-message-author-role`` first and
 *      falls back to class-name / aria-label keywords.
 *   3. Session-hint extraction handles Office Web GUID-in-querystring
 *      shapes + explicit ``/copilot/chat/<id>`` URLs.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  getContainer,
  getSessionHint,
  normalizeText,
} from "../m365-copilot.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips Copilot chat chrome labels", () => {
    expect(normalizeText("copy\ncopied\nreal")).toBe("real");
    expect(normalizeText("Try again\nReal answer")).toBe("Real answer");
    expect(normalizeText("1 of 3\nthe real thing")).toBe("the real thing");
  });

  it("preserves ordinary multi-line content", () => {
    expect(normalizeText("line a\nline b")).toBe("line a\nline b");
  });

  it("handles null/empty", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts explicit copilot chat id", () => {
    expect(getSessionHint("/copilot/chat/abcd-1234", "")).toBe("abcd-1234");
  });

  it("extracts a brace-wrapped Office GUID from querystring", () => {
    const s = "?sourcedoc=%7B12345678-1234-1234-1234-1234567890AB%7D";
    const hint = getSessionHint("/personal/user/_layouts/WopiFrame.aspx", s);
    // GUID letters get normalised — only curly braces are stripped.
    expect(hint).toBe("12345678-1234-1234-1234-1234567890AB");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/word/view.aspx", "")).toBe("/word/view.aspx");
  });

  it("returns null when both inputs empty", () => {
    expect(getSessionHint("", "")).toBeNull();
  });
});

describe("getContainer", () => {
  it("prefers the documented Copilot aside/panel markers", () => {
    document.body.innerHTML = `
      <aside aria-labelledby="copilot-heading" id="panel">x</aside>
      <main id="m">y</main>`;
    expect(getContainer(document)!.id).toBe("panel");
  });

  it("falls back to main when no panel", () => {
    document.body.innerHTML = `<main id="m">y</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to body when nothing matches", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("detectRole", () => {
  it("prefers data-message-author-role", () => {
    document.body.innerHTML = `<div id="x" data-message-author-role="user">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("falls back to class-name keywords", () => {
    document.body.innerHTML = `<div id="x" class="chat-bubble assistant">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("falls back to aria-label hints", () => {
    document.body.innerHTML = `<div id="x" aria-label="Copilot said">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("returns unknown when no signal", () => {
    document.body.innerHTML = `<div id="x" class="neutral">?</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

describe("extractMessages", () => {
  it("returns [] when no Copilot panel mounted", () => {
    document.body.innerHTML = `<main><p>ordinary Word document</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });

  it("pairs user + assistant turns with data-message-author-role", () => {
    document.body.innerHTML = `
      <aside aria-label="Copilot">
        <div data-message-author-role="user">what is the Q3 revenue?</div>
        <div data-message-author-role="assistant">Q3 revenue was $1.2B.</div>
      </aside>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0]).toEqual({ role: "user", content: "what is the Q3 revenue?" });
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("Q3 revenue");
  });

  it("dedupes repeated role+content pairs", () => {
    document.body.innerHTML = `
      <aside aria-label="Copilot">
        <div data-message-author-role="user">hello</div>
        <div data-message-author-role="user">hello</div>
      </aside>`;
    expect(extractMessages(document)).toHaveLength(1);
  });

  it("skips turns without any role signal", () => {
    document.body.innerHTML = `
      <aside aria-label="Copilot">
        <div>ambiguous — no role attr or class</div>
      </aside>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
