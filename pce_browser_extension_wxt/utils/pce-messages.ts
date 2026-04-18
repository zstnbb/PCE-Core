// SPDX-License-Identifier: Apache-2.0
/**
 * Shared PCE message shapes.
 *
 * These interfaces describe the payloads exchanged between content
 * scripts and the background service worker via
 * `chrome.runtime.sendMessage`. Keeping them in one place so future TS
 * content-script ports get strict type-checking without having to
 * duplicate the shape.
 */

export type PCEMessageKind =
  | "PCE_CAPTURE"
  | "PCE_NETWORK_CAPTURE"
  | "PCE_SNIPPET"
  | "PCE_AI_PAGE_DETECTED"
  | "PCE_HEALTH_PING";

export interface PCECaptureMessage {
  type: "PCE_CAPTURE";
  payload: CapturePayload;
}

export interface PCENetworkCaptureMessage {
  type: "PCE_NETWORK_CAPTURE";
  payload: NetworkCapturePayload;
}

export interface PCESnippetMessage {
  type: "PCE_SNIPPET";
  payload: SnippetPayload;
}

export interface PCEAIPageDetectedMessage {
  type: "PCE_AI_PAGE_DETECTED";
  payload: AIPageDetection;
}

export type PCEMessage =
  | PCECaptureMessage
  | PCENetworkCaptureMessage
  | PCESnippetMessage
  | PCEAIPageDetectedMessage;

// ---------------------------------------------------------------------------
// Payload shapes — kept loose; aligned with what the legacy JS already
// sends. Tighten over time as the content scripts move to TS.
// ---------------------------------------------------------------------------

export interface CapturePayload {
  direction: "conversation" | "request" | "response";
  provider: string;
  host: string;
  path?: string;
  method?: string;
  body?: string;
  body_format?: "json" | "text" | "html" | "sse";
  model_name?: string | null;
  session_hint?: string | null;
  meta?: Record<string, unknown>;
}

export interface NetworkCapturePayload {
  direction: "network_intercept";
  provider: string;
  host: string;
  path: string;
  method: string;
  status_code?: number;
  latency_ms?: number;
  url: string;
  headers?: Record<string, string>;
  request_body?: string | null;
  response_body?: string | null;
  is_streaming?: boolean;
  meta?: Record<string, unknown>;
}

export interface SnippetPayload {
  source_url: string;
  source_domain: string;
  provider?: string;
  category?: "general" | "code" | "prompt" | "output" | "reference";
  content_text: string;
  note?: string;
  favorited?: boolean;
}

export interface AIPageDetection {
  domain: string;
  confidence: "manual" | "high" | "medium" | "low";
  score: number;
  signals: string[];
  detected_at?: number;
}

// ---------------------------------------------------------------------------
// Ingest URL helper — lets TS content scripts read it without
// hand-reproducing the string.
// ---------------------------------------------------------------------------

export const DEFAULT_PCE_INGEST_URL =
  "http://127.0.0.1:9800/api/v1/captures";

export const DEFAULT_PCE_SERVER_ORIGIN = "http://127.0.0.1:9800";
