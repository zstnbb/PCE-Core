// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/poe.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  extractText,
  getContainer,
  getMessageNodes,
  getModelName,
  getSessionHint,
  normalizeText,
} from "../poe.content";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips HH:MM timestamps", () => {
    expect(normalizeText("12:34\nreal content")).toBe("real content");
    expect(normalizeText("9:05\nreal content")).toBe("real content");
  });

  it("strips 'compare' / 'share' / 'copy' / 'copied' lines", () => {
    expect(normalizeText("compare\nshare\ncopy\ncopied\nreal")).toBe("real");
  });

  it("collapses internal whitespace", () => {
    expect(normalizeText("line   with   spaces")).toBe("line with spaces");
  });

  it("returns empty string for null/empty input", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText(undefined)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts the last path segment for /chat/<bot>/<id>", () => {
    expect(getSessionHint("/chat/gpt-4/abc123")).toBe("abc123");
  });

  it("falls back to pathname when not a chat URL", () => {
    expect(getSessionHint("/explore")).toBe("/explore");
  });
});

describe("getContainer", () => {
  it("prefers ChatMessagesScrollWrapper container", () => {
    document.body.innerHTML = `
      <div class="ChatMessagesScrollWrapper_scrollableContainerWrapper" id="c">x</div>`;
    expect(getContainer(document)!.id).toBe("c");
  });

  it("falls back to main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });
});

describe("getMessageNodes / detectRole", () => {
  it("finds [id^=message-] elements with ChatMessage_chatMessage class", () => {
    document.body.innerHTML = `
      <div id="message-1" class="ChatMessage_chatMessage_xxx">
        <div class="rightSideMessageWrapper">u</div>
      </div>
      <div id="message-2" class="ChatMessage_chatMessage_xxx">
        <div class="leftSideMessageBubble_yyy">a</div>
      </div>
      <div id="other" class="ChatMessage_chatMessage_zzz">ignored unless role found</div>`;
    const nodes = getMessageNodes(document);
    expect(nodes.length).toBe(3);
  });

  it("detects right-side as user", () => {
    document.body.innerHTML = `
      <div id="m" class="rightSideMessageWrapper_xxx">u</div>`;
    expect(detectRole(document.getElementById("m")!)).toBe("user");
  });

  it("detects left-side / BotMessageHeader as assistant", () => {
    document.body.innerHTML = `
      <div id="m1" class="leftSideMessageBubble_xxx">a</div>
      <div id="m2">
        <div class="BotMessageHeader_x">bot</div>
      </div>`;
    expect(detectRole(document.getElementById("m1")!)).toBe("assistant");
    expect(detectRole(document.getElementById("m2")!)).toBe("assistant");
  });

  it("returns unknown when no markers match", () => {
    document.body.innerHTML = `<div id="m">x</div>`;
    expect(detectRole(document.getElementById("m")!)).toBe("unknown");
  });
});

describe("extractText", () => {
  it("prefers Message_messageTextContainer > Markdown > selectableText", () => {
    document.body.innerHTML = `
      <div id="x">
        <div class="Message_messageTextContainer_xx">
          the message body
        </div>
      </div>`;
    expect(extractText(document.getElementById("x")!)).toBe("the message body");
  });
});

describe("getModelName", () => {
  it("reads bot name from BotHeader_textContainer p", () => {
    document.body.innerHTML = `
      <div class="BotHeader_textContainer_x">
        <p>Claude-3.5-Sonnet</p>
      </div>`;
    expect(getModelName(document)).toBe("Claude-3.5-Sonnet");
  });

  it("returns null when no bot-name element exists", () => {
    document.body.innerHTML = `<p>nothing</p>`;
    expect(getModelName(document)).toBeNull();
  });
});

describe("extractMessages", () => {
  it("extracts user + assistant pair, dedupes duplicates", () => {
    document.body.innerHTML = `
      <div id="message-1" class="ChatMessage_chatMessage_x">
        <div class="rightSideMessageWrapper_x">
          <div class="Message_messageTextContainer_x">user question</div>
        </div>
      </div>
      <div id="message-2" class="ChatMessage_chatMessage_x">
        <div class="rightSideMessageWrapper_x">
          <div class="Message_messageTextContainer_x">user question</div>
        </div>
      </div>
      <div id="message-3" class="ChatMessage_chatMessage_x">
        <div class="leftSideMessageBubble_y">
          <div class="Message_messageTextContainer_x">assistant reply</div>
        </div>
      </div>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("returns [] when no message nodes match", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
