/**
 * PCE – Behavior Tracker
 *
 * Collects behavioral metadata about user-AI interactions:
 *   - request_sent_at: when user clicks send
 *   - copy_events: when user copies AI output
 *   - scroll_depth: how far user scrolls through response
 *
 * Timestamps from the network interceptor (first_token_at, stream_complete_at)
 * are captured separately in network_interceptor.js.
 *
 * This runs as a content script on AI pages (injected by detector or manifest).
 * It stores state per-tab and attaches behavior data to outgoing captures.
 */

(function () {
  "use strict";

  // Guard against double-injection
  if (window.__PCE_BEHAVIOR_TRACKER_ACTIVE) return;
  window.__PCE_BEHAVIOR_TRACKER_ACTIVE = true;

  const TAG = "[PCE:behavior]";

  // -----------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------

  const state = {
    lastRequestSentAt: null,
    copyEvents: [],
    scrollDepthPct: 0,
    messageTimestamps: [], // track intervals between user messages
  };

  // -----------------------------------------------------------------------
  // 1. Detect "Send" button clicks → request_sent_at
  // -----------------------------------------------------------------------

  // Common send button selectors across AI sites
  const SEND_BUTTON_SELECTORS = [
    'button[data-testid="send-button"]',        // ChatGPT
    'button[aria-label="Send message"]',         // ChatGPT
    'button[aria-label*="Send"]',                // Generic
    'button[data-testid="send-message-button"]', // Claude
    'button.send-button',                        // Generic
    'button[type="submit"]',                     // Generic form submit
  ];

  // Listen for clicks on send buttons via event delegation
  document.addEventListener("click", function (event) {
    const target = event.target;
    if (!target) return;

    // Check if the click target (or an ancestor) matches a send button
    for (const sel of SEND_BUTTON_SELECTORS) {
      if (target.matches(sel) || target.closest(sel)) {
        state.lastRequestSentAt = Date.now();
        state.messageTimestamps.push(state.lastRequestSentAt);
        return;
      }
    }

    // Fallback: any button inside a form-like chat input area
    const btn = target.closest("button");
    if (btn) {
      const parent = btn.parentElement;
      if (parent && (
        parent.querySelector("textarea") ||
        parent.querySelector('[contenteditable="true"]') ||
        parent.closest("form")
      )) {
        state.lastRequestSentAt = Date.now();
        state.messageTimestamps.push(state.lastRequestSentAt);
      }
    }
  }, true);

  // Also detect Enter key in textarea (common send shortcut)
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.shiftKey) return;

    const target = event.target;
    if (target && (
      target.tagName === "TEXTAREA" ||
      target.getAttribute("contenteditable") === "true"
    )) {
      // Enter without Shift in a textarea → likely sending
      state.lastRequestSentAt = Date.now();
      state.messageTimestamps.push(state.lastRequestSentAt);
    }
  }, true);

  // -----------------------------------------------------------------------
  // 2. Copy events → track when user copies AI output
  // -----------------------------------------------------------------------

  document.addEventListener("copy", function () {
    try {
      const selection = window.getSelection();
      const selectedText = selection ? selection.toString() : "";
      if (selectedText.length > 10) {
        state.copyEvents.push({
          at: Date.now(),
          length: selectedText.length,
        });
      }
    } catch { /* never break the page */ }
  });

  // -----------------------------------------------------------------------
  // 3. Scroll depth tracking
  // -----------------------------------------------------------------------

  let scrollTrackTimer = null;

  function updateScrollDepth() {
    try {
      const scrollTop = document.documentElement.scrollTop || document.body.scrollTop;
      const scrollHeight = document.documentElement.scrollHeight || document.body.scrollHeight;
      const clientHeight = document.documentElement.clientHeight;

      if (scrollHeight > clientHeight) {
        const depth = Math.round(
          ((scrollTop + clientHeight) / scrollHeight) * 100
        );
        if (depth > state.scrollDepthPct) {
          state.scrollDepthPct = depth;
        }
      }
    } catch { /* never break the page */ }
  }

  window.addEventListener("scroll", function () {
    if (scrollTrackTimer) return;
    scrollTrackTimer = setTimeout(() => {
      updateScrollDepth();
      scrollTrackTimer = null;
    }, 500);
  }, { passive: true });

  // -----------------------------------------------------------------------
  // Public API: get current behavior snapshot
  // -----------------------------------------------------------------------

  /**
   * Returns the current behavior metadata and optionally resets transient state.
   * Called by content scripts (chatgpt.js, etc.) when sending a capture.
   */
  function getBehaviorSnapshot(reset) {
    const snapshot = {
      request_sent_at: state.lastRequestSentAt,
      copy_events: state.copyEvents.slice(-10), // Keep last 10
      scroll_depth_pct: state.scrollDepthPct,
    };

    // Compute think_interval_ms from last two message timestamps
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

  // Expose to other content scripts on the same page
  window.__PCE_BEHAVIOR = { getBehaviorSnapshot, state };

  // Also listen for requests from the service worker / other content scripts
  window.addEventListener("message", function (event) {
    if (event.source !== window) return;
    if (event.data && event.data.type === "PCE_GET_BEHAVIOR") {
      window.postMessage(
        {
          type: "PCE_BEHAVIOR_SNAPSHOT",
          payload: getBehaviorSnapshot(event.data.reset),
        },
        window.location.origin
      );
    }
  });
})();
