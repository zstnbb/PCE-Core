# Chrome Web Store — Screenshot Checklist (v1.0.0)

You (the human) need to produce these images. Cascade can't — they
require running the real product and interacting with real AI sites.

Budget: **30–60 minutes** once PCE Core is running.

Output location: `Docs/store/screenshots/`  (create the folder, save
the PNGs with the exact filenames below). These files stay in the repo
as the canonical source — if Chrome ever asks for refreshed images,
we re-upload from here.

---

## Required image inventory

Chrome Web Store asks for:

| Slot | Dimensions | Required? | Filename |
|---|---|---|---|
| Screenshot 1 | 1280×800 or 640×400 (prefer 1280×800) | **required**, max 5 | `01-dashboard-overview.png` |
| Screenshot 2 | 1280×800 | strongly recommended | `02-live-capture-chatgpt.png` |
| Screenshot 3 | 1280×800 | strongly recommended | `03-session-detail.png` |
| Screenshot 4 | 1280×800 | strongly recommended | `04-snippet-save.png` |
| Screenshot 5 | 1280×800 | recommended | `05-onboarding-wizard.png` |
| Small promo tile | 440×280 | optional but boosts placement | `promo-small-440x280.png` |
| Marquee promo tile | 1400×560 | optional, needed for "Featured" | `promo-marquee-1400x560.png` |
| Icon | 128×128 PNG | **already in repo** | `public/icons/icon128.png` |

Listings with 4-5 screenshots convert 3-5× better than listings with 1.
**Do all five.**

---

## Setup before taking screenshots

1. **Run PCE Core** (`python pce.py` or the built exe) — verify
   dashboard loads at `http://127.0.0.1:9800`.
2. **Install the extension** in dev mode from `.output/chrome-mv3/`
   (we'll produce this in step B1).
3. **Have real-but-safe content** to capture:
   - Use a fresh ChatGPT conversation with a neutral topic (e.g.,
     "explain how a CPU cache works"). **Do not screenshot anything
     with real names, emails, API keys, or personal data.**
   - Ask the AI 2-3 follow-up questions so the session looks active.
4. **Browser setup for clean screenshots:**
   - Open an Incognito window with extensions allowed (or a fresh
     Chrome profile) so the top-right doesn't show a dozen other
     extension icons.
   - Zoom level: 100%.
   - Window size: exactly 1280×800. In Chrome DevTools → device
     toolbar → "Dimensions: Responsive" → type 1280 x 800 → "No
     throttling". Or use a tool like `windowResizer`.
5. **Crop to exactly 1280×800** before saving. Chrome Web Store
   rejects off-size images silently (stretches them).

---

## Screenshot composition — what each should show

### 01 — dashboard-overview.png (HERO SHOT)

**Goal:** In 1 second a visitor understands "this is a local archive
of my AI chats".

**What to show:**
- Full PCE dashboard at `127.0.0.1:9800`.
- Left nav visible (Captures / Sessions / Snippets / Onboarding / Settings).
- Main area: list of recent sessions with provider icons (ChatGPT,
  Claude, Copilot badges visible).
- Top-right corner health widget showing green "Connected" status.
- At least 5-8 session rows with readable titles.

**Overlay text** (optional, in an image editor): a single caption at
top like _"Every AI conversation. Searchable. On your machine."_

**What to avoid:**
- Empty/loading states.
- Real usernames or email addresses.
- Any error toasts or "Update available" banners.

### 02 — live-capture-chatgpt.png

**Goal:** Prove the capture is happening live on a real AI site.

**What to show:**
- Left half: ChatGPT web UI with a conversation in progress (1 user
  message, 1 assistant reply visible).
- Right half: PCE dashboard → same session, showing the two messages
  have already arrived (with timestamp matching the ChatGPT side).
- Browser window side-by-side split, OR two overlapping windows with
  the PCE one on top-right.

**Effect:** viewer sees the same message appearing in two places —
instant "I understand what this does".

### 03 — session-detail.png

**Goal:** Show the value prop — full searchable conversation with
metadata.

**What to show:**
- PCE dashboard → click a specific session → detail view.
- Message list with user/assistant alternation.
- Metadata sidebar: provider, model, start time, message count, token
  count (if visible).
- One message expanded showing code block with syntax highlighting
  (makes the UI look capable).

### 04 — snippet-save.png

**Goal:** Demonstrate the right-click-save feature (unique to PCE,
differentiator from browser history tools).

**What to show:**
- Open AI conversation (ChatGPT or Claude) with some text selected.
- The PCE floating "Save to PCE snippets" button visible on top of
  the selection OR the right-click context menu open with the PCE
  entry highlighted.
- Small toast in corner: "Saved to snippets" (if you can time the
  capture to that moment).

### 05 — onboarding-wizard.png

**Goal:** Show that setup is simple (counters the "proxy setup sounds
scary" objection).

**What to show:**
- Dashboard `/onboarding` page.
- 4-step checklist with 2-3 steps already green-check'd:
  1. ✅ PCE Core running
  2. ✅ Browser extension installed
  3. ⚪ Visit a supported AI site
  4. ⚪ Send your first message
- Bottom status bar: "Ready in 2 more steps".

---

## Promotional tiles (optional but high-ROI)

### promo-small-440x280.png

Shown as a tile in the "Productivity" category browse page. Text on
this tile is tiny — keep it to:

- PCE logo (top-left, ~60px)
- Big number: **"17 AI sites"** or **"Local-first"**
- Single benefit line: **"Your AI history. Your machine."**
- Background: subtle gradient or a blurred screenshot of the dashboard.

### promo-marquee-1400x560.png

Used if Google picks PCE for "Editor's choice" or category feature.
Wider canvas, more room for composition. Same brand elements but
larger + a clear CTA visual (e.g., an arrow from a ChatGPT window
into a local database icon).

---

## Tools

- **Built-in OS tools:** Windows Snipping Tool (Win+Shift+S), macOS
  Screenshot.app (Cmd+Shift+4).
- **Browser dev-tools capture:** Chrome DevTools → three-dot menu →
  "Run command" → "Capture full size screenshot". This is best for
  capturing the dashboard at exactly 1280×800 without window chrome.
- **Editor for annotations + resizing:** Figma (free tier) or
  Photopea (free web-based Photoshop clone) at photopea.com.
- **Avoid:** Lightshot/Greenshot overlays that add watermarks.

---

## Pre-submission self-check

Before uploading, verify each PNG:

- [ ] Exactly 1280×800 pixels (use `identify` or any image viewer's
      "properties").
- [ ] File size < 5 MB each (Chrome Web Store hard limit).
- [ ] No PII anywhere (usernames, emails, API keys, real names in
      captures).
- [ ] No out-of-focus / blurry text (screenshots of screenshots are
      an immediate visual red flag).
- [ ] Consistent browser chrome (same theme, same window mode) across
      all 5 — reviewers notice inconsistency.
- [ ] The first screenshot is the hero — it's the only one shown on
      search result pages.
