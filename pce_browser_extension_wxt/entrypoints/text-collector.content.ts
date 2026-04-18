/**
 * PCE — Text selection collector content script.
 *
 * TypeScript port of ``content_scripts/text_collector.js`` (P2.5
 * Phase 3b batch 5).
 *
 * Detects text selection on covered AI sites and shows a floating
 * "Save Snippet" button. On click, posts a ``PCE_SNIPPET`` message to
 * the service worker (which forwards it to ``/api/v1/snippets``).
 *
 * The DOM-manipulation code path is left intact — the floating UI is
 * rendered inline via a template string with scoped CSS. The pure
 * helpers (``detectProvider``, ``CATEGORIES`` metadata,
 * ``clampFloatingPosition``) are exported at module scope so the
 * Vitest suite can cover them without driving the mouseup/selection
 * DOM dance.
 */

declare global {
  interface Window {
    __PCE_TEXT_COLLECTOR_ACTIVE?: boolean;
  }
}

// ---------------------------------------------------------------------------
// Pure helpers (module-scope for testability)
// ---------------------------------------------------------------------------

const TAG = "[PCE:text-collector]";
export const MIN_SELECTION_LENGTH = 5;

export const DOMAIN_PROVIDER_MAP: Readonly<Record<string, string>> = {
  "chatgpt.com": "openai",
  "chat.openai.com": "openai",
  "claude.ai": "anthropic",
  "gemini.google.com": "google",
  "aistudio.google.com": "google",
  "chat.deepseek.com": "deepseek",
  "chat.z.ai": "zhipu",
  "www.perplexity.ai": "perplexity",
  "copilot.microsoft.com": "microsoft",
  "poe.com": "poe",
  "huggingface.co": "huggingface",
  "grok.com": "grok",
  "manus.im": "manus",
  "chat.mistral.ai": "mistral",
  "kimi.moonshot.cn": "moonshot",
  "www.kimi.com": "moonshot",
  "kimi.com": "moonshot",
};

export interface CategoryDef {
  id: "general" | "code" | "prompt" | "output" | "reference";
  label: string;
  icon: string;
}

export const CATEGORIES: readonly CategoryDef[] = [
  { id: "general", label: "General", icon: "📝" },
  { id: "code", label: "Code", icon: "💻" },
  { id: "prompt", label: "Prompt", icon: "💬" },
  { id: "output", label: "Output", icon: "🤖" },
  { id: "reference", label: "Reference", icon: "📎" },
];

export function detectProvider(
  hostname: string = location.hostname,
): string {
  const direct = DOMAIN_PROVIDER_MAP[hostname];
  if (direct) return direct;
  const parts = hostname.split(".");
  return parts.slice(-2, -1)[0] || "unknown";
}

/**
 * Clamp a floating-button position inside the viewport. Matches the
 * legacy ``showFloat`` arithmetic exactly:
 *   - clamp x to ``vw - 200`` (leave room for the button width).
 *   - prefer ``y + 8`` below the cursor; flip to ``y - 48`` if it
 *     would overflow the bottom edge.
 *   - clamp x to min 8 px.
 */
export function clampFloatingPosition(
  x: number,
  y: number,
  viewportWidth: number,
  viewportHeight: number,
): { left: number; top: number } {
  let left = Math.min(x, viewportWidth - 200);
  let top = y + 8;
  if (top + 40 > viewportHeight) top = y - 48;
  if (left < 8) left = 8;
  return { left, top };
}

// ---------------------------------------------------------------------------
// Floating-button template
// ---------------------------------------------------------------------------

function renderFloatingHtml(): string {
  const options = CATEGORIES.map(
    (c) => `<option value="${c.id}">${c.icon} ${c.label}</option>`,
  ).join("");
  // The inline <style> is the legacy CSS byte-for-byte.
  return `
    <style>
      #pce-snippet-float {
        position: fixed;
        z-index: 2147483647;
        display: none;
        flex-direction: column;
        gap: 4px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        pointer-events: auto;
      }
      #pce-snippet-float .pce-snip-main {
        display: flex;
        align-items: stretch;
        background: #1a1a2e;
        border: 1px solid rgba(129, 140, 248, 0.3);
        border-radius: 8px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        overflow: hidden;
      }
      #pce-snippet-float .pce-snip-save {
        display: flex;
        align-items: center;
        gap: 5px;
        padding: 6px 12px;
        background: #818cf8;
        color: #fff;
        border: none;
        cursor: pointer;
        font-size: 12px;
        font-weight: 600;
        white-space: nowrap;
        transition: background 0.15s;
      }
      #pce-snippet-float .pce-snip-save:hover {
        background: #6366f1;
      }
      #pce-snippet-float .pce-snip-save svg {
        width: 14px;
        height: 14px;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
      }
      #pce-snippet-float .pce-snip-cat {
        display: flex;
        align-items: center;
      }
      #pce-snippet-float .pce-snip-cat select {
        appearance: none;
        background: #16213e;
        color: #e6edf3;
        border: none;
        padding: 6px 24px 6px 8px;
        font-size: 11px;
        cursor: pointer;
        outline: none;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%238b949e' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
        background-repeat: no-repeat;
        background-position: right 6px center;
      }
      #pce-snippet-float .pce-snip-feedback {
        display: none;
        align-items: center;
        gap: 5px;
        padding: 6px 12px;
        background: #1a1a2e;
        border: 1px solid rgba(52, 211, 153, 0.3);
        border-radius: 8px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        color: #34d399;
        font-size: 12px;
        font-weight: 600;
        white-space: nowrap;
        animation: pce-fade-in 0.2s ease;
      }
      @keyframes pce-fade-in {
        from { opacity: 0; transform: translateY(4px); }
        to { opacity: 1; transform: translateY(0); }
      }
    </style>
    <div class="pce-snip-main">
      <button class="pce-snip-save" title="Save selection as snippet">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        Save
      </button>
      <div class="pce-snip-cat">
        <select title="Category">${options}</select>
      </div>
    </div>
    <div class="pce-snip-feedback">\u2713 Saved</div>
  `;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default defineContentScript({
  matches: [
    "https://chatgpt.com/*",
    "https://chat.openai.com/*",
    "https://claude.ai/*",
    "https://gemini.google.com/*",
    "https://chat.deepseek.com/*",
    "https://aistudio.google.com/*",
    "https://www.perplexity.ai/*",
    "https://copilot.microsoft.com/*",
    "https://poe.com/*",
    "https://grok.com/*",
    "https://huggingface.co/chat/*",
    "https://manus.im/*",
    "https://chat.z.ai/*",
    "https://chat.mistral.ai/*",
    "https://kimi.moonshot.cn/*",
    "https://www.kimi.com/*",
    "https://kimi.com/*",
  ],
  runAt: "document_idle",
  main() {
    if (window.__PCE_TEXT_COLLECTOR_ACTIVE) return;
    window.__PCE_TEXT_COLLECTOR_ACTIVE = true;

    let floatingEl: HTMLDivElement | null = null;
    let selectedText = "";
    let hideTimeout: ReturnType<typeof setTimeout> | null = null;

    const saveSnippet = (): void => {
      if (!selectedText || selectedText.length < MIN_SELECTION_LENGTH) return;

      const selectEl =
        floatingEl?.querySelector<HTMLSelectElement>("select");
      const category = (selectEl?.value as CategoryDef["id"]) || "general";

      const payload = {
        content_text: selectedText,
        source_url: location.href,
        source_domain: location.hostname,
        provider: detectProvider(),
        category,
      };

      try {
        chrome.runtime.sendMessage(
          { type: "PCE_SNIPPET", payload },
          (response: unknown) => {
            if (chrome.runtime.lastError) {
              console.warn(
                TAG,
                "Failed to send snippet:",
                chrome.runtime.lastError.message,
              );
              return;
            }
            const resp = response as { ok?: boolean } | null;
            if (resp?.ok) {
              showFeedback();
            } else {
              console.warn(TAG, "Snippet save failed:", response);
            }
          },
        );
      } catch (err) {
        console.warn(TAG, "Extension context lost:", (err as Error).message);
      }
    };

    const createFloatingButton = (): HTMLDivElement => {
      if (floatingEl) return floatingEl;
      const container = document.createElement("div");
      container.id = "pce-snippet-float";
      container.innerHTML = renderFloatingHtml();
      document.body.appendChild(container);
      floatingEl = container;

      container
        .querySelector<HTMLButtonElement>(".pce-snip-save")!
        .addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          saveSnippet();
        });

      // Prevent selection loss when clicking the category dropdown.
      container.addEventListener("mousedown", (e) => {
        e.stopPropagation();
      });

      return container;
    };

    const showFloat = (x: number, y: number): void => {
      const el = createFloatingButton();
      el.style.display = "flex";
      (el.querySelector<HTMLDivElement>(".pce-snip-main")!).style.display =
        "flex";
      (el.querySelector<HTMLDivElement>(".pce-snip-feedback")!).style.display =
        "none";

      const { left, top } = clampFloatingPosition(
        x,
        y,
        window.innerWidth,
        window.innerHeight,
      );
      el.style.left = left + "px";
      el.style.top = top + "px";
    };

    const hideFloat = (): void => {
      if (floatingEl) floatingEl.style.display = "none";
    };

    const showFeedback = (): void => {
      if (!floatingEl) return;
      (floatingEl.querySelector<HTMLDivElement>(
        ".pce-snip-main",
      )!).style.display = "none";
      const fb = floatingEl.querySelector<HTMLDivElement>(
        ".pce-snip-feedback",
      )!;
      fb.style.display = "flex";
      setTimeout(hideFloat, 1200);
    };

    document.addEventListener("mouseup", (e: MouseEvent) => {
      if (floatingEl && floatingEl.contains(e.target as Node)) return;
      if (hideTimeout) clearTimeout(hideTimeout);
      hideTimeout = setTimeout(() => {
        const sel = window.getSelection();
        const text = sel ? sel.toString().trim() : "";
        if (text.length >= MIN_SELECTION_LENGTH) {
          selectedText = text;
          showFloat(e.clientX, e.clientY);
        } else {
          selectedText = "";
          hideFloat();
        }
      }, 150);
    });

    document.addEventListener("scroll", hideFloat, true);
    document.addEventListener("keydown", (e: KeyboardEvent) => {
      if (e.key === "Escape") hideFloat();
    });
  },
});
