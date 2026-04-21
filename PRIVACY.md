# PCE Privacy Policy

**Effective date:** 2026-04-21
**Applies to:** PCE browser extension (Chrome, Firefox) and PCE Core desktop application, version 1.0.0 and later.

## TL;DR

- PCE is **local-first**. All data stays on your machine.
- The extension sends captured AI conversations **only** to a PCE Core instance running on your own computer at `http://127.0.0.1:9800`.
- We operate **no backend server**, collect **no telemetry**, and use **no third-party analytics or advertising SDKs**.
- Source code is open (Apache-2.0). You can audit exactly what is transmitted.

## 1. Who runs PCE

PCE is an open-source project. This Privacy Policy is maintained by the project owner:

- **Publisher:** zstnbb (individual)
- **Repository:** https://github.com/zstnbb/PCE-Core
- **Contact:** See the `SECURITY.md` and `CONTRIBUTING.md` files in the repository for current contact addresses.

PCE is **not** operated as a hosted service. The "publisher" maintains the open-source code; it does not host or receive any user data.

## 2. What the browser extension does

The PCE browser extension is a passive observer. When you visit a supported AI site (ChatGPT, Claude, Gemini, Copilot, Notion AI, M365 Copilot, Figma AI, Gmail "Help me write", and others listed in the extension manifest), it:

1. Reads the conversation DOM (messages you sent and replies you received).
2. Reads page metadata needed to identify the conversation (URL, page title, provider name).
3. Optionally records lightweight user-interaction signals (send-button clicks, Enter keypresses, scroll position) to improve the timing of captures. These signals are **aggregated locally into the same capture payload** and never sent elsewhere.
4. Forwards the captured payload to the PCE Core server running locally on your computer at `http://127.0.0.1:9800`.

If PCE Core is not running on your machine, the extension stores the captures in a local in-browser IndexedDB queue and forwards them later when PCE Core becomes reachable. Queued data never leaves your browser.

## 3. What data is collected

| Category | Collected? | Where it goes | Notes |
|---|---|---|---|
| Conversation content (messages, replies, code blocks, attachments) | Yes, on covered AI sites | Your local PCE Core (`127.0.0.1:9800`) | Content may include anything you type into the AI — treat it as sensitive. |
| Page URL and title of the AI session | Yes | Your local PCE Core | Used to group messages into sessions. |
| Provider / model name | Yes | Your local PCE Core | Extracted from DOM or page metadata. |
| User interaction events (clicks, key-sends, scroll) on covered sites | Yes | Your local PCE Core | Aggregated into capture timing metadata. |
| Extension settings (PCE Core URL, capture mode) | Yes | `chrome.storage` (in-browser, local) | Never transmitted. |
| Your browsing history on sites **not** listed in the manifest | **No** | — | The extension does not run on unlisted sites. |
| Personally identifiable information (name, email, phone) | **Not intentionally** | — | If you type such information into an AI prompt, it will be part of the captured content on your own machine. |
| Authentication tokens, cookies, passwords | **No** | — | The extension reads DOM text, not browser credential storage. |
| Location, device identifiers, advertising IDs | **No** | — | Not read. |
| Crash reports, usage analytics, heartbeat pings | **No** | — | PCE transmits nothing to any server we operate. |

## 4. Where your data is stored

All captured data is stored in a SQLite database on your local machine, managed by the PCE Core desktop application. The default location is:

- **Windows:** `%USERPROFILE%\.pce\data\pce.db`
- **macOS / Linux:** `~/.pce/data/pce.db`

You can relocate the database by setting the `PCE_DATA_DIR` environment variable.

The database is **not encrypted at rest by default**. If you want disk-level protection, use your operating system's full-disk encryption (FileVault, BitLocker, LUKS).

## 5. Who the data is shared with

**Nobody, by default.**

- We operate no backend. The extension cannot send data to us even if it wanted to — there is no server to send it to.
- The extension's only network destination is `http://127.0.0.1:9800`, the loopback address on your own machine.
- PCE Core does not transmit your data to any third party.
- Optional integrations (e.g., OpenTelemetry export) are **off by default** and only activate if you explicitly configure an endpoint you control.

If you export data from PCE Core manually (e.g., JSON export, clipboard copy), that becomes your responsibility. PCE does not do this automatically.

## 6. Permissions used by the extension and why

| Permission | Why PCE needs it |
|---|---|
| `storage` | Persist extension settings (PCE Core URL, enabled/disabled state) locally in the browser. |
| `activeTab` | Read the URL of the tab you are currently viewing to detect whether it is a supported AI site. |
| `scripting` | Inject the universal extractor into AI pages that are covered but don't have a dedicated extractor yet. |
| `tabs` | Detect navigation events so captures are routed to the correct session. |
| `contextMenus` | Provide the "Save selection as snippet" right-click menu. |
| `host_permissions` (list of AI sites) | Run content scripts on those specific sites to read conversation DOM. Only AI sites are listed; the Webstore build does **not** request `<all_urls>`. |

## 7. Children's privacy

PCE is not directed at children under 13 and does not knowingly collect information from children. If you are under 13, please do not use PCE.

## 8. Your controls

- **Disable capture temporarily:** Click the PCE toolbar icon → "Pause capture".
- **Stop all capture permanently:** Uninstall the browser extension or close the PCE Core desktop app. With PCE Core closed, queued data sits in your browser's IndexedDB until you either restart PCE Core or clear the extension's storage via `chrome://extensions/`.
- **Delete all captured data:** Delete the `~/.pce/data/` folder on your machine.
- **Inspect the source code:** https://github.com/zstnbb/PCE-Core is public and licensed under Apache-2.0.

## 9. Changes to this policy

We will update this file in the repository when the behavior of the extension or PCE Core changes in a way that affects privacy. The effective date at the top reflects the last substantive change. Historic versions are visible in the git history of `PRIVACY.md`.

## 10. Contact

For privacy questions or concerns, please open an issue at https://github.com/zstnbb/PCE-Core/issues, or email the address listed in `SECURITY.md` in the repository.
