# Chrome Web Store — Submission Playbook (v1.0.0)

**Purpose:** Single ordered checklist for getting PCE extension 1.0.0
live on the Chrome Web Store. Every step is either (a) a file already
in this repo that you copy-paste from, or (b) a click-through in the
Chrome Web Store Developer Dashboard.

**Who runs this:** You, on your developer account. Cascade has already
produced every artefact referenced below; the remaining work is in the
browser + Dashboard.

**Expected elapsed time:**
- Day 1 (today): steps 1–3 below, ~2 hours including screenshots.
- Day 2 onwards: wait for Google review. Status visible in the Dashboard.
- Approval median: 3–7 days for standard review, 1–3 weeks if
  Extended Review triggers.

---

## State at the moment you read this

| Artefact | Status | Location |
|---|---|---|
| Webstore zip (994 KB) | ✅ Built | `pce_browser_extension_wxt/.output/pce-browser-extension-wxt-1.0.0-chrome.zip` |
| Webstore unpacked folder (for B3 test) | ✅ Built | `pce_browser_extension_wxt/.output/chrome-mv3/` |
| Manifest audited | ✅ 0 `<all_urls>` in content_scripts | — |
| PRIVACY.md | ✅ Written | `PRIVACY.md` (repo root) |
| Store listing copy (EN + ZH) | ✅ Written | `Docs/store/listing.md` |
| Permission justifications | ✅ Written | `Docs/store/justification.md` |
| Screenshot checklist | ✅ Written | `Docs/store/screenshots.md` (user takes PNGs) |
| Local smoke test checklist | ✅ Written | `Docs/store/local-smoke-test.md` (user runs) |
| Developer account | ✅ Registered by you | chrome.google.com/webstore/devconsole |
| Identity verification | ❓ Check | Dashboard → Account tab |
| Screenshots (PNGs) | ❌ Pending | `Docs/store/screenshots/` (you produce) |
| Dashboard submission | ❌ Pending | You upload zip + fill forms |

---

## Step 1 — Pre-flight (15 min, before touching Dashboard)

### 1.1 Confirm identity verification is DONE on your developer account

1. Go to https://chrome.google.com/webstore/devconsole/.
2. Left nav → **Account**.
3. Look for "Payments profile verified" and "Identity verified" status.
4. If not verified: upload the documents Google asks for (government ID
   or phone-based verification, depending on your region). **This can
   take up to 2 business days** and blocks the submission — do it now
   even before screenshots are ready.

### 1.2 Run the B3 local smoke test

Follow `Docs/store/local-smoke-test.md` end to end. Estimated 15 min.

Pass criterion: 4 AI sites captured, 0 `<all_urls>` permission prompts,
clean Chrome DevTools console.

**If B3 fails, STOP here.** A failing build will pass store review
(Google only checks permissions + policy) but give real users a broken
first impression. Fix locally, rebuild, re-zip.

### 1.3 Take screenshots

Follow `Docs/store/screenshots.md`. Produce at minimum:

- `01-dashboard-overview.png` (1280×800) — the hero
- `02-live-capture-chatgpt.png` (1280×800)
- `03-session-detail.png` (1280×800)
- `04-snippet-save.png` (1280×800)
- `05-onboarding-wizard.png` (1280×800)

Optional but high-ROI:
- `promo-small-440x280.png`
- `promo-marquee-1400x560.png`

Save under `Docs/store/screenshots/` and commit — if Google later asks
for refreshes, we pull from git.

---

## Step 2 — Dashboard submission (45–90 min)

### 2.1 Create new item

1. Dashboard → **Items** → **+ New item**.
2. Drag the zip: `.output/pce-browser-extension-wxt-1.0.0-chrome.zip`.
3. Wait for the upload + manifest parse. If it errors, it will tell
   you which manifest field is invalid.

### 2.2 Store listing tab

Copy-paste from `Docs/store/listing.md`:

- **Title** → 41-char line from section EN / §Title.
- **Summary** → 120-char line from §Summary.
- **Description** → full block from §Detailed description.
- **Category** → `Productivity`.
- **Language** → `English (United States)` as primary. Add
  `Chinese (Simplified)` as secondary and paste the ZH block.
- **Icon 128×128** → auto-filled from the zip's `icons/icon128.png`.
- **Small promo tile** (optional) → upload `promo-small-440x280.png`.
- **Marquee promo tile** (optional) → upload `promo-marquee-1400x560.png`.
- **Screenshots** → upload the 5 PNGs in the order produced.

Click **Save draft** before moving on.

### 2.3 Privacy practices tab

Copy-paste from `Docs/store/justification.md`:

- **Single purpose** → §1 block.
- **Permission justification** — for each of `storage`, `activeTab`,
  `scripting`, `tabs`, `contextMenus`, paste the matching §2 block.
- **Host permissions justification** → §3 block.
- **Remote code** → "No" + §4 text.
- **Data usage categories** → tick exactly the ones §5 lists.
- **Compliance certifications** → tick all three checkboxes in §5.
- **Privacy policy URL** → `https://github.com/zstnbb/pce/blob/master/PRIVACY.md`
  (exact URL from §6 — it 404s until the repo goes public, which is
  fine; Google's reviewer typically checks this AFTER the first
  functional-review email exchange, giving you a week or two of
  slack).

Click **Save draft**.

### 2.4 Distribution tab

- **Visibility** → `Public`.
- **Regions** → `All regions`.
- **Pricing** → `Free`.
- **Family-friendly** → No.
- **Mature content** → No.

### 2.5 Review & submit

- **Submit for review** button turns green once all required fields
  are filled. Click it.
- Confirmation dialog appears → tick "I agree" → **Submit**.
- The item moves to "In review" state. Review clock starts.

---

## Step 3 — After submit (passive, 3–21 days)

### 3.1 Watch for Google emails

Google sends:
- Receipt confirmation (immediate).
- Clarification requests (0 to N, if reviewer has questions). §7 of
  `Docs/store/justification.md` pre-drafts responses for the 4 most
  common questions.
- Approval / rejection email.

Respond to clarifications within 24h if possible — each round of
silence adds a day to the queue.

### 3.2 Flip the repo public (before approval email ships)

If the `pce` GitHub repo is still private: make it public **before
Google publishes the listing**, so the Privacy Policy URL starts
resolving. Otherwise the listing page shows a broken link on day 1.

```
GitHub → Settings → Danger Zone → Change repository visibility → Public
```

### 3.3 On approval

- The listing URL becomes live. Grab it from the Dashboard.
- Update `Docs/store/SUBMISSION-PLAYBOOK.md` with the final listing
  URL (amend this file).
- Announce: GitHub release notes for v1.0.0, HN / Reddit / relevant
  Discords.

---

## Rollback / re-submit

If approved then you find a showstopper:

1. `cd pce_browser_extension_wxt && pnpm build --mode webstore && pnpm zip --mode webstore`
2. **Bump version** in `wxt.config.ts` + `package.json` (e.g., 1.0.0 → 1.0.1) — Chrome Web Store rejects uploads with the same or lower version.
3. Dashboard → Items → PCE → **Upload new package** → drag the new zip.
4. Store listing / privacy tabs stay as-is unless you're changing them.
5. **Submit for review**. Second-time reviews are usually faster (1–3 days).

---

## Quick reference — files Cascade produced

- `PRIVACY.md`
- `Docs/store/listing.md`
- `Docs/store/justification.md`
- `Docs/store/screenshots.md`
- `Docs/store/local-smoke-test.md`
- `Docs/store/SUBMISSION-PLAYBOOK.md` (this file)
- `pce_browser_extension_wxt/.output/pce-browser-extension-wxt-1.0.0-chrome.zip`

Do **not** delete the `.output/` zip until at least the review emails
stop coming — Google sometimes asks for the original package to
re-scan.
