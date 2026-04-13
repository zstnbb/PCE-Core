/**
 * PCE – Text Selection Collector
 *
 * Detects text selection on covered AI sites and shows a floating
 * "Save Snippet" button. On click, sends the selected text + metadata
 * to the PCE Core API via the service worker.
 *
 * Features:
 *   - Floating button appears near the selection after mouseup
 *   - Auto-detects provider from the current domain
 *   - Category picker: general | code | prompt | output | reference
 *   - Sends PCE_SNIPPET message to service worker
 *   - Button disappears on click elsewhere or scroll
 */

(function () {
  "use strict";

  // Guard against double-injection
  if (window.__PCE_TEXT_COLLECTOR_ACTIVE) return;
  window.__PCE_TEXT_COLLECTOR_ACTIVE = true;

  const TAG = "[PCE:text-collector]";
  const MIN_SELECTION_LENGTH = 5;

  // ── Provider detection from hostname ─────────────────────────────
  const DOMAIN_PROVIDER_MAP = {
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

  function detectProvider() {
    const host = location.hostname;
    return DOMAIN_PROVIDER_MAP[host] || host.split(".").slice(-2, -1)[0] || "unknown";
  }

  // ── Category definitions ─────────────────────────────────────────
  const CATEGORIES = [
    { id: "general",   label: "General",   icon: "📝" },
    { id: "code",      label: "Code",      icon: "💻" },
    { id: "prompt",    label: "Prompt",    icon: "💬" },
    { id: "output",    label: "Output",    icon: "🤖" },
    { id: "reference", label: "Reference", icon: "📎" },
  ];

  // ── Create floating UI ───────────────────────────────────────────
  let floatingEl = null;
  let selectedText = "";

  function createFloatingButton() {
    if (floatingEl) return floatingEl;

    const container = document.createElement("div");
    container.id = "pce-snippet-float";
    container.innerHTML = `
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
          <select title="Category">
            ${CATEGORIES.map((c) => `<option value="${c.id}">${c.icon} ${c.label}</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="pce-snip-feedback">✓ Saved</div>
    `;

    document.body.appendChild(container);
    floatingEl = container;

    // Save button click
    container.querySelector(".pce-snip-save").addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      saveSnippet();
    });

    // Prevent selection loss when clicking the category dropdown
    container.addEventListener("mousedown", (e) => {
      e.stopPropagation();
    });

    return container;
  }

  function showFloat(x, y) {
    const el = createFloatingButton();
    el.style.display = "flex";
    el.querySelector(".pce-snip-main").style.display = "flex";
    el.querySelector(".pce-snip-feedback").style.display = "none";

    // Position near selection, clamped to viewport
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = Math.min(x, vw - 200);
    let top = y + 8;
    if (top + 40 > vh) top = y - 48;
    if (left < 8) left = 8;

    el.style.left = left + "px";
    el.style.top = top + "px";
  }

  function hideFloat() {
    if (floatingEl) {
      floatingEl.style.display = "none";
    }
  }

  function showFeedback() {
    if (!floatingEl) return;
    floatingEl.querySelector(".pce-snip-main").style.display = "none";
    const fb = floatingEl.querySelector(".pce-snip-feedback");
    fb.style.display = "flex";
    setTimeout(hideFloat, 1200);
  }

  // ── Save snippet ─────────────────────────────────────────────────
  function saveSnippet() {
    if (!selectedText || selectedText.length < MIN_SELECTION_LENGTH) return;

    const category = floatingEl
      ? floatingEl.querySelector("select").value
      : "general";

    const payload = {
      content_text: selectedText,
      source_url: location.href,
      source_domain: location.hostname,
      provider: detectProvider(),
      category: category,
    };

    try {
      chrome.runtime.sendMessage(
        { type: "PCE_SNIPPET", payload: payload },
        (response) => {
          if (chrome.runtime.lastError) {
            console.warn(TAG, "Failed to send snippet:", chrome.runtime.lastError.message);
            return;
          }
          if (response && response.ok) {
            showFeedback();
          } else {
            console.warn(TAG, "Snippet save failed:", response);
          }
        }
      );
    } catch (err) {
      console.warn(TAG, "Extension context lost:", err.message);
    }
  }

  // ── Event listeners ──────────────────────────────────────────────
  let hideTimeout = null;

  document.addEventListener("mouseup", (e) => {
    // Ignore clicks on the floating UI itself
    if (floatingEl && floatingEl.contains(e.target)) return;

    clearTimeout(hideTimeout);

    // Small delay to let the selection finalize
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

  // Hide on scroll or Escape
  document.addEventListener("scroll", hideFloat, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideFloat();
  });
})();
