# B3 — Local smoke test of the Webstore build

**Why you need this:** The `.output/chrome-mv3/` folder is exactly what
Chrome Web Store will serve to every user after approval. Anything that
breaks here breaks in production — and the review queue won't tell you
in time.

**Time budget:** 15 minutes.

**Pass criterion:** Four AI sites, four captures, zero console errors.

---

## 0. Preconditions

- [ ] PCE Core is running at `http://127.0.0.1:9800` (Dashboard loads in browser).
- [ ] You have accounts at: ChatGPT, Claude, Copilot, Notion (any one — more is better).
- [ ] Chrome is a recent version (MV3 required, so ≥ v88; current stable is fine).
- [ ] Previous PCE extensions are **uninstalled** (check `chrome://extensions/`) — otherwise two versions will both try to capture and muddy the results.

---

## 1. Install the unpacked Webstore build

1. Open `chrome://extensions/`.
2. Top-right: toggle **Developer mode** ON.
3. Click **Load unpacked**.
4. Pick the folder (not the zip): `f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\.output\chrome-mv3\`
5. Extension card appears. **Verify on the card:**
   - [ ] Name: `PCE - AI Interaction Capture`
   - [ ] Version: `1.0.1` (previous 1.0.0 was rejected — see `SUBMISSION-PLAYBOOK.md` §Step 0)
   - [ ] No red error badge
   - [ ] Click "Details" → scroll to "Permissions":
     - [ ] Shows the explicit site list — NOT "Read and change all your data on all websites"
     - [ ] If you see "all websites", the detector hook didn't fire — stop and tell Cascade.

---

## 2. Four-site capture run

For each site below: open, send ONE neutral message (e.g. "what's 2+2"),
wait for reply, then check the PCE dashboard.

### 2.1 ChatGPT

1. Open `https://chatgpt.com/` in a **fresh tab**.
2. Type a message, send it, wait for the reply to finish streaming.
3. Switch to `http://127.0.0.1:9800/` (PCE dashboard).
4. Check:
   - [ ] The new session appears in the list within ~5 s.
   - [ ] Click into it. Both your message and the assistant reply are visible.
   - [ ] Provider: ChatGPT / model name populated.
5. DevTools check (F12 on the ChatGPT tab → Console):
   - [ ] No red `[PCE]` errors.
   - [ ] Expected logs: `[PCE:bridge] ...`, `[PCE CHATGPT] ...`.

### 2.2 Claude

1. Open `https://claude.ai/new`.
2. Send a message, wait for reply.
3. Dashboard:
   - [ ] Session appears.
   - [ ] Both sides visible.
4. Console:
   - [ ] No errors.

### 2.3 Microsoft Copilot

1. Open `https://copilot.microsoft.com/`.
2. Send a message, wait for reply.
3. Dashboard:
   - [ ] Session appears.
   - [ ] Both sides visible.
4. Console:
   - [ ] No errors.
   - Copilot's DOM changes frequently — if the reply is captured but empty, note which selector missed; don't block the submission on this (known minor).

### 2.4 Notion AI (F2 — stretch check)

*Optional but recommended — this is new F2 scaffolding.*

1. Open any Notion page you can edit.
2. Press `/ai` → "Ask AI" → type a prompt → accept the insertion.
3. Dashboard:
   - [ ] A capture with provider `notion` appears.
   - If no capture: the selectors need live-page validation (this is a P5.A-9 scaffolding note). Log what you see and move on — don't block submission.

---

## 3. Permission prompt sanity check

Go back to `chrome://extensions/` → Details → Permissions:

- [ ] "This extension can read and change your data on these sites" → lists the 17 AI hosts.
- [ ] The list does NOT include `<all_urls>` or "all websites".
- [ ] No host appears that isn't in our `COVERED_SITES` array.

**This is the exact same list Chrome will show users at install time** after the submission is approved. If it contains anything unexpected, we need to fix before submitting.

---

## 4. Quick regression

- [ ] Click the PCE toolbar icon → popup opens without errors.
- [ ] Popup shows "Connected to PCE Core" (green dot).
- [ ] Right-click on any AI page → "Capture This Page (PCE)" menu item is there. (Note: the floating "Save" button that appears when you highlight text is a SEPARATE feature — that one is a DOM overlay, not a Chrome context menu.)
- [ ] Toggle extension OFF then ON in `chrome://extensions/` — reloading any AI tab still captures.

---

## 5. Record findings

Make a quick note on what worked / what didn't. Template:

```
2026-04-2X B3 smoke test — pce-browser-extension-wxt-1.0.1-chrome.zip
  ChatGPT ........ pass
  Claude ......... pass
  Copilot ........ pass (empty reply on model switcher, known)
  Notion AI ...... skip (selector needs live validation)
  Permission list  OK (17 hosts, no <all_urls>)
  DevTools console .. clean except one unrelated deprecation warning
```

Save this note somewhere; it goes into the P5.A-completion.md later.

---

## If something fails

**Extension won't load (red error on the card):**
Copy the error text. Most likely: a malformed manifest. Re-run the
build: `cd pce_browser_extension_wxt && pnpm build --mode webstore`.

**"All websites" permission shows up:**
The hook in `wxt.config.ts` didn't strip detector. Ask Cascade to
verify with: `Get-Content .output\chrome-mv3\manifest.json | Select-String '<all_urls>'`.

**No captures on ChatGPT:**
1. Is PCE Core actually on `127.0.0.1:9800`? `curl http://127.0.0.1:9800/api/v1/health`.
2. Open the extension service-worker DevTools:
   `chrome://extensions/` → PCE → "Service worker" → "Inspect".
3. Look for failed fetch() to the API.

**Captures show up but only user side, not assistant reply:**
DOM selectors need refresh for that specific site. Note it, don't block.
Fixable post-submission with a 1.0.1 patch; the store auto-delivers updates.
