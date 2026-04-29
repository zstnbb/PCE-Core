# SPDX-License-Identifier: Apache-2.0
"""CDP-based browser-fingerprint stealth patches for E2E live-site runs.

Cloudflare Turnstile / OpenAI / Anthropic / Perplexity all detect Selenium
via JS-level fingerprint signals that Chrome command-line flags alone
cannot suppress (`--disable-blink-features=AutomationControlled` only
removes the boolean attribute exposed to the renderer; it does not hide
`cdc_*` chromedriver injection markers, missing `navigator.plugins`,
WebGL vendor/renderer, etc).

Strategy
--------
Inject one consolidated JS payload via CDP
``Page.addScriptToEvaluateOnNewDocument`` so it runs in EVERY new
document (including Turnstile iframes) BEFORE any site script.

This module is launch-mode agnostic: it works whether Selenium drove
``webdriver.Chrome``, attached to an existing debugger via
``options.debugger_address``, or used WebDriver BiDi to install the PCE
extension. All callers funnel through :func:`apply_stealth`.

Skip via env: ``PCE_E2E_STEALTH=0`` (useful when isolating a non-stealth
regression).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("pce.e2e.stealth")

# Stealth payload. Idempotent: guarded by ``window.__pce_stealth_applied``
# so re-injection in same realm is a no-op. All overrides use
# ``Object.defineProperty`` with ``configurable: true`` to coexist with
# other stealth layers (e.g., the PCE extension itself, which currently
# does not touch these surfaces but might in the future).
_STEALTH_JS = r"""
(() => {
  if (window.__pce_stealth_applied) return;
  try {
    Object.defineProperty(window, '__pce_stealth_applied', {
      value: true, enumerable: false, writable: false, configurable: false,
    });
  } catch (_) { window.__pce_stealth_applied = true; }

  // ---- 0. Make our patched functions look native to toString() probes ----
  const ORIG_TOSTRING = Function.prototype.toString;
  const NATIVE_LABELS = new WeakMap();
  const markNative = (fn, name) => {
    try { NATIVE_LABELS.set(fn, 'function ' + name + '() { [native code] }'); } catch (_) {}
    return fn;
  };
  const patchedToString = function toString() {
    const label = NATIVE_LABELS.get(this);
    if (label) return label;
    return ORIG_TOSTRING.call(this);
  };
  try {
    Function.prototype.toString = patchedToString;
    markNative(Function.prototype.toString, 'toString');
  } catch (_) {}

  // ---- 1. navigator.webdriver -> undefined ----
  // Cloudflare reads this directly; the --disable-blink flag only hides
  // it from CDP-level checks, not from JS getter access.
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: markNative(function webdriver() { return undefined; }, 'get webdriver'),
      configurable: true,
      enumerable: true,
    });
  } catch (_) {}

  // ---- 2. Strip chromedriver evidence (cdc_* / $cdc_*) ----
  // chromedriver injects a global with a name like
  // ``cdc_adoQpoasnfa76pfcZLmcfl_Array``. Cloudflare iterates window
  // keys looking for this prefix.
  const stripCdc = (target) => {
    try {
      for (const key of Object.getOwnPropertyNames(target)) {
        if (/^[$_]?cdc_[A-Za-z0-9]+/.test(key)) {
          try { delete target[key]; } catch (_) {}
        }
      }
    } catch (_) {}
  };
  stripCdc(window);
  stripCdc(document);

  // ---- 3. navigator.plugins / mimeTypes (real Chrome has 3+) ----
  // A length-0 plugins array is a strong automation signal. We mimic
  // the standard set every modern Chrome on Windows ships.
  const fakePlugin = (name, filename, description, mimeData) => {
    const plugin = Object.create(Plugin.prototype);
    Object.defineProperties(plugin, {
      name:        { value: name,        enumerable: true },
      filename:    { value: filename,    enumerable: true },
      description: { value: description, enumerable: true },
      length:      { value: mimeData.length, enumerable: true },
    });
    mimeData.forEach((m, i) => {
      const mime = Object.create(MimeType.prototype);
      Object.defineProperties(mime, {
        type:        { value: m.type,        enumerable: true },
        suffixes:    { value: m.suffixes,    enumerable: true },
        description: { value: m.description, enumerable: true },
        enabledPlugin: { value: plugin,      enumerable: true },
      });
      plugin[i] = mime;
      plugin[m.type] = mime;
    });
    return plugin;
  };
  let fakePlugins;
  try {
    const plugins = [
      fakePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [
        { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        { type: 'text/pdf',        suffixes: 'pdf', description: 'Portable Document Format' },
      ]),
      fakePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [
        { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        { type: 'text/pdf',        suffixes: 'pdf', description: 'Portable Document Format' },
      ]),
      fakePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [
        { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        { type: 'text/pdf',        suffixes: 'pdf', description: 'Portable Document Format' },
      ]),
    ];
    const arr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { arr[i] = p; arr[p.name] = p; });
    Object.defineProperties(arr, {
      length:    { value: plugins.length, enumerable: true },
      item:      { value: markNative(function item(i) { return arr[i] || null; }, 'item') },
      namedItem: { value: markNative(function namedItem(n) { return arr[n] || null; }, 'namedItem') },
      refresh:   { value: markNative(function refresh() {}, 'refresh') },
    });
    fakePlugins = arr;
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: markNative(function plugins() { return fakePlugins; }, 'get plugins'),
      configurable: true, enumerable: true,
    });
  } catch (_) {}

  // ---- 4. navigator.languages ----
  // Selenium default in non-localised builds is ['en-US']; mismatched
  // against UA / Accept-Language is suspicious. CN-resident dev profile
  // most plausibly has this list.
  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: markNative(function languages() { return ['zh-CN', 'zh', 'en-US', 'en']; }, 'get languages'),
      configurable: true, enumerable: true,
    });
  } catch (_) {}

  // ---- 5. window.chrome shim ----
  // Real Chrome exposes ``chrome`` even on https://* pages. Selenium
  // strips it. Cloudflare checks ``'chrome' in window`` AND probes
  // ``chrome.runtime``.
  try {
    if (!window.chrome) {
      window.chrome = {};
    }
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
        PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
        PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
        RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
      };
    }
    if (!window.chrome.loadTimes) {
      window.chrome.loadTimes = markNative(function loadTimes() {
        return {
          requestTime: Date.now() / 1000 - 1,
          startLoadTime: Date.now() / 1000 - 1,
          commitLoadTime: Date.now() / 1000 - 0.5,
          finishDocumentLoadTime: Date.now() / 1000 - 0.2,
          finishLoadTime: Date.now() / 1000,
          firstPaintTime: Date.now() / 1000,
          firstPaintAfterLoadTime: 0,
          navigationType: 'Other',
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
          npnNegotiatedProtocol: 'h2',
          wasAlternateProtocolAvailable: false,
          connectionInfo: 'h2',
        };
      }, 'loadTimes');
    }
    if (!window.chrome.csi) {
      window.chrome.csi = markNative(function csi() {
        return { onloadT: Date.now(), startE: Date.now(), pageT: 0, tran: 15 };
      }, 'csi');
    }
  } catch (_) {}

  // ---- 6. Permissions <-> Notification.permission consistency ----
  // Headless/automated Chrome returns query.state='granted' while
  // Notification.permission='default' -- a textbook automation tell.
  try {
    if (navigator.permissions && navigator.permissions.query) {
      const origQuery = navigator.permissions.query.bind(navigator.permissions);
      const patched = markNative(function query(params) {
        if (params && params.name === 'notifications') {
          return Promise.resolve({
            state: (typeof Notification !== 'undefined') ? Notification.permission : 'denied',
            onchange: null,
          });
        }
        return origQuery(params);
      }, 'query');
      navigator.permissions.query = patched;
    }
  } catch (_) {}

  // ---- 7. WebGL UNMASKED_VENDOR / RENDERER ----
  // Selenium-driven Chrome on a real GPU still returns 'Google Inc.' /
  // 'ANGLE (Google, ...)' which Cloudflare cross-checks against
  // navigator.platform. Spoof to a common Intel iGPU.
  const VENDOR  = 37445; // UNMASKED_VENDOR_WEBGL
  const RENDERER = 37446; // UNMASKED_RENDERER_WEBGL
  const FAKE_VENDOR = 'Intel Inc.';
  const FAKE_RENDERER = 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
  const patchGetParameter = (proto) => {
    try {
      const orig = proto.getParameter;
      proto.getParameter = markNative(function getParameter(p) {
        if (p === VENDOR) return FAKE_VENDOR;
        if (p === RENDERER) return FAKE_RENDERER;
        return orig.call(this, p);
      }, 'getParameter');
    } catch (_) {}
  };
  if (window.WebGLRenderingContext) patchGetParameter(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) patchGetParameter(WebGL2RenderingContext.prototype);

  // ---- 8. hardwareConcurrency / deviceMemory sane values ----
  try {
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
      get: markNative(function hardwareConcurrency() { return 8; }, 'get hardwareConcurrency'),
      configurable: true, enumerable: true,
    });
  } catch (_) {}
  try {
    Object.defineProperty(Navigator.prototype, 'deviceMemory', {
      get: markNative(function deviceMemory() { return 8; }, 'get deviceMemory'),
      configurable: true, enumerable: true,
    });
  } catch (_) {}

  // ---- 9. iframe contentWindow re-injection ----
  // Cloudflare Turnstile renders its widget inside an iframe and
  // re-runs all probes in that iframe's window. If we don't touch it,
  // the iframe shows webdriver=true and the widget refuses to render
  // (the "empty box" symptom). Patch by overriding contentWindow to
  // re-run our setup. The injected document loads from
  // challenges.cloudflare.com so the same-origin policy still allows
  // us to access its window from the parent realm via about:srcdoc
  // / sandbox shenanigans -- but the safer path is to ensure
  // ``Object.defineProperty(Navigator.prototype, ...)`` we did above
  // is inherited (prototype walks survive iframe creation). We still
  // re-stamp ``window.chrome`` since that's per-realm.
  try {
    const origCWDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (origCWDesc && origCWDesc.get) {
      const origGet = origCWDesc.get;
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: markNative(function contentWindow() {
          const w = origGet.call(this);
          try {
            if (w && !w.__pce_stealth_applied) {
              // Mark guard early so re-entry doesn't loop.
              try { Object.defineProperty(w, '__pce_stealth_applied', { value: true }); } catch (_) {}
              if (!w.chrome) w.chrome = window.chrome;
            }
          } catch (_) {}
          return w;
        }, 'get contentWindow'),
        configurable: true, enumerable: true,
      });
    }
  } catch (_) {}
})();
"""


def apply_stealth(driver: Any, *, label: str = "") -> bool:
    """Inject the stealth payload into ``driver`` via two complementary paths.

    1. ``Page.addScriptToEvaluateOnNewDocument`` (CDP) — registers the
       payload to fire on the next navigation. NOTE: in Selenium 4 the
       chromedriver-mediated ``execute_cdp_cmd`` is bound to the target
       attached at session-start; subsequent ``switch_to.new_window``
       calls do **not** re-route CDP commands to the new tab. So this
       step really only covers the original tab unless the caller is
       using a fresh CDP session per tab.
    2. ``driver.execute_script`` (WebDriver standard) — runs the same
       payload synchronously in the **currently focused** Selenium tab,
       patching the live window. This is the workaround for the CDP
       target-binding issue and is the path that actually patches the
       page Cloudflare's challenge JS samples.

    Together they ensure the patches survive both fresh navigations and
    inspection of the current document.

    Returns True if at least one path succeeded. Never raises.
    Skip via ``PCE_E2E_STEALTH=0``.
    """
    if os.environ.get("PCE_E2E_STEALTH", "1").strip() == "0":
        logger.info("Stealth disabled via PCE_E2E_STEALTH=0 [%s]", label or "default")
        return False

    cdp_ok = False
    script_ok = False

    try:
        driver.execute_cdp_cmd("Page.enable", {})
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _STEALTH_JS},
        )
        cdp_ok = True
    except Exception as exc:
        logger.debug("CDP addScriptToEvaluateOnNewDocument failed [%s]: %s",
                     label or "default", exc)

    # Always also run via WebDriver Execute Script. This routes through
    # Selenium's window-aware command bus and lands in the currently
    # focused tab's renderer, regardless of CDP target-binding state.
    try:
        driver.execute_script(_STEALTH_JS)
        script_ok = True
    except Exception as exc:
        logger.debug("WebDriver execute_script stealth failed [%s]: %s",
                     label or "default", exc)

    if not (cdp_ok or script_ok):
        logger.warning(
            "Stealth injection failed via both CDP and execute_script [%s]. "
            "Cloudflare/Turnstile may block live-site E2E.",
            label or "default",
        )
        return False

    logger.info(
        "Stealth injected [%s] (cdp=%s, script=%s)",
        label or "default", cdp_ok, script_ok,
    )
    return True


def apply_stealth_all_tabs(driver: Any, *, label: str = "") -> int:
    """Apply stealth to every currently open tab.

    ``addScriptToEvaluateOnNewDocument`` registrations do not propagate
    across tabs (each tab is a separate CDP target). Launchers that
    open multiple tabs (e.g. ``_open_login_tabs.py``, or any test that
    spawns ``window.open`` children) MUST call this after every tab
    spawn so the registration is present before the next navigation
    of that target.

    Returns the number of tabs the patch was successfully registered on.
    """
    if os.environ.get("PCE_E2E_STEALTH", "1").strip() == "0":
        return 0

    try:
        original = driver.current_window_handle
    except Exception:
        original = None

    count = 0
    handles: list[str] = []
    try:
        handles = list(driver.window_handles)
    except Exception as exc:
        logger.warning("Could not enumerate window handles for stealth: %s", exc)
        return 0

    for handle in handles:
        try:
            driver.switch_to.window(handle)
            tag = f"{label}:{handle[:8]}" if label else handle[:8]
            if apply_stealth(driver, label=tag):
                count += 1
        except Exception as exc:
            logger.warning("Failed switch+stealth on tab %s: %s", handle[:8], exc)

    if original is not None:
        try:
            driver.switch_to.window(original)
        except Exception:
            pass

    return count
