# PCE Browser Extension

Chrome Manifest V3 extension that captures AI conversations from web-based AI tools.

## Supported Sites

| Site | Provider | Content Script |
|------|----------|---------------|
| ChatGPT (chatgpt.com) | openai | `chatgpt.js` |
| Claude (claude.ai) | anthropic | `claude.js` |
| Gemini (gemini.google.com) | google | `generic.js` |
| DeepSeek (chat.deepseek.com) | deepseek | `generic.js` |
| Perplexity (perplexity.ai) | perplexity | `generic.js` |
| Grok (grok.com) | xai | `generic.js` |
| Poe (poe.com) | poe | `generic.js` |

## How It Works

1. Content scripts inject into AI chat pages via `MutationObserver`
2. When new messages appear, the script extracts conversation content from the DOM
3. Extracted data is sent to the background service worker via `chrome.runtime.sendMessage`
4. The service worker POSTs to the local PCE Ingest API at `http://127.0.0.1:9800/api/v1/captures`
5. PCE stores the capture and auto-normalizes it into sessions/messages

## Prerequisites

- PCE Core server running locally: `python -m pce_core`
- Chrome or Chromium-based browser

## Installation (Development)

1. Start PCE Core server:
   ```
   cd "PCE Core"
   python -m pce_core
   ```

2. Open Chrome → `chrome://extensions/`
3. Enable **Developer mode** (top right)
4. Click **Load unpacked** → select the `pce_browser_extension/` folder
5. The PCE icon should appear in your toolbar

## Architecture

```
popup/              → Status panel (server status, capture count, enable/disable)
background/         → Service worker (receives messages, POSTs to Ingest API)
content_scripts/    → DOM observers (site-specific + generic)
icons/              → Extension icons
```

## Notes

- All data stays local – the extension only communicates with `127.0.0.1:9800`
- Captures are debounced (2-3s) to avoid excessive writes during streaming
- Duplicate detection via text fingerprinting prevents re-capturing the same state
- If the PCE server is offline, captures are silently dropped (fail-open)
