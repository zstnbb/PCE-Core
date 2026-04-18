// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Behavior tracker content script.
 *
 * TypeScript port of ``content_scripts/behavior_tracker.js`` (P2.5
 * Phase 3b batch 5).
 *
 * Collects behavioural metadata about user ↔ AI interactions:
 *   - ``request_sent_at``: when the user clicks send
 *   - ``copy_events``: the last 10 copy actions (timestamp + length)
 *   - ``scroll_depth_pct``: max scroll depth reached
 *   - ``think_interval_ms``: time between the last two user messages
 *
 * Exposes `window.__PCE_BEHAVIOR = { getBehaviorSnapshot, state }`
 * so the capture runtime (and any remaining legacy JS) can read the
 * snapshot when assembling `meta.behavior` in a capture payload.
 *
 * The pure ``createBehaviorState`` + ``getBehaviorSnapshot`` helpers
 * are exported at module scope so the Vitest suite can drive them
 * without the DOM-event plumbing.
 */

declare global {
  interface Window {
    __PCE_BEHAVIOR_TRACKER_ACTIVE?: boolean;
    // `__PCE_BEHAVIOR` is also declared in
    // `chatgpt.content.ts` as a looser shape (``{
    // getBehaviorSnapshot?: ... }``). TypeScript merges the two
    // interfaces and keeps the narrower intersection, so we leave
    // `state` unlisted here and attach it via a type cast at runtime.
    __PCE_BEHAVIOR?: {
      getBehaviorSnapshot?: (reset?: boolean) => Record<string, unknown>;
    };
  }
}

// ---------------------------------------------------------------------------
// Pure state + snapshot helpers (module-scope for testability)
// ---------------------------------------------------------------------------

export interface CopyEvent {
  at: number;
  length: number;
}

export interface BehaviorState {
  lastRequestSentAt: number | null;
  copyEvents: CopyEvent[];
  scrollDepthPct: number;
  messageTimestamps: number[];
}

export interface BehaviorSnapshot {
  request_sent_at: number | null;
  copy_events: CopyEvent[];
  scroll_depth_pct: number;
  think_interval_ms?: number;
}

export function createBehaviorState(): BehaviorState {
  return {
    lastRequestSentAt: null,
    copyEvents: [],
    scrollDepthPct: 0,
    messageTimestamps: [],
  };
}

/**
 * Build a snapshot from the current state and optionally reset the
 * transient parts (``copy_events`` + ``scroll_depth_pct``). Preserves
 * the legacy contract: the last 10 copy events only,
 * ``think_interval_ms`` computed from the last two message timestamps.
 */
export function getBehaviorSnapshot(
  state: BehaviorState,
  reset: boolean = false,
): BehaviorSnapshot {
  const snapshot: BehaviorSnapshot = {
    request_sent_at: state.lastRequestSentAt,
    copy_events: state.copyEvents.slice(-10),
    scroll_depth_pct: state.scrollDepthPct,
  };
  if (state.messageTimestamps.length >= 2) {
    const len = state.messageTimestamps.length;
    snapshot.think_interval_ms =
      state.messageTimestamps[len - 1] - state.messageTimestamps[len - 2];
  }
  if (reset) {
    state.copyEvents = [];
    state.scrollDepthPct = 0;
  }
  return snapshot;
}

// ---------------------------------------------------------------------------
// Button / keyboard event classifiers (pure, testable)
// ---------------------------------------------------------------------------

export const SEND_BUTTON_SELECTORS = [
  'button[data-testid="send-button"]', // ChatGPT
  'button[aria-label="Send message"]', // ChatGPT
  'button[aria-label*="Send"]', // Generic
  'button[data-testid="send-message-button"]', // Claude
  "button.send-button", // Generic
  'button[type="submit"]', // Generic form submit
] as const;

/**
 * True if the given element is (or is nested inside) a known
 * send-message button, OR a button whose parent form-like container
 * holds a textarea / contenteditable (generic fallback).
 */
export function isSendButtonTarget(target: Element | null): boolean {
  if (!target) return false;

  for (const sel of SEND_BUTTON_SELECTORS) {
    try {
      if (target.matches?.(sel) || target.closest?.(sel)) return true;
    } catch {
      // Invalid selector at runtime (should not happen for this list)
    }
  }

  const btn = target.closest?.("button");
  if (!btn) return false;
  const parent = btn.parentElement;
  if (!parent) return false;
  if (
    parent.querySelector("textarea") ||
    parent.querySelector('[contenteditable="true"]') ||
    parent.closest("form")
  ) {
    return true;
  }
  return false;
}

export function isSendKeyEvent(evt: {
  key?: string;
  shiftKey?: boolean;
  target?: { tagName?: string; getAttribute?: (name: string) => string | null } | null;
}): boolean {
  if (!evt || evt.key !== "Enter" || evt.shiftKey) return false;
  const target = evt.target;
  if (!target) return false;
  if (target.tagName === "TEXTAREA") return true;
  if (target.getAttribute?.("contenteditable") === "true") return true;
  return false;
}

/**
 * Compute the scroll depth as an integer 0-100 from the given DOM
 * dimensions. Returns the CURRENT depth (ignoring previous highs).
 * The caller can take the max of current and previous.
 */
export function computeScrollDepth(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
): number {
  if (scrollHeight <= clientHeight) return 0;
  return Math.round(((scrollTop + clientHeight) / scrollHeight) * 100);
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
  runAt: "document_start",
  main() {
    if (window.__PCE_BEHAVIOR_TRACKER_ACTIVE) return;
    window.__PCE_BEHAVIOR_TRACKER_ACTIVE = true;

    const state = createBehaviorState();

    // --- 1. Send-button clicks ---------------------------------------
    document.addEventListener(
      "click",
      (event) => {
        const target = event.target as Element | null;
        if (isSendButtonTarget(target)) {
          const now = Date.now();
          state.lastRequestSentAt = now;
          state.messageTimestamps.push(now);
        }
      },
      true,
    );

    // --- 2. Enter-key send shortcut ---------------------------------
    document.addEventListener(
      "keydown",
      (event) => {
        if (
          isSendKeyEvent({
            key: event.key,
            shiftKey: event.shiftKey,
            target: event.target as {
              tagName?: string;
              getAttribute?: (name: string) => string | null;
            } | null,
          })
        ) {
          const now = Date.now();
          state.lastRequestSentAt = now;
          state.messageTimestamps.push(now);
        }
      },
      true,
    );

    // --- 3. Copy events ---------------------------------------------
    document.addEventListener("copy", () => {
      try {
        const selection = window.getSelection();
        const text = selection ? selection.toString() : "";
        if (text.length > 10) {
          state.copyEvents.push({ at: Date.now(), length: text.length });
        }
      } catch {
        // Never break the host page
      }
    });

    // --- 4. Scroll depth --------------------------------------------
    let scrollTrackTimer: ReturnType<typeof setTimeout> | null = null;
    const updateScrollDepth = (): void => {
      try {
        const scrollTop =
          document.documentElement.scrollTop || document.body.scrollTop;
        const scrollHeight =
          document.documentElement.scrollHeight ||
          document.body.scrollHeight;
        const clientHeight = document.documentElement.clientHeight;
        const depth = computeScrollDepth(scrollTop, scrollHeight, clientHeight);
        if (depth > state.scrollDepthPct) state.scrollDepthPct = depth;
      } catch {
        // Never break the host page
      }
    };
    window.addEventListener(
      "scroll",
      () => {
        if (scrollTrackTimer) return;
        scrollTrackTimer = setTimeout(() => {
          updateScrollDepth();
          scrollTrackTimer = null;
        }, 500);
      },
      { passive: true },
    );

    // --- 5. Public surface for other content scripts ----------------
    // Assignment goes through a type cast because we also expose the
    // internal `state` object (for legacy debug tooling) that isn't
    // in the globally-declared interface.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__PCE_BEHAVIOR = {
      getBehaviorSnapshot: (reset?: boolean) =>
        getBehaviorSnapshot(state, Boolean(reset)) as unknown as Record<
          string,
          unknown
        >,
      state,
    };

    // Listen for `postMessage`-style requests (legacy API).
    window.addEventListener("message", (event) => {
      if (event.source !== window) return;
      const data = event.data as { type?: string; reset?: boolean } | null;
      if (data && data.type === "PCE_GET_BEHAVIOR") {
        window.postMessage(
          {
            type: "PCE_BEHAVIOR_SNAPSHOT",
            payload: getBehaviorSnapshot(state, Boolean(data.reset)),
          },
          window.location.origin,
        );
      }
    });
  },
});
