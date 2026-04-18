"""E2E Smoke: Chrome subprocess + Selenium + addScriptToEvaluateOnNewDocument.

Strategy:
1. Launch Chrome with existing profile (has login sessions) + --load-extension
2. Connect Selenium via debugger_address
3. Use Page.addScriptToEvaluateOnNewDocument to inject capture scripts BEFORE page load
4. The injected shim replaces bridge.js: captures network data and POSTs directly to PCE
"""
import time, os, subprocess, json, urllib.request
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# ---------- Config ----------
EXT_DIR = Path("pce_browser_extension_wxt/.output/chrome-mv3").resolve()
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9224
PCE_API = "http://127.0.0.1:9800/api/v1/captures"

def get_cd():
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        return ChromeDriverManager().install()
    except:
        return str(Path.home() / ".wdm" / "drivers" / "chromedriver" / "win64" / "146.0.7680.165" / "chromedriver-win32" / "chromedriver.exe")

# ---------- Build injection script ----------
def build_inject_script():
    """Build a single JS blob that replaces bridge.js + content script injection.

    This script:
    1. Sets __PCE_AI_PAGE_CONFIRMED = true
    2. Injects ai_patterns.js content inline
    3. Injects network_interceptor.js content inline
    4. Listens for PCE_NETWORK_CAPTURE postMessages and POSTs directly to PCE server
    """
    ai_patterns = (EXT_DIR / "interceptor" / "ai_patterns.js").read_text(encoding="utf-8")
    interceptor = (EXT_DIR / "interceptor" / "network_interceptor.js").read_text(encoding="utf-8")

    # The shim replaces bridge.js: instead of chrome.runtime.sendMessage,
    # it POSTs captured data directly to the PCE ingest API.
    shim = f"""
(function() {{
  if (window.__PCE_E2E_SHIM) return;
  window.__PCE_E2E_SHIM = true;

  // 1. Mark page as confirmed AI (what bridge.js does)
  window.__PCE_AI_PAGE_CONFIRMED = true;

  // 2. Inject ai_patterns.js via indirect eval (runs in global scope, no DOM needed)
  try {{
    (0, eval)({json.dumps(ai_patterns)});
  }} catch(e) {{ console.warn('[PCE:e2e] ai_patterns eval failed', e); }}

  // 3. Inject network_interceptor.js (slight delay so patterns are ready)
  setTimeout(() => {{
    try {{
      (0, eval)({json.dumps(interceptor)});
    }} catch(e) {{ console.warn('[PCE:e2e] interceptor eval failed', e); }}
  }}, 50);

  // 4. Bridge: listen for PCE_NETWORK_CAPTURE and POST directly to PCE server
  window.addEventListener('message', function(event) {{
    if (event.source !== window) return;
    if (!event.data || event.data.type !== 'PCE_NETWORK_CAPTURE') return;
    const p = event.data.payload;
    if (!p) return;

    const body = {{
      source_type: 'browser_extension',
      source_name: 'chrome-ext-' + (p.capture_type || 'network'),
      direction: 'network_intercept',
      provider: p.provider || 'unknown',
      host: p.host || location.hostname,
      path: p.path || '/',
      method: p.method || 'POST',
      model_name: p.model || null,
      status_code: p.status_code || null,
      latency_ms: p.latency_ms || null,
      headers_json: '{{}}',
      body_json: JSON.stringify({{
        request_body: p.request_body,
        response_body: p.response_body,
        url: p.url,
        is_streaming: p.is_streaming,
        response_content_type: p.response_content_type,
        confidence: p.confidence,
      }}),
      body_format: 'json',
      session_hint: location.pathname,
      meta: {{
        page_url: location.href,
        page_title: document.title,
        captured_at: new Date().toISOString(),
        capture_method: 'network_intercept',
        capture_type: p.capture_type,
        capture_id: p.capture_id,
      }},
    }};

    fetch('{PCE_API}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body),
    }}).catch(() => {{}});
  }});

  console.log('[PCE:e2e] Capture shim installed');
}})();
"""
    return shim

# ---------- 1. Launch Chrome ----------
print(f"Ext:     {EXT_DIR}")
print(f"Profile: {PROFILE}")

args = [
    CHROME,
    f"--user-data-dir={PROFILE}",
    f"--remote-debugging-port={PORT}",
    "--enable-extensions",
    f"--load-extension={EXT_DIR}",
    "--proxy-bypass-list=127.0.0.1;localhost",
    "--no-first-run", "--no-default-browser-check",
    "--disable-blink-features=AutomationControlled",
    "--remote-allow-origins=*",
    "--window-size=1280,900",
    "about:blank",
]
print("Launching Chrome...")
proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

# ---------- 2. Wait for CDP ----------
for _ in range(15):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
        break
    except:
        time.sleep(1)
else:
    print("FATAL: CDP not available"); proc.terminate(); exit(1)
print("CDP ready")

# ---------- 3. Connect Selenium ----------
opts = Options()
opts.debugger_address = f"127.0.0.1:{PORT}"
drv = webdriver.Chrome(service=Service(get_cd()), options=opts)
print(f"Selenium: Chrome {drv.capabilities.get('browserVersion', '?')}")

# ---------- 4. Install capture shim via addScriptToEvaluateOnNewDocument ----------
shim_js = build_inject_script()
drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": shim_js})
print(f"Capture shim installed ({len(shim_js)} chars)")

# ---------- 5. Navigate to DeepSeek ----------
print("\n--- Navigate to DeepSeek ---")
drv.get("https://chat.deepseek.com/")
time.sleep(8)
print(f"  Title: {drv.title}")

pce = drv.execute_script("""
    return {
        confirmed: !!window.__PCE_AI_PAGE_CONFIRMED,
        patterns: !!window.__PCE_AI_PATTERNS,
        interceptor: !!window.__PCE_INTERCEPTOR_ACTIVE,
        shim: !!window.__PCE_E2E_SHIM,
    }
""")
print(f"  PCE flags: {pce}")

# ---------- 6. Check PCE API for captures ----------
print("\n--- Check PCE API ---")
try:
    resp = urllib.request.urlopen("http://127.0.0.1:9800/api/v1/captures?limit=5", timeout=5)
    captures = json.loads(resp.read())
    print(f"  Recent captures: {len(captures) if isinstance(captures, list) else captures}")
except Exception as e:
    print(f"  PCE API error: {e}")

# ---------- Cleanup ----------
drv.quit()
proc.terminate()
print("\nDone.")
