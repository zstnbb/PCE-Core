// SPDX-License-Identifier: Apache-2.0
/**
 * Tests for `entrypoints/google-ai-studio.content.ts`.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  attachmentOnlyText,
  cleanContainerText,
  dedupeAttachments,
  extractAssistantText,
  extractLocalAttachments,
  extractMessages,
  extractUserText,
  getContainer,
  getModelName,
  getSessionHint,
  imageMediaType,
  isStreaming,
  normalizeText,
} from "../google-ai-studio.content";
import type { PceAttachment } from "../../utils/pce-dom";

beforeEach(() => {
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// normalizeText
// ---------------------------------------------------------------------------

describe("normalizeText", () => {
  it("strips control-line noise (edit / more_vert / thumb_up)", () => {
    const input = "edit\nmore_vert\nthumb_up\nthe actual message";
    expect(normalizeText(input)).toBe("the actual message");
  });

  it("strips meta lines like 'user 12:34' / 'model 1:05'", () => {
    const input = "user 12:34\nfirst real line\nmodel 1:05\nsecond real line";
    expect(normalizeText(input)).toBe("first real line\nsecond real line");
  });

  it("strips the 'google ai models may make mistakes' disclaimer", () => {
    const input = "Google AI models may make mistakes, check responses\nreal content";
    expect(normalizeText(input)).toBe("real content");
  });

  it("collapses whitespace + dedupes consecutive duplicate lines", () => {
    const input = "line   with   spaces\nline   with   spaces\nother";
    expect(normalizeText(input)).toBe("line with spaces\nother");
  });

  it("returns empty string on null / undefined / empty", () => {
    expect(normalizeText(null)).toBe("");
    expect(normalizeText(undefined)).toBe("");
    expect(normalizeText("")).toBe("");
  });
});

// ---------------------------------------------------------------------------
// dedupeAttachments
// ---------------------------------------------------------------------------

describe("dedupeAttachments", () => {
  it("removes duplicates matching on type/url/name/title/code", () => {
    const list = [
      { type: "file", name: "doc.pdf" },
      { type: "file", name: "doc.pdf" },
      { type: "file", name: "other.pdf" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ] as any as PceAttachment[];
    const out = dedupeAttachments(list);
    expect(out).toHaveLength(2);
  });

  it("skips nullish / non-object entries", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const list = [null, undefined, "x", { type: "file", name: "x" }] as any;
    const out = dedupeAttachments(list);
    expect(out).toHaveLength(1);
  });

  it("handles null input gracefully", () => {
    expect(dedupeAttachments(null)).toEqual([]);
    expect(dedupeAttachments(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// imageMediaType
// ---------------------------------------------------------------------------

describe("imageMediaType", () => {
  it("reads media type from data: URL", () => {
    expect(imageMediaType("data:image/png;base64,abc", "")).toBe("image/png");
    expect(imageMediaType("data:image/jpeg;base64,abc", "")).toBe("image/jpeg");
  });

  it("infers media type from filename extension", () => {
    expect(imageMediaType("/path/file.png", "")).toBe("image/png");
    expect(imageMediaType("", "photo.jpg")).toBe("image/jpeg");
    expect(imageMediaType("", "photo.jpeg")).toBe("image/jpeg");
    expect(imageMediaType("", "anim.gif")).toBe("image/gif");
    expect(imageMediaType("", "asset.webp")).toBe("image/webp");
  });

  it("returns '' when no hint is available", () => {
    expect(imageMediaType("", "")).toBe("");
    expect(imageMediaType("unknown", "file")).toBe("");
  });
});

// ---------------------------------------------------------------------------
// attachmentOnlyText
// ---------------------------------------------------------------------------

describe("attachmentOnlyText", () => {
  it("labels images + files", () => {
    const out = attachmentOnlyText([
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { type: "image_url", name: "diagram.png" } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { type: "file", name: "notes.pdf" } as any,
    ]);
    expect(out).toContain("[Image attachment: diagram.png]");
    expect(out).toContain("[File attachment: notes.pdf]");
  });

  it("returns empty string when list has no relevant types", () => {
    expect(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      attachmentOnlyText([{ type: "citation", url: "x" } as any]),
    ).toBe("");
  });

  it("accepts null / undefined", () => {
    expect(attachmentOnlyText(null)).toBe("");
    expect(attachmentOnlyText(undefined)).toBe("");
  });
});

// ---------------------------------------------------------------------------
// cleanContainerText
// ---------------------------------------------------------------------------

describe("cleanContainerText", () => {
  it("strips buttons / icons / tooltip triggers / feedback rows", () => {
    document.body.innerHTML = `
      <div id="x">
        <button>copy</button>
        <mat-icon>thumb_up</mat-icon>
        <div class="feedback-buttons"><button>up</button></div>
        <div class="author-label">User</div>
        <div class="timestamp">12:34</div>
        this is the real body
      </div>`;
    const t = cleanContainerText(document.getElementById("x"));
    expect(t).toBe("this is the real body");
  });

  it("honours stripCode", () => {
    document.body.innerHTML = `
      <div id="x">
        visible body
        <pre>const x = 1;</pre>
      </div>`;
    const t = cleanContainerText(document.getElementById("x"), { stripCode: true });
    expect(t).toContain("visible body");
    expect(t).not.toContain("const x = 1");
  });

  it("honours stripLinks", () => {
    document.body.innerHTML = `
      <div id="x">
        visible body
        <a href="https://example.com">link</a>
      </div>`;
    const t = cleanContainerText(document.getElementById("x"), { stripLinks: true });
    expect(t).toContain("visible body");
    expect(t).not.toContain("link");
  });

  it("returns empty string on null", () => {
    expect(cleanContainerText(null)).toBe("");
  });

  it("strips current AI Studio action/header chrome from turn containers", () => {
    document.body.innerHTML = `
      <div id="x" class="chat-turn-container code-block-aligner model render ng-star-inserted">
        <div class="actions-container">
          <div class="actions hover-or-edit">
            <ms-chat-turn-options>more_vert</ms-chat-turn-options>
          </div>
        </div>
        <div class="turn-content">
          <div class="author-label">Model <span class="timestamp">12:00</span></div>
          <div class="info-container">
            <span class="model-run-time">0.6s</span>
          </div>
          <div class="prompt-container">
            <div class="markdown">The real assistant reply.</div>
          </div>
        </div>
      </div>`;
    expect(cleanContainerText(document.getElementById("x"))).toBe(
      "The real assistant reply.",
    );
  });
});

// ---------------------------------------------------------------------------
// extractLocalAttachments
// ---------------------------------------------------------------------------

describe("extractLocalAttachments", () => {
  it("extracts <ms-image-chunk> images", () => {
    document.body.innerHTML = `
      <div id="turn">
        <ms-image-chunk>
          <img src="https://img.example.com/photo.png" alt="a diagram" />
        </ms-image-chunk>
      </div>`;
    const out = extractLocalAttachments(
      document.getElementById("turn"),
      "aistudio.google.com",
    );
    expect(out.length).toBeGreaterThan(0);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const img = out.find((a: any) => a.type === "image_url") as any;
    expect(img).toBeDefined();
    expect(img.url).toContain("photo.png");
    expect(img.alt).toBe("a diagram");
    expect(img.media_type).toBe("image/png");
  });

  it("extracts <ms-file-chunk> file entries", () => {
    document.body.innerHTML = `
      <div id="turn">
        <ms-file-chunk>
          <div class="name" title="report.pdf">report.pdf</div>
        </ms-file-chunk>
      </div>`;
    const out = extractLocalAttachments(
      document.getElementById("turn"),
      "aistudio.google.com",
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const f = out.find((a: any) => a.type === "file") as any;
    expect(f).toBeDefined();
    expect(f.name).toBe("report.pdf");
  });

  it("extracts <pre> code blocks", () => {
    document.body.innerHTML = `
      <div id="turn">
        <pre>const x = 42;</pre>
      </div>`;
    const out = extractLocalAttachments(document.getElementById("turn"), "x");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const code = out.find((a: any) => a.type === "code_block") as any;
    expect(code).toBeDefined();
    expect(code.code).toContain("const x = 42");
  });

  it("extracts external citation links but skips same-origin", () => {
    document.body.innerHTML = `
      <div id="turn">
        <a href="https://aistudio.google.com/internal">self</a>
        <a href="https://external.example.com/paper">External paper</a>
      </div>`;
    const out = extractLocalAttachments(
      document.getElementById("turn"),
      "aistudio.google.com",
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const citations = out.filter((a: any) => a.type === "citation");
    expect(citations.length).toBe(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((citations[0] as any).url).toContain("external.example.com");
  });

  it("returns [] for null input", () => {
    expect(extractLocalAttachments(null)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// isStreaming
// ---------------------------------------------------------------------------

describe("isStreaming", () => {
  it("true when a Stop/Cancel generation button is visible", () => {
    document.body.innerHTML = `<button aria-label="Stop generation">X</button>`;
    expect(isStreaming(document)).toBe(true);
  });

  it("false when idle", () => {
    document.body.innerHTML = `<p>quiet page</p>`;
    expect(isStreaming(document)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// getContainer / getSessionHint / getModelName
// ---------------------------------------------------------------------------

describe("getContainer", () => {
  it("prefers .chat-view-container", () => {
    document.body.innerHTML = `<div class="chat-view-container" id="c">x</div>`;
    expect(getContainer(document)!.id).toBe("c");
  });

  it("falls back to body", () => {
    document.body.innerHTML = `<p>x</p>`;
    expect(getContainer(document)).toBe(document.body);
  });
});

describe("getSessionHint", () => {
  it("extracts IDs from /prompts/<id>", () => {
    expect(getSessionHint("/prompts/abc123")).toBe("abc123");
  });

  it("falls back to pathname when no match", () => {
    expect(getSessionHint("/library")).toBe("/library");
  });
});

describe("getModelName", () => {
  it("reads from ms-model-selector", () => {
    document.body.innerHTML = `
      <ms-model-selector>
        <span class="mat-mdc-select-value-text">gemini-2.5-pro</span>
      </ms-model-selector>`;
    expect(getModelName(document)).toBe("gemini-2.5-pro");
  });

  it("falls back to body-text gemini-* regex", () => {
    document.body.innerHTML = `<p>running on gemini-2.0-flash today</p>`;
    expect(getModelName(document)).toBe("gemini-2.0-flash");
  });

  it("recognises non-gemini model families like imagen", () => {
    document.body.innerHTML = `<p>using imagen-3-fast-generate-preview</p>`;
    expect(getModelName(document)).toBe("imagen-3-fast-generate-preview");
  });

  it("returns null when nothing matches", () => {
    document.body.innerHTML = `<p>nothing here</p>`;
    expect(getModelName(document)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// extractUserText / extractAssistantText / extractMessages
// ---------------------------------------------------------------------------

describe("extractUserText", () => {
  it("uses .chat-turn-container.user when present", () => {
    document.body.innerHTML = `
      <ms-chat-turn id="t">
        <div class="chat-turn-container user">user question here</div>
      </ms-chat-turn>`;
    expect(extractUserText(document.getElementById("t")!)).toBe(
      "user question here",
    );
  });

  it("falls back to turn text when no user container", () => {
    document.body.innerHTML = `
      <ms-chat-turn id="t">
        generic turn content
      </ms-chat-turn>`;
    expect(extractUserText(document.getElementById("t")!)).toContain(
      "generic turn content",
    );
  });
});

describe("extractAssistantText", () => {
  it("extracts from .chat-turn-container.model with thinking wrap", () => {
    document.body.innerHTML = `
      <ms-chat-turn id="t">
        <div class="chat-turn-container model">
          <details>
            <summary>Thinking</summary>
            <div class="markdown">step-by-step analysis</div>
          </details>
          <div class="markdown">the final reply</div>
        </div>
      </ms-chat-turn>`;
    const t = extractAssistantText(document.getElementById("t")!);
    expect(t).toContain("<thinking>");
    expect(t).toContain("step-by-step analysis");
    expect(t).toContain("the final reply");
  });

  it("returns empty string when no model container present", () => {
    document.body.innerHTML = `
      <ms-chat-turn id="t">
        <div class="chat-turn-container user">q</div>
      </ms-chat-turn>`;
    expect(extractAssistantText(document.getElementById("t")!)).toBe("");
  });

  it("ignores collapsed AI Studio thought headers and keeps the real reply", () => {
    document.body.innerHTML = `
      <ms-chat-turn id="t">
        <div class="chat-turn-container code-block-aligner model render ng-star-inserted">
          <div class="actions-container"><ms-chat-turn-options>more_vert</ms-chat-turn-options></div>
          <div class="turn-content">
            <div class="author-label">Model <span class="timestamp">12:00</span></div>
            <ms-prompt-chunk class="text-chunk ng-star-inserted">
              <ms-thought-chunk class="ng-star-inserted">
                <mat-accordion>
                  <mat-expansion-panel class="mat-expansion-panel thought-panel">
                    <mat-expansion-panel-header>
                      <mat-panel-title>Thoughts</mat-panel-title>
                      <span>Expand to view model thoughts</span>
                      <span>chevron_right</span>
                    </mat-expansion-panel-header>
                  </mat-expansion-panel>
                </mat-accordion>
              </ms-thought-chunk>
              <div class="markdown">The final assistant reply.</div>
            </ms-prompt-chunk>
          </div>
        </div>
      </ms-chat-turn>`;
    expect(extractAssistantText(document.getElementById("t")!)).toBe(
      "The final assistant reply.",
    );
  });

  it("drops pure error turns instead of capturing the banner text", () => {
    document.body.innerHTML = `
      <ms-chat-turn id="t">
        <div class="chat-turn-container code-block-aligner model render ng-star-inserted">
          <div class="actions-container"><ms-chat-turn-options>more_vert</ms-chat-turn-options></div>
          <div class="turn-content">
            <div class="author-label">Model <span class="timestamp">12:00</span></div>
            <div class="prompt-container">
              <div class="error-icon">error</div>
              <div>An internal error has occurred.</div>
            </div>
          </div>
        </div>
      </ms-chat-turn>`;
    expect(extractAssistantText(document.getElementById("t")!)).toBe("");
  });
});

describe("extractMessages", () => {
  it("captures user + assistant ms-chat-turn pairs", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn>
          <div class="chat-turn-container user">what is gemini?</div>
        </ms-chat-turn>
        <ms-chat-turn>
          <div class="chat-turn-container model">
            <div class="markdown">Gemini is a Google AI model.</div>
          </div>
        </ms-chat-turn>
      </main>`;
    const msgs = extractMessages(document, "aistudio.google.com");
    expect(msgs.length).toBe(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toContain("what is gemini?");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("Gemini is a Google AI model");
  });

  it("returns [] when no turn-like nodes are present", () => {
    document.body.innerHTML = `<main><p>nothing</p></main>`;
    expect(extractMessages(document)).toEqual([]);
  });

  it("captures freeform/structured virtual-scroll containers without ms-chat-turn", () => {
    document.body.innerHTML = `
      <main>
        <div class="virtual-scroll-container user-prompt-container">
          <div class="author-label">User</div>
          <div class="turn-content">
            <ms-prompt-chunk class="text-chunk">
              <ms-text-chunk>
                <ms-cmark-node class="cmark-node v3-font-body user-chunk">
                  Prompt TOKEN-FREEFORM
                </ms-cmark-node>
              </ms-text-chunk>
            </ms-prompt-chunk>
          </div>
        </div>
        <div class="virtual-scroll-container model-prompt-container">
          <div class="author-label">Model</div>
          <div class="turn-content">
            <div class="markdown">Reply TOKEN-FREEFORM</div>
          </div>
        </div>
      </main>`;
    const msgs = extractMessages(document, "aistudio.google.com");
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toContain("Prompt TOKEN-FREEFORM");
    expect(msgs[1].role).toBe("assistant");
    expect(msgs[1].content).toContain("Reply TOKEN-FREEFORM");
  });

  // P5.B gap A3 regression: turns with no .user/.model container
  // must NOT produce ghost messages.
  it("skips ghost turns that have no .user or .model container (A3)", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn>
          <div class="chat-turn-container">
            <div class="loading-indicator">...</div>
          </div>
        </ms-chat-turn>
      </main>`;
    expect(extractMessages(document, "aistudio.google.com")).toEqual([]);
  });

  it("skips completely empty ms-chat-turn (A3)", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn></ms-chat-turn>
      </main>`;
    expect(extractMessages(document, "aistudio.google.com")).toEqual([]);
  });

  // A3 follow-up: even in the ambiguous branch (no outer class marker),
  // a model-container-only turn must extract ONLY the assistant side —
  // not a ghost user turn containing the model's reply.
  it("ambiguous turn with model container only -> assistant only (A3)", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn>
          <div class="chat-turn-container">
            <div class="chat-turn-container model">
              <div class="markdown">the model's reply only</div>
            </div>
          </div>
        </ms-chat-turn>
      </main>`;
    const msgs = extractMessages(document, "aistudio.google.com");
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("assistant");
    expect(msgs[0].content).toContain("the model's reply only");
  });

  it("ambiguous turn with user container only -> user only (A3)", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn>
          <div class="chat-turn-container">
            <div class="chat-turn-container user">just the user text</div>
          </div>
        </ms-chat-turn>
      </main>`;
    const msgs = extractMessages(document, "aistudio.google.com");
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].content).toContain("just the user text");
  });

  it("uses [Attachment] placeholder for image-only user messages", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn>
          <div class="chat-turn-container user">
            <ms-image-chunk>
              <img src="https://img.example.com/diagram.png" alt="a diagram" />
            </ms-image-chunk>
          </div>
        </ms-chat-turn>
      </main>`;
    const msgs = extractMessages(document, "aistudio.google.com");
    expect(msgs).toHaveLength(1);
    expect(msgs[0].role).toBe("user");
    expect(msgs[0].attachments).toBeDefined();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const att = msgs[0].attachments as any[];
    expect(att.length).toBeGreaterThan(0);
  });

  it("captures current AI Studio DOM without leaking action-bar noise", () => {
    document.body.innerHTML = `
      <main>
        <ms-chat-turn>
          <div class="chat-turn-container code-block-aligner render user ng-star-inserted">
            <div class="actions-container">
              <div class="actions hover-or-edit">
                <button aria-label="Edit">edit</button>
                <ms-chat-turn-options>more_vert</ms-chat-turn-options>
              </div>
            </div>
            <div class="turn-content">
              <div class="author-label">User <span class="timestamp">12:00</span></div>
              <ms-prompt-chunk class="text-chunk ng-star-inserted">
                <ms-text-chunk>
                  <ms-cmark-node class="cmark-node v3-font-body user-chunk ng-star-inserted">
                    Reply with exactly TOKEN-123
                  </ms-cmark-node>
                </ms-text-chunk>
              </ms-prompt-chunk>
            </div>
          </div>
        </ms-chat-turn>
        <ms-chat-turn>
          <div class="chat-turn-container code-block-aligner model render ng-star-inserted">
            <div class="actions-container">
              <div class="actions hover-or-edit">
                <button aria-label="Rerun this turn"></button>
                <ms-chat-turn-options>more_vert</ms-chat-turn-options>
              </div>
            </div>
            <div class="turn-content">
              <div class="author-label">Model <span class="timestamp">12:00</span></div>
              <div class="prompt-container">
                <div class="markdown">ACK TOKEN-123</div>
              </div>
            </div>
          </div>
        </ms-chat-turn>
      </main>`;
    const msgs = extractMessages(document, "aistudio.google.com");
    expect(msgs).toHaveLength(2);
    expect(msgs[0].content).toBe("Reply with exactly TOKEN-123");
    expect(msgs[1].content).toBe("ACK TOKEN-123");
  });
});
