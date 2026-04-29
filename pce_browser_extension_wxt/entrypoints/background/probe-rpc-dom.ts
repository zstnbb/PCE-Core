// SPDX-License-Identifier: Apache-2.0
/**
 * PCE Probe — ``dom.*`` verb handlers.
 *
 * Every verb dispatches to a function injected into the target tab via
 * ``chrome.scripting.executeScript`` (world: ISOLATED). The injected
 * function MUST be self-contained — it cannot reference closures from
 * this file because Chrome serializes only the function source.
 *
 * Selector failures throw ``selector_not_found`` (or
 * ``selector_ambiguous`` when ``require_unique`` is set). We always
 * include a small DOM excerpt in the error context so the agent can
 * inspect what the page actually looks like.
 */

import type {
  DomClickParams,
  DomClickResult,
  DomExecuteJsParams,
  DomExecuteJsResult,
  DomMatch,
  DomPressKeyParams,
  DomPressKeyResult,
  DomQueryParams,
  DomQueryResult,
  DomScrollToParams,
  DomScrollToResult,
  DomTypeParams,
  DomTypeResult,
  DomWaitForSelectorParams,
  DomWaitForSelectorResult,
} from "../../utils/probe-protocol";
import { ProbeException, type VerbHandler } from "./probe-rpc-shared";

const DOM_EXCERPT_MAX = 4_000;

// ---------------------------------------------------------------------------
// Common helpers
// ---------------------------------------------------------------------------

function requireString(
  obj: Record<string, unknown>,
  field: string,
): string {
  const v = obj[field];
  if (typeof v !== "string" || v.length === 0) {
    throw new ProbeException("params_invalid", `field '${field}' must be a non-empty string`);
  }
  return v;
}

function requireNumber(
  obj: Record<string, unknown>,
  field: string,
): number {
  const v = obj[field];
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new ProbeException("params_invalid", `field '${field}' must be a finite number`);
  }
  return v;
}

async function execInTab<TArgs extends unknown[], TRet>(
  tabId: number,
  func: (...args: TArgs) => TRet,
  args: TArgs,
): Promise<TRet> {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "ISOLATED",
    func: func as (...a: unknown[]) => unknown,
    args: args as unknown[],
  });
  if (!results || results.length === 0) {
    throw new ProbeException(
      "script_threw",
      "executeScript returned no results",
      { tab_id: tabId },
    );
  }
  // The first frame's result is what we want; secondary frames are
  // ignored. If the script threw, the result entry has ``error`` and
  // ``result`` is undefined. ``InjectionResult`` does not type ``error``
  // in @types/chrome, but Chrome populates it at runtime; cast through
  // ``unknown`` to access it without TS complaints.
  const r = results[0] as unknown as { error?: { message?: string }; result: TRet };
  if (r.error) {
    throw new ProbeException(
      "script_threw",
      r.error.message ?? "unknown",
      { tab_id: tabId },
    );
  }
  return r.result;
}

async function gatherErrorContext(tabId: number): Promise<{
  tab_id: number;
  url?: string;
  title?: string;
  dom_excerpt?: string;
}> {
  try {
    const tab = await chrome.tabs.get(tabId);
    let domExcerpt: string | undefined;
    try {
      domExcerpt = await execInTab(
        tabId,
        (max: number) => {
          const html = document.documentElement?.outerHTML ?? "";
          return html.length > max ? html.slice(0, max) + "..." : html;
        },
        [DOM_EXCERPT_MAX],
      );
    } catch {
      domExcerpt = undefined;
    }
    return {
      tab_id: tabId,
      url: tab.url,
      title: tab.title,
      dom_excerpt: domExcerpt,
    };
  } catch {
    return { tab_id: tabId };
  }
}

// ---------------------------------------------------------------------------
// Injected helpers (self-contained — copied into the page)
// ---------------------------------------------------------------------------

function _injected_query(
  selector: string,
  all: boolean,
  domExcerptMax: number,
): {
  matches: DomMatch[];
  dom_excerpt: string;
} {
  const nodes = all
    ? Array.from(document.querySelectorAll(selector))
    : Array.from(document.querySelectorAll(selector)).slice(0, 1);
  const matches: DomMatch[] = nodes.map((el) => {
    const html = (el as Element).outerHTML ?? "";
    const text = (el as HTMLElement).innerText ?? (el as Element).textContent ?? "";
    const rect = (el as Element).getBoundingClientRect?.() ?? null;
    const visible = rect
      ? rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
      : false;
    return {
      outer_html_excerpt: html.length > 600 ? html.slice(0, 600) + "..." : html,
      text_excerpt: (text || "").slice(0, 500),
      visible,
    };
  });
  const fullHtml = document.documentElement?.outerHTML ?? "";
  return {
    matches,
    dom_excerpt:
      fullHtml.length > domExcerptMax
        ? fullHtml.slice(0, domExcerptMax) + "..."
        : fullHtml,
  };
}

function _injected_wait_for_selector(
  selector: string,
  visibleRequired: boolean,
  budgetMs: number,
): { matched: number; reason?: string } {
  // Synchronous polling within the page's main loop. We use
  // ``Date.now()`` against a budget; the dispatcher applies the outer
  // timeout via ``signal.abort()``, so this loop is bounded twice.
  const start = Date.now();
  while (Date.now() - start < budgetMs) {
    const el = document.querySelector(selector);
    if (el) {
      if (!visibleRequired) return { matched: 1 };
      const rect = el.getBoundingClientRect?.();
      if (
        rect &&
        rect.width > 0 &&
        rect.height > 0 &&
        rect.bottom > 0 &&
        rect.right > 0
      ) {
        return { matched: 1 };
      }
    }
    // crude busy wait — small sleep via synchronous polling
    const tick = Date.now();
    while (Date.now() - tick < 80) {
      /* spin */
    }
  }
  return { matched: 0, reason: "timeout" };
}

function _injected_click(
  selector: string,
  scrollIntoView: boolean,
): { ok: boolean; reason?: string } {
  const el = document.querySelector(selector) as HTMLElement | null;
  if (!el) return { ok: false, reason: "selector_not_found" };
  try {
    if (scrollIntoView && el.scrollIntoView) {
      el.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
    }
    el.click();
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: (err as Error).message };
  }
}

function _injected_type(
  selector: string,
  text: string,
  clear: boolean,
  submit: boolean,
): { ok: boolean; reason?: string } {
  const el = document.querySelector(selector) as
    | (HTMLInputElement | HTMLTextAreaElement | HTMLElement)
    | null;
  if (!el) return { ok: false, reason: "selector_not_found" };

  try {
    (el as HTMLElement).focus();
  } catch {
    /* some elements aren't focusable; try anyway */
  }

  const proto =
    el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : el instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : null;

  if (proto) {
    // React-controlled inputs: bypass the wrapper setter.
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (clear && setter) setter.call(el, "");
    if (clear)
      (el as HTMLInputElement | HTMLTextAreaElement).dispatchEvent(
        new Event("input", { bubbles: true }),
      );
    const next =
      ((el as HTMLInputElement | HTMLTextAreaElement).value ?? "") + text;
    if (setter) setter.call(el, next);
    (el as HTMLInputElement | HTMLTextAreaElement).dispatchEvent(
      new Event("input", { bubbles: true }),
    );
    (el as HTMLInputElement | HTMLTextAreaElement).dispatchEvent(
      new Event("change", { bubbles: true }),
    );
  } else {
    // contenteditable / div-based inputs (ChatGPT's ProseMirror,
    // Gemini's Quill .ql-editor, Claude's ProseMirror, Grok's TipTap,
    // etc.). The previous implementation cleared via
    // ``textContent = ""`` THEN focused THEN ``execCommand insertText``
    // \u2014 but clearing textContent destroys the active selection,
    // so insertText runs without a cursor anchor and silently inserts
    // nothing. On Gemini specifically, Quill's MutationObserver also
    // sees the textContent wipe and re-asserts its empty model,
    // leaving the editor with just a stray "\n" (Quill's blank-line
    // sentinel).
    const ce = el as HTMLElement;
    if (ce.isContentEditable || ce.getAttribute("contenteditable") === "true") {
      // 1. Focus FIRST so the selection lives inside this element.
      try {
        ce.focus();
      } catch {
        /* ignore */
      }
      // 2. Build a selection that covers the existing content (if
      //    clearing) or collapses to the end (if appending). The
      //    Selection API is what real keyboard input mutates, so
      //    every editor we care about (Quill, ProseMirror, TipTap,
      //    Lexical, Slate) reads from it.
      const sel = window.getSelection();
      if (sel) {
        const range = document.createRange();
        range.selectNodeContents(ce);
        if (!clear) {
          range.collapse(false); // cursor at end \u2014 append, don't replace
        }
        sel.removeAllRanges();
        sel.addRange(range);
      }
      // 3. Dispatch ``beforeinput`` so editors listening for it (Quill,
      //    ProseMirror) see the same signal a real keystroke would
      //    produce. We don't honor ``preventDefault`` because our
      //    fallbacks below cover the case where the editor cancels
      //    the synthetic event.
      let beforeAccepted = true;
      try {
        const beforeEvt = new InputEvent("beforeinput", {
          bubbles: true,
          cancelable: true,
          inputType: clear ? "insertReplacementText" : "insertText",
          data: text,
        });
        beforeAccepted = ce.dispatchEvent(beforeEvt);
      } catch {
        beforeAccepted = true;
      }
      // 4. Insert the text. ``execCommand("insertText")`` respects
      //    the active selection \u2014 selection covers all content
      //    when clearing, so it replaces; collapsed at end when
      //    appending, so it inserts. Returns false if the browser
      //    rejected the command (e.g. the editor canceled the
      //    beforeinput); we fall through to the manual path.
      let inserted = false;
      if (beforeAccepted) {
        try {
          inserted = document.execCommand("insertText", false, text);
        } catch {
          inserted = false;
        }
      }
      if (!inserted) {
        // Manual fallback: write into the DOM ourselves and dispatch
        // a synthetic ``input`` event matching what insertText would
        // emit. Editors that gate on textContent mutations alone
        // (rare \u2014 most modern ones use beforeinput) still pick
        // this up.
        if (clear) {
          ce.textContent = text;
        } else {
          ce.textContent = (ce.textContent ?? "") + text;
        }
      }
      // 5. Always dispatch a final ``input`` event with
      //    ``inputType: insertText`` so listeners that wait for the
      //    canonical post-input signal (Gemini's send-button
      //    enable-on-input handler, AI Studio's debounced model-
      //    state push) fire regardless of which insertion path we
      //    took.
      try {
        ce.dispatchEvent(
          new InputEvent("input", {
            bubbles: true,
            inputType: "insertText",
            data: text,
          }),
        );
      } catch {
        ce.dispatchEvent(new Event("input", { bubbles: true }));
      }
    } else {
      return { ok: false, reason: "element_not_typable" };
    }
  }

  if (submit) {
    const ev = new KeyboardEvent("keydown", {
      key: "Enter",
      code: "Enter",
      keyCode: 13,
      which: 13,
      bubbles: true,
      cancelable: true,
    });
    el.dispatchEvent(ev);
    el.dispatchEvent(
      new KeyboardEvent("keyup", {
        key: "Enter",
        code: "Enter",
        keyCode: 13,
        which: 13,
        bubbles: true,
        cancelable: true,
      }),
    );
  }
  return { ok: true };
}

function _injected_press_key(
  selector: string | null,
  key: string,
  modifiers: string[],
): { ok: boolean; reason?: string } {
  let target: HTMLElement | Document = document;
  if (selector) {
    const el = document.querySelector(selector) as HTMLElement | null;
    if (!el) return { ok: false, reason: "selector_not_found" };
    target = el;
    try {
      el.focus();
    } catch {
      /* ignore */
    }
  }
  const init: KeyboardEventInit = {
    key,
    code: key,
    bubbles: true,
    cancelable: true,
    shiftKey: modifiers.includes("Shift"),
    ctrlKey: modifiers.includes("Control"),
    altKey: modifiers.includes("Alt"),
    metaKey: modifiers.includes("Meta"),
  };
  target.dispatchEvent(new KeyboardEvent("keydown", init));
  target.dispatchEvent(new KeyboardEvent("keyup", init));
  return { ok: true };
}

function _injected_scroll_to(
  selector: string | null,
  y: number | null,
): { ok: boolean; reason?: string } {
  if (selector) {
    const el = document.querySelector(selector) as HTMLElement | null;
    if (!el) return { ok: false, reason: "selector_not_found" };
    el.scrollIntoView({ block: "center", behavior: "auto" });
    return { ok: true };
  }
  if (typeof y === "number") {
    window.scrollTo({ top: y, behavior: "auto" });
    return { ok: true };
  }
  return { ok: false, reason: "must_provide_selector_or_y" };
}

function _injected_execute_js(code: string, args: unknown[]): unknown {
  // We wrap user code so it can use ``return`` even at top level.
  // `new Function` runs in the page context but world:ISOLATED means
  // it cannot read the page's window globals (it has its own).
  const fn = new Function(
    "args",
    `"use strict";\n${code}\n`,
  ) as (a: unknown[]) => unknown;
  return fn(args);
}

// ---------------------------------------------------------------------------
// Verb implementations
// ---------------------------------------------------------------------------

async function domQuery(rawParams: unknown): Promise<DomQueryResult> {
  const p = rawParams as Partial<DomQueryParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const selector = requireString(p as Record<string, unknown>, "selector");
  const all = Boolean(p.all);
  const requireUnique = Boolean(p.require_unique);

  const out = await execInTab(
    tabId,
    _injected_query,
    [selector, all, DOM_EXCERPT_MAX],
  );
  if (out.matches.length === 0) {
    throw new ProbeException(
      "selector_not_found",
      `querySelector('${selector}') matched 0 nodes`,
      { tab_id: tabId, dom_excerpt: out.dom_excerpt },
      "selector likely changed; check the corresponding *.content.ts",
    );
  }
  if (requireUnique && out.matches.length > 1) {
    throw new ProbeException(
      "selector_ambiguous",
      `querySelector('${selector}') matched ${out.matches.length} nodes (require_unique=true)`,
      { tab_id: tabId, matches: out.matches },
    );
  }
  return { matches: out.matches };
}

async function domWaitForSelector(
  rawParams: unknown,
  signal: AbortSignal,
): Promise<DomWaitForSelectorResult> {
  const p = rawParams as Partial<DomWaitForSelectorParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const selector = requireString(p as Record<string, unknown>, "selector");
  const timeoutMs = typeof p.timeout_ms === "number" ? p.timeout_ms : 15_000;
  const visibleRequired = p.visible !== false;

  // Poll from the background side at 200ms cadence so the per-page
  // call is short (avoids holding a long synchronous tick in the page).
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (signal.aborted) {
      throw new ProbeException(
        "timeout",
        "dom.wait_for_selector aborted by dispatcher",
        { tab_id: tabId },
      );
    }
    try {
      const out = await execInTab(
        tabId,
        _injected_wait_for_selector,
        [selector, visibleRequired, 250],
      );
      if (out.matched > 0) return { matched: out.matched };
    } catch (err) {
      // tab might be navigating; keep retrying within the budget.
      if (err instanceof ProbeException && err.code === "tab_not_found") {
        throw err;
      }
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  const ctx = await gatherErrorContext(tabId);
  throw new ProbeException(
    "selector_not_found",
    `selector '${selector}' did not appear within ${timeoutMs}ms`,
    ctx,
    "page may not be on the expected route, or the selector changed",
  );
}

async function domClick(rawParams: unknown): Promise<DomClickResult> {
  const p = rawParams as Partial<DomClickParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const selector = requireString(p as Record<string, unknown>, "selector");
  const scroll = p.scroll_into_view !== false;

  const out = await execInTab(tabId, _injected_click, [selector, scroll]);
  if (!out.ok) {
    if (out.reason === "selector_not_found") {
      const ctx = await gatherErrorContext(tabId);
      throw new ProbeException(
        "selector_not_found",
        `dom.click: selector '${selector}' matched 0 nodes`,
        ctx,
      );
    }
    throw new ProbeException(
      "script_threw",
      `dom.click failed: ${out.reason ?? "unknown"}`,
      { tab_id: tabId },
    );
  }
  return { ok: true };
}

async function domType(rawParams: unknown): Promise<DomTypeResult> {
  const p = rawParams as Partial<DomTypeParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const selector = requireString(p as Record<string, unknown>, "selector");
  const text = (() => {
    const v = (p as Record<string, unknown>).text;
    if (typeof v !== "string") {
      throw new ProbeException("params_invalid", "field 'text' must be a string");
    }
    return v;
  })();
  const clear = p.clear !== false;
  const submit = Boolean(p.submit);

  const out = await execInTab(
    tabId,
    _injected_type,
    [selector, text, clear, submit],
  );
  if (!out.ok) {
    if (out.reason === "selector_not_found") {
      const ctx = await gatherErrorContext(tabId);
      throw new ProbeException(
        "selector_not_found",
        `dom.type: selector '${selector}' matched 0 nodes`,
        ctx,
      );
    }
    throw new ProbeException(
      "script_threw",
      `dom.type failed: ${out.reason ?? "unknown"}`,
      { tab_id: tabId },
    );
  }
  return { ok: true };
}

async function domPressKey(rawParams: unknown): Promise<DomPressKeyResult> {
  const p = rawParams as Partial<DomPressKeyParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const key = requireString(p as Record<string, unknown>, "key");
  const selector = typeof p.selector === "string" ? p.selector : null;
  const modifiers = Array.isArray(p.modifiers)
    ? (p.modifiers as string[]).filter((m) =>
        ["Shift", "Control", "Alt", "Meta"].includes(m),
      )
    : [];

  const out = await execInTab(
    tabId,
    _injected_press_key,
    [selector, key, modifiers],
  );
  if (!out.ok) {
    if (out.reason === "selector_not_found") {
      const ctx = await gatherErrorContext(tabId);
      throw new ProbeException(
        "selector_not_found",
        `dom.press_key: selector '${selector}' matched 0 nodes`,
        ctx,
      );
    }
    throw new ProbeException(
      "script_threw",
      `dom.press_key failed: ${out.reason ?? "unknown"}`,
      { tab_id: tabId },
    );
  }
  return { ok: true };
}

async function domScrollTo(rawParams: unknown): Promise<DomScrollToResult> {
  const p = rawParams as Partial<DomScrollToParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const selector = typeof p.selector === "string" ? p.selector : null;
  const y = typeof p.y === "number" ? p.y : null;
  if (!selector && y === null) {
    throw new ProbeException(
      "params_invalid",
      "dom.scroll_to: must provide either 'selector' or 'y'",
    );
  }
  const out = await execInTab(tabId, _injected_scroll_to, [selector, y]);
  if (!out.ok) {
    if (out.reason === "selector_not_found") {
      const ctx = await gatherErrorContext(tabId);
      throw new ProbeException(
        "selector_not_found",
        `dom.scroll_to: selector '${selector}' matched 0 nodes`,
        ctx,
      );
    }
    throw new ProbeException(
      "params_invalid",
      `dom.scroll_to failed: ${out.reason ?? "unknown"}`,
      { tab_id: tabId },
    );
  }
  return { ok: true };
}

async function domExecuteJs(rawParams: unknown): Promise<DomExecuteJsResult> {
  const p = rawParams as Partial<DomExecuteJsParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const code = requireString(p as Record<string, unknown>, "code");
  const args = Array.isArray(p.args) ? p.args : [];

  const value = await execInTab(tabId, _injected_execute_js, [code, args]);
  return { value };
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const domVerbs: Record<string, VerbHandler> = {
  "dom.query": domQuery as VerbHandler,
  "dom.wait_for_selector": domWaitForSelector as VerbHandler,
  "dom.click": domClick as VerbHandler,
  "dom.type": domType as VerbHandler,
  "dom.press_key": domPressKey as VerbHandler,
  "dom.scroll_to": domScrollTo as VerbHandler,
  "dom.execute_js": domExecuteJs as VerbHandler,
};
