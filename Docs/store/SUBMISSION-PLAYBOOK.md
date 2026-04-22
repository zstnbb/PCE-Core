# Chrome Web Store — Submission Playbook (v1.0.1, post-rejection)

**Purpose:** Single ordered checklist for getting PCE extension live on
the Chrome Web Store. Every step is either (a) a file already in this
repo that you copy-paste from, or (b) a click-through in the Chrome
Web Store Developer Dashboard.

**Who runs this:** You, on your developer account. Cascade has already
produced every artefact referenced below; the remaining work is in the
browser + Dashboard.

**Expected elapsed time:**
- Day 1: steps 1–3 below, ~2 hours including screenshots.
- Day 2 onwards: wait for Google review. Status visible in the Dashboard.
- Approval median: 3–7 days for standard review, 1–3 weeks if Extended
  Review triggers. Metadata-only resubmissions after a rejection are
  typically fastest (1–3 business days).

---

## 2026-04-22 — v1.0.0 was rejected. Start at **Step 0** below.

Google rejected v1.0.0 with **transfer ID `FZSL`** ("Yellow Argon",
keyword-spam violation). Root cause + fix are documented in
`Docs/store/listing.md` → "Rejection history". TL;DR: the previous
description contained a bullet list of 14 AI-tool brand names with
URLs, which triggers Chrome's SEO-stuffing heuristic.

**The updated listing copy is already in `Docs/store/listing.md`.**
Before re-submitting:

### Step 0 — v1.0.1 resubmission (do this FIRST)

1. **Bump the extension version.** Edit
   `pce_browser_extension_wxt/wxt.config.ts`, change `version: "1.0.0"`
   to `version: "1.0.1"`. The Chrome Web Store rejects uploads whose
   version is the same as or lower than the currently submitted one
   — you cannot replace the rejected v1.0.0 with another v1.0.0.

2. **Rebuild the webstore zip.**
   ```
   cd pce_browser_extension_wxt
   pnpm build --mode webstore
   pnpm zip --mode webstore
   ```
   Output: `.output/pce-browser-extension-wxt-1.0.1-chrome.zip`.

3. **Re-run the B3 local smoke test** against the new unpacked
   directory (`.output/chrome-mv3/`) following
   `Docs/store/local-smoke-test.md`. Rejection fix is metadata-only
   so functional parity is expected, but do NOT skip the smoke test
   — a late-breaking regression at v1.0.1 would be embarrassing on
   a resubmission.

4. **Dashboard → Items → PCE → Package tab → Upload new package**
   → drag the new zip. Chrome replaces the rejected 1.0.0 archive
   with 1.0.1.

5. **Dashboard → Items → PCE → Store listing tab:**
   - Replace the **Summary** with the new line from
     `Docs/store/listing.md` → §Summary.
   - Replace the **Description** with the updated full block from
     §Detailed description (both EN and ZH if ZH is enabled).
   - Everything else (title, category, screenshots, icons) stays.

6. **Privacy-practices tab:** no changes required.
   `Docs/store/justification.md` §3 host-permissions text still
   accurately describes the 17 hosts — and per Chrome Web Store
   policy, the **host-permissions justification field is allowed
   and in fact required to enumerate each host**; the spam policy
   only applies to the public description. Do NOT trim §3.

7. **Submit for review.** The resubmit banner at the top of the
   Dashboard item page will show a dedicated "Resubmit after
   rejection" button.

8. **Appeal vs resubmit?** Appeal is appropriate only when you
   believe the rejection itself was wrong. For keyword-spam the
   correct path is resubmission with a rewritten description — do
   NOT file an appeal for 1.0.0. The appeal queue is slower than
   the resubmission queue, and the outcome is almost always "your
   appeal is denied because the decision was correct; please
   resubmit after rewriting".

Then pick up the original flow below from **Step 1 §1.2** (smoke
test) or **Step 2** (Dashboard submission) — Steps 1.1 and 1.3
(identity verification, screenshots) are reused from v1.0.0 and
don't need to be redone.

---

## State at the moment you read this

| Artefact | Status | Location |
|---|---|---|
| Webstore zip (v1.0.1, 1.01 MB) | ✅ Built | `pce_browser_extension_wxt/.output/pce-browser-extension-wxt-1.0.1-chrome.zip` |
| Webstore zip (v1.0.0, 994 KB, REJECTED) | ⚠️ Kept for reference | `pce_browser_extension_wxt/.output/pce-browser-extension-wxt-1.0.0-chrome.zip` |
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
2. Drag the zip: `.output/pce-browser-extension-wxt-1.0.1-chrome.zip` (the v1.0.0 zip was rejected — see Step 0).
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
- **Privacy policy URL** → `https://github.com/zstnbb/PCE-Core/blob/master/PRIVACY.md`
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
- Announce: GitHub release notes for v1.0.1, HN / Reddit / relevant
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
- `pce_browser_extension_wxt/.output/pce-browser-extension-wxt-1.0.1-chrome.zip`

Do **not** delete the `.output/` zip until at least the review emails
stop coming — Google sometimes asks for the original package to
re-scan.
