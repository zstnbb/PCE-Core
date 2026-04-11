"""Reload extension + verify Kimi segment selector extraction."""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

opts = Options()
opts.debugger_address = "127.0.0.1:42888"
svc = Service(executable_path=r"C:\Users\ZST\.wdm\drivers\chromedriver\win64\146.0.7680.165\chromedriver-win32\chromedriver.exe")
d = webdriver.Chrome(service=svc, options=opts)

# Step 1: Reload extension
print("=== Reloading extension ===")
d.execute_script("window.open()")
d.switch_to.window(d.window_handles[-1])
d.get("chrome://extensions/")
time.sleep(2)

JS_RELOAD = r"""
const mgr = document.querySelector("extensions-manager");
if (!mgr || !mgr.shadowRoot) return "no extensions-manager";
const toolbar = mgr.shadowRoot.querySelector("extensions-toolbar");
if (toolbar && toolbar.shadowRoot) {
    const toggle = toolbar.shadowRoot.querySelector("#devMode");
    if (toggle && !toggle.checked) toggle.click();
}
const itemList = mgr.shadowRoot.querySelector("extensions-item-list");
if (!itemList || !itemList.shadowRoot) return "no item-list";
const items = itemList.shadowRoot.querySelectorAll("extensions-item");
for (const item of items) {
    const sr = item.shadowRoot;
    if (!sr) continue;
    const nameEl = sr.querySelector("#name");
    const nameText = nameEl ? nameEl.textContent.trim() : "";
    if (nameText.toLowerCase().includes("pce")) {
        const btn = sr.querySelector("#dev-reload-button");
        if (btn) { btn.click(); return "RELOADED: " + nameText; }
        return "FOUND but no reload btn: " + nameText;
    }
}
return "NOT FOUND";
"""
result = d.execute_script(JS_RELOAD)
print(f"  {result}")
time.sleep(3)
try:
    d.close()
except Exception:
    pass

# Step 2: Navigate to existing Kimi conversation
time.sleep(1)
for h in d.window_handles:
    try:
        d.switch_to.window(h)
        if "kimi" in d.current_url:
            break
    except Exception:
        continue

conv_url = "https://www.kimi.com/chat/19d77af5-8142-8129-8000-09bdd1b0de88"
print(f"\n=== Navigating to conversation ===")
d.execute_script(f"window.location.href = '{conv_url}';")
time.sleep(7)
print(f"URL: {d.current_url}")

# Step 3: Test the new Kimi selector
print("\n=== Testing .segment.segment-user, .segment.segment-assistant ===")
sel = ".segment.segment-user, .segment.segment-assistant"
els = d.find_elements(By.CSS_SELECTOR, sel)
for e in els:
    cls = (e.get_attribute("class") or "")[:60]
    txt = (e.text or "")[:80].replace("\n", " ")
    role = "user" if "user" in cls else "assistant" if "assistant" in cls else "unknown"
    print(f"  [{role}] cls='{cls}' text='{txt}'")

print(f"\nTotal segments: {len(els)}")
print("Done.")
