/**
 * PCE – Network Interceptor
 *
 * Monkey-patches fetch, XMLHttpRequest, WebSocket, and EventSource to
 * capture AI-related network traffic from the page context.
 *
 * This file is injected into the PAGE context (not content script context)
 * via a <script> tag so it can intercept the page's own network calls.
 *
 * Communication back to the extension:
 *   page context → window.postMessage → content script (bridge.js)
 *     → chrome.runtime.sendMessage → service_worker.js → PCE Ingest API
 *
 * Depends on: ai_patterns.js (must be injected first, exposes window.__PCE_AI_PATTERNS)
 */

(function () {
  "use strict";

  // Guard against double-injection
  if (window.__PCE_INTERCEPTOR_ACTIVE) return;
  window.__PCE_INTERCEPTOR_ACTIVE = true;

  const TAG = "[PCE:interceptor]";
  const SSE_TIMEOUT_MS = 30000; // Force-flush SSE after 30s of silence
  const MSG_TYPE = "PCE_NETWORK_CAPTURE";

  // Wait for ai_patterns.js to be available
  function getPatterns() {
    return window.__PCE_AI_PATTERNS || null;
  }

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

  function safeJsonParse(str) {
    if (!str || typeof str !== "string") return null;
    try {
      return JSON.parse(str);
    } catch {
      return null;
    }
  }

  function simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      const ch = str.charCodeAt(i);
      hash = ((hash << 5) - hash + ch) | 0;
    }
    return hash.toString(36);
  }

  function truncate(str, maxLen) {
    if (!str) return str;
    if (str.length <= maxLen) return str;
    return str.slice(0, maxLen) + "...[truncated]";
  }

  function postCapture(data) {
    try {
      window.postMessage(
        { type: MSG_TYPE, payload: data },
        window.location.origin
      );
    } catch (e) {
      // Silently fail – never break the page
    }
  }

  function extractBodyString(body) {
    if (!body) return null;
    if (typeof body === "string") return body;
    if (body instanceof URLSearchParams) return body.toString();
    if (body instanceof FormData) return "[FormData]";
    if (body instanceof Blob) return "[Blob]";
    if (body instanceof ArrayBuffer || ArrayBuffer.isView(body)) {
      try {
        return new TextDecoder().decode(body);
      } catch {
        return "[Binary]";
      }
    }
    return null;
  }

  // -----------------------------------------------------------------------
  // 1. FETCH interceptor
  // -----------------------------------------------------------------------

  const _origFetch = window.fetch;

  window.fetch = function patchedFetch(input, init) {
    const patterns = getPatterns();
    if (!patterns) return _origFetch.apply(this, arguments);

    let url;
    try {
      url = typeof input === "string" ? input
        : input instanceof URL ? input.href
        : input instanceof Request ? input.url
        : String(input);
    } catch {
      return _origFetch.apply(this, arguments);
    }

    // Extract request body for pattern matching
    let reqBodyStr = null;
    if (init && init.body) {
      reqBodyStr = extractBodyString(init.body);
    } else if (input instanceof Request) {
      // Can't reliably read Request body without consuming it
      reqBodyStr = null;
    }

    const reqBodyObj = safeJsonParse(reqBodyStr);
    const match = patterns.isAIRequest(url, reqBodyObj);

    if (!match.isAI) {
      return _origFetch.apply(this, arguments);
    }

    const captureId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const requestTime = performance.now();

    return _origFetch.apply(this, arguments).then(async (response) => {
      try {
        const contentType = response.headers.get("content-type") || "";
        const isStreaming = patterns.isStreamingResponse(contentType);

        if (isStreaming) {
          // SSE / streaming: read the stream, accumulate chunks, then post
          return handleStreamingResponse(
            response, captureId, requestTime, match, reqBodyStr, url
          );
        } else {
          // Non-streaming: clone and read
          const clone = response.clone();
          const text = await clone.text();
          const latencyMs = performance.now() - requestTime;

          postCapture({
            capture_type: "fetch",
            capture_id: captureId,
            url: url,
            method: (init && init.method) || "GET",
            host: match.host,
            path: match.path,
            provider: match.provider,
            model: match.model,
            confidence: match.confidence,
            status_code: response.status,
            latency_ms: Math.round(latencyMs),
            request_body: truncate(reqBodyStr, 50000),
            response_body: truncate(text, 200000),
            response_content_type: contentType,
            is_streaming: false,
            timestamps: {
              request_sent_at: Date.now() - Math.round(latencyMs),
              first_token_at: Date.now(),
              stream_complete_at: Date.now(),
            },
          });
        }
      } catch (e) {
        // Never break the page
      }
      return response;
    });
  };

  // Disguise patched fetch
  try {
    Object.defineProperty(window.fetch, "toString", {
      value: function () { return "function fetch() { [native code] }"; },
      writable: false, configurable: true,
    });
    Object.defineProperty(window.fetch, "name", {
      value: "fetch", writable: false, configurable: true,
    });
  } catch { /* best effort */ }

  // -----------------------------------------------------------------------
  // SSE / Streaming response handler (v1: rough but stable)
  // -----------------------------------------------------------------------

  function handleStreamingResponse(response, captureId, requestTime, match, reqBodyStr, url) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const chunks = [];
    let timeoutHandle = null;
    let firstChunkTime = null;
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
        url: url,
        method: "POST",
        host: match.host,
        path: match.path,
        provider: match.provider,
        model: match.model,
        confidence: match.confidence,
        status_code: response.status,
        latency_ms: Math.round(latencyMs),
        request_body: truncate(reqBodyStr, 50000),
        response_body: truncate(fullText, 200000),
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
    const newStream = new ReadableStream({
      start(controller) {
        resetTimeout();

        function pump() {
          reader.read().then(({ done: readerDone, value }) => {
            if (readerDone) {
              controller.close();
              if (!done) flushAndPost();
              return;
            }

            // Record chunk
            const text = decoder.decode(value, { stream: true });
            chunks.push(text);
            if (!firstChunkTime) firstChunkTime = Date.now();
            resetTimeout();

            // Pass through to the page
            controller.enqueue(value);
            pump();
          }).catch((err) => {
            controller.error(err);
            if (!done) flushAndPost();
          });
        }

        pump();
      },
    });

    // Return a new Response with the pass-through stream
    return new Response(newStream, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  // -----------------------------------------------------------------------
  // 2. XMLHttpRequest interceptor
  // -----------------------------------------------------------------------

  const _origXHROpen = XMLHttpRequest.prototype.open;
  const _origXHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__pce_method = method;
    this.__pce_url = typeof url === "string" ? url : String(url);
    return _origXHROpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (body) {
    const patterns = getPatterns();
    if (!patterns) return _origXHRSend.call(this, body);

    const url = this.__pce_url;
    const method = this.__pce_method || "GET";
    const reqBodyStr = extractBodyString(body);
    const reqBodyObj = safeJsonParse(reqBodyStr);
    const match = patterns.isAIRequest(url || "", reqBodyObj);

    if (match.isAI) {
      const requestTime = performance.now();
      const captureId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

      this.addEventListener("load", function () {
        try {
          const latencyMs = performance.now() - requestTime;
          postCapture({
            capture_type: "xhr",
            capture_id: captureId,
            url: url,
            method: method,
            host: match.host,
            path: match.path,
            provider: match.provider,
            model: match.model,
            confidence: match.confidence,
            status_code: this.status,
            latency_ms: Math.round(latencyMs),
            request_body: truncate(reqBodyStr, 50000),
            response_body: truncate(this.responseText, 200000),
            response_content_type: this.getResponseHeader("content-type") || "",
            is_streaming: false,
            timestamps: {
              request_sent_at: Date.now() - Math.round(latencyMs),
              first_token_at: Date.now(),
              stream_complete_at: Date.now(),
            },
          });
        } catch { /* never break the page */ }
      });
    }

    return _origXHRSend.call(this, body);
  };

  // Disguise patched XHR methods
  try {
    Object.defineProperty(XMLHttpRequest.prototype.open, "toString", {
      value: function () { return "function open() { [native code] }"; },
      writable: false, configurable: true,
    });
    Object.defineProperty(XMLHttpRequest.prototype.send, "toString", {
      value: function () { return "function send() { [native code] }"; },
      writable: false, configurable: true,
    });
  } catch { /* best effort */ }

  // -----------------------------------------------------------------------
  // 3. WebSocket interceptor
  // -----------------------------------------------------------------------

  const _origWebSocket = window.WebSocket;

  window.WebSocket = function PatchedWebSocket(url, protocols) {
    const ws = protocols !== undefined
      ? new _origWebSocket(url, protocols)
      : new _origWebSocket(url);

    const patterns = getPatterns();
    if (!patterns) return ws;

    const urlMatch = patterns.matchUrl(url);

    if (urlMatch.isAI) {
      const chunks = [];
      const startTime = Date.now();

      ws.addEventListener("message", function (event) {
        try {
          const data = typeof event.data === "string"
            ? event.data
            : "[Binary WebSocket frame]";

          chunks.push(data);

          // Debounce: post accumulated WS messages every 5 seconds
          if (!ws.__pce_flush_timer) {
            ws.__pce_flush_timer = setTimeout(() => {
              const allData = chunks.splice(0, chunks.length).join("\n");
              postCapture({
                capture_type: "websocket",
                capture_id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                url: url,
                method: "WS",
                host: urlMatch.host,
                path: urlMatch.path,
                provider: urlMatch.provider,
                model: null,
                confidence: urlMatch.confidence,
                status_code: null,
                latency_ms: null,
                request_body: null,
                response_body: truncate(allData, 200000),
                response_content_type: "websocket",
                is_streaming: true,
                timestamps: {
                  request_sent_at: startTime,
                  first_token_at: startTime,
                  stream_complete_at: Date.now(),
                },
              });
              ws.__pce_flush_timer = null;
            }, 5000);
          }
        } catch { /* never break the page */ }
      });

      ws.addEventListener("close", function () {
        if (ws.__pce_flush_timer) {
          clearTimeout(ws.__pce_flush_timer);
          ws.__pce_flush_timer = null;
        }
        if (chunks.length > 0) {
          const allData = chunks.splice(0, chunks.length).join("\n");
          postCapture({
            capture_type: "websocket",
            capture_id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
            url: url,
            method: "WS",
            host: urlMatch.host,
            path: urlMatch.path,
            provider: urlMatch.provider,
            model: null,
            confidence: urlMatch.confidence,
            status_code: null,
            latency_ms: Date.now() - startTime,
            request_body: null,
            response_body: truncate(allData, 200000),
            response_content_type: "websocket",
            is_streaming: true,
            timestamps: {
              request_sent_at: startTime,
              first_token_at: startTime,
              stream_complete_at: Date.now(),
            },
          });
        }
      });
    }

    return ws;
  };

  // Copy static properties and prototype
  window.WebSocket.prototype = _origWebSocket.prototype;
  window.WebSocket.CONNECTING = _origWebSocket.CONNECTING;
  window.WebSocket.OPEN = _origWebSocket.OPEN;
  window.WebSocket.CLOSING = _origWebSocket.CLOSING;
  window.WebSocket.CLOSED = _origWebSocket.CLOSED;

  try {
    Object.defineProperty(window.WebSocket, "toString", {
      value: function () { return "function WebSocket() { [native code] }"; },
      writable: false, configurable: true,
    });
  } catch { /* best effort */ }

  // -----------------------------------------------------------------------
  // 4. EventSource interceptor
  // -----------------------------------------------------------------------

  const _origEventSource = window.EventSource;

  if (_origEventSource) {
    window.EventSource = function PatchedEventSource(url, config) {
      const es = config ? new _origEventSource(url, config) : new _origEventSource(url);

      const patterns = getPatterns();
      if (!patterns) return es;

      const urlMatch = patterns.matchUrl(url);

      if (urlMatch.isAI) {
        const chunks = [];
        const startTime = Date.now();
        let firstChunkTime = null;
        let timeoutHandle = null;

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
            capture_id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
            url: url,
            method: "GET",
            host: urlMatch.host,
            path: urlMatch.path,
            provider: urlMatch.provider,
            model: null,
            confidence: urlMatch.confidence,
            status_code: null,
            latency_ms: Date.now() - startTime,
            request_body: null,
            response_body: truncate(allData, 200000),
            response_content_type: "text/event-stream",
            is_streaming: true,
            timestamps: {
              request_sent_at: startTime,
              first_token_at: firstChunkTime || Date.now(),
              stream_complete_at: Date.now(),
            },
          });
        }

        es.addEventListener("message", function (event) {
          try {
            chunks.push(event.data);
            if (!firstChunkTime) firstChunkTime = Date.now();
            resetTimeout();
          } catch { /* never break the page */ }
        });

        es.addEventListener("error", function () {
          flushAndPost();
        });

        // Store ref for cleanup
        es.__pce_flush = flushAndPost;
      }

      return es;
    };

    window.EventSource.prototype = _origEventSource.prototype;
    window.EventSource.CONNECTING = _origEventSource.CONNECTING;
    window.EventSource.OPEN = _origEventSource.OPEN;
    window.EventSource.CLOSED = _origEventSource.CLOSED;

    try {
      Object.defineProperty(window.EventSource, "toString", {
        value: function () { return "function EventSource() { [native code] }"; },
        writable: false, configurable: true,
      });
    } catch { /* best effort */ }
  }

  console.log(TAG, "Network interceptor active (fetch + XHR + WebSocket + EventSource)");
})();
