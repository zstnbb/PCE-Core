# Chrome Web Store — Review Justifications (v1.0.0)

Copy-paste targets for the Developer Dashboard "Privacy practices" tab.
Every field below corresponds to one form field Chrome Web Store shows
when you submit a new extension.

Google's reviewers read these verbatim. They are not marketing copy;
they are compliance statements. Keep them short, concrete, and
testable — "we need X because it lets us do Y on site Z" is good;
"we need X for enhanced user experience" will get rejected.

---

## 1. Single purpose

Chrome requires a one-sentence statement of what the extension does.
Max effective length ~150 chars (no hard cap, but brevity helps).

```
Capture AI chat conversations from supported AI sites and forward them to a PCE Core instance running locally on the user's own computer.
```

---

## 2. Permission justifications (one per permission)

For each permission the manifest declares, the form requires a
1–2 sentence justification. **The reviewer compares this against the
actual code paths that use the permission** — don't claim a use the
code doesn't back up.

### `storage`

```
Persist user settings — the PCE Core URL, capture on/off state, and site-level enablement — in chrome.storage.local so they survive browser restarts. No chat content is stored via this permission.
```

### `activeTab`

```
Read the URL of the tab the user is currently viewing to determine whether it is a supported AI site and whether capture should be active. Used only in response to the toolbar action and the detector content script.
```

### `scripting`

```
Inject the universal extractor (entrypoints/universal-extractor.ts) into supported AI pages that do not have a dedicated site extractor yet. This keeps the extension functional on new AI UIs without requiring a new release for every minor DOM change.
```

### `tabs`

```
Receive tab update events so the background service worker can route captures to the correct conversation session when the user navigates between conversations in the same tab.
```

### `contextMenus`

```
Provide a right-click menu item "Save selection as snippet" on supported AI sites, letting users keep specific quotes from a conversation for later reference.
```

---

## 3. Host permissions justification (the one reviewers care most about)

Because PCE requests a list of 17+ AI hosts, this field gets the most
scrutiny. Keep it concrete — enumerate the categories and tie each to
a capability the extension actually ships.

```
The extension needs to read conversation DOM on AI chat sites to capture messages. Each host on the list is a supported AI tool:

— Dedicated AI chat UIs (14 hosts): chatgpt.com, chat.openai.com (legacy ChatGPT domain, kept for 301-redirect handling on bookmarked URLs), claude.ai, gemini.google.com, aistudio.google.com, copilot.microsoft.com, chat.deepseek.com, perplexity.ai, poe.com, huggingface.co/chat, grok.com, chat.mistral.ai, kimi.com + kimi.moonshot.cn (Kimi's current and legacy domains), chat.z.ai (Zhipu), manus.im.

— AI features embedded in productivity tools (4 hosts): m365.cloud.microsoft + *.cloud.microsoft + *.officeapps.live.com (Microsoft 365 Copilot across Word/Excel/PowerPoint/Outlook), notion.so (Notion AI), figma.com (Figma AI), mail.google.com (Gmail "Help me write").

The extension does NOT request <all_urls>, and it does not run on any site outside this list. Captured data is sent exclusively to http://127.0.0.1:9800 on the user's own machine — never to any server operated by us.
```

---

## 4. Remote code

The form asks: "Are you using remote code?"

```
No. All JavaScript executed by the extension is bundled at build time. No eval, no new Function, no dynamic <script src="https://..."> from outside the extension package. The page-context interceptor scripts (interceptor-*.js) are bundled inside the extension and injected via chrome.runtime.getURL, not fetched from any remote origin.
```

---

## 5. Data usage disclosures

The Dashboard shows a matrix of data categories; tick the boxes that
apply and confirm each with the statements below. **These choices
govern what users see on the install-time permission dialog**, so
under-disclosing is a policy violation.

### Categories to tick

| Category | Tick? | Rationale |
|---|---|---|
| Personally identifiable information | **No** | Extension does not intentionally collect name/email/phone. Users may type such information into AI prompts, but that content flows only to the user's own local database. |
| Health information | No | Not collected. |
| Financial / payment information | No | Not collected. |
| Authentication information | No | Does not read cookies, tokens, or credentials. |
| Personal communications | **Yes** | Conversation content captured from AI chat sites may contain personal messages. |
| Location | No | Not read. |
| Web history | No | Only runs on listed AI sites; does not track general browsing. |
| User activity | **Yes** | Click events and send-button presses on AI sites are read to correlate capture timing with user actions. |
| Website content | **Yes** | Reads conversation DOM on AI chat sites. |

### Required compliance certifications

All three checkboxes must be ticked before submission:

- [x] **I do not sell or transfer user data to third parties**, apart from the approved use cases. *(True — extension sends data only to localhost.)*
- [x] **I do not use or transfer user data for purposes unrelated to my item's single purpose.** *(True — data only flows to PCE Core; no analytics, no ads.)*
- [x] **I do not use or transfer user data to determine creditworthiness or for lending purposes.** *(True — obviously.)*

---

## 6. Privacy policy URL

```
https://github.com/zstnbb/PCE-Core/blob/master/PRIVACY.md
```

(Valid the moment the `PCE-Core` repo is flipped to public. Until then,
the Dashboard will show a 404-warning — submit anyway, then flip the
repo public before the reviewer actually clicks through. Review times
of 1–3 weeks give plenty of slack for this.)

---

## 7. What to do if the reviewer asks for clarification

Typical review questions and suggested responses. All of these have
happened to other extensions; none are unique to PCE.

### "Why do you need `<all_urls>`?"

```
The Webstore build does NOT request <all_urls>. It requests a list of 17 specific AI hosts. The <all_urls> pattern appears only in `web_accessible_resources.matches`, which is the syntax for declaring which sites may load extension-internal files — it does not grant the extension any read/inject capability on those sites. Chrome does not gate web_accessible_resources exposure behind host_permissions; the <all_urls> match there simply allows the AI sites the extension already runs on to load the bundled interceptor.
```

### "What does PCE Core do with the captured data?"

```
PCE Core is a separate open-source desktop application written in Python. It stores the captures in a SQLite database on the user's local file system (default: ~/.pce/data/pce.db) and exposes a web dashboard at http://127.0.0.1:9800 for searching the history. Source: https://github.com/zstnbb/PCE-Core.
```

### "Why does the extension capture DOM content instead of using an official API?"

```
The supported AI providers (OpenAI, Anthropic, Google, Microsoft, etc.) do not offer public APIs that expose the user's own in-browser chat history. The only way a user can archive their conversations locally without relying on each vendor's export policy is to read the DOM as the conversation happens. Users are in full control — they can disable capture per-site, pause it entirely, or uninstall the extension.
```

### "Is the capture logged on a remote server?"

```
No. The extension's sole network destination is http://127.0.0.1:9800 (loopback). When PCE Core is not running, captures are queued in the browser's IndexedDB and forwarded when the local server becomes reachable. Nothing leaves the user's machine. Confirmed by inspecting the bundled service worker at entrypoints/background.ts — all fetch() calls use a configurable base URL that defaults to 127.0.0.1 and cannot be pointed at a remote host without the user explicitly changing it via the settings UI.
```
