/**
 * Tests for `entrypoints/manus.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  dedupeAttachments,
  extractLocalAttachments,
  extractMessages,
  getChatBox,
  getContainer,
  getModelName,
  getSessionHint,
  normalizeText,
} from "../manus.content";
import type { PceAttachment } from "../../utils/pce-dom";

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("normalizeText", () => {
  it("strips Chinese UI noise lines", () => {
    expect(normalizeText("Lite\n分享\n升级\n任务已完成\n推荐追问\nreal")).toBe(
      "real",
    );
  });

  it("collapses internal whitespace", () => {
    expect(normalizeText("line   one\nline   two")).toBe("line one\nline two");
  });

  it("handles null / empty", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText(undefined)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts ID from /app/<id>", () => {
    expect(getSessionHint("/app/task-abc-123")).toBe("task-abc-123");
  });

  it("falls back to pathname", () => {
    expect(getSessionHint("/home")).toBe("/home");
  });
});

describe("getChatBox / getContainer", () => {
  it("prefers #manus-chat-box", () => {
    document.body.innerHTML = `<div id="manus-chat-box">x</div>`;
    expect(getChatBox(document)!.id).toBe("manus-chat-box");
    expect(getContainer(document)!.id).toBe("manus-chat-box");
  });

  it("falls back to #manus-home-page-session-content", () => {
    document.body.innerHTML = `<div id="manus-home-page-session-content">x</div>`;
    expect(getChatBox(document)!.id).toBe("manus-home-page-session-content");
  });

  it("getContainer falls back to body when no chat box", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("getModelName", () => {
  it("reads from #manus-chat-box [aria-haspopup='dialog'] span.truncate", () => {
    document.body.innerHTML = `
      <div id="manus-chat-box">
        <div aria-haspopup="dialog">
          <span class="truncate">Manus-Pro-v3</span>
        </div>
      </div>`;
    expect(getModelName(document)).toBe("Manus-Pro-v3");
  });

  it("falls back to #manus-chat-box span.truncate", () => {
    document.body.innerHTML = `
      <div id="manus-chat-box">
        <span class="truncate">Manus-Lite</span>
      </div>`;
    expect(getModelName(document)).toBe("Manus-Lite");
  });

  it("returns 'Manus' ultimate fallback", () => {
    document.body.innerHTML = `<p>nothing</p>`;
    expect(getModelName(document)).toBe("Manus");
  });
});

describe("dedupeAttachments", () => {
  it("removes duplicate attachments by JSON.stringify key", () => {
    const list = [
      { type: "code_block", code: "x" },
      { type: "code_block", code: "x" },
      { type: "citation", url: "y", title: "Y" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ] as any as PceAttachment[];
    expect(dedupeAttachments(list)).toHaveLength(2);
  });

  it("handles nullish input", () => {
    expect(dedupeAttachments(null)).toEqual([]);
    expect(dedupeAttachments(undefined)).toEqual([]);
  });
});

describe("extractLocalAttachments", () => {
  it("extracts <pre> code blocks with language label", () => {
    document.body.innerHTML = `
      <div id="wrap">
        <div class="manus-markdown">
          <span class="text-sm">python</span>
          <pre>print('hello')</pre>
        </div>
      </div>`;
    const out = extractLocalAttachments(
      document.getElementById("wrap")!.querySelector(".manus-markdown"),
      "manus.im",
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const code = out.find((a: any) => a.type === "code_block") as any;
    expect(code).toBeDefined();
    expect(code.code).toContain("print('hello')");
    expect(code.language).toBe("python");
  });

  it("extracts external citation links, skips same-origin", () => {
    document.body.innerHTML = `
      <div id="wrap">
        <a href="https://manus.im/internal">self</a>
        <a href="https://external.example.com/paper">external</a>
      </div>`;
    const out = extractLocalAttachments(
      document.getElementById("wrap")!,
      "manus.im",
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const citations = out.filter((a: any) => a.type === "citation");
    expect(citations).toHaveLength(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((citations[0] as any).url).toContain("external.example.com");
  });

  it("returns [] for null input", () => {
    expect(extractLocalAttachments(null)).toEqual([]);
  });
});

describe("extractMessages", () => {
  it("captures a user bubble + assistant markdown pair", () => {
    document.body.innerHTML = `
      <div id="manus-chat-box">
        <div class="items-end">
          <div class="u-break-words">user prompt here</div>
        </div>
        <div class="manus-markdown">
          <div class="prose">assistant reply body</div>
        </div>
      </div>`;
    const msgs = extractMessages(document, "manus.im");
    expect(msgs.length).toBeGreaterThanOrEqual(2);
    expect(msgs.some((m) => m.role === "user" && m.content.includes("user prompt"))).toBe(
      true,
    );
    expect(
      msgs.some((m) => m.role === "assistant" && m.content.includes("assistant reply")),
    ).toBe(true);
  });

  it("dedupes repeated role+content pairs", () => {
    document.body.innerHTML = `
      <div id="manus-chat-box">
        <div class="items-end">
          <div class="u-break-words">dup user</div>
        </div>
        <div class="items-end">
          <div class="u-break-words">dup user</div>
        </div>
        <div class="manus-markdown">a</div>
      </div>`;
    const msgs = extractMessages(document, "manus.im");
    const userMsgs = msgs.filter((m) => m.role === "user");
    expect(userMsgs).toHaveLength(1);
  });

  it("returns [] when no chat box exists", () => {
    document.body.innerHTML = `<p>no chat box</p>`;
    expect(extractMessages(document, "manus.im")).toEqual([]);
  });
});
