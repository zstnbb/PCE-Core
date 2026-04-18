// SPDX-License-Identifier: Apache-2.0
/**
 * PCE — Network Interceptor (page-context).
 *
 * TypeScript port of ``interceptor/network_interceptor.js`` (P2.5 Phase 2).
 *
 * Monkey-patches ``fetch`` / ``XMLHttpRequest`` / ``WebSocket`` /
 * ``EventSource`` to capture AI-related network traffic from the page
 * context, then forwards captures via ``window.postMessage`` to the
 * content-script bridge.
 *
 * Depends on: ``interceptor-ai-patterns.ts`` (must run first, sets
 * ``window.__PCE_AI_PATTERNS``).
 *
 * Intentionally self-contained — no ESM imports. Coupling with
 * ai-patterns is a runtime convention via ``window.__PCE_AI_PATTERNS``.
 */

// The shape of the API exposed at runtime by interceptor-ai-patterns.ts.
// We cast through this shape instead of declaring a `Window` global here
// to avoid a declaration-merge conflict with the stricter type exported
// by interceptor-ai-patterns.ts itself.
interface AIPatternsAPIShape {
  matchUrl: (url: string) => { isAI: boolean; confidence: string; provider: string | null; host: string | null; path: string | null };
  matchRequestBody: (body: unknown) => { isAI: boolean; confidence: string; model: string | null };
  isStreamingResponse: (contentType: string | null) => boolean;
  isAIRequest: (url: string, body: unknown) => { isAI: boolean; confidence: string; provider: string | null; model: string | null; host: string | null; path: string | null };
  AI_API_DOMAINS: Set<string>;
  WEB_UI_DOMAINS: Set<string>;
  HOST_TO_PROVIDER: Record<string, string>;
}

declare global {
  interface Window {
    __PCE_INTERCEPTOR_ACTIVE?: boolean;
    __PCE_AI_PAGE_CONFIRMED?: boolean;
  }
}

interface CapturePayload {
  capture_type:
    | "fetch"
    | "fetch_stream"
    | "fetch_error"
    | "xhr"
    | "xhr_error"
    | "websocket"
    | "eventsource";
  capture_id: string;
  url: string;
  method: string;
  host: string | null;
  path: string | null;
  provider: string | null;
  model: string | null;
  confidence: string;
  status_code: number | null;
  latency_ms: number;
  request_body: string | null;
  response_body: string | null;
  response_content_type: string;
  is_streaming: boolean;
  timestamps: {
    request_sent_at: number;
    first_token_at?: number;
    stream_complete_at?: number;
  };
}

interface MatchState {
  isAI: boolean;
  confidence: string;
  provider: string | null;
  model: string | null;
  host: string | null;
  path: string | null;
  _pendingStreamCheck?: boolean;
}

export default defineUnlistedScript(() => {
  // Guard against double-injection
  if (window.__PCE_INTERCEPTOR_ACTIVE) return;
  window.__PCE_INTERCEPTOR_ACTIVE = true;

  const TAG = "[PCE:interceptor]";
  const SSE_TIMEOUT_MS = 120_000; // Force-flush SSE after 120s of silence
  const MSG_TYPE = "PCE_NETWORK_CAPTURE";

  function getPatterns(): AIPatternsAPIShape | null {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return ((window as any).__PCE_AI_PATTERNS as AIPatternsAPIShape) || null;
  }

  // ─────────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────────

  function safeJsonParse(str: string | null | undefined): unknown {
    if (!str || typeof str !== "string") return null;
    try {
      return JSON.parse(str);
    } catch {
      return null;
    }
  }

  function truncate(str: string | null, maxLen: number): string | null {
    if (!str) return str;
    if (str.length <= maxLen) return str;
    return str.slice(0, maxLen) + "...[truncated]";
  }

  function postCapture(data: CapturePayload): void {
    try {
      window.postMessage(
        { type: MSG_TYPE, payload: data },
        window.location.origin,
      );
    } catch {
      // Silently fail — never break the page
    }
  }

  function extractBodyString(body: unknown): string | null {
    if (!body) return null;
    if (typeof body === "string") return body;
    if (body instanceof URLSearchParams) return body.toString();
    if (body instanceof FormData) return "[FormData]";
    if (body instanceof Blob) return "[Blob]";
    if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) {
      try {
        return new TextDecoder().decode(body as ArrayBuffer | ArrayBufferView);
      } catch {
        return "[Binary]";
      }
    }
    return null;
  }

  // ─────────────────────────────────────────────────────────────────────
  // Path-based noise filter — skip non-AI traffic on AI pages
  // ─────────────────────────────────────────────────────────────────────

  const NOISE_PATH_PATTERNS: RegExp[] = [
    /\/sentinel\//i,
    /\/telemetry/i,
    /\/analytics/i,
    /\/statsc\//i,
    /\/rgstr$/i,
    /\/ping$/i,
    /\/heartbeat/i,
    /\/health$/i,
    /\/log$/i,
    /\/ces\//i,
    /\/lat\//i,
    /\/aip\/connectors/i,
    /\/register_websocket/i,
    /\/_next\//i,
    /\/assets\//i,
    /\/static\//i,
    /\.(js|css|png|jpg|svg|woff|ico)(\?|$)/i,
    /\/realtime\/status/i,
    /\/connectors\/check/i,
    /\/sentry/i,
  ];

  function isNoisePath(path: string | null): boolean {
    if (!path) return false;
    for (const re of NOISE_PATH_PATTERNS) {
      if (re.test(path)) return true;
    }
    return false;
  }

  // ─────────────────────────────────────────────────────────────────────
  // Provider guess from page context (for aggressive mode)
  // ─────────────────────────────────────────────────────────────────────

  function guessProviderFromPage(): string | null {
    const patterns = getPatterns();
    if (patterns && patterns.HOST_TO_PROVIDER) {
      const host = location.hostname;
      if (patterns.HOST_TO_PROVIDER[host]) return patterns.HOST_TO_PROVIDER[host];
      // Check parent domain (e.g., chat.z.ai → z.ai)
      const parts = host.split(".");
      for (let i = 1; i < parts.length - 1; i++) {
        const parent = parts.slice(i).join(".");
        if (patterns.HOST_TO_PROVIDER[parent]) return patterns.HOST_TO_PROVIDER[parent];
      }
    }
    const title = (document.title || "").toLowerCase();
    const host = location.hostname.toLowerCase();
    const hints: Array<[RegExp, string]> = [
      [/zhipu|chatglm|glm|z\.ai/i, "zhipu"],
      [/openai|chatgpt/i, "openai"],
      [/claude|anthropic/i, "anthropic"],
      [/gemini|google/i, "google"],
      [/deepseek/i, "deepseek"],
      [/kimi|moonshot/i, "moonshot"],
      [/qwen|tongyi|aliyun/i, "alibaba"],
      [/ernie|yiyan|baidu/i, "baidu"],
      [/doubao|bytedance/i, "bytedance"],
    ];
    for (const [re, provider] of hints) {
      if (re.test(title) || re.test(host)) return provider;
    }
    return null;
  }

  function isConfirmedAIPage(): boolean {
    return (
      Boolean(window.__PCE_AI_PAGE_CONFIRMED) ||
      document.documentElement.getAttribute("data-pce-ai-confirmed") === "1"
    );
  }

  function randomCaptureId(): string {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  // ─────────────────────────────────────────────────────────────────────
  // 1. FETCH interceptor
  // ─────────────────────────────────────────────────────────────────────

  const _origFetch = window.fetch;

  window.fetch = function patchedFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    // eslint-disable-next-line prefer-rest-params
    const originalArgs = arguments as unknown as [RequestInfo | URL, RequestInit?];
    const patterns = getPatterns();
    if (!patterns) return _origFetch.apply(this, originalArgs);

    let url: string;
    try {
      url =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.href
          : input instanceof Request
          ? input.url
          : String(input);
    } catch {
      return _origFetch.apply(this, originalArgs);
    }

    // Extract request body for pattern matching
    let reqBodyStr: string | null = null;
    if (init && init.body) {
      reqBodyStr = extractBodyString(init.body);
    } else if (input instanceof Request && input.method === "POST") {
      // Try to clone and read Request body (common in Svelte/React apps)
      try {
        const cloned = input.clone();
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const bodyInit = (cloned as any)._bodyInit;
        if (bodyInit) {
          reqBodyStr = extractBodyString(bodyInit);
        }
      } catch {
        reqBodyStr = null;
      }
    }

    const reqBodyObj = safeJsonParse(reqBodyStr);
    let match: MatchState = patterns.isAIRequest(url, reqBodyObj) as MatchState;

    // ── Aggressive mode: on confirmed AI pages, also capture POST
    // requests with AI-like JSON bodies, even if URL is unknown ──
    if (!match.isAI && isConfirmedAIPage()) {
      // Skip known noise paths
      if (match.path && isNoisePath(match.path)) {
        return _origFetch.apply(this, originalArgs);
      }
      try {
        const parsed = new URL(url);
        if (isNoisePath(parsed.pathname)) return _origFetch.apply(this, originalArgs);
      } catch {
        /* ignore */
      }

      const method =
        (init && init.method) ||
        (input instanceof Request ? input.method : "GET");
      if (method === "POST" && reqBodyObj) {
        const bodyCheck = patterns.matchRequestBody(reqBodyObj);
        if (bodyCheck.isAI) {
          match = {
            isAI: true,
            confidence: "aggressive",
            provider: guessProviderFromPage() || "unknown",
            model: bodyCheck.model,
            host: null,
            path: null,
          };
          try {
            const parsed = new URL(url);
            match.host = parsed.hostname;
            match.path = parsed.pathname;
          } catch {
            /* ignore */
          }
        }
      }
      // Also capture any SSE/streaming POST on confirmed AI pages
      if (!match.isAI && method === "POST") {
        match = {
          isAI: false,
          confidence: "none",
          provider: null,
          model: null,
          host: null,
          path: null,
          _pendingStreamCheck: true,
        };
        try {
          const parsed = new URL(url);
          match.host = parsed.hostname;
          match.path = parsed.pathname;
        } catch {
          /* ignore */
        }
      }
    }

    if (!match.isAI && !match._pendingStreamCheck) {
      return _origFetch.apply(this, originalArgs);
    }

    const captureId = randomCaptureId();
    const requestTime = performance.now();

    return _origFetch
      .apply(this, originalArgs)
      .then(async (response: Response) => {
        try {
          const contentType = response.headers.get("content-type") || "";
          const isStreaming = patterns.isStreamingResponse(contentType);

          // ── Pending stream check: only capture if response is actually SSE/JSON ──
          if (match._pendingStreamCheck) {
            const isJson = contentType.includes("application/json");
            if (!isStreaming && !isJson) {
              return response;
            }
            match = {
              isAI: true,
              confidence: "aggressive-stream",
              provider: guessProviderFromPage() || "unknown",
              model: null,
              host: match.host,
              path: match.path,
            };
          }

          if (isStreaming) {
            return handleStreamingResponse(
              response,
              captureId,
              requestTime,
              match,
              reqBodyStr,
              url,
            );
          } else {
            // Non-streaming: clone and read
            const clone = response.clone();
            let text: string;
            try {
              text = await clone.text();
            } catch {
              text = "[unreadable response body]";
            }
            const latencyMs = performance.now() - requestTime;

            postCapture({
              capture_type: "fetch",
              capture_id: captureId,
              url,
              method: (init && init.method) || "GET",
              host: match.host,
              path: match.path,
              provider: match.provider,
              model: match.model,
              confidence: match.confidence,
              status_code: response.status,
              latency_ms: Math.round(latencyMs),
              request_body: truncate(reqBodyStr, 50_000),
              response_body: truncate(text, 200_000),
              response_content_type: contentType,
              is_streaming: false,
              timestamps: {
                request_sent_at: Date.now() - Math.round(latencyMs),
                first_token_at: Date.now(),
                stream_complete_at: Date.now(),
              },
            });
          }
        } catch {
          // Never break the page
        }
        return response;
      })
      .catch((fetchErr: Error) => {
        // Network error — log the failed attempt so self-check can detect silent failures
        try {
          postCapture({
            capture_type: "fetch_error",
            capture_id: captureId,
            url,
            method: (init && init.method) || "GET",
            host: match.host,
            path: match.path,
            provider: match.provider,
            model: match.model,
            confidence: match.confidence,
            status_code: 0,
            latency_ms: Math.round(performance.now() - requestTime),
            request_body: truncate(reqBodyStr, 50_000),
            response_body: JSON.stringify({ error: fetchErr.message }),
            response_content_type: "",
            is_streaming: false,
            timestamps: { request_sent_at: Date.now() },
          });
        } catch {
          /* never break the page */
        }
        throw fetchErr;
      });
  };

  // Disguise patched fetch
  try {
    Object.defineProperty(window.fetch, "toString", {
      value: function () {
        return "function fetch() { [native code] }";
      },
      writable: false,
      configurable: true,
    });
    Object.defineProperty(window.fetch, "name", {
      value: "fetch",
      writable: false,
      configurable: true,
    });
  } catch {
    /* best effort */
  }

  // ─────────────────────────────────────────────────────────────────────
  // SSE / Streaming response handler (v1: rough but stable)
  // ─────────────────────────────────────────────────────────────────────

  function handleStreamingResponse(
    response: Response,
    captureId: string,
    requestTime: number,
    match: MatchState,
    reqBodyStr: string | null,
    url: string,
  ): Response {
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    const chunks: string[] = [];
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
    let firstChunkTime: number | null = null;
    let done = false;

    function resetTimeout() {
      if (timeoutHandle) clearTimeout(timeoutHandle);
      timeoutHandle = setTimeout(() => {
        if (!done) flushAndPost();
      }, SSE_TIMEOUT_MS);
    }

    function flushAndPost() {
      done = true;
      if (timeoutHandle) clearTimeout(timeoutHandle);
      const fullText = chunks.join("");
      const latencyMs = performance.now() - requestTime;

      postCapture({
        capture_type: "fetch_stream",
        capture_id: captureId,
        url,
        method: "POST",
        host: match.host,
        path: match.path,
        provider: match.provider,
        model: match.model,
        confidence: match.confidence,
        status_code: response.status,
        latency_ms: Math.round(latencyMs),
        request_body: truncate(reqBodyStr, 50_000),
        response_body: truncate(fullText, 200_000),
        response_content_type: response.headers.get("content-type") || "",
        is_streaming: true,
        timestamps: {
          request_sent_at: Date.now() - Math.round(latencyMs),
          first_token_at: firstChunkTime || Date.now(),
          stream_complete_at: Date.now(),
        },
      });
    }

    // Create a new ReadableStream that passes data through while capturing
    const newStream = new ReadableStream<Uint8Array>({
      start(controller) {
        resetTimeout();

        function pump(): void {
          reader
            .read()
            .then(({ done: readerDone, value }) => {
              if (readerDone) {
                controller.close();
                if (!done) flushAndPost();
                return;
              }

              if (value) {
                const text = decoder.decode(value, { stream: true });
                chunks.push(text);
                if (!firstChunkTime) firstChunkTime = Date.now();
                resetTimeout();
                controller.enqueue(value);
              }
              pump();
            })
            .catch((err) => {
              controller.error(err);
              if (!done) flushAndPost();
            });
        }

        pump();
      },
    });

    return new Response(newStream, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  // ─────────────────────────────────────────────────────────────────────
  // 2. XMLHttpRequest interceptor
  // ─────────────────────────────────────────────────────────────────────

  const _origXHROpen = XMLHttpRequest.prototype.open;
  const _origXHRSend = XMLHttpRequest.prototype.send;

  interface PatchedXHR extends XMLHttpRequest {
    __pce_method?: string;
    __pce_url?: string;
  }

  XMLHttpRequest.prototype.open = function (
    this: PatchedXHR,
    method: string,
    url: string | URL,
    async?: boolean,
    username?: string | null,
    password?: string | null,
  ): void {
    this.__pce_method = method;
    this.__pce_url = typeof url === "string" ? url : String(url);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (_origXHROpen as any).call(this, method, url, async, username, password);
  } as typeof XMLHttpRequest.prototype.open;

  XMLHttpRequest.prototype.send = function (
    this: PatchedXHR,
    body?: Document | XMLHttpRequestBodyInit | null,
  ): void {
    const patterns = getPatterns();
    if (!patterns) return _origXHRSend.call(this, body);

    const url = this.__pce_url;
    const method = this.__pce_method || "GET";
    const reqBodyStr = extractBodyString(body);
    const reqBodyObj = safeJsonParse(reqBodyStr);
    const match = patterns.isAIRequest(url || "", reqBodyObj);

    if (match.isAI) {
      const requestTime = performance.now();
      const captureId = randomCaptureId();
      const xhr = this;

      this.addEventListener("load", function () {
        try {
          const latencyMs = performance.now() - requestTime;
          postCapture({
            capture_type: "xhr",
            capture_id: captureId,
            url: url!,
            method,
            host: match.host,
            path: match.path,
            provider: match.provider,
            model: match.model,
            confidence: match.confidence,
            status_code: xhr.status,
            latency_ms: Math.round(latencyMs),
            request_body: truncate(reqBodyStr, 50_000),
            response_body: truncate(xhr.responseText, 200_000),
            response_content_type: xhr.getResponseHeader("content-type") || "",
            is_streaming: false,
            timestamps: {
              request_sent_at: Date.now() - Math.round(latencyMs),
              first_token_at: Date.now(),
              stream_complete_at: Date.now(),
            },
          });
        } catch {
          /* never break */
        }
      });

      this.addEventListener("error", function () {
        try {
          postCapture({
            capture_type: "xhr_error",
            capture_id: captureId,
            url: url!,
            method,
            host: match.host,
            path: match.path,
            provider: match.provider,
            model: match.model,
            confidence: match.confidence,
            status_code: 0,
            latency_ms: Math.round(performance.now() - requestTime),
            request_body: truncate(reqBodyStr, 50_000),
            response_body: JSON.stringify({ error: "XHR network error" }),
            response_content_type: "",
            is_streaming: false,
            timestamps: { request_sent_at: Date.now() },
          });
        } catch {
          /* never break */
        }
      });
    }

    return _origXHRSend.call(this, body);
  } as typeof XMLHttpRequest.prototype.send;

  try {
    Object.defineProperty(XMLHttpRequest.prototype.open, "toString", {
      value: function () {
        return "function open() { [native code] }";
      },
      writable: false,
      configurable: true,
    });
    Object.defineProperty(XMLHttpRequest.prototype.send, "toString", {
      value: function () {
        return "function send() { [native code] }";
      },
      writable: false,
      configurable: true,
    });
  } catch {
    /* best effort */
  }

  // ─────────────────────────────────────────────────────────────────────
  // 3. WebSocket interceptor (enhanced for ChatGPT/Claude WS streams)
  // ─────────────────────────────────────────────────────────────────────

  const _origWebSocket = window.WebSocket;

  interface ExtractedConversation {
    content: string;
    model: string | null;
    tool_calls: Array<{ type: string; name: string; result?: string; arguments?: string }>;
  }

  function extractWsConversationContent(messages: string[]): ExtractedConversation {
    const contentParts: string[] = [];
    let model: string | null = null;
    let lastSeenText = "";
    const toolCalls: Array<{ type: string; name: string; result?: string; arguments?: string }> = [];

    for (const raw of messages) {
      const obj = safeJsonParse(raw) as Record<string, unknown> | null;
      if (!obj) {
        // Could be SSE-formatted data over WS
        if (raw.startsWith("data: ")) {
          const inner = safeJsonParse(raw.slice(6)) as Record<string, unknown> | null;
          if (inner) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const delta = (inner.choices as any)?.[0]?.delta;
            if (delta?.content) contentParts.push(delta.content);
            if (inner.model) model = inner.model as string;
          }
        }
        continue;
      }

      // ChatGPT "v1" streaming format: {"type":"...", "body":"..."}
      if (obj.body !== undefined) {
        const body =
          (safeJsonParse(obj.body as string) as Record<string, unknown> | null) ||
          (typeof obj.body === "object" ? (obj.body as Record<string, unknown>) : null);
        if (body && typeof body === "object") {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const msg = (body as any).message;
          if (msg?.content?.parts) {
            const textParts: string[] = [];
            for (const p of msg.content.parts as unknown[]) {
              if (typeof p === "string") {
                textParts.push(p);
              } else if (typeof p === "object" && p !== null) {
                const pObj = p as Record<string, unknown>;
                if (pObj.asset_pointer || pObj.url) {
                  toolCalls.push({
                    type: "tool_call",
                    name: (pObj.content_type as string) || "image_generation",
                    result: truncate(JSON.stringify(pObj), 2_000) ?? "",
                  });
                }
              }
            }
            const text = textParts.join("");
            if (text && text.length > lastSeenText.length) {
              lastSeenText = text;
            }
          }
          // Deep Research: result may appear in msg.content.result or msg.content.text
          if (msg?.content?.result && typeof msg.content.result === "string") {
            const r = msg.content.result;
            if (r.length > lastSeenText.length) lastSeenText = r;
          }
          if (msg?.content?.text && typeof msg.content.text === "string") {
            const t = msg.content.text;
            if (t.length > lastSeenText.length) lastSeenText = t;
          }
          // Tool invocations in metadata
          if (
            msg?.recipient &&
            msg.recipient !== "all" &&
            msg.recipient !== "browser"
          ) {
            toolCalls.push({
              type: "tool_call",
              name: msg.recipient,
              arguments: truncate(JSON.stringify(msg?.content?.parts || []), 2_000) ?? "",
            });
          }
          if (msg?.metadata?.model_slug) model = msg.metadata.model_slug as string;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          if ((body as any).model) model = (body as any).model as string;
        }
      }

      // Standard OpenAI streaming format
      if (obj.choices) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const delta = (obj.choices as any)[0]?.delta;
        if (delta?.content) contentParts.push(delta.content);
        if (obj.model) model = obj.model as string;
      }

      // Direct content field
      if (obj.content && typeof obj.content === "string" && obj.role === "assistant") {
        contentParts.push(obj.content);
      }

      // Deep Research: top-level result/text fields
      if (obj.result && typeof obj.result === "string" && (obj.result as string).length > 50) {
        if ((obj.result as string).length > lastSeenText.length) lastSeenText = obj.result as string;
      }
      if (
        obj.text &&
        typeof obj.text === "string" &&
        (obj.text as string).length > 50 &&
        obj.type !== "ping"
      ) {
        if ((obj.text as string).length > lastSeenText.length) lastSeenText = obj.text as string;
      }
    }

    // Merge: if cumulative parts from ChatGPT format are longer, use those
    const joined = contentParts.join("");
    const finalContent = lastSeenText.length > joined.length ? lastSeenText : joined;

    // De-duplicate tool_calls by name+result
    const seenTools = new Set<string>();
    const uniqueToolCalls = toolCalls.filter((tc) => {
      const key = (tc.name || "") + (tc.result || tc.arguments || "");
      if (seenTools.has(key)) return false;
      seenTools.add(key);
      return true;
    });

    return { content: finalContent, model, tool_calls: uniqueToolCalls };
  }

  const PatchedWebSocket = function (this: WebSocket, url: string, protocols?: string | string[]) {
    const ws =
      protocols !== undefined
        ? new _origWebSocket(url, protocols)
        : new _origWebSocket(url);

    const patterns = getPatterns();
    if (!patterns) return ws;

    let wsHost: string;
    let wsPath: string;
    let wsProvider: string | null = null;
    try {
      const parsed = new URL(url);
      wsHost = parsed.hostname;
      wsPath = parsed.pathname;
      wsProvider = patterns.HOST_TO_PROVIDER[wsHost] || null;
    } catch {
      return ws;
    }

    const isAIDomain = patterns.AI_API_DOMAINS.has(wsHost);
    if (!isAIDomain) return ws;

    const receivedChunks: string[] = [];
    const sentChunks: string[] = [];
    let startTime = Date.now();
    let firstChunkTime: number | null = null;
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    let flushed = false;
    const FLUSH_DELAY_MS = 8_000;

    function resetFlushTimer() {
      if (flushTimer) clearTimeout(flushTimer);
      flushTimer = setTimeout(flushAndPost, FLUSH_DELAY_MS);
    }

    function flushAndPost() {
      if (flushed) return;
      if (flushTimer) clearTimeout(flushTimer);

      const extracted = extractWsConversationContent(receivedChunks);
      if (!extracted.content || extracted.content.length < 20) {
        receivedChunks.length = 0;
        sentChunks.length = 0;
        flushed = false;
        return;
      }

      flushed = true;

      // Build a synthetic response body matching the normalizer format
      const assistantMsg: Record<string, unknown> = {
        role: "assistant",
        content: extracted.content,
      };
      if (extracted.tool_calls && extracted.tool_calls.length > 0) {
        assistantMsg.tool_calls = extracted.tool_calls;
      }
      const respBody = JSON.stringify({
        model: extracted.model,
        choices: [{ message: assistantMsg }],
      });

      // Try to extract the user's request from sent messages
      let reqBody: string | null = null;
      for (const raw of sentChunks) {
        const obj = safeJsonParse(raw) as Record<string, unknown> | null;
        if (obj?.messages || obj?.message || obj?.prompt) {
          reqBody = raw;
          break;
        }
        if (obj?.body) {
          const inner = safeJsonParse(obj.body as string) as Record<string, unknown> | null;
          if (inner?.messages) {
            reqBody = obj.body as string;
            break;
          }
        }
      }

      postCapture({
        capture_type: "websocket",
        capture_id: randomCaptureId(),
        url,
        method: "WS",
        host: wsHost,
        path: wsPath,
        provider: wsProvider || "unknown",
        model: extracted.model,
        confidence: "high",
        status_code: null,
        latency_ms: Date.now() - startTime,
        request_body: truncate(reqBody, 50_000),
        response_body: truncate(respBody, 200_000),
        response_content_type: "websocket",
        is_streaming: true,
        timestamps: {
          request_sent_at: startTime,
          first_token_at: firstChunkTime || Date.now(),
          stream_complete_at: Date.now(),
        },
      });

      // Reset for next conversation turn on the same WS connection
      receivedChunks.length = 0;
      sentChunks.length = 0;
      startTime = Date.now();
      firstChunkTime = null;
      flushed = false;
    }

    ws.addEventListener("message", function (event: MessageEvent) {
      try {
        let data: string;
        if (typeof event.data === "string") {
          data = event.data;
        } else if (event.data instanceof ArrayBuffer) {
          try {
            data = new TextDecoder().decode(event.data);
          } catch {
            data = "[Binary]";
          }
        } else if (event.data instanceof Blob) {
          data = "[Blob]";
        } else {
          data = "[Binary]";
        }
        // Skip heartbeat / ping / empty messages
        if (typeof data === "string" && (data.length < 5 || data === "{}" || data === '""')) {
          return;
        }
        receivedChunks.push(data);
        if (!firstChunkTime) firstChunkTime = Date.now();
        resetFlushTimer();
      } catch {
        /* never break */
      }
    });

    // Intercept outgoing messages
    const _origSend = ws.send.bind(ws);
    ws.send = function (data: string | ArrayBufferLike | Blob | ArrayBufferView) {
      try {
        if (typeof data === "string") {
          const obj = safeJsonParse(data) as Record<string, unknown> | null;
          const innerBodyParsed = obj?.body
            ? (safeJsonParse(obj.body as string) as Record<string, unknown> | null)
            : null;
          const isUserMsg =
            obj &&
            (obj.messages ||
              obj.message ||
              obj.prompt ||
              (obj.body &&
                (innerBodyParsed?.messages || innerBodyParsed?.action === "next")));
          if (isUserMsg && receivedChunks.length > 0 && !flushed) {
            flushAndPost();
          }
          sentChunks.push(data);
        }
      } catch {
        /* never break */
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return _origSend(data as any);
    };

    ws.addEventListener("close", function () {
      if (flushTimer) clearTimeout(flushTimer);
      if (!flushed && receivedChunks.length > 0) {
        flushAndPost();
      }
    });

    return ws;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;

  // Copy static properties and prototype
  PatchedWebSocket.prototype = _origWebSocket.prototype;
  PatchedWebSocket.CONNECTING = _origWebSocket.CONNECTING;
  PatchedWebSocket.OPEN = _origWebSocket.OPEN;
  PatchedWebSocket.CLOSING = _origWebSocket.CLOSING;
  PatchedWebSocket.CLOSED = _origWebSocket.CLOSED;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).WebSocket = PatchedWebSocket;

  try {
    Object.defineProperty(window.WebSocket, "toString", {
      value: function () {
        return "function WebSocket() { [native code] }";
      },
      writable: false,
      configurable: true,
    });
  } catch {
    /* best effort */
  }

  // ─────────────────────────────────────────────────────────────────────
  // 4. EventSource interceptor
  // ─────────────────────────────────────────────────────────────────────

  const _origEventSource = window.EventSource;

  if (_origEventSource) {
    const PatchedEventSource = function (this: EventSource, url: string, config?: EventSourceInit) {
      const es = config ? new _origEventSource(url, config) : new _origEventSource(url);

      const patterns = getPatterns();
      if (!patterns) return es;

      const urlMatch = patterns.matchUrl(url);

      if (urlMatch.isAI) {
        const chunks: string[] = [];
        const startTime = Date.now();
        let firstChunkTime: number | null = null;
        let timeoutHandle: ReturnType<typeof setTimeout> | null = null;

        function resetTimeout() {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          timeoutHandle = setTimeout(flushAndPost, SSE_TIMEOUT_MS);
        }

        function flushAndPost() {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          if (chunks.length === 0) return;
          const allData = chunks.splice(0, chunks.length).join("\n");
          postCapture({
            capture_type: "eventsource",
            capture_id: randomCaptureId(),
            url,
            method: "GET",
            host: urlMatch.host,
            path: urlMatch.path,
            provider: urlMatch.provider,
            model: null,
            confidence: urlMatch.confidence,
            status_code: null,
            latency_ms: Date.now() - startTime,
            request_body: null,
            response_body: truncate(allData, 200_000),
            response_content_type: "text/event-stream",
            is_streaming: true,
            timestamps: {
              request_sent_at: startTime,
              first_token_at: firstChunkTime || Date.now(),
              stream_complete_at: Date.now(),
            },
          });
        }

        es.addEventListener("message", function (event: MessageEvent) {
          try {
            chunks.push(event.data);
            if (!firstChunkTime) firstChunkTime = Date.now();
            resetTimeout();
          } catch {
            /* never break */
          }
        });

        es.addEventListener("error", function () {
          flushAndPost();
        });

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (es as any).__pce_flush = flushAndPost;

        // Override close() to flush before closing
        const _origClose = es.close.bind(es);
        es.close = function () {
          flushAndPost();
          return _origClose();
        };
      }

      return es;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;

    PatchedEventSource.prototype = _origEventSource.prototype;
    PatchedEventSource.CONNECTING = _origEventSource.CONNECTING;
    PatchedEventSource.OPEN = _origEventSource.OPEN;
    PatchedEventSource.CLOSED = _origEventSource.CLOSED;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).EventSource = PatchedEventSource;

    try {
      Object.defineProperty(window.EventSource, "toString", {
        value: function () {
          return "function EventSource() { [native code] }";
        },
        writable: false,
        configurable: true,
      });
    } catch {
      /* best effort */
    }
  }

  console.log(TAG, "Network interceptor active (fetch + XHR + WebSocket + EventSource)");
});
