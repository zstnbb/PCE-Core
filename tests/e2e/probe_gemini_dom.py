"""
Quick Gemini DOM probe - launches Chrome, navigates to Gemini,
and dumps the DOM structure of response elements for analysis.

Usage:
    python tests/e2e/probe_gemini_dom.py
    
Requires: Chrome with existing login session to gemini.google.com
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# Use existing Chrome profile to preserve login
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
# Use default profile for login persistence
USER_DATA_DIR = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data")
PROFILE = "Default"
DEBUG_PORT = 9234

EXT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "pce_browser_extension")
EXT_PATH = os.path.abspath(EXT_PATH)

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "gemini_rich_dom_probe.json")


def launch_chrome():
    """Launch Chrome with debugging port using existing profile."""
    # Use a copy of the profile to avoid locking issues
    temp_profile = os.path.join(tempfile.gettempdir(), "pce_gemini_probe_profile")
    if os.path.exists(temp_profile):
        shutil.rmtree(temp_profile, ignore_errors=True)
    
    # Copy just the Default profile
    os.makedirs(os.path.join(temp_profile, PROFILE), exist_ok=True)
    
    # Check if Chrome is already running with debug port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", DEBUG_PORT))
        sock.close()
        print(f"Chrome already running on debug port {DEBUG_PORT}")
        return None
    except ConnectionRefusedError:
        pass
    
    args = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={temp_profile}",
        f"--profile-directory={PROFILE}",
        "--enable-extensions",
        f"--load-extension={EXT_PATH}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Chrome launched (PID={proc.pid}), waiting for startup...")
    time.sleep(3)
    return proc


def connect_selenium():
    """Connect to Chrome via debugging port."""
    opts = Options()
    opts.debugger_address = f"127.0.0.1:{DEBUG_PORT}"
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def probe_dom(driver):
    """Probe Gemini's DOM for rich content structure."""
    
    probe_script = """
    const results = {
        timestamp: new Date().toISOString(),
        url: location.href,
        title: document.title,
        selectors: {},
        modelResponses: [],
        codeBlocks: [],
        citations: [],
        images: [],
        customElements: [],
        searchGrounding: [],
        thinkingPanels: [],
    };
    
    // Test selectors
    const sels = {
        "model-response": "model-response",
        "user-query": "user-query", 
        "message-content": "message-content",
        "code-block": "code-block",
        ".markdown": ".markdown",
        ".markdown-main-panel": ".markdown-main-panel",
        "pre": "pre",
        "pre > code": "pre > code",
        "code": "code",
        "a[href]": "a[href]",
        "img:not([width='1'])": "img:not([width='1'])",
        "details": "details",
        "summary": "summary",
        "[class*='citation']": "[class*='citation']",
        "[class*='source']": "[class*='source']",
        "[class*='grounding']": "[class*='grounding']",
        "[class*='search']": "[class*='search']",
        "[class*='tool']": "[class*='tool']",
        "[class*='think']": "[class*='think']",
        "[class*='code']": "[class*='code']",
        "[class*='file']": "[class*='file']",
        "[class*='footnote']": "[class*='footnote']",
        "[class*='response']": "[class*='response']",
        "[class*='query']": "[class*='query']",
        "[class*='turn']": "[class*='turn']",
        "[class*='message']": "[class*='message']",
        "[class*='markdown']": "[class*='markdown']",
        "[class*='chip']": "[class*='chip']",
        "[class*='attachment']": "[class*='attachment']",
        "[class*='upload']": "[class*='upload']",
        "[class*='image']": "[class*='image']",
        "[class*='generated']": "[class*='generated']",
        "[class*='canvas']": "[class*='canvas']",
        "[class*='artifact']": "[class*='artifact']",
    };
    
    for (const [label, sel] of Object.entries(sels)) {
        try {
            const els = document.querySelectorAll(sel);
            if (els.length > 0) {
                results.selectors[label] = {
                    count: els.length,
                    samples: Array.from(els).slice(0, 3).map(el => ({
                        tag: el.tagName?.toLowerCase(),
                        class: (el.className || "").toString().slice(0, 300),
                        text: (el.innerText || "").slice(0, 150),
                        childTags: Array.from(el.children).slice(0, 10).map(c => c.tagName?.toLowerCase()),
                        attrs: Array.from(el.attributes || []).slice(0, 10).map(a => a.name + "=" + String(a.value).slice(0, 80)),
                    })),
                };
            }
        } catch (e) {}
    }
    
    // Probe model-response elements in detail
    document.querySelectorAll("model-response").forEach((mr, i) => {
        if (i >= 3) return;
        const info = {
            index: i,
            outerHTML_head: mr.outerHTML.slice(0, 800),
            directChildren: Array.from(mr.children).map(c => ({
                tag: c.tagName?.toLowerCase(),
                class: (c.className || "").toString().slice(0, 300),
                childCount: c.children.length,
                text: (c.innerText || "").slice(0, 200),
            })),
            pre_count: mr.querySelectorAll("pre").length,
            code_count: mr.querySelectorAll("code").length,
            codeBlock_count: mr.querySelectorAll("code-block").length,
            img_count: mr.querySelectorAll("img").length,
            link_count: mr.querySelectorAll("a[href]").length,
            details_count: mr.querySelectorAll("details").length,
        };
        
        // Probe code blocks within this response
        mr.querySelectorAll("pre, code-block").forEach((cb, j) => {
            if (j >= 3) return;
            info["codeBlock_" + j] = {
                tag: cb.tagName?.toLowerCase(),
                class: (cb.className || "").toString().slice(0, 300),
                parentTag: cb.parentElement?.tagName?.toLowerCase(),
                parentClass: (cb.parentElement?.className || "").toString().slice(0, 200),
                hasCodeChild: !!cb.querySelector("code"),
                codeChildClass: (cb.querySelector("code")?.className || "").toString().slice(0, 200),
                text: (cb.innerText || "").slice(0, 300),
                outerHTML: cb.outerHTML.slice(0, 600),
                // Look for language indicators
                langAttr: cb.getAttribute("language") || cb.getAttribute("data-language") || "",
                prevSibText: (cb.previousElementSibling?.innerText || "").slice(0, 80),
                prevSibClass: (cb.previousElementSibling?.className || "").toString().slice(0, 200),
            };
        });
        
        // Probe links
        mr.querySelectorAll("a[href]").forEach((a, j) => {
            if (j >= 5) return;
            results.citations.push({
                inResponse: i,
                href: (a.href || "").slice(0, 300),
                text: (a.innerText || "").slice(0, 100),
                class: (a.className || "").toString().slice(0, 200),
                parentTag: a.parentElement?.tagName?.toLowerCase(),
                parentClass: (a.parentElement?.className || "").toString().slice(0, 200),
            });
        });
        
        results.modelResponses.push(info);
    });
    
    // Custom elements
    const customTags = new Set();
    document.querySelectorAll("*").forEach(el => {
        const tag = el.tagName?.toLowerCase() || "";
        if (tag.includes("-")) customTags.add(tag);
    });
    results.customElements = Array.from(customTags).sort();
    
    return results;
    """
    
    return driver.execute_script(probe_script)


def send_test_message(driver, message):
    """Send a message to Gemini."""
    try:
        # Find input field
        input_sels = [
            '.ql-editor[contenteditable="true"]',
            'rich-textarea .ql-editor',
            '[contenteditable="true"][role="textbox"]',
            '.text-input-field textarea',
        ]
        
        input_el = None
        for sel in input_sels:
            try:
                input_el = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                if input_el:
                    break
            except:
                continue
        
        if not input_el:
            print("ERROR: Could not find input field")
            return False
        
        input_el.click()
        time.sleep(0.3)
        input_el.send_keys(message)
        time.sleep(0.5)
        
        # Find and click send button
        send_sels = [
            'button[aria-label*="Send" i]',
            'button.send-button',
            'button[mattooltip*="Send" i]',
        ]
        
        sent = False
        for sel in send_sels:
            try:
                btns = driver.find_elements(By.CSS_SELECTOR, sel)
                for btn in btns:
                    if btn.is_displayed():
                        btn.click()
                        sent = True
                        break
                if sent:
                    break
            except:
                continue
        
        if not sent:
            input_el.send_keys(Keys.ENTER)
        
        print(f"Message sent: {message[:60]}")
        return True
    except Exception as e:
        print(f"Send failed: {e}")
        return False


def wait_for_response(driver, timeout=45):
    """Wait for Gemini to finish generating."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            # Check for stop button (streaming)
            stops = driver.find_elements(By.CSS_SELECTOR, 
                'button[aria-label*="Stop" i], button[class*="stop"]')
            streaming = any(s.is_displayed() for s in stops)
            if not streaming:
                # Check if we have a response
                responses = driver.find_elements(By.CSS_SELECTOR, 
                    'model-response, .model-response-text, message-content')
                if responses:
                    time.sleep(2)  # Settle
                    return True
        except:
            pass
        time.sleep(1)
    return False


def main():
    chrome_proc = None
    driver = None
    
    try:
        print("=" * 60)
        print("Gemini DOM Rich Content Probe")
        print("=" * 60)
        
        # Launch or connect to Chrome
        chrome_proc = launch_chrome()
        time.sleep(2)
        
        driver = connect_selenium()
        print(f"Connected to Chrome, current URL: {driver.current_url}")
        
        # Navigate to Gemini
        if "gemini.google.com" not in driver.current_url:
            driver.get("https://gemini.google.com/app")
            time.sleep(5)
        
        print(f"On: {driver.current_url}")
        print(f"Title: {driver.title}")
        
        # Check if logged in (look for input field)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 
                    '.ql-editor, [contenteditable="true"], rich-textarea'))
            )
            print("✓ Input field found (logged in)")
        except:
            print("✗ No input field found - may need to log in manually")
            print("  Please log in at gemini.google.com and re-run this script")
            input("  Press Enter after logging in...")
        
        # First, probe the current page
        print("\n--- Probing current page ---")
        results = probe_dom(driver)
        
        if not results.get("modelResponses"):
            # No responses yet, send a test message that triggers code + citations
            print("\nNo responses found. Sending test message with code request...")
            
            test_msg = 'Write a Python function that calculates fibonacci numbers, and explain the time complexity. Include links to relevant resources.'
            
            if send_test_message(driver, test_msg):
                print("Waiting for response...")
                if wait_for_response(driver, timeout=60):
                    print("✓ Response received, probing DOM...")
                    time.sleep(2)
                    results = probe_dom(driver)
                else:
                    print("✗ Response timeout")
        
        # Save results
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n✓ Results saved to {OUTPUT_FILE}")
        
        # Print summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        
        print(f"\nMatching selectors ({len(results.get('selectors', {}))} found):")
        for sel, info in results.get("selectors", {}).items():
            print(f"  {sel}: {info['count']} elements")
        
        print(f"\nModel responses: {len(results.get('modelResponses', []))}")
        for mr in results.get("modelResponses", []):
            print(f"  Response {mr['index']}:")
            print(f"    pre: {mr.get('pre_count', 0)}, code: {mr.get('code_count', 0)}, " +
                  f"code-block: {mr.get('codeBlock_count', 0)}, img: {mr.get('img_count', 0)}, " +
                  f"links: {mr.get('link_count', 0)}, details: {mr.get('details_count', 0)}")
            for key in mr:
                if key.startswith("codeBlock_"):
                    cb = mr[key]
                    print(f"    {key}: tag={cb['tag']}, lang={cb.get('langAttr','')}, " +
                          f"hasCode={cb['hasCodeChild']}")
        
        print(f"\nCitations: {len(results.get('citations', []))}")
        for c in results.get("citations", [])[:5]:
            print(f"  [{c.get('text', '')[:40]}] -> {c.get('href', '')[:80]}")
        
        print(f"\nCustom elements: {', '.join(results.get('customElements', []))}")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            # Don't close - let user inspect
            print("\nBrowser left open for inspection. Close manually when done.")


if __name__ == "__main__":
    main()
