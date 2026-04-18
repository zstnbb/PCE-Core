// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Shared capture runtime for site-specific content scripts.
 *
 * The three legacy site extractors (``chatgpt.js``, ``claude.js``,
 * ``gemini.js``) were ~80 % boilerplate: MutationObserver on a
 * conversation container, debounced capture, fingerprint-based change
 * detection, SPA navigation handling, ``chrome.runtime.sendMessage``
 * with rollback on failure. Only the ``extractMessages`` body
 * differed meaningfully.
 *
 * This module factors that shared plumbing into a ``createCaptureRuntime``
 * factory. Each TS content-script entrypoint supplies its provider
 * identity, selectors, and an ``extractMessages`` callback, then calls
 * ``runtime.start()`` at ``DOMContentLoaded``. The runtime handles
 * everything else.
 *
 * Design properties:
 *
 * - **No globals.** The runtime accepts all its dependencies as
 *   options (doc, win, chrome) so happy-dom tests can drive it
 *   without installing fake extension APIs.
 * - **Zero behaviour drift.** Timing constants, fingerprint format,
 *   rollback semantics, and the SPA-nav hook pattern were lifted
 *   straight from the legacy JS. Each site extractor's test suite
 *   re-asserts the payload shape + the observer lifecycle.
 * - **Two capture modes.** ``incremental`` (ChatGPT/Gemini pattern)
 *   tracks ``sentCount`` and submits only new messages plus an
 *   optionally-updated last message. ``full`` (Claude pattern)
 *   always submits the whole message list when the fingerprint
 *   changes.
 */

import { fingerprintConversation, type PceAttachment } from "./pce-dom";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Minimal chrome.runtime surface the runtime uses. */
export interface ChromeRuntimeLike {
  sendMessage: (
    msg: unknown,
    cb: (response: unknown) => void,
  ) => void;
  lastError?: { message?: string } | null;
}

export interface ExtractedMessage {
  role: string;
  content: string;
  attachments?: PceAttachment[];
}

export type CaptureMode = "incremental" | "full";

export interface CapturePayload {
  provider: string;
  source_name: string;
  host: string;
  path: string;
  model_name: string | null;
  session_hint: string | null;
  conversation: {
    conversation_id?: string;
    messages: ExtractedMessage[];
    total_messages?: number;
    url: string;
    title: string;
  };
  meta: Record<string, unknown>;
}

export interface CaptureRuntimeOptions {
  // --- Identity ---
  /** Canonical provider name (e.g. ``"openai"``). */
  provider: string;
  /** Source label attached to captures (e.g. ``"chatgpt-web"``). */
  sourceName: string;
  /** Short tag used in logs / error messages (e.g. ``"ChatGPT"``). */
  siteName: string;
  /** `extraction_strategy` field inserted into `meta`. */
  extractionStrategy: string;
  /** Incremental delta vs. full resend. */
  captureMode: CaptureMode;

  // --- Timing ---
  /** Debounce window for coalescing observer fires. */
  debounceMs: number;
  /** Retry interval while streaming is detected. Default 1500 ms. */
  streamCheckMs?: number;
  /** SPA URL-polling interval. Default 3000 ms. */
  pollIntervalMs?: number;
  /** Max container-wait retries (2s between attempts). Default 15. */
  maxContainerRetries?: number;

  // --- DOM accessors (all optional except extractMessages) ---
  getContainer: () => Element | null;
  isStreaming?: () => boolean;
  extractMessages: () => ExtractedMessage[];
  getSessionHint: () => string | null;
  getModelName?: () => string | null;

  // --- Behaviour flags ---
  /**
   * If true, the runtime patches ``history.pushState`` /
   * ``history.replaceState`` and listens to ``popstate`` to detect
   * SPA navigation (ChatGPT pattern). If false, only the
   * ``pollIntervalMs`` URL-polling heuristic is used (Claude/Gemini
   * pattern).
   */
  hookHistoryApi?: boolean;

  /**
   * If true, the runtime defers capture (at ``streamCheckMs``
   * cadence) until the extracted messages contain BOTH a user role
   * and an assistant role. Used by sites whose DOM briefly reveals
   * only one side mid-render (Poe / Grok pattern). Defaults to false.
   */
  requireBothRoles?: boolean;

  // --- Injection points ---
  /** Injectable window handle. Defaults to `globalThis.window`. */
  win?: Window & typeof globalThis;
  /** Injectable document. Defaults to `win.document`. */
  doc?: Document;
  /**
   * Injectable ``chrome.runtime`` surface. Defaults to
   * ``globalThis.chrome?.runtime`` if available. Tests pass a stub.
   */
  chromeRuntime?: ChromeRuntimeLike;
  /** Logger replacement (defaults to `console`). */
  logger?: {
    log: (...args: unknown[]) => void;
    error: (...args: unknown[]) => void;
    debug?: (...args: unknown[]) => void;
  };
  /**
   * Optional callback fired when SPA nav is detected. Site extractors
   * use this to reset local state (e.g. ChatGPT's ``currentConvId``).
   * The runtime resets its own fingerprint + sentCount unconditionally.
   */
  onNavChange?: () => void;

  /**
   * Optional override for the conversation_id field. ChatGPT uses a
   * special "_new_" + timestamp synthetic ID for chats that haven't
   * received their ``/c/<uuid>`` URL yet. The runtime calls this if
   * set, otherwise falls back to ``getSessionHint()``.
   */
  resolveConversationId?: (
    messages: ExtractedMessage[],
  ) => string | null;
}

export interface CaptureRuntime {
  /** Start the observer + SPA hooks. Safe to call once. */
  start(): void;
  /**
   * Force an immediate capture attempt, bypassing the debounce.
   * Used by the manual-capture DOM bridge. Resets the fingerprint so
   * the payload is re-sent even when the conversation hasn't changed.
   */
  triggerCapture(): void;
  /** Tear down the observer + timers. Idempotent. */
  disconnect(): void;
  /** Test hook: current fingerprint (empty string before first capture). */
  readonly fingerprint: string;
  /** Test hook: number of messages already sent (incremental mode only). */
  readonly sentCount: number;
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

export function createCaptureRuntime(
  options: CaptureRuntimeOptions,
): CaptureRuntime {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g: any = globalThis;
  const win: Window & typeof globalThis =
    options.win ?? (typeof window !== "undefined" ? window : g);
  const doc: Document =
    options.doc ?? (win && win.document) ?? g.document;
  const chromeRuntime: ChromeRuntimeLike | null =
    options.chromeRuntime ??
    (g.chrome && g.chrome.runtime ? (g.chrome.runtime as ChromeRuntimeLike) : null);

  const log = options.logger ?? console;
  const TAG = `[PCE:${options.siteName}]`;

  const DEBOUNCE_MS = options.debounceMs;
  const STREAM_CHECK_MS = options.streamCheckMs ?? 1500;
  const POLL_INTERVAL_MS = options.pollIntervalMs ?? 3000;
  const MAX_CONTAINER_RETRIES = options.maxContainerRetries ?? 15;

  // --- Internal state ----------------------------------------------
  let sentFingerprint = "";
  let sentCount = 0;
  let observer: MutationObserver | null = null;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let containerRetries = 0;
  let navHooked = false;
  let started = false;

  // --- Helpers -----------------------------------------------------

  function clearDebounce(): void {
    if (debounceTimer != null) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
  }

  function schedule(delayMs: number = DEBOUNCE_MS): void {
    clearDebounce();
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      captureNow();
    }, delayMs);
  }

  function buildPayload(
    allMsgs: ExtractedMessage[],
    payloadMsgs: ExtractedMessage[],
    captureModeMeta: "message_delta" | "message_update" | "full",
  ): CapturePayload {
    const modelName = options.getModelName ? options.getModelName() : null;
    const sessionHint = options.getSessionHint();
    const convId = options.resolveConversationId
      ? options.resolveConversationId(allMsgs)
      : sessionHint;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const behavior: Record<string, unknown> = (() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const b = (win as any).__PCE_BEHAVIOR;
      if (b && typeof b.getBehaviorSnapshot === "function") {
        try {
          return (b.getBehaviorSnapshot(true) as Record<string, unknown>) || {};
        } catch {
          return {};
        }
      }
      return {};
    })();

    const conversation: CapturePayload["conversation"] = {
      messages: payloadMsgs,
      url: win.location.href,
      title: doc.title,
    };
    if (convId) conversation.conversation_id = convId;
    if (options.captureMode === "incremental") {
      conversation.total_messages = allMsgs.length;
    }

    const meta: Record<string, unknown> = {
      extraction_strategy: options.extractionStrategy,
      behavior,
    };
    if (options.captureMode === "incremental") {
      meta.new_message_count = payloadMsgs.length;
      meta.total_message_count = allMsgs.length;
      meta.capture_mode = captureModeMeta;
      meta.updated_message_index =
        captureModeMeta === "message_update"
          ? allMsgs.length - 1
          : null;
    } else {
      meta.message_count = allMsgs.length;
    }

    return {
      provider: options.provider,
      source_name: options.sourceName,
      host: win.location.hostname,
      path: win.location.pathname,
      model_name: modelName,
      session_hint: sessionHint,
      conversation,
      meta,
    };
  }

  function captureNow(): void {
    // Defer while streaming is still in-flight.
    if (options.isStreaming && options.isStreaming()) {
      schedule(STREAM_CHECK_MS);
      return;
    }

    let allMsgs: ExtractedMessage[];
    try {
      allMsgs = options.extractMessages();
    } catch (err) {
      log.error(TAG, "extractMessages threw:", (err as Error).message);
      return;
    }
    if (!Array.isArray(allMsgs) || allMsgs.length === 0) return;

    // Require-both-roles guard: defer capture when DOM briefly reveals
    // only one side (Poe / Grok pattern).
    if (options.requireBothRoles) {
      const hasUser = allMsgs.some((m) => m && m.role === "user");
      const hasAssistant = allMsgs.some((m) => m && m.role === "assistant");
      if (!hasUser || !hasAssistant) {
        schedule(STREAM_CHECK_MS);
        return;
      }
    }

    const fp = fingerprintConversation(
      allMsgs as Array<{ role?: string; content?: string; attachments?: Array<Record<string, unknown>> }>,
    );
    if (fp === sentFingerprint) return;

    let payloadMsgs: ExtractedMessage[];
    let captureModeMeta: "message_delta" | "message_update" | "full";
    if (options.captureMode === "incremental") {
      const updateOnly =
        allMsgs.length > 0 && allMsgs.slice(sentCount).length === 0;
      payloadMsgs = updateOnly
        ? [allMsgs[allMsgs.length - 1]]
        : allMsgs.slice(sentCount);
      captureModeMeta = updateOnly ? "message_update" : "message_delta";
    } else {
      payloadMsgs = allMsgs;
      captureModeMeta = "full";
    }

    // Stash previous state so we can roll back on error.
    const prevFingerprint = sentFingerprint;
    const prevCount = sentCount;

    // Optimistically advance state.
    sentFingerprint = fp;
    sentCount = allMsgs.length;

    const payload = buildPayload(allMsgs, payloadMsgs, captureModeMeta);

    if (!chromeRuntime) {
      // No chrome.runtime (e.g. under Vitest without a chrome stub).
      // Treat as success — tests that care about the payload install
      // their own stub and intercept sendMessage there.
      log.debug?.(TAG, "no chrome.runtime — dry run");
      return;
    }

    try {
      chromeRuntime.sendMessage(
        { type: "PCE_CAPTURE", payload },
        (response: unknown) => {
          const lastError = chromeRuntime.lastError;
          if (lastError) {
            log.error(TAG, "Send failed:", lastError.message || "unknown");
            sentFingerprint = prevFingerprint;
            sentCount = prevCount;
            return;
          }
          const resp = (response || {}) as { ok?: boolean };
          if (resp.ok) {
            log.log(
              TAG,
              `+${payloadMsgs.length} msg(s) captured (total ${allMsgs.length})`,
            );
          } else {
            log.log(TAG, "Capture not ok:", response);
            sentFingerprint = prevFingerprint;
            sentCount = prevCount;
          }
        },
      );
    } catch (err) {
      log.error(TAG, "sendMessage threw:", (err as Error).message);
      sentFingerprint = prevFingerprint;
      sentCount = prevCount;
    }
  }

  // --- Observer lifecycle -----------------------------------------

  function disconnectObserver(): void {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  function startObserver(): void {
    if (observer) return;

    const container = options.getContainer();
    if (!container) {
      containerRetries++;
      if (containerRetries <= MAX_CONTAINER_RETRIES) {
        setTimeout(() => startObserver(), 2_000);
      } else {
        log.error(TAG, "Container not found after max retries; giving up");
      }
      return;
    }

    containerRetries = 0;
    observer = new MutationObserver((mutations) => {
      const relevant = mutations.some(
        (m) => m.addedNodes.length > 0 || m.type === "characterData",
      );
      if (relevant) schedule();
    });
    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    log.log(TAG, "Observer started");

    // First capture attempt after the observer is attached.
    schedule();
  }

  // --- SPA navigation hooks ---------------------------------------

  function onNavChange(): void {
    sentFingerprint = "";
    sentCount = 0;
    if (options.onNavChange) {
      try {
        options.onNavChange();
      } catch (err) {
        log.error(TAG, "onNavChange threw:", (err as Error).message);
      }
    }
    schedule();
  }

  function installHistoryHooks(): void {
    if (navHooked || !options.hookHistoryApi) return;
    navHooked = true;

    const history = win.history;
    const origPush = history.pushState;
    history.pushState = function (...args: Parameters<History["pushState"]>) {
      origPush.apply(this, args);
      onNavChange();
    };
    const origReplace = history.replaceState;
    history.replaceState = function (
      ...args: Parameters<History["replaceState"]>
    ) {
      origReplace.apply(this, args);
      onNavChange();
    };
    win.addEventListener("popstate", () => onNavChange());
  }

  function installPollingNavHook(): void {
    if (pollTimer != null) return;
    let lastHref = win.location.href;
    pollTimer = setInterval(() => {
      if (win.location.href !== lastHref) {
        lastHref = win.location.href;
        onNavChange();
      }
    }, POLL_INTERVAL_MS);
  }

  // --- Public API -------------------------------------------------

  function start(): void {
    if (started) return;
    started = true;
    installHistoryHooks();
    installPollingNavHook();
    startObserver();
  }

  function triggerCapture(): void {
    sentFingerprint = "";
    sentCount = 0;
    clearDebounce();
    captureNow();
  }

  function disconnect(): void {
    clearDebounce();
    disconnectObserver();
    if (pollTimer != null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  return {
    start,
    triggerCapture,
    disconnect,
    get fingerprint() {
      return sentFingerprint;
    },
    get sentCount() {
      return sentCount;
    },
  };
}
