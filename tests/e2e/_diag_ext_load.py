# SPDX-License-Identifier: Apache-2.0
"""Diagnostic — launch Chrome directly and check if the WXT extension loads.

Bypasses Selenium's Chromedriver to rule out arg-manipulation issues.
Uses raw subprocess.Popen + CDP WebSocket to eval window flags.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket  # from websocket-client; selenium depends on it

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXT = PROJECT_ROOT / "pce_browser_extension_wxt" / ".output" / "chrome-mv3"
CHROME = os.environ.get(
    "PCE_DIAG_BROWSER",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
PORT = 9223


def find_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if not EXT.is_dir():
        print("ERR: extension dir missing:", EXT)
        return 2
    profile = Path(tempfile.mkdtemp(prefix="pce-diag-"))
    port = find_free_port()
    args = [
        CHROME,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--enable-extensions",
        f"--load-extension={EXT}",
        "--disable-features=DisableLoadExtensionCommandLineSwitch",
        "about:blank",
    ]
    print("Launching Chrome:", " ".join(args))
    log_path = Path(tempfile.gettempdir()) / "pce-diag-chrome.log"
    log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        args + [
            # Chrome logs go to stderr by default; force structured log.
            "--enable-logging=stderr",
            "--v=1",
        ],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print(f"Chrome stderr/stdout -> {log_path}")
    try:
        # Wait for the debugger
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                r = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1
                )
                r.read()
                break
            except Exception:
                time.sleep(0.3)
        else:
            print("ERR: debugger never came up")
            return 2

        # Let the extension register
        time.sleep(3)

        # List targets
        tabs = json.loads(
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=3
            ).read()
        )
        ext_targets = [t for t in tabs if t.get("type") == "background_page"
                       or t.get("type") == "service_worker"
                       or (t.get("url", "").startswith("chrome-extension://"))]
        print(f"\nTargets ({len(tabs)} total, {len(ext_targets)} extension):")
        for t in tabs:
            print(f"  - {t.get('type','?'):<18} {t.get('url','')[:100]}")

        # Pick the about:blank page to navigate
        page = next(
            (t for t in tabs if t.get("type") == "page"), None
        )
        if not page:
            print("ERR: no page target to navigate")
            return 2
        ws_url = page["webSocketDebuggerUrl"]
        ws = websocket.create_connection(ws_url, timeout=5)

        def cmd(method, params=None, msg_id=[0]):
            msg_id[0] += 1
            ws.send(json.dumps({
                "id": msg_id[0],
                "method": method,
                "params": params or {},
            }))
            while True:
                resp = json.loads(ws.recv())
                if resp.get("id") == msg_id[0]:
                    return resp

        # Enable runtime + page
        cmd("Page.enable")
        cmd("Runtime.enable")

        # Navigate to gemini
        print("\nNavigating to https://gemini.google.com/app ...")
        cmd("Page.navigate", {"url": "https://gemini.google.com/app"})
        time.sleep(6)  # let content scripts + page load settle

        # Eval window.__PCE_* flags
        expr = (
            "(() => {"
            "  const keys = Object.keys(window).filter(k => k.startsWith('__PCE'));"
            "  const out = {};"
            "  for (const k of keys) out[k] = typeof window[k] === 'object' ? 'obj' : String(window[k]);"
            "  out.url = location.href;"
            "  out.title = document.title;"
            "  out.__data_pce_ai_confirmed = document.documentElement.getAttribute('data-pce-ai-confirmed');"
            "  return JSON.stringify(out);"
            "})()"
        )
        resp = cmd(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True},
        )
        val = resp.get("result", {}).get("result", {}).get("value")
        print("\nPage state:", val)

        ws.close()
        return 0
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(1)
        try:
            proc.kill()
        except Exception:
            pass
        import shutil
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
