// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — wire protocol shared between the extension's
 * ``probe-rpc`` module and the Python ``pce_probe`` server.
 *
 * Contract version: 1. Any change here that is NOT additive (renaming
 * fields, removing verbs, narrowing types) MUST bump
 * ``PROBE_SCHEMA_VERSION`` and round-trip through an ADR. See
 * ``Docs/docs/engineering/PCE-PROBE-API.md`` §8 for the full policy.
 */

// ---------------------------------------------------------------------------
// Schema constants
// ---------------------------------------------------------------------------

export const PROBE_SCHEMA_VERSION = 1 as const;

/** Default loopback endpoint where the agent-side server listens. */
export const PROBE_DEFAULT_WS_URL = "ws://127.0.0.1:9888";

/**
 * The endpoint can be overridden at extension build/runtime via this
 * env-shaped config. WXT exposes ``import.meta.env`` to background
 * code at build time only; at runtime we additionally consult
 * ``chrome.storage.local["pce_probe_ws_url"]`` so a power user can
 * point the extension at a different probe server without rebuilding.
 */
export const PROBE_STORAGE_KEY_WS_URL = "pce_probe_ws_url" as const;

/**
 * Storage key used to gate the probe entirely. When set to ``false``
 * the extension's probe-rpc module never opens a WebSocket. Default
 * is ``true`` because the connection is harmless when nothing is
 * listening (extension just retries with backoff).
 */
export const PROBE_STORAGE_KEY_ENABLED = "pce_probe_enabled" as const;

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

export interface ProbeRequest<P = unknown> {
  v: typeof PROBE_SCHEMA_VERSION;
  id: string;
  verb: ProbeVerb;
  params: P;
  /** Default 30 000 ms when omitted. */
  timeout_ms?: number;
}

export interface ProbeSuccessResponse<R = unknown> {
  v: typeof PROBE_SCHEMA_VERSION;
  id: string;
  ok: true;
  result: R;
  /** Wall-clock duration spent inside the extension's verb handler. */
  elapsed_ms: number;
}

export interface ProbeErrorResponse {
  v: typeof PROBE_SCHEMA_VERSION;
  id: string;
  ok: false;
  error: ProbeError;
  elapsed_ms: number;
}

export type ProbeResponse<R = unknown> =
  | ProbeSuccessResponse<R>
  | ProbeErrorResponse;

/** Hello frame, sent by the extension after the WS opens. */
export interface ProbeHelloFrame {
  v: typeof PROBE_SCHEMA_VERSION;
  hello: {
    extension_version: string;
    schema_version: number;
    capabilities: ProbeVerb[];
    user_agent?: string;
  };
}

/** Hello-ack frame, sent by the server in response. */
export interface ProbeHelloAckFrame {
  v: typeof PROBE_SCHEMA_VERSION;
  ok: true;
  server_version: string;
  /** All schema versions the server understands; useful for graceful upgrades. */
  supported_schema_versions: number[];
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export type ProbeErrorCode =
  | "schema_mismatch"
  | "unknown_verb"
  | "params_invalid"
  | "tab_not_found"
  | "selector_not_found"
  | "selector_ambiguous"
  | "navigation_failed"
  | "script_threw"
  | "timeout"
  | "capture_not_seen"
  | "host_blocked"
  | "extension_internal";

export interface ProbeErrorContext {
  tab_id?: number;
  url?: string;
  title?: string;
  /** Truncated outerHTML of the current document, for selector-not-found etc. */
  dom_excerpt?: string;
  /** Last ~20 capture-pipeline events, for capture_not_seen etc. */
  last_capture_events?: CaptureEventSummary[];
  /** Optional base64 PNG screenshot, only when explicitly requested. */
  screenshot_b64?: string | null;
  /** Inner exception stack, for script_threw etc. */
  stack?: string;
  /** Free-form extra fields any verb may add. */
  [k: string]: unknown;
}

export interface ProbeError {
  code: ProbeErrorCode;
  message: string;
  context?: ProbeErrorContext;
  /** One-line hint for the calling agent on what to investigate. */
  agent_hint?: string;
}

/** Snapshot of one capture pipeline event, used in error contexts. */
export interface CaptureEventSummary {
  ts: number;
  /** ``PCE_CAPTURE`` / ``PCE_NETWORK_CAPTURE`` / ``PCE_SNIPPET``. */
  kind: string;
  provider?: string;
  host?: string;
  session_hint?: string | null;
  /** Truncated body fingerprint, for matching. */
  fingerprint?: string;
}

// ---------------------------------------------------------------------------
// Verb catalog (string-typed; concrete param/result types below)
// ---------------------------------------------------------------------------

export type ProbeVerb =
  // system.*
  | "system.ping"
  | "system.version"
  // tab.*
  | "tab.list"
  | "tab.open"
  | "tab.activate"
  | "tab.close"
  | "tab.navigate"
  | "tab.wait_for_load"
  | "tab.find_by_url"
  // dom.*
  | "dom.query"
  | "dom.wait_for_selector"
  | "dom.click"
  | "dom.type"
  | "dom.press_key"
  | "dom.scroll_to"
  | "dom.execute_js"
  // page.*
  | "page.dump_state"
  | "page.screenshot"
  | "page.network_log"
  // capture.*
  | "capture.wait_for_token"
  | "capture.recent_events"
  | "capture.pipeline_state";

// ---------------------------------------------------------------------------
// Verb param / result schemas
// ---------------------------------------------------------------------------

// system.* ------------------------------------------------------------------

export type SystemPingParams = Record<string, never>;
export interface SystemPingResult {
  pong: true;
  extension_version: string;
}

export type SystemVersionParams = Record<string, never>;
export interface SystemVersionResult {
  schema_version: number;
  extension_version: string;
  capabilities: ProbeVerb[];
}

// tab.* ---------------------------------------------------------------------

export interface TabSummary {
  id: number;
  url: string;
  title: string;
  active: boolean;
}

export type TabListParams = Record<string, never>;
export interface TabListResult {
  tabs: TabSummary[];
}

export interface TabOpenParams {
  url: string;
  active?: boolean;
}
export interface TabOpenResult {
  tab_id: number;
  url: string;
}

export interface TabActivateParams {
  tab_id: number;
}
export interface TabActivateResult {
  ok: true;
}

export interface TabCloseParams {
  tab_id: number;
}
export interface TabCloseResult {
  ok: true;
}

export interface TabNavigateParams {
  tab_id: number;
  url: string;
}
export interface TabNavigateResult {
  tab_id: number;
  url: string;
}

export interface TabWaitForLoadParams {
  tab_id: number;
  /** Default 15 000 ms. */
  timeout_ms?: number;
}
export interface TabWaitForLoadResult {
  tab_id: number;
  url: string;
  title: string;
}

export interface TabFindByUrlParams {
  /** JS regex source (without slashes). */
  url_pattern: string;
}
export interface TabFindByUrlResult {
  tabs: TabSummary[];
}

// dom.* ---------------------------------------------------------------------

export interface DomMatch {
  outer_html_excerpt: string;
  text_excerpt: string;
  visible: boolean;
}

export interface DomQueryParams {
  tab_id: number;
  selector: string;
  all?: boolean;
  require_unique?: boolean;
}
export interface DomQueryResult {
  matches: DomMatch[];
}

export interface DomWaitForSelectorParams {
  tab_id: number;
  selector: string;
  /** Default 15 000 ms. */
  timeout_ms?: number;
  /** When true, also waits for the matched element to be visible (default). */
  visible?: boolean;
}
export interface DomWaitForSelectorResult {
  matched: number;
}

export interface DomClickParams {
  tab_id: number;
  selector: string;
  /** Default true. */
  scroll_into_view?: boolean;
}
export interface DomClickResult {
  ok: true;
}

export interface DomTypeParams {
  tab_id: number;
  selector: string;
  text: string;
  /** Default true \u2014 clear element value before typing. */
  clear?: boolean;
  /** When true, dispatches Enter at the end. Default false. */
  submit?: boolean;
}
export interface DomTypeResult {
  ok: true;
}

export interface DomPressKeyParams {
  tab_id: number;
  selector?: string;
  key: string;
  modifiers?: Array<"Shift" | "Control" | "Alt" | "Meta">;
}
export interface DomPressKeyResult {
  ok: true;
}

export interface DomScrollToParams {
  tab_id: number;
  selector?: string;
  y?: number;
}
export interface DomScrollToResult {
  ok: true;
}

export interface DomExecuteJsParams {
  tab_id: number;
  /** Function body. Wrapped in `() => { ... }` then passed to executeScript. */
  code: string;
  /** Serializable args forwarded to the function. */
  args?: unknown[];
}
export interface DomExecuteJsResult {
  value: unknown;
}

// page.* --------------------------------------------------------------------

export interface PageDumpStateParams {
  tab_id: number;
  /** Truncates the dom_excerpt to this many chars. Default 8 000. */
  dom_max_chars?: number;
}
export interface PageDumpStateResult {
  url: string;
  title: string;
  dom_excerpt: string;
  ready_state: DocumentReadyState;
  location_search: string;
  cookies_count: number;
}

export interface PageScreenshotParams {
  tab_id: number;
  format?: "png" | "jpeg";
}
export interface PageScreenshotResult {
  image_b64: string;
  width: number;
  height: number;
}

export interface PageNetworkLogParams {
  tab_id: number;
  last_n?: number;
}
export interface PageNetworkLogResult {
  requests: Array<{
    url: string;
    method: string;
    status: number | null;
    /** Unix epoch ms. */
    t: number;
  }>;
  /** True if the interceptor on this tab is active and feeding the log. */
  interceptor_active: boolean;
}

// capture.* -----------------------------------------------------------------

export interface CaptureWaitForTokenParams {
  /** Substring agents look for in any captured message body. */
  token: string;
  /** Default 60 000 ms. */
  timeout_ms?: number;
  /** Optional provider filter (``openai`` etc.). */
  provider?: string;
}
export interface CaptureWaitForTokenResult {
  matched: true;
  /** Capture-event timestamp (ms). */
  ts: number;
  provider?: string;
  host?: string;
  session_hint?: string | null;
  /** Truncated body fingerprint, identical to the one in error contexts. */
  fingerprint?: string;
}

export interface CaptureRecentEventsParams {
  last_n?: number;
  provider?: string;
}
export interface CaptureRecentEventsResult {
  events: CaptureEventSummary[];
}

export type CapturePipelineStateParams = Record<string, never>;
export interface CapturePipelineStateResult {
  enabled: boolean;
  capture_count: number;
  last_error: string | null;
  server_online: boolean;
  queued_captures: number;
}

// ---------------------------------------------------------------------------
// Verb -> params/result type map (for type-safe dispatcher impl)
// ---------------------------------------------------------------------------

export interface ProbeVerbMap {
  "system.ping":           { params: SystemPingParams;          result: SystemPingResult };
  "system.version":        { params: SystemVersionParams;       result: SystemVersionResult };
  "tab.list":              { params: TabListParams;             result: TabListResult };
  "tab.open":              { params: TabOpenParams;             result: TabOpenResult };
  "tab.activate":          { params: TabActivateParams;         result: TabActivateResult };
  "tab.close":             { params: TabCloseParams;            result: TabCloseResult };
  "tab.navigate":          { params: TabNavigateParams;         result: TabNavigateResult };
  "tab.wait_for_load":     { params: TabWaitForLoadParams;      result: TabWaitForLoadResult };
  "tab.find_by_url":       { params: TabFindByUrlParams;        result: TabFindByUrlResult };
  "dom.query":             { params: DomQueryParams;            result: DomQueryResult };
  "dom.wait_for_selector": { params: DomWaitForSelectorParams;  result: DomWaitForSelectorResult };
  "dom.click":             { params: DomClickParams;            result: DomClickResult };
  "dom.type":              { params: DomTypeParams;             result: DomTypeResult };
  "dom.press_key":         { params: DomPressKeyParams;         result: DomPressKeyResult };
  "dom.scroll_to":         { params: DomScrollToParams;         result: DomScrollToResult };
  "dom.execute_js":        { params: DomExecuteJsParams;        result: DomExecuteJsResult };
  "page.dump_state":       { params: PageDumpStateParams;       result: PageDumpStateResult };
  "page.screenshot":       { params: PageScreenshotParams;      result: PageScreenshotResult };
  "page.network_log":      { params: PageNetworkLogParams;      result: PageNetworkLogResult };
  "capture.wait_for_token":{ params: CaptureWaitForTokenParams; result: CaptureWaitForTokenResult };
  "capture.recent_events": { params: CaptureRecentEventsParams; result: CaptureRecentEventsResult };
  "capture.pipeline_state":{ params: CapturePipelineStateParams;result: CapturePipelineStateResult };
}

export type VerbParams<V extends ProbeVerb> = ProbeVerbMap[V]["params"];
export type VerbResult<V extends ProbeVerb> = ProbeVerbMap[V]["result"];
