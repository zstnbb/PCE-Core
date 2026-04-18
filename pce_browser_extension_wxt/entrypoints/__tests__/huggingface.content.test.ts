/**
 * Tests for `entrypoints/huggingface.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  detectRole,
  extractMessages,
  extractText,
  getContainer,
  getModelName,
  getSessionHint,
} from "../huggingface.content";

beforeEach(() => {
  document.body.innerHTML = "";
  document.title = "";
});

describe("getSessionHint", () => {
  it("extracts hex IDs from /chat/conversation/<id>", () => {
    expect(getSessionHint("/chat/conversation/abc123def")).toBe("abc123def");
  });

  it("is case-insensitive", () => {
    expect(getSessionHint("/Chat/Conversation/DEADBEEF")).toBe("DEADBEEF");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/chat/models/some-model")).toBe(
      "/chat/models/some-model",
    );
  });
});

describe("getContainer", () => {
  it("prefers [class*=chat-container]", () => {
    document.body.innerHTML = `<div class="chat-container-wrap" id="c">x</div>`;
    expect(getContainer(document)!.id).toBe("c");
  });

  it("falls back to main", () => {
    document.body.innerHTML = `<main id="m">x</main>`;
    expect(getContainer(document)!.id).toBe("m");
  });

  it("falls back to body", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("detectRole", () => {
  it("data-message-role=user → user", () => {
    document.body.innerHTML = `<div id="x" data-message-role="user">u</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("user");
  });

  it("data-message-role=assistant → assistant", () => {
    document.body.innerHTML = `<div id="x" data-message-role="assistant">a</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("assistant");
  });

  it("class keywords user / human → user", () => {
    document.body.innerHTML = `
      <div id="u" class="message-user">u</div>
      <div id="h" class="human-turn">h</div>`;
    expect(detectRole(document.getElementById("u")!)).toBe("user");
    expect(detectRole(document.getElementById("h")!)).toBe("user");
  });

  it("class keywords assistant / bot / model → assistant", () => {
    document.body.innerHTML = `
      <div id="a" class="message-assistant">a</div>
      <div id="b" class="bot-turn">b</div>
      <div id="m" class="model-reply">m</div>`;
    expect(detectRole(document.getElementById("a")!)).toBe("assistant");
    expect(detectRole(document.getElementById("b")!)).toBe("assistant");
    expect(detectRole(document.getElementById("m")!)).toBe("assistant");
  });

  it("returns unknown when nothing matches", () => {
    document.body.innerHTML = `<div id="x">x</div>`;
    expect(detectRole(document.getElementById("x")!)).toBe("unknown");
  });
});

describe("extractText", () => {
  it("strips noise nodes (action / copy / feedback / buttons)", () => {
    document.body.innerHTML = `
      <div id="x">
        <button>copy</button>
        <span class="action-menu">...</span>
        <span class="feedback-rating">👍</span>
        the real body
      </div>`;
    const t = extractText(document.getElementById("x")!);
    expect(t).toContain("the real body");
    expect(t).not.toContain("copy");
  });

  it("prefers .prose / .markdown / [class*=rendered]", () => {
    document.body.innerHTML = `
      <div id="x">
        header noise
        <div class="prose">the rendered body</div>
      </div>`;
    expect(extractText(document.getElementById("x")!)).toBe("the rendered body");
  });
});

describe("getModelName", () => {
  it("reads from [class*=model-name] header", () => {
    document.body.innerHTML = `<span class="model-name">Llama-3-70B</span>`;
    expect(getModelName(document, "/")).toBe("Llama-3-70B");
  });

  it("falls back to /chat/models/<name> URL segment", () => {
    document.body.innerHTML = `<p>no header</p>`;
    expect(getModelName(document, "/chat/models/meta-llama/Llama-3-8B")).toBe(
      "meta-llama/Llama-3-8B",
    );
  });

  it("URL-decodes the model segment", () => {
    document.body.innerHTML = `<p>none</p>`;
    expect(
      getModelName(document, "/chat/models/meta-llama%2FLlama-3-8B"),
    ).toBe("meta-llama/Llama-3-8B");
  });

  it("falls back to page title prefix containing /", () => {
    document.title = "meta-llama/Llama-3 - HuggingFace";
    document.body.innerHTML = `<p>none</p>`;
    expect(getModelName(document, "/")).toBe("meta-llama/Llama-3");
  });

  it("returns null when nothing matches", () => {
    document.body.innerHTML = `<p>x</p>`;
    document.title = "HuggingFace Chat";
    expect(getModelName(document, "/")).toBeNull();
  });
});

describe("extractMessages", () => {
  it("captures a user + assistant pair from [class*=message][class*=user|assistant]", () => {
    document.body.innerHTML = `
      <main>
        <div class="message-user"><div class="prose">u question</div></div>
        <div class="message-assistant"><div class="prose">a reply</div></div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toBe("u question");
    expect(msgs[1].role).toBe("assistant");
  });

  it("captures [data-message-role] pairs", () => {
    document.body.innerHTML = `
      <main>
        <div data-message-role="user"><div class="prose">u</div></div>
        <div data-message-role="assistant"><div class="prose">a</div></div>
      </main>`;
    const msgs = extractMessages(document);
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("returns [] when no matches", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });
});
