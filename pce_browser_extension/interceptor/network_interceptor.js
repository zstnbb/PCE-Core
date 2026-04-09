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
  const SSE_TIMEOUT_MS = 120000; // Force-flush SSE after 120s of silence (long AI responses)
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
  // Path-based noise filter – skip non-AI traffic on AI pages
  // -----------------------------------------------------------------------

  const _NOISE_PATH_PATTERNS = [
    /\/sentinel\//i,
    /\/telemetry/i,
    /\/analytics/i,
    /\/statsc\//i,
    /\/rgstr$/i,
    /\/ping$/i,
    /\/heartbeat/i,
    /\/health$/i,
    /\/log$/i,
    /\/ces\//i,              // ChatGPT event streaming (analytics)
    /\/lat\//i,              // ChatGPT latency tracking
    /\/aip\/connectors/i,    // ChatGPT connector checks
    /\/register_websocket/i,
    /\/_next\//i,            // Next.js assets
    /\/assets\//i,
    /\/static\//i,
    /\.(js|css|png|jpg|svg|woff|ico)(\?|$)/i,
    /\/realtime\/status/i,
    /\/connectors\/check/i,
    /\/sentry/i,
  ];

  function _isNoisePath(path) {
    if (!path) return false;
    for (const re of _NOISE_PATH_PATTERNS) {
      if (re.test(path)) return true;
    }
    return false;
  }

  // -----------------------------------------------------------------------
  // Provider guess from page context (for aggressive mode)
  // -----------------------------------------------------------------------

  function _guessProviderFromPage() {
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
    // Fallback: extract from page title or hostname
    const title = (document.title || "").toLowerCase();
    const host = location.hostname.toLowerCase();
    const hints = [
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
    } else if (input instanceof Request && input.method === "POST") {
      // Try to clone and read Request body (common in Svelte/React apps)
      try {
        const cloned = input.clone();
        // Override input with the original; we read the clone
        reqBodyStr = null; // will be populated async below if needed
        // Synchronous path: can't await here, so we do best-effort
        // by extracting from _body if accessible
        if (cloned._bodyInit) {
          reqBodyStr = extractBodyString(cloned._bodyInit);
        }
      } catch {
        reqBodyStr = null;
      }
    }

    const reqBodyObj = safeJsonParse(reqBodyStr);
    let match = patterns.isAIRequest(url, reqBodyObj);

    // ── Aggressive mode: on confirmed AI pages, also capture POST
    // requests with AI-like JSON bodies, even if URL is unknown ──
    const _isConfirmedAIPage = window.__PCE_AI_PAGE_CONFIRMED ||
      document.documentElement.getAttribute("data-pce-ai-confirmed") === "1";
    if (!match.isAI && _isConfirmedAIPage) {
      // Skip known noise paths (analytics, heartbeat, telemetry, etc.)
      if (match.path && _isNoisePath(match.path)) {
        return _origFetch.apply(this, arguments);
      }
      // Also check URL directly if match.path isn't set yet
      try {
        const parsed = new URL(url);
        if (_isNoisePath(parsed.pathname)) return _origFetch.apply(this, arguments);
      } catch {}

      const method = (init && init.method) || (input instanceof Request ? input.method : "GET");
      if (method === "POST" && reqBodyObj) {
        // Body has AI-signature fields → capture
        const bodyCheck = patterns.matchRequestBody(reqBodyObj);
        if (bodyCheck.isAI) {
          match = {
            isAI: true,
            confidence: "aggressive",
            provider: _guessProviderFromPage() || "unknown",
            model: bodyCheck.model,
            host: null,
            path: null,
          };
          try {
            const parsed = new URL(url);
            match.host = parsed.hostname;
            match.path = parsed.pathname;
          } catch {}
        }
      }
      // Also capture any SSE/streaming POST on confirmed AI pages
      if (!match.isAI && method === "POST") {
        // We'll check response content-type after fetch returns
        // Mark as "pending" — will be decided in response phase
        match = { isAI: false, _pendingStreamCheck: true, host: null, path: null };
        try {
          const parsed = new URL(url);
          match.host = parsed.hostname;
          match.path = parsed.pathname;
        } catch {}
      }
    }

    if (!match.isAI && !match._pendingStreamCheck) {
      return _origFetch.apply(this, arguments);
    }

    const captureId = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const requestTime = performance.now();

    return _origFetch.apply(this, arguments).then(async (response) => {
      try {
        const contentType = response.headers.get("content-type") || "";
        const isStreaming = patterns.isStreamingResponse(contentType);

        // ── Pending stream check: only capture if response is actually SSE/JSON ──
        if (match._pendingStreamCheck) {
          const isJson = contentType.includes("application/json");
          if (!isStreaming && !isJson) {
            // Not AI traffic — bail out silently
            return response;
          }
          // Promote to a real match
          match = {
            isAI: true,
            confidence: "aggressive-stream",
            provider: _guessProviderFromPage() || "unknown",
            model: null,
            host: match.host,
            path: match.path,
          };
        }

        if (isStreaming) {
          // SSE / streaming: read the stream, accumulate chunks, then post
          return handleStreamingResponse(
            response, captureId, requestTime, match, reqBodyStr, url
          );
        } else {
          // Non-streaming: clone and read
          const clone = response.clone();
          let text;
          try {
            text = await clone.text();
          } catch {
            text = "[unreadable response body]";
          }
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
    }).catch((fetchErr) => {
      // Network error – log the failed attempt so self-check can detect silent failures
      try {
        postCapture({
          capture_type: "fetch_error",
          capture_id: captureId,
          url: url,
          method: (init && init.method) || "GET",
          host: match.host,
          path: match.path,
          provider: match.provider,
          model: match.model,
          confidence: match.confidence,
          status_code: 0,
          latency_ms: Math.round(performance.now() - requestTime),
          request_body: truncate(reqBodyStr, 50000),
          response_body: JSON.stringify({ error: fetchErr.message }),
          response_content_type: "",
          is_streaming: false,
          timestamps: { request_sent_at: Date.now() },
        });
      } catch { /* never break the page */ }
      throw fetchErr; // Re-throw so the page sees the original error
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

      this.addEventListener("error", function () {
        try {
          postCapture({
            capture_type: "xhr_error",
            capture_id: captureId,
            url: url,
            method: method,
            host: match.host,
            path: match.path,
            provider: match.provider,
            model: match.model,
            confidence: match.confidence,
            status_code: 0,
            latency_ms: Math.round(performance.now() - requestTime),
            request_body: truncate(reqBodyStr, 50000),
            response_body: JSON.stringify({ error: "XHR network error" }),
            response_content_type: "",
            is_streaming: false,
            timestamps: { request_sent_at: Date.now() },
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
  // 3. WebSocket interceptor (enhanced for ChatGPT/Claude WS streams)
  // -----------------------------------------------------------------------

  const _origWebSocket = window.WebSocket;

  // ChatGPT WS messages use a custom protocol. Extract assistant content
  // from various message formats encountered in the wild, including Deep
  // Research reports and Canvas artifacts.
  function _extractWsConversationContent(messages) {
    const contentParts = [];
    let model = null;
    let lastSeenText = "";  // De-dup: ChatGPT sends cumulative parts
    const toolCalls = [];   // Collect tool invocations (browsing, code, DALL-E)

    for (const raw of messages) {
      const obj = safeJsonParse(raw);
      if (!obj) {
        // Could be SSE-formatted data over WS
        if (raw.startsWith("data: ")) {
          const inner = safeJsonParse(raw.slice(6));
          if (inner) {
            const delta = inner.choices?.[0]?.delta;
            if (delta?.content) contentParts.push(delta.content);
            if (inner.model) model = inner.model;
          }
        }
        continue;
      }

      // ChatGPT "v1" streaming format: {"type":"...", "body":"..."}
      if (obj.body) {
        const body = safeJsonParse(obj.body) || obj.body;
        if (typeof body === "object") {
          // Look for conversation turn data
          const msg = body.message;
          if (msg?.content?.parts) {
            const textParts = [];
            for (const p of msg.content.parts) {
              if (typeof p === "string") {
                textParts.push(p);
              } else if (typeof p === "object" && p !== null) {
                // DALL-E image result, code output, etc.
                if (p.asset_pointer || p.url) {
                  toolCalls.push({
                    type: "tool_call",
                    name: p.content_type || "image_generation",
                    result: truncate(JSON.stringify(p), 2000),
                  });
                }
              }
            }
            const text = textParts.join("");
            // ChatGPT sends cumulative parts — keep only the newest/longest
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
          // Tool invocations in metadata (browsing, code interpreter, DALL-E)
          if (msg?.recipient && msg.recipient !== "all" && msg.recipient !== "browser") {
            toolCalls.push({
              type: "tool_call",
              name: msg.recipient,
              arguments: truncate(JSON.stringify(msg?.content?.parts || []), 2000),
            });
          }
          if (msg?.metadata?.model_slug) model = msg.metadata.model_slug;
          if (body.model) model = body.model;
        }
      }

      // Standard OpenAI streaming format
      if (obj.choices) {
        const delta = obj.choices[0]?.delta;
        if (delta?.content) contentParts.push(delta.content);
        if (obj.model) model = obj.model;
      }

      // Direct content field
      if (obj.content && typeof obj.content === "string" && obj.role === "assistant") {
        contentParts.push(obj.content);
      }

      // Deep Research: top-level result/text fields
      if (obj.result && typeof obj.result === "string" && obj.result.length > 50) {
        if (obj.result.length > lastSeenText.length) lastSeenText = obj.result;
      }
      if (obj.text && typeof obj.text === "string" && obj.text.length > 50 && obj.type !== "ping") {
        if (obj.text.length > lastSeenText.length) lastSeenText = obj.text;
      }
    }

    // Merge: if cumulative parts from ChatGPT format are longer, use those
    const joined = contentParts.join("");
    const finalContent = lastSeenText.length > joined.length ? lastSeenText : joined;

    // De-duplicate tool_calls by name+result
    const seenTools = new Set();
    const uniqueToolCalls = toolCalls.filter(tc => {
      const key = (tc.name || "") + (tc.result || tc.arguments || "");
      if (seenTools.has(key)) return false;
      seenTools.add(key);
      return true;
    });

    return { content: finalContent, model, tool_calls: uniqueToolCalls };
  }

  window.WebSocket = function PatchedWebSocket(url, protocols) {
    const ws = protocols !== undefined
      ? new _origWebSocket(url, protocols)
      : new _origWebSocket(url);

    const patterns = getPatterns();
    if (!patterns) return ws;

    // For WebSocket, use permissive domain-level matching.
    // WS connections on AI domains are almost always conversation streams,
    // unlike fetch which includes tons of telemetry/heartbeat noise.
    let wsHost, wsPath, wsProvider;
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

    const receivedChunks = [];
    const sentChunks = [];
    let startTime = Date.now();
    let firstChunkTime = null;
    let flushTimer = null;
    let flushed = false;
    const FLUSH_DELAY_MS = 8000; // Wait for stream to settle

    function resetFlushTimer() {
      if (flushTimer) clearTimeout(flushTimer);
      flushTimer = setTimeout(flushAndPost, FLUSH_DELAY_MS);
    }

    function flushAndPost() {
      if (flushed) return;
      if (flushTimer) clearTimeout(flushTimer);

      // Content-based filtering: only post if we found meaningful content
      const extracted = _extractWsConversationContent(receivedChunks);
      if (!extracted.content || extracted.content.length < 20) {
        // No meaningful AI content — might be ping/pong or control frames
        receivedChunks.length = 0;
        sentChunks.length = 0;
        flushed = false; // Allow re-capture for next turn
        return;
      }

      flushed = true;

      // Build a synthetic response body matching the format the normalizer expects
      const assistantMsg = { role: "assistant", content: extracted.content };
      if (extracted.tool_calls && extracted.tool_calls.length > 0) {
        assistantMsg.tool_calls = extracted.tool_calls;
      }
      const respBody = JSON.stringify({
        model: extracted.model,
        choices: [{ message: assistantMsg }],
      });

      // Try to extract the user's request from sent messages
      let reqBody = null;
      for (const raw of sentChunks) {
        const obj = safeJsonParse(raw);
        if (obj?.messages || obj?.message || obj?.prompt) {
          reqBody = raw;
          break;
        }
        // ChatGPT "v1" format: request inside body field
        if (obj?.body) {
          const inner = safeJsonParse(obj.body);
          if (inner?.messages) {
            reqBody = obj.body;
            break;
          }
        }
      }

      postCapture({
        capture_type: "websocket",
        capture_id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        url: url,
        method: "WS",
        host: wsHost,
        path: wsPath,
        provider: wsProvider || "unknown",
        model: extracted.model,
        confidence: "high",
        status_code: null,
        latency_ms: Date.now() - startTime,
        request_body: truncate(reqBody, 50000),
        response_body: truncate(respBody, 200000),
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

    // Intercept incoming messages
    ws.addEventListener("message", function (event) {
      try {
        let data;
        if (typeof event.data === "string") {
          data = event.data;
        } else if (event.data instanceof ArrayBuffer) {
          try { data = new TextDecoder().decode(event.data); } catch { data = "[Binary]"; }
        } else if (event.data instanceof Blob) {
          data = "[Blob]"; // Cannot synchronously read Blob
        } else {
          data = "[Binary]";
        }
        // Skip heartbeat / ping / empty messages — don't reset flush timer for noise
        if (typeof data === "string" && (data.length < 5 || data === '{}' || data === '""')) {
          return;
        }
        receivedChunks.push(data);
        if (!firstChunkTime) firstChunkTime = Date.now();
        resetFlushTimer();
      } catch { /* never break the page */ }
    });

    // Intercept outgoing messages (monkey-patch ws.send)
    const _origSend = ws.send.bind(ws);
    ws.send = function (data) {
      try {
        if (typeof data === "string") {
          // Detect conversation boundary: if user sends a new message and we
          // have accumulated response data, flush the previous turn first.
          const obj = safeJsonParse(data);
          const isUserMsg = obj && (obj.messages || obj.message || obj.prompt ||
            (obj.body && (safeJsonParse(obj.body)?.messages || safeJsonParse(obj.body)?.action === 'next')));
          if (isUserMsg && receivedChunks.length > 0 && !flushed) {
            flushAndPost();
          }
          sentChunks.push(data);
        }
      } catch { /* never break */ }
      return _origSend(data);
    };

    ws.addEventListener("close", function () {
      if (flushTimer) clearTimeout(flushTimer);
      if (!flushed && receivedChunks.length > 0) {
        flushAndPost();
      }
    });

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

        // Override close() to flush before closing
        const _origClose = es.close.bind(es);
        es.close = function () {
          flushAndPost();
          return _origClose();
        };
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
