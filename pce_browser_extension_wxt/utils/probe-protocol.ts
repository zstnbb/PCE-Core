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
  /** Tail excerpt so long prompts with tokens at the end remain searchable. */
  body_tail?: string;
}

// ---------------------------------------------------------------------------
// Verb catalog (string-typed; concrete param/result types below)
// ---------------------------------------------------------------------------

export type ProbeVerb =
  // system.*
  | "system.ping"
  | "system.version"
  | "system.reload"
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
  | "dom.click_action_main"
  | "dom.dispatch_paste_main"
  | "dom.set_file_input_main"
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

/**
 * ``system.reload`` — trigger ``chrome.runtime.reload()`` from the
 * extension's service worker. Used by the agent loop after building a
 * fresh extension bundle so the new code is picked up without manual
 * intervention. The handler responds first, THEN schedules the reload
 * on a short setTimeout so the response actually flushes; the WS will
 * then drop ~50–100 ms later as the SW shuts down. The agent should
 * call ``ProbeClient.reload_extension()`` (which sends this verb,
 * waits for the WS drop, and re-attaches to the new SW) instead of
 * invoking ``system.reload`` directly unless it knows what it's doing.
 */
export type SystemReloadParams = Record<string, never>;
export interface SystemReloadResult {
  reloading: true;
  /** Hint to the agent: SW will die roughly this many ms after this response. */
  delay_ms: number;
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
  /**
   * Execution world. Default ``"isolated"`` (content-script context).
   * Use ``"main"`` to run in the page's JS context — required when the
   * code must dispatch DOM events whose payload (e.g. ``DataTransfer``
   * carrying a ``File``) needs to be observable by the page's own
   * listeners. Synthetic events crossing isolated→main lose their
   * non-primitive payload (Chromium serializer drops the ``File``
   * reference), which is why ``upload_file_via_paste`` requires
   * ``"main"`` on ChatGPT/Claude/etc.
   */
  world?: "isolated" | "main";
}
export interface DomExecuteJsResult {
  value: unknown;
}

export interface DomClickActionMainParams {
  tab_id: number;
  action: string;
  scope?: "document" | "last_assistant" | "last_user" | "last_turn";
  root_selectors?: string[];
  selectors?: string[];
  labels?: string[];
  more_selectors?: string[];
  menu_labels?: string[];
  prefer_last?: boolean;
  /** Scan candidates without clicking the final action. */
  dry_run?: boolean;
  /** In dry-run mode, click/open the More menu only to inspect menu labels. */
  scan_menu?: boolean;
}
export interface DomClickActionCandidate {
  via: string;
  selector: string | null;
  label: string | null;
  text: string | null;
  html_excerpt: string | null;
}
export interface DomClickActionMainResult {
  ok: boolean;
  marker: string;
  via: string;
  action: string;
  matched_selector: string | null;
  matched_label: string | null;
  clicked_text: string | null;
  clicked_html_excerpt: string | null;
  candidate_count: number;
  root_count?: number;
  more_candidate_count?: number;
  candidates?: DomClickActionCandidate[];
  menu_candidates?: DomClickActionCandidate[];
  controls?: DomClickActionCandidate[];
  err: string | null;
}

/**
 * Dispatch a synthetic ``paste`` event in the page's MAIN world,
 * carrying a real ``File`` built from base64 bytes. This verb exists
 * because ``dom.execute_js`` is ISOLATED-only — see the comment block
 * above ``_injected_execute_js`` in ``probe-rpc-dom.ts`` for why
 * dynamic eval cannot be used in MAIN under strict page CSP.
 *
 * The function body is shipped pre-compiled by
 * ``chrome.scripting.executeScript({ world: "MAIN" })``, which
 * bypasses the page's CSP via Chrome's privileged scripting channel.
 */
export interface DomDispatchPasteMainParams {
  tab_id: number;
  /** CSS selector for the contenteditable / textarea to dispatch on. */
  selector: string;
  /** Base64-encoded bytes of the file to attach. */
  file_b64: string;
  /** ``File.name`` value seen by the page. */
  file_name: string;
  /** ``File.type`` (MIME) value seen by the page. */
  media_type: string;
}
export interface DomDispatchPasteMainResult {
  ok: boolean;
  /** Last reached marker — ``init`` / ``focused`` / ``file_built`` /
   * ``dt_built`` / ``event_built`` / ``paste_dispatched``. Lets the
   * caller pinpoint where the chain failed. */
  marker: string;
  /** Non-null when an exception fell through. */
  err: string | null;
  target_tag: string | null;
  target_id: string | null;
  dt_files_len: number;
  /** ``ClipboardEvent`` if the constructor succeeded; else
   * ``Event+defineProperty`` (fallback). */
  ev_class: string;
}

/**
 * Set ``input[type=file].files`` programmatically using React's native
 * setter, then dispatch input+change. MAIN-world static dispatch
 * (CSP-safe). Replaces the dynamic-eval path that ``dom.execute_js``
 * used to drive — that path silently no-op'd on MV3 due to extension-
 * page CSP forbidding ``new Function`` in ISOLATED.
 */
export interface DomSetFileInputMainParams {
  tab_id: number;
  /** Ordered list of CSS selectors; first match wins. Pass the
   * adapter's ``image_input_selectors`` or ``file_input_selectors``. */
  selectors: string[];
  file_b64: string;
  file_name: string;
  media_type: string;
}
export interface DomSetFileInputMainResult {
  ok: boolean;
  marker: string;
  err: string | null;
  /** Selector that actually matched, or null when none did. */
  matched_selector: string | null;
  /** ``react_native_setter`` on success, ``direct`` if descriptor
   * unavailable, ``fallback_direct:<err>`` on setter throw. */
  via: string;
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
  /**
   * Optional kind filter. When set, only events whose ``kind`` exactly
   * matches will be considered for the token search. Use this to
   * disambiguate the DOM-extracted ``PCE_CAPTURE`` (the canonical
   * "extension captured a chat turn" signal) from the side-channel
   * ``PCE_NETWORK_CAPTURE`` whose request body often *also* contains
   * the prompt token. Without this filter, ``wait_for_token`` resolves
   * on the first arrival, which on Claude / Grok is the network
   * capture \u2014 but those carry ``session_hint=null`` because the
   * interceptor does not know about chat-URL conventions, breaking
   * downstream session-key matching.
   */
  kind?: "PCE_CAPTURE" | "PCE_NETWORK_CAPTURE" | "PCE_SNIPPET";
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
  "system.reload":         { params: SystemReloadParams;        result: SystemReloadResult };
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
  "dom.click_action_main": { params: DomClickActionMainParams;  result: DomClickActionMainResult };
  "dom.dispatch_paste_main": { params: DomDispatchPasteMainParams; result: DomDispatchPasteMainResult };
  "dom.set_file_input_main": { params: DomSetFileInputMainParams; result: DomSetFileInputMainResult };
  "page.dump_state":       { params: PageDumpStateParams;       result: PageDumpStateResult };
  "page.screenshot":       { params: PageScreenshotParams;      result: PageScreenshotResult };
  "page.network_log":      { params: PageNetworkLogParams;      result: PageNetworkLogResult };
  "capture.wait_for_token":{ params: CaptureWaitForTokenParams; result: CaptureWaitForTokenResult };
  "capture.recent_events": { params: CaptureRecentEventsParams; result: CaptureRecentEventsResult };
  "capture.pipeline_state":{ params: CapturePipelineStateParams;result: CapturePipelineStateResult };
}

export type VerbParams<V extends ProbeVerb> = ProbeVerbMap[V]["params"];
export type VerbResult<V extends ProbeVerb> = ProbeVerbMap[V]["result"];
