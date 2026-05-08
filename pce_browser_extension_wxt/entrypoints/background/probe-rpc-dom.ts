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
  DomClickActionMainParams,
  DomClickActionMainResult,
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
  func: (...args: TArgs) => TRet | Promise<TRet>,
  args: TArgs,
  world: "ISOLATED" | "MAIN" = "ISOLATED",
): Promise<TRet> {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world,
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
  // ``new Function`` works in ISOLATED (extension content-script origin
  // permits eval). It does NOT work in MAIN on CSP-strict pages
  // (ChatGPT/Claude/Google) — the page's ``script-src`` blocks the
  // Function constructor and Chrome silently returns ``null`` from the
  // executeScript call. It also does NOT work in BACKGROUND service
  // worker — MV3's default SW CSP forbids eval/Function entirely.
  //
  // Therefore: ``dom.execute_js`` is ISOLATED-only. For MAIN-world
  // operations that need real Web APIs (DataTransfer carrying File,
  // synthetic paste/drop dispatch with payload visible to the page's
  // own listeners), use a STATIC verb whose function body is shipped
  // pre-compiled by ``chrome.scripting.executeScript`` — see
  // ``_main_dispatch_paste`` and the ``dom.dispatch_paste_main`` verb
  // below.
  const fn = new Function(
    "args",
    `"use strict";\n${code}\n`,
  ) as (a: unknown[]) => unknown;
  return fn(args);
}

// ---------------------------------------------------------------------------
// MAIN-world helpers (pre-compiled functions; no runtime eval needed)
// ---------------------------------------------------------------------------

interface _MainPasteParams {
  selector: string;
  file_b64: string;
  file_name: string;
  media_type: string;
}

interface _MainPasteResult {
  ok: boolean;
  marker: string;
  err: string | null;
  target_tag: string | null;
  target_id: string | null;
  dt_files_len: number;
  ev_class: string;
}

interface _MainSetFileInputParams {
  selectors: string[];
  file_b64: string;
  file_name: string;
  media_type: string;
}

interface _MainSetFileInputResult {
  ok: boolean;
  marker: string;
  err: string | null;
  matched_selector: string | null;
  via: string;
}

type _ClickScope = "document" | "last_assistant" | "last_user" | "last_turn";

interface _MainClickActionParams {
  action: string;
  scope: _ClickScope;
  root_selectors: string[];
  selectors: string[];
  labels: string[];
  more_selectors: string[];
  menu_labels: string[];
  prefer_last: boolean;
  dry_run: boolean;
  scan_menu: boolean;
}

function _main_click_action(p: _MainClickActionParams): Promise<DomClickActionMainResult> {
  const out: DomClickActionMainResult = {
    ok: false,
    marker: "init",
    via: "",
    action: p.action,
    matched_selector: null,
    matched_label: null,
    clicked_text: null,
    clicked_html_excerpt: null,
    candidate_count: 0,
    root_count: 0,
    more_candidate_count: 0,
    candidates: [],
    menu_candidates: [],
    controls: [],
    err: null,
  };

  const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
  const textOf = (el: Element | null): string => {
    if (!el) return "";
    const parts = [
      el.getAttribute("aria-label"),
      el.getAttribute("title"),
      el.getAttribute("data-testid"),
      el.getAttribute("data-test-id"),
      (el as HTMLElement).innerText,
      el.textContent,
    ].filter(Boolean);
    return parts.join(" ").trim();
  };
  const visibleEnough = (el: Element): boolean => {
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const disabled = (el as HTMLButtonElement).disabled;
    if (disabled || el.getAttribute("aria-disabled") === "true") return false;
    const classList = (el as HTMLElement).classList;
    if (
      classList.contains("mat-mdc-button-disabled") ||
      classList.contains("mdc-button--disabled")
    ) {
      return false;
    }
    return true;
  };
  const fireHover = (el: Element | null): void => {
    if (!el) return;
    for (const t of ["pointerover", "pointerenter", "mouseover", "mouseenter"]) {
      try {
        el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
      } catch {
        // ignore
      }
    }
  };
  const fullClick = (el: Element): void => {
    try { (el as HTMLElement).scrollIntoView?.({ block: "center", inline: "center" }); } catch {}
    fireHover(el);
    for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      try {
        const Ctor = t.startsWith("pointer") && typeof PointerEvent !== "undefined"
          ? PointerEvent
          : MouseEvent;
        el.dispatchEvent(new Ctor(t, { bubbles: true, cancelable: true, view: window }));
      } catch {
        // ignore
      }
    }
    try { (el as HTMLElement).click?.(); } catch {}
  };
  const recordClick = (
    el: Element,
    via: string,
    selector: string | null,
    label: string | null,
  ): void => {
    out.ok = true;
    out.marker = "clicked";
    out.via = via;
    out.matched_selector = selector;
    out.matched_label = label;
    out.clicked_text = textOf(el).slice(0, 240) || null;
    const html = (el as HTMLElement).outerHTML || "";
    out.clicked_html_excerpt = html.slice(0, 240) || null;
    try {
      document.documentElement.setAttribute("data-pce-last-action", p.action);
      document.documentElement.setAttribute("data-pce-last-action-via", via);
      document.documentElement.setAttribute("data-pce-last-action-at", String(Date.now()));
    } catch {
      // ignore
    }
    if (p.action === "branch_prev" || p.action === "branch_next") {
      setTimeout(() => {
        try {
          document.dispatchEvent(new CustomEvent("pce-manual-capture"));
        } catch {
          // ignore
        }
      }, 800);
    }
  };
  const isMenuTrigger = (el: Element): boolean => {
    const className = String((el as HTMLElement).className || "");
    return (
      el.getAttribute("aria-haspopup") === "menu" ||
      className.includes("menu-trigger") ||
      className.includes("mat-mdc-menu-trigger")
    );
  };
  const pushCandidate = (
    bucket: "candidates" | "menu_candidates" | "controls",
    el: Element,
    via: string,
    selector: string | null,
    label: string | null,
  ): void => {
    const arr = out[bucket] ?? [];
    if (arr.length >= 40) return;
    arr.push({
      via,
      selector,
      label,
      text: textOf(el).slice(0, 240) || null,
      html_excerpt: ((el as HTMLElement).outerHTML || "").slice(0, 240) || null,
    });
    out[bucket] = arr;
  };
  const collectControls = (
    roots: Element[],
    selectors: string[],
  ): Element[] => {
    const outControls: Element[] = [];
    const seen = new Set<Element>();
    for (const root of roots) {
      for (const sel of selectors) {
        try {
          root.querySelectorAll(sel).forEach((el) => {
            if (seen.has(el) || !visibleEnough(el)) return;
            seen.add(el);
            outControls.push(el);
          });
        } catch {
          // ignore invalid selectors
        }
      }
    }
    return outControls;
  };
  const collectMenuRoots = (): Element[] => {
    const roots: Element[] = [];
    const seen = new Set<Element>();
    for (const sel of [
      "[role='menu']",
      "[role='listbox']",
      "[data-radix-menu-content]",
      "[data-radix-popper-content-wrapper]",
      "[data-state='open']",
      ".mat-mdc-menu-panel",
      ".cdk-overlay-pane",
      ".cdk-overlay-connected-position-bounding-box",
    ]) {
      try {
        document.querySelectorAll(sel).forEach((el) => {
          if (seen.has(el) || !visibleEnough(el)) return;
          seen.add(el);
          roots.push(el);
        });
      } catch {
        // ignore invalid selectors
      }
    }
    return roots;
  };
  const resolveRoots = (): Element[] => {
    if (p.scope === "document") return [document.documentElement];
    const roots: Element[] = [];
    const defaults =
      p.scope === "last_user"
        ? [
            '[data-message-author-role="user"]',
            '[data-testid*="user-message"]',
            '[data-turn="user"]',
          ]
        : p.scope === "last_assistant"
          ? [
              '[data-message-author-role="assistant"]',
              '[data-testid*="assistant-message"]',
              '[data-turn="assistant"]',
              '[data-test-id*="response"]',
              "message-content",
              "article",
            ]
          : [
              '[data-message-author-role]',
              '[data-testid*="message"]',
              "[data-turn]",
              "article",
            ];
    const selectors = (p.root_selectors.length ? p.root_selectors : defaults);
    for (const sel of selectors) {
      try {
        roots.push(...Array.from(document.querySelectorAll(sel)));
      } catch {
        // ignore invalid selectors
      }
    }
    const last = roots[roots.length - 1];
    if (!last) return [document.documentElement];
    const scoped: Element[] = [];
    const seen = new Set<Element>();
    const push = (el: Element | null | undefined): void => {
      if (!el || seen.has(el)) return;
      seen.add(el);
      scoped.push(el);
    };
    let cursor: Element | null = last;
    for (let depth = 0; cursor && depth < 8; depth++) {
      if (cursor === document.body || cursor === document.documentElement) break;
      push(cursor);
      push(cursor.previousElementSibling);
      push(cursor.nextElementSibling);
      cursor = cursor.parentElement;
    }
    return scoped;
  };
  const collectBySelectors = (roots: Element[], selectors: string[]): Array<{ el: Element; selector: string }> => {
    const hits: Array<{ el: Element; selector: string }> = [];
    const seen = new Set<Element>();
    for (const sel of selectors) {
      for (const root of roots) {
        try {
          root.querySelectorAll(sel).forEach((el) => {
            if (seen.has(el)) return;
            if (visibleEnough(el)) hits.push({ el, selector: sel });
            seen.add(el);
          });
        } catch {
          // ignore invalid selectors
        }
      }
    }
    return hits;
  };
  const collectByLabels = (roots: Element[], labels: string[]): Array<{ el: Element; label: string }> => {
    if (!labels.length) return [];
    const normalized = labels.map((l) => l.toLowerCase());
    const hits: Array<{ el: Element; label: string }> = [];
    const seen = new Set<Element>();
    for (const root of roots) {
      const nodes = Array.from(
        root.querySelectorAll("button, [role='button'], [role='menuitem'], a"),
      );
      for (const el of nodes) {
        if (seen.has(el)) continue;
        if (!visibleEnough(el)) continue;
        const text = textOf(el).toLowerCase();
        const label = normalized.find((candidate) => text.includes(candidate));
        if (label) hits.push({ el, label });
        seen.add(el);
      }
    }
    return hits;
  };
  const pick = <T,>(items: T[]): T | null => {
    if (!items.length) return null;
    return p.prefer_last ? items[items.length - 1] : items[0];
  };

  return (async () => {
    try {
      const roots = resolveRoots();
      out.root_count = roots.length;
      roots.forEach(fireHover);
      out.marker = "roots_resolved";
      if (p.dry_run) {
        collectControls(
          roots,
          ["button", "[role='button']", "a", "[aria-label]", "[mattooltip]", "[title]"],
        ).forEach((el) => pushCandidate("controls", el, "root_control", null, null));
      }

      const direct = collectBySelectors(roots, p.selectors);
      out.candidate_count += direct.length;
      direct.forEach((hit) => pushCandidate("candidates", hit.el, "direct_selector", hit.selector, null));
      const directHit = pick(direct);
      if (directHit) {
        if (p.dry_run) {
          if (p.scan_menu && isMenuTrigger(directHit.el)) {
            fullClick(directHit.el);
            await wait(250);
            const menuRoots = collectMenuRoots();
            collectControls(
              menuRoots,
              [
                "[role='menuitem']",
                "[role='option']",
                "[role='menu'] button",
                ".mat-mdc-menu-panel button",
                ".cdk-overlay-pane button",
              ],
            ).forEach((el) => pushCandidate("menu_candidates", el, "direct_menu_control", null, null));
            out.marker = "dry_direct_menu";
            return out;
          }
          out.marker = "dry_direct_selector";
          return out;
        }
        if (isMenuTrigger(directHit.el)) {
          fullClick(directHit.el);
          out.marker = "direct_menu_clicked";
          await wait(250);
          for (let attempt = 0; attempt < 12; attempt++) {
            const menuRoots = collectMenuRoots();
            const menuHits = collectByLabels(
              menuRoots,
              [...p.menu_labels, ...p.labels],
            );
            out.candidate_count += menuHits.length;
            menuHits.forEach((hit) => pushCandidate("menu_candidates", hit.el, "direct_menu_label", null, hit.label));
            const menuHit = pick(menuHits);
            if (menuHit) {
              fullClick(menuHit.el);
              recordClick(menuHit.el, "direct_menu_label", directHit.selector, menuHit.label);
              return out;
            }
            await wait(150);
          }
          out.marker = "direct_menu_no_match";
          return out;
        }
        fullClick(directHit.el);
        recordClick(directHit.el, "direct_selector", directHit.selector, null);
        return out;
      }

      const labelHits = collectByLabels(roots, p.labels);
      out.candidate_count += labelHits.length;
      labelHits.forEach((hit) => pushCandidate("candidates", hit.el, "direct_label", null, hit.label));
      const labelHit = pick(labelHits);
      if (labelHit) {
        if (p.dry_run) {
          out.marker = "dry_direct_label";
          return out;
        }
        fullClick(labelHit.el);
        recordClick(labelHit.el, "direct_label", null, labelHit.label);
        return out;
      }

      const existingMenuRoots = collectMenuRoots();
      if (existingMenuRoots.length && p.menu_labels.length) {
        if (p.dry_run && p.scan_menu) {
          collectControls(
            existingMenuRoots,
            [
              "[role='menuitem']",
              "[role='option']",
              "[role='menu'] button",
              "[data-radix-menu-content] button",
              "[data-radix-popper-content-wrapper] button",
              "[data-state='open'] button",
              ".mat-mdc-menu-panel button",
              ".cdk-overlay-pane button",
            ],
          ).forEach((el) => pushCandidate("menu_candidates", el, "existing_menu_control", null, null));
        }
        const menuHits = collectByLabels(existingMenuRoots, p.menu_labels);
        out.candidate_count += menuHits.length;
        menuHits.forEach((hit) => pushCandidate("menu_candidates", hit.el, "existing_menu_label", null, hit.label));
        const menuHit = pick(menuHits);
        if (menuHit) {
          if (p.dry_run) {
            out.marker = "dry_existing_menu_label";
            return out;
          }
          fullClick(menuHit.el);
          recordClick(menuHit.el, "existing_menu_label", null, menuHit.label);
          return out;
        }
        if (p.dry_run && p.scan_menu) {
          out.marker = "dry_existing_menu_no_match";
          return out;
        }
      }

      const moreHits = collectBySelectors(roots, p.more_selectors);
      out.more_candidate_count = moreHits.length;
      moreHits.forEach((hit) => pushCandidate("candidates", hit.el, "more_selector", hit.selector, null));
      const more = pick(moreHits);
      if (!more) {
        out.marker = "no_direct_no_more";
        return out;
      }
      if (p.dry_run && !p.scan_menu) {
        out.marker = "dry_more_available";
        return out;
      }
      fullClick(more.el);
      out.marker = "more_clicked";
      await wait(250);

      for (let attempt = 0; attempt < 12; attempt++) {
        if (p.dry_run && p.scan_menu) {
          collectControls(
            [document.documentElement],
            [
              "[role='menuitem']",
              "[role='option']",
              "[role='menu'] button",
              "[data-radix-menu-content] button",
              "[data-radix-popper-content-wrapper] button",
              "[data-state='open'] button",
              ".mat-mdc-menu-panel button",
              ".cdk-overlay-pane button",
            ],
          ).forEach((el) => pushCandidate("menu_candidates", el, "menu_control", null, null));
        }
        const menuHits = collectByLabels([document.documentElement], p.menu_labels);
        out.candidate_count += menuHits.length;
        menuHits.forEach((hit) => pushCandidate("menu_candidates", hit.el, "menu_label", null, hit.label));
        const menuHit = pick(menuHits);
        if (menuHit) {
          if (p.dry_run) {
            out.marker = "dry_menu_label";
            return out;
          }
          fullClick(menuHit.el);
          recordClick(menuHit.el, "menu_label", null, menuHit.label);
          return out;
        }
        await wait(150);
      }
      out.marker = "menu_no_match";
    } catch (e) {
      out.err = String(e);
      out.marker = "threw";
    }
    return out;
  })();
}

/**
 * Set ``input[type=file].files`` programmatically using the prototype's
 * native setter so React's value tracker sees the assignment, then
 * dispatch ``input``+``change`` so the page's onChange handler fires.
 * Runs in the page's MAIN world via static dispatch — same CSP-bypass
 * rationale as ``_main_dispatch_paste``.
 *
 * This replaces the dynamic-eval implementation that ``dom.execute_js``
 * used to do, which silently no-op'd on MV3 due to extension-page CSP
 * forbidding ``new Function`` even in content-script ISOLATED.
 */
function _main_set_file_input(p: _MainSetFileInputParams): _MainSetFileInputResult {
  const out: _MainSetFileInputResult = {
    ok: false,
    marker: "init",
    err: null,
    matched_selector: null,
    via: "",
  };
  try {
    let input: HTMLInputElement | null = null;
    for (const sel of p.selectors) {
      const el = document.querySelector(sel) as HTMLInputElement | null;
      if (el) {
        input = el;
        out.matched_selector = sel;
        break;
      }
    }
    if (!input) {
      out.marker = "no_input";
      return out;
    }
    out.marker = "input_resolved";

    const bin = atob(p.file_b64);
    const bytes = new Uint8Array(bin.length);
    for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
    const blob = new Blob([bytes], { type: p.media_type });
    const file = new File([blob], p.file_name, { type: p.media_type });
    out.marker = "file_built";

    const dt = new DataTransfer();
    dt.items.add(file);
    out.marker = "dt_built";

    // Use the prototype's native setter so React's tracker sees the
    // assignment. Without this, React intercepts ``input.files = ...``
    // and bails on the change event (thinks value didn't change), and
    // the upload chain never runs even though the chip might render
    // briefly.
    try {
      const desc = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "files",
      );
      if (desc && typeof desc.set === "function") {
        desc.set.call(input, dt.files);
        out.via = "react_native_setter";
      } else {
        input.files = dt.files;
        out.via = "direct";
      }
    } catch (e) {
      try { input.files = dt.files; } catch (_e) { /* ignore */ }
      out.via = "fallback_direct:" + String(e);
    }
    out.marker = "files_set";

    try { input.dispatchEvent(new Event("input", { bubbles: true })); } catch (_e) { /* ignore */ }
    try { input.dispatchEvent(new Event("change", { bubbles: true })); } catch (_e) { /* ignore */ }
    out.marker = "events_dispatched";
    out.ok = true;
  } catch (e) {
    out.err = String(e);
  }
  return out;
}

/**
 * Build a synthetic ``paste`` event carrying a real ``File`` and
 * dispatch it on the resolved input element. Runs in the page's MAIN
 * world via a static ``chrome.scripting.executeScript`` call so the
 * page's React/ProseMirror paste listeners observe a populated
 * ``clipboardData.files``. NO ``new Function`` / ``eval`` — the body
 * uses only pure Web APIs so strict page CSP doesn't reject it.
 */
function _main_dispatch_paste(p: _MainPasteParams): _MainPasteResult {
  const out: _MainPasteResult = {
    ok: false,
    marker: "init",
    err: null,
    target_tag: null,
    target_id: null,
    dt_files_len: 0,
    ev_class: "",
  };
  try {
    let input: Element | null = document.querySelector(p.selector);
    if (!input) input = document.activeElement;
    if (!input) input = document.body;
    out.target_tag = (input as Element).tagName;
    out.target_id = (input as Element).id || null;
    try { (input as HTMLElement).focus?.(); } catch (_e) { /* ignore */ }
    try { (input as HTMLElement).click?.(); } catch (_e) { /* ignore */ }
    out.marker = "focused";

    const bin = atob(p.file_b64);
    const bytes = new Uint8Array(bin.length);
    for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
    const file = new File([bytes], p.file_name, { type: p.media_type });
    out.marker = "file_built";

    const dt = new DataTransfer();
    dt.items.add(file);
    out.dt_files_len = dt.files.length;
    out.marker = "dt_built";

    let ev: Event;
    try {
      ev = new ClipboardEvent("paste", {
        clipboardData: dt,
        bubbles: true,
        cancelable: true,
      });
      out.ev_class = "ClipboardEvent";
    } catch (_e) {
      ev = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "clipboardData", { value: dt });
      out.ev_class = "Event+defineProperty";
    }
    out.marker = "event_built";

    (input as HTMLElement).dispatchEvent(ev);
    out.marker = "paste_dispatched";
    out.ok = true;
  } catch (e) {
    out.err = String(e);
  }
  return out;
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
  // ``dom.execute_js`` is ISOLATED-only by design. The ``world`` param
  // is accepted for forward compatibility but ignored — see the long
  // explanation above ``_injected_execute_js``. Callers that need MAIN
  // world should use a dedicated static verb instead (e.g.
  // ``dom.dispatch_paste_main``).
  const value = await execInTab(tabId, _injected_execute_js, [code, args]);
  return { value };
}

async function domClickActionMain(rawParams: unknown): Promise<DomClickActionMainResult> {
  const p = rawParams as Partial<DomClickActionMainParams>;
  const tabId = requireNumber(p as Record<string, unknown>, "tab_id");
  const action = requireString(p as Record<string, unknown>, "action");
  const scope = (
    p.scope === "last_assistant" ||
    p.scope === "last_user" ||
    p.scope === "last_turn" ||
    p.scope === "document"
  ) ? p.scope : "document";
  const params: _MainClickActionParams = {
    action,
    scope,
    root_selectors: Array.isArray(p.root_selectors) ? p.root_selectors.map(String) : [],
    selectors: Array.isArray(p.selectors) ? p.selectors.map(String) : [],
    labels: Array.isArray(p.labels) ? p.labels.map(String) : [],
    more_selectors: Array.isArray(p.more_selectors) ? p.more_selectors.map(String) : [],
    menu_labels: Array.isArray(p.menu_labels) ? p.menu_labels.map(String) : [],
    prefer_last: p.prefer_last !== false,
    dry_run: Boolean(p.dry_run),
    scan_menu: Boolean(p.scan_menu),
  };
  return await execInTab(tabId, _main_click_action, [params], "MAIN");
}

async function domDispatchPasteMain(rawParams: unknown): Promise<unknown> {
  const p = rawParams as Record<string, unknown>;
  const tabId = requireNumber(p, "tab_id");
  const selector = requireString(p, "selector");
  const fileB64 = requireString(p, "file_b64");
  const fileName = requireString(p, "file_name");
  const mediaType = requireString(p, "media_type");
  const out = await execInTab(
    tabId,
    _main_dispatch_paste,
    [{
      selector,
      file_b64: fileB64,
      file_name: fileName,
      media_type: mediaType,
    }],
    "MAIN",
  );
  return out;
}

async function domSetFileInputMain(rawParams: unknown): Promise<unknown> {
  const p = rawParams as Record<string, unknown>;
  const tabId = requireNumber(p, "tab_id");
  const selectorsRaw = p.selectors;
  if (!Array.isArray(selectorsRaw) || selectorsRaw.length === 0) {
    throw new ProbeException(
      "params_invalid",
      "field 'selectors' must be a non-empty array of strings",
    );
  }
  const selectors = selectorsRaw.map((s) => String(s));
  const fileB64 = requireString(p, "file_b64");
  const fileName = requireString(p, "file_name");
  const mediaType = requireString(p, "media_type");
  const out = await execInTab(
    tabId,
    _main_set_file_input,
    [{
      selectors,
      file_b64: fileB64,
      file_name: fileName,
      media_type: mediaType,
    }],
    "MAIN",
  );
  return out;
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
  "dom.click_action_main": domClickActionMain as VerbHandler,
  "dom.dispatch_paste_main": domDispatchPasteMain as VerbHandler,
  "dom.set_file_input_main": domSetFileInputMain as VerbHandler,
};
