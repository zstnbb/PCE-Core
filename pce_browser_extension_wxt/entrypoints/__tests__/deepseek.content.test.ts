/**
 * Tests for `entrypoints/deepseek.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  cleanText,
  extractMessages,
  extractUserFromTurn,
  findTurnContainer,
  getContainer,
  getModelName,
  getSessionHint,
} from "../deepseek.content";

beforeEach(() => {
  document.body.innerHTML = "";
  // happy-dom default title is empty; reset for determinism
  document.title = "";
});

describe("getSessionHint", () => {
  it("extracts IDs from /chat/<id>", () => {
    expect(getSessionHint("/chat/abc-123")).toBe("abc-123");
  });

  it("extracts IDs from /chat/s/<id>", () => {
    expect(getSessionHint("/chat/s/abc123XYZ")).toBe("abc123XYZ");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/history")).toBe("/history");
  });
});

describe("getContainer", () => {
  it("prefers main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to body when no main", () => {
    document.body.innerHTML = `<p>hi</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("findTurnContainer", () => {
  it("locates the parent whose siblings hold the user message", () => {
    document.body.innerHTML = `
      <main>
        <div id="turn">
          <div class="user-side">the user question</div>
          <div class="assistant-side">
            <div class="ds-markdown" id="md">the reply</div>
          </div>
        </div>
      </main>`;
    const md = document.getElementById("md")!;
    const turn = findTurnContainer(md);
    expect(turn).not.toBeNull();
    // The user-side has visible text and no nested .ds-markdown,
    // so findTurnContainer walks up until it finds a parent whose
    // children include both sides. The resulting container is
    // <div id="turn">'s parent (the <main>) in this structure.
    expect(turn!.contains(md)).toBe(true);
  });

  it("returns null when no user-side sibling exists", () => {
    document.body.innerHTML = `
      <main>
        <div>
          <div class="ds-markdown" id="md">only the reply</div>
        </div>
      </main>`;
    expect(findTurnContainer(document.getElementById("md")!)).toBeNull();
  });
});

describe("extractUserFromTurn", () => {
  it("returns the sibling text that doesn't contain .ds-markdown", () => {
    document.body.innerHTML = `
      <div id="turn">
        <div id="u">the user question here</div>
        <div id="a">
          <div class="ds-markdown" id="md">reply</div>
        </div>
      </div>`;
    const turn = document.getElementById("turn")!;
    const md = document.getElementById("md")!;
    expect(extractUserFromTurn(turn, md)).toContain("the user question here");
  });

  it("returns null when all siblings contain .ds-markdown", () => {
    document.body.innerHTML = `
      <div id="turn">
        <div><div class="ds-markdown" id="md1">a</div></div>
        <div><div class="ds-markdown">b</div></div>
      </div>`;
    const turn = document.getElementById("turn")!;
    const md = document.getElementById("md1")!;
    expect(extractUserFromTurn(turn, md)).toBeNull();
  });
});

describe("cleanText", () => {
  it("strips noise elements (icons, buttons, avatars, toolbars)", () => {
    document.body.innerHTML = `
      <div id="x">
        <button>copy</button>
        <svg>icon</svg>
        <div class="avatar">A</div>
        <div class="toolbar">T</div>
        visible message body
      </div>`;
    const t = cleanText(document.getElementById("x"));
    expect(t).toContain("visible message body");
    expect(t).not.toContain("copy");
    expect(t).not.toContain("icon");
  });

  it("prefers inner .ds-markdown text when present", () => {
    document.body.innerHTML = `
      <div id="x">
        noise header
        <div class="ds-markdown">the markdown body</div>
      </div>`;
    expect(cleanText(document.getElementById("x"))).toBe("the markdown body");
  });
});

describe("extractMessages — Strategy 1 (.ds-markdown anchor)", () => {
  it("pairs user text with assistant reply when siblings present", () => {
    document.body.innerHTML = `
      <main>
        <div class="turn-wrap">
          <div>the user question</div>
          <div>
            <div class="ds-markdown">the assistant reply</div>
          </div>
        </div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs.some((m) => m.role === "user" && m.content.includes("user question"))).toBe(true);
    expect(msgs.some((m) => m.role === "assistant" && m.content.includes("assistant reply"))).toBe(true);
  });
});

describe("extractMessages — Strategy 2 (role-attributed)", () => {
  it("captures [data-role] pairs", () => {
    document.body.innerHTML = `
      <main>
        <div data-role="user">u content here</div>
        <div data-role="assistant">a content here</div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });
});

describe("extractMessages — empty", () => {
  it("returns [] when no markers match", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});

describe("getModelName", () => {
  it("reads from [class*='model'] [class*='name']", () => {
    document.body.innerHTML = `<div class="model-info"><span class="name">DeepSeek-V3</span></div>`;
    expect(getModelName(document)).toBe("DeepSeek-V3");
  });

  it("falls back to title regex", () => {
    document.title = "Chat with DeepSeek-R1";
    expect(getModelName(document)).toContain("DeepSeek");
  });

  it("falls back to body-text DeepSeek-R1 detection", () => {
    document.body.innerHTML = `<p>We are using DeepSeek-R1 for this session</p>`;
    expect(getModelName(document)).toBe("DeepSeek-R1");
  });

  it("returns 'DeepSeek' as ultimate fallback", () => {
    document.body.innerHTML = `<p>no markers here</p>`;
    expect(getModelName(document)).toBe("DeepSeek");
  });
});
