# P5.B — Stability Hardening Plan

**Context (updated 2026-04-22):** v1.0.0 was **rejected** by Chrome Web
Store on 2026-04-22 (transfer ID `FZSL`, Yellow Argon keyword-spam —
the SUPPORTED SITES bullet list in the description). The rejection is
documented in `Docs/store/listing.md` → "Rejection history".

**v1.0.1 has been rebuilt** (commit `3c79fe1`) with:
- rewritten store listing (no bullet list of brand names — see `listing.md`)
- 16 static gap fixes across S1 + S2 (specs in `Docs/stability/*-COVERAGE*.md`)
- 616/616 unit tests green, 0 regressions
- output zip: `pce_browser_extension_wxt/.output/pce-browser-extension-wxt-1.0.1-chrome.zip` (1.01 MB)

**Pending user action before resubmission** (see `SUBMISSION-PLAYBOOK.md` §Step 0):
1. B3 smoke test on the 1.0.1 unpacked build
2. Dashboard → Store listing tab: paste new Summary + Description
3. Dashboard → Package tab: upload 1.0.1 zip
4. Submit for review

Since v1.0.0 never reached Chrome's store front-end (it was rejected in
review), v1.0.1 becomes the **first public release**. There is no
installed user base to auto-push to yet. The "v1.0.1 = first-day UX"
framing in the original plan still applies — it's just the literal
first release instead of a fast follow-up.

**Non-goal:** feature parity or new capabilities. Only **reliability of
what v1.0.1 promises in the Chrome Web Store listing**.

---

## Inventory — 21 content scripts, 3 maturity tiers

### Tier 0 — infrastructure (5 files, stable, no user-visible changes planned)

| File | Role | v1.0.1 action |
|---|---|---|
| `bridge.content.ts` | Cross-context postMessage bridge | none |
| `detector.content.ts` | Unknown-AI-site scoring (sideload build only) | none |
| `generic.content.ts` | Fallback extractor when no site-specific adapter matches | none |
| `behavior-tracker.content.ts` | Focus/blur telemetry | none |
| `text-collector.content.ts` | Selection → snippet save | none |

### Tier 1 — F1 production sites (12 files, shipped, variable drift)

All ported from `P2.5 Phase 3b` and carry real-site selectors. Any of
them may have DOM-drifted since original port.

| File | Site | User volume signal | 1.0.1 priority |
|---|---|---|---|
| `chatgpt.content.ts` | chatgpt.com | TOP (most visitors — cap landing page) | **P0** |
| `claude.content.ts` | claude.ai | TOP | **P0** |
| `gemini.content.ts` | gemini.google.com | TOP | **P0** |
| `copilot.content.ts` | copilot.microsoft.com | HIGH — user observed empty-reply capture | **P0** |
| `google-ai-studio.content.ts` | aistudio.google.com | MEDIUM (developers) | P1 |
| `deepseek.content.ts` | chat.deepseek.com | MEDIUM (CN market + OSS devs) | P1 |
| `perplexity.content.ts` | perplexity.ai | MEDIUM | P1 |
| `poe.content.ts` | poe.com | MEDIUM | P2 |
| `huggingface.content.ts` | huggingface.co/chat | LOW (dev niche) | P2 |
| `grok.content.ts` | grok.com / x.com | LOW | P2 |
| `manus.content.ts` | manus.ai | LOW (new) | P2 |
| `zhipu.content.ts` | chatglm.cn | LOW (CN-only) | P2 |

### Tier 2 — F2 scaffolding (4 files, explicitly placeholder)

File headers literally say "scaffolding". These will capture *something*
only if the live DOM happens to match the guess selectors. Real users
on these surfaces will likely see empty captures.

| File | Site | What it's supposed to capture | Honest 1.0.1 readiness |
|---|---|---|---|
| `notion.content.ts` | notion.so | Inline `/ai` block invocations | **NOT READY** — needs real Notion account + hours of DOM inspection |
| `m365-copilot.content.ts` | m365.cloud.microsoft | Copilot side-panel inside Office Web | **NOT READY** — needs M365 Business account (paid) |
| `gmail.content.ts` | mail.google.com | "Help me write" dialog | **NOT READY** — gated behind Google Workspace tier |
| `figma.content.ts` | figma.com | First Draft / Figma Make / Jambot panels | **NOT READY** — canvas-heavy, DOM-opaque |

---

## Recommended v1.0.1 scope

**Ship when Tier-1 P0 (4 sites) is solid. Treat everything else as
v1.1 candidates.** Reasons:

1. Users searching "AI history logger" on Chrome Web Store will try
   ChatGPT / Claude / Gemini / Copilot **first**. First-minute
   impression of v1.0.0 is made or broken on those four.
2. F2 scaffolding honestly delivered in v1.1 beats F2 half-delivered in
   v1.0.1. Shipping obviously-broken capture on Notion / M365 invites
   1-star reviews that stick to the Store page forever.
3. Tier-1 P1 / P2 sites are claims made in the listing — they should
   also work, but users on DeepSeek / Poe / HuggingChat are more
   forgiving of version-zero bugs than users on ChatGPT.

### v1.0.1 exit criteria

- [ ] ChatGPT: 5 consecutive captures on 5 different conversation types (vanilla, streaming, tool-call, canvas, attachment) → all include both sides, correct provider, no console errors
- [ ] Claude: same
- [ ] Gemini: same
- [ ] Copilot: same + **the "empty reply" issue the user flagged is reproduced + root-caused + fixed + regression-tested**
- [ ] No Tier-1 P0 selector changes without a matching `__tests__/<site>.content.test.ts` case
- [ ] `pnpm test` green on all P0 tests
- [ ] Extension still loads cleanly from webstore zip (re-run the B3 "install + permission" check)
- [ ] `wxt.config.ts` version bumped to 1.0.1

### v1.1 scope (parked — do NOT attempt in v1.0.1)

- F2 Notion / M365 / Gmail / Figma realistic implementation
- Tier-1 P1 sites (DeepSeek, Perplexity, AI Studio) selector refresh
- Cross-conversation memory / session continuity
- Dashboard UX improvements
- Performance work (IndexedDB queue under load, etc.)

---

## Per-site validation protocol (repeat for each P0)

Time budget: 30 min per site.

1. **Prep:** PCE Core running, unpacked webstore build loaded, fresh browser window.
2. **Open site, start a new conversation, send 3 messages.** Neutral topics (math, grammar). Avoid anything in real work.
3. **PCE Dashboard check:**
   - Session appears within 5 s of assistant reply finishing
   - Both sides captured, text matches what the page shows
   - Provider + model populated
4. **Edge cases — do one each:**
   - Message with a code block (ChatGPT/Claude) or tool call (Claude)
   - Message with an attachment (ChatGPT/Gemini)
   - A streaming response interrupted by stop-button
5. **Chrome DevTools:**
   - `F12` on the AI site tab
   - Console filtered by `PCE` → no red lines
6. **If selector fails anywhere:**
   - `Inspect` the failing element
   - Note its selector. Compare to the one in `<site>.content.ts`
   - Draft the new selector. Add to the file **with** a companion
     test in `__tests__/<site>.content.test.ts`
   - `pnpm test <site>` green
   - Rebuild, reload extension, re-run from step 2

After all 4 P0 sites pass: 
```
cd pce_browser_extension_wxt
# bump to 1.0.1 in wxt.config.ts + package.json
pnpm build --mode webstore
pnpm zip --mode webstore
# zip sits at .output/pce-browser-extension-wxt-1.0.1-chrome.zip
# upload to Dashboard when v1.0.0 review clears
```

---

## What Cascade can help with — without the user sitting at a browser

- Read site-specific content scripts and propose selector modernisations
  based on known DOM patterns (e.g., React 18 + shadcn classes)
- Write regression tests (`__tests__/<site>.content.test.ts`) before
  the user validates live — the tests lock in what the selector is
  supposed to match, so when the user changes the selector to fit
  the live page, the test gets updated in the same commit
- Run `pnpm test` and lint across the board after each fix
- Research Notion / M365 / Figma DOM patterns from public sources when
  the user doesn't have accounts readily available
- Audit `capture-runtime.ts` + `selector-engine.ts` utilities for any
  brittleness that affects ALL sites (common-code wins beat per-site
  wins when available)

## What only the user can do

- Open each P0 site with a real account
- Observe whether captures appear / are complete
- Inspect live DOM when selectors fail
- Report observations back (screenshot of DevTools, or the new
  selector that works) — Cascade converts that into code + tests

## Workflow in practice

```
1. User picks a P0 site (say ChatGPT) and runs the validation protocol
2. Reports back: "ChatGPT works except when the assistant uses a
   canvas — the canvas content isn't captured"
3. Cascade:
   a. Reads chatgpt.content.ts
   b. Reads __tests__/chatgpt.content.test.ts
   c. Looks for canvas handling
   d. Proposes a selector / code change
   e. Writes a failing test that reproduces the canvas miss
   f. Makes it pass with minimal change
   g. Commits
4. User rebuilds, reloads, re-validates
5. Repeat until ChatGPT passes all edge cases → move on to Claude
```

This keeps Cascade doing the things Cascade is good at (code,
reasoning, test discipline) and the user doing the things only the
user can do (browsing with real accounts, observing real UIs).
