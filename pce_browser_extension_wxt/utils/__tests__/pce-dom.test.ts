/**
 * Tests for `utils/pce-dom.ts`.
 *
 * Covers the pure helpers (no DOM required) and a representative
 * slice of attachment/reply/thinking extraction against happy-dom
 * fixtures. The goal is not to cover every heuristic — the legacy JS
 * was shipped with no unit tests at all, so any regression coverage
 * we add here is strictly better than the status quo.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  extractAttachments,
  extractReplyContent,
  extractThinking,
  fingerprintConversation,
  getSessionHint,
  inferMediaTypeFromName,
  installManualCaptureBridge,
  normalizeCitationUrl,
  type PceAttachment,
} from "../pce-dom";

beforeEach(() => {
  document.body.innerHTML = "";
  document.documentElement.removeAttribute("data-pce-manual-capture");
});

describe("normalizeCitationUrl", () => {
  it("returns empty string for null/undefined/empty", () => {
    expect(normalizeCitationUrl(null)).toBe("");
    expect(normalizeCitationUrl(undefined)).toBe("");
    expect(normalizeCitationUrl("")).toBe("");
  });

  it("unwraps Google /url redirects via ?q=", () => {
    expect(
      normalizeCitationUrl(
        "https://www.google.com/url?q=https%3A//example.com/foo&rct=j",
      ),
    ).toBe("https://example.com/foo");
  });

  it("unwraps Google /url redirects via ?url=", () => {
    expect(
      normalizeCitationUrl(
        "https://google.com/url?url=https://example.org",
      ),
    ).toBe("https://example.org");
  });

  it("leaves non-Google URLs untouched", () => {
    expect(normalizeCitationUrl("https://example.com/foo")).toBe(
      "https://example.com/foo",
    );
  });

  it("resolves relative URLs against the provided base", () => {
    expect(
      normalizeCitationUrl("/bar", "https://example.com/foo/"),
    ).toBe("https://example.com/bar");
  });

  it("returns the input verbatim when URL parsing fails", () => {
    expect(normalizeCitationUrl("not a url", "not a base either")).toBe(
      "not a url",
    );
  });
});

describe("fingerprintConversation", () => {
  it("returns empty string for non-arrays", () => {
    expect(fingerprintConversation(null)).toBe("");
    expect(fingerprintConversation(undefined)).toBe("");
  });

  it("concatenates role:content:att per message, joined by |", () => {
    expect(
      fingerprintConversation([
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ]),
    ).toBe("user:hi:|assistant:hello:");
  });

  it("truncates content to 160 chars", () => {
    const long = "a".repeat(500);
    const fp = fingerprintConversation([{ role: "user", content: long }]);
    expect(fp.length).toBe("user:".length + 160 + ":".length);
  });

  it("serialises attachments by type + meaningful field", () => {
    const fp = fingerprintConversation([
      {
        role: "assistant",
        content: "see attached",
        attachments: [
          { type: "file", file_id: "abc-123" },
          { type: "image_url", url: "https://host/img.png" },
        ],
      },
    ]);
    expect(fp).toContain("file:abc-123");
    expect(fp).toContain("image_url:https://host/img.png");
  });

  it("tolerates missing role / content / attachments fields", () => {
    expect(fingerprintConversation([{}])).toBe("unknown::");
  });
});

describe("inferMediaTypeFromName", () => {
  const cases: Array<[string, string]> = [
    ["foo.pdf", "application/pdf"],
    ["bar.PNG", "image/png"],
    ["baz.JPEG", "image/jpeg"],
    ["qux.py", "text/x-python"],
    [
      "doc.docx",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
  ];
  it.each(cases)("%s → %s", (name, expected) => {
    expect(inferMediaTypeFromName(name)).toBe(expected);
  });

  it("returns empty string for names without an extension", () => {
    expect(inferMediaTypeFromName("noext")).toBe("");
    expect(inferMediaTypeFromName("")).toBe("");
    expect(inferMediaTypeFromName(null)).toBe("");
  });

  it("returns empty string for unknown extensions", () => {
    expect(inferMediaTypeFromName("foo.qwerty")).toBe("");
  });
});

describe("getSessionHint", () => {
  it("extracts ID from /c/<id>", () => {
    expect(getSessionHint("/c/abcd1234-ef56")).toBe("abcd1234-ef56");
  });

  it("extracts ID from /chat/<id>", () => {
    expect(getSessionHint("/chat/a1b2c3d4e5f6")).toBe("a1b2c3d4e5f6");
  });

  it("requires a minimum 8-char hex/dash ID", () => {
    expect(getSessionHint("/c/short")).toBe("/c/short"); // path fallback
  });

  it("falls back to the whole path when no pattern matches", () => {
    expect(getSessionHint("/settings")).toBe("/settings");
  });

  it("returns null for root-only paths", () => {
    expect(getSessionHint("/")).toBeNull();
    expect(getSessionHint("")).toBeNull();
  });
});

describe("extractAttachments — images", () => {
  it("captures user-uploaded images", () => {
    document.body.innerHTML = `
      <div id="turn">
        <img src="blob:https://chatgpt.com/abc" alt="user upload" />
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    const img = atts.find((a) => a.type === "image_url");
    expect(img).toBeDefined();
    expect(img!.url).toContain("blob:");
  });

  it("tags AI-generated images separately", () => {
    document.body.innerHTML = `
      <div id="turn">
        <img src="https://oaidalleapi.example/img.png" alt="dall-e" />
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    expect(atts.some((a) => a.type === "image_generation")).toBe(true);
  });

  it("skips tiny UI icons + avatars", () => {
    document.body.innerHTML = `
      <div id="turn">
        <img src="https://example.com/avatar.png" />
        <img src="https://example.com/icon.svg" />
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    expect(atts.filter((a) => a.type === "image_url")).toHaveLength(0);
  });
});

describe("extractAttachments — code blocks", () => {
  it("captures pre>code with a language class", () => {
    document.body.innerHTML = `
      <div id="turn">
        <pre><code class="language-python">print("hi")</code></pre>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    const code = atts.find((a) => a.type === "code_block");
    expect(code).toBeDefined();
    expect(code!.language).toBe("python");
    expect(code!.code).toContain("print");
  });

  it("skips pre blocks shorter than 5 chars", () => {
    document.body.innerHTML = `<div id="turn"><pre>hi</pre></div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    expect(atts.filter((a) => a.type === "code_block")).toHaveLength(0);
  });
});

describe("extractAttachments — citations", () => {
  it("captures external <a href> links", () => {
    document.body.innerHTML = `
      <div id="turn">
        <a href="https://example.com/article">example article</a>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    const cit = atts.find((a) => a.type === "citation");
    expect(cit).toBeDefined();
    expect(cit!.title).toBe("example article");
  });

  it("skips same-host internal links", () => {
    // happy-dom defaults to localhost; use that as the link host.
    document.body.innerHTML = `
      <div id="turn">
        <a href="http://localhost/internal">internal</a>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    expect(atts.filter((a) => a.type === "citation")).toHaveLength(0);
  });

  it("skips javascript: / # links", () => {
    document.body.innerHTML = `
      <div id="turn">
        <a href="javascript:void(0)">jump</a>
        <a href="#section">anchor</a>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    expect(atts.filter((a) => a.type === "citation")).toHaveLength(0);
  });
});

describe("extractAttachments — tool calls", () => {
  it("detects common English patterns", () => {
    document.body.innerHTML = `
      <div id="turn">
        <details><summary>Searched the web</summary></details>
        <button>Used calculator</button>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    const toolCalls = atts.filter((a) => a.type === "tool_call");
    const names = toolCalls.map((a) => a.name);
    expect(names).toContain("the web");
    expect(names).toContain("calculator");
  });

  it("deduplicates by name", () => {
    document.body.innerHTML = `
      <div id="turn">
        <button>Searched the web</button>
        <button>Searched the web</button>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    const toolCalls = atts.filter((a) => a.type === "tool_call");
    expect(toolCalls).toHaveLength(1);
  });
});

describe("extractReplyContent", () => {
  it("prefers .markdown content", () => {
    document.body.innerHTML = `
      <div id="turn">
        <div class="markdown prose">real answer text here</div>
        <details><summary>think</summary>internal thinking</details>
      </div>`;
    const reply = extractReplyContent(document.getElementById("turn"));
    expect(reply).toBe("real answer text here");
  });

  it("falls back to cloned body sans <details>", () => {
    document.body.innerHTML = `
      <div id="turn">
        <details>thinking block</details>
        <p>answer text</p>
      </div>`;
    const reply = extractReplyContent(document.getElementById("turn"));
    expect(reply).toContain("answer text");
    expect(reply).not.toContain("thinking block");
  });

  it("returns empty string for null element", () => {
    expect(extractReplyContent(null)).toBe("");
  });
});

describe("extractThinking", () => {
  it("extracts <details> body text", () => {
    document.body.innerHTML = `
      <div id="turn">
        <details>
          <summary>Thinking…</summary>
          <div class="markdown">step 1: analyse</div>
        </details>
      </div>`;
    const th = extractThinking(document.getElementById("turn"));
    expect(th).toBe("step 1: analyse");
  });

  it("falls back to [class*=think] when no <details>", () => {
    document.body.innerHTML = `
      <div id="turn">
        <div class="thought-container">internal reasoning</div>
      </div>`;
    const th = extractThinking(document.getElementById("turn"));
    expect(th).toContain("internal reasoning");
  });

  it("returns empty string for null", () => {
    expect(extractThinking(null)).toBe("");
  });
});

describe("installManualCaptureBridge", () => {
  it("fires pce-manual-capture when attribute is set, once per token", async () => {
    const events: Event[] = [];
    document.addEventListener("pce-manual-capture", (e) => events.push(e));

    const teardown = installManualCaptureBridge(document);
    document.documentElement.setAttribute("data-pce-manual-capture", "t1");

    // MutationObserver callbacks are asynchronous; yield a macrotask.
    await new Promise((r) => setTimeout(r, 10));
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("pce-manual-capture");

    // Same token — must not fire again
    document.documentElement.setAttribute("data-pce-manual-capture", "t1");
    await new Promise((r) => setTimeout(r, 10));
    expect(events).toHaveLength(1);

    // Different token — fires
    document.documentElement.setAttribute("data-pce-manual-capture", "t2");
    await new Promise((r) => setTimeout(r, 10));
    expect(events).toHaveLength(2);

    teardown();
  });

  it("teardown disconnects the observer", async () => {
    const events: Event[] = [];
    document.addEventListener("pce-manual-capture", (e) => events.push(e));
    const teardown = installManualCaptureBridge(document);
    teardown();

    document.documentElement.setAttribute("data-pce-manual-capture", "x");
    await new Promise((r) => setTimeout(r, 10));
    expect(events).toHaveLength(0);
  });
});

describe("extractAttachments — robustness", () => {
  it("returns [] for null", () => {
    const result: PceAttachment[] = extractAttachments(null);
    expect(result).toEqual([]);
  });

  it("merges file attachments by case-insensitive name", () => {
    document.body.innerHTML = `
      <div id="turn">
        <div class="file-chip">
          <a href="https://example.com/doc.pdf"><span class="name">Doc.pdf</span></a>
        </div>
        <button>DOC.pdf</button>
      </div>`;
    const atts = extractAttachments(document.getElementById("turn"));
    const files = atts.filter((a) => a.type === "file");
    // Should not have two entries just because of case differences.
    expect(files).toHaveLength(1);
  });
});
