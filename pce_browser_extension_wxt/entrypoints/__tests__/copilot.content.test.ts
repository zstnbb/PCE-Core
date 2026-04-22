// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/copilot.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  extractText,
  getContainer,
  getModelName,
  getSessionHint,
  isStreaming,
} from "../copilot.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("getSessionHint", () => {
  it("matches /chat/<id>", () => {
    expect(getSessionHint("/chat/abc123")).toBe("abc123");
  });

  it("matches /c/<id>", () => {
    expect(getSessionHint("/c/xyz-789")).toBe("xyz-789");
  });

  it("matches /thread/<id>", () => {
    expect(getSessionHint("/thread/thread_abc")).toBe("thread_abc");
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

  it("falls back to [class*=chat-container]", () => {
    document.body.innerHTML = `<div class="chat-container-wrap" id="c">x</div>`;
    expect(getContainer(document)!.id).toBe("c");
  });

  it("falls back to body", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("detectRole", () => {
  it("flags user via class / source / data-testid", () => {
    document.body.innerHTML = `
      <div id="c" class="user-message">u</div>
      <div id="s" source="user">u</div>
      <div id="t" data-testid="user-msg-1">u</div>`;
    expect(detectRole(document.getElementById("c")!)).toBe("user");
    expect(detectRole(document.getElementById("s")!)).toBe("user");
    expect(detectRole(document.getElementById("t")!)).toBe("user");
  });

  it("flags assistant via class keywords (bot / copilot) / source / testId", () => {
    document.body.innerHTML = `
      <div id="b" class="bot-message">a</div>
      <div id="co" class="copilot-response">a</div>
      <div id="s" source="bot">a</div>
      <div id="t" data-testid="bot-msg-1">a</div>`;
    expect(detectRole(document.getElementById("b")!)).toBe("assistant");
    expect(detectRole(document.getElementById("co")!)).toBe("assistant");
    expect(detectRole(document.getElementById("s")!)).toBe("assistant");
    expect(detectRole(document.getElementById("t")!)).toBe("assistant");
  });

  it("returns unknown when nothing matches", () => {
    document.body.innerHTML = `<div id="x">x</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

describe("extractText", () => {
  it("strips script/style/button/citation/action nodes", () => {
    document.body.innerHTML = `
      <div id="x">
        <button>copy</button>
        <span class="citation">[1]</span>
        <span class="action-menu">...</span>
        visible body text
      </div>`;
    const t = extractText(document.getElementById("x")!);
    expect(t).toContain("visible body text");
    expect(t).not.toContain("copy");
    expect(t).not.toContain("[1]");
  });

  it("prefers .ac-textBlock / .markdown children", () => {
    document.body.innerHTML = `
      <div id="x">
        header noise
        <div class="ac-textBlock">the actual reply body</div>
      </div>`;
    expect(extractText(document.getElementById("x")!)).toBe(
      "the actual reply body",
    );
  });

  // P5.B gap MCP6 (empty-reply bug): when the rendered child is an
  // empty React scaffold, fall through to the whole-clone text so the
  // runtime's requireBothRoles gate has a chance to observe a
  // non-empty assistant side. Before this fix the empty ``.ac-textBlock``
  // caused ``extractText`` to return "", the message was filtered out,
  // and the capture landed with only the user side.
  it("falls through to whole-clone text when the rendered child is empty", () => {
    document.body.innerHTML = `
      <div id="x">
        non-empty body scaffold text
        <div class="ac-textBlock"></div>
      </div>`;
    const t = extractText(document.getElementById("x")!);
    expect(t).toContain("non-empty body scaffold text");
    expect(t).not.toBe("");
  });

  it("returns the rendered child text when it is non-empty (no regression)", () => {
    document.body.innerHTML = `
      <div id="x">
        outer noise that should be ignored
        <div class="markdown">actual markdown body</div>
      </div>`;
    expect(extractText(document.getElementById("x")!)).toBe(
      "actual markdown body",
    );
  });
});

// P5.B gap MCP1 regression: isStreaming gate must detect Copilot's
// Stop/Cancel button so mid-stream capture is deferred.
describe("isStreaming", () => {
  it("true when a Stop-generating button is present", () => {
    document.body.innerHTML = `<button aria-label="Stop generating">X</button>`;
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
    document.body.innerHTML = `<button aria-label="Send">Send</button>`;
    expect(isStreaming(document)).toBe(false);
  });
});

describe("getModelName", () => {
  it("reads model text from [class*=model] / [data-model]", () => {
    document.body.innerHTML = `<div class="model-badge">GPT-4o</div>`;
    expect(getModelName(document)).toBe("GPT-4o");
  });

  it("falls back to [data-model] attribute when textContent is empty", () => {
    document.body.innerHTML = `<div class="model" data-model="gpt-4"></div>`;
    expect(getModelName(document)).toBe("gpt-4");
  });

  it("returns null when no match", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getModelName(document)).toBeNull();
  });
});

describe("extractMessages", () => {
  it("captures user + bot pairs from [class*=message]", () => {
    document.body.innerHTML = `
      <main>
        <div class="user-message"><div class="ac-textBlock">u question</div></div>
        <div class="bot-message"><div class="ac-textBlock">bot answer</div></div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toBe("u question");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toBe("bot answer");
  });

  it("captures cib-message-group[source] entries", () => {
    document.body.innerHTML = `
      <main>
        <cib-message-group source="user"><p>u</p></cib-message-group>
        <cib-message-group source="bot"><p>a</p></cib-message-group>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("returns [] when no strategy yields ≥ 2 messages", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});

// P5.B gap MCP4 regression: shared URLs must return [] even when the
// DOM has valid-looking turns (because the current user didn't author
// them). Mirrors the G8 / C9 pattern in gemini/claude content scripts.
describe("extractMessages — /share/ URL skip (MCP4)", () => {
  it("returns [] on /share/<id> paths regardless of DOM content", () => {
    document.body.innerHTML = `
      <main>
        <div class="user-message"><div class="ac-textBlock">someone else's question</div></div>
        <div class="bot-message"><div class="ac-textBlock">someone else's answer</div></div>
      </main>`;
    expect(extractMessages(document, "/share/abc123")).toEqual([]);
  });

  it("still captures on non-share paths with the same DOM", () => {
    document.body.innerHTML = `
      <main>
        <div class="user-message"><div class="ac-textBlock">u question</div></div>
        <div class="bot-message"><div class="ac-textBlock">bot answer</div></div>
      </main>`;
    const msgs = extractMessages(document, "/chat/thread_1");
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("is case-insensitive on /Share/<id>", () => {
    document.body.innerHTML = `
      <main>
        <div class="user-message"><div class="ac-textBlock">x</div></div>
        <div class="bot-message"><div class="ac-textBlock">y</div></div>
      </main>`;
    expect(extractMessages(document, "/Share/abc")).toEqual([]);
  });
});
