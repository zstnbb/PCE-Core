# Changelog

All notable changes to PCE (core + browser extension) are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-04-22 — first public release (v1.0.0 was rejected in review)

v1.0.0 was submitted to the Chrome Web Store on 2026-04-21 and rejected on
2026-04-22 for a keyword-spam violation in the description (transfer ID
`FZSL`, "Yellow Argon"). v1.0.1 is the first version that actually reaches
Chrome Web Store users.

### Added

- `Docs/stability/REQUIREBOTHROLES-AUDIT.md` — cross-site audit of the
  partial-capture defense gate after the Copilot MCP6 fix. Documents which
  sites already have it (Copilot, Zhipu, Poe, Grok, M365), which have
  equivalent native defenses (ChatGPT), and which remain v1.0.2 candidates
  without live-user evidence (Claude, Gemini, DeepSeek).
- `CHANGELOG.md` (this file).
- README.md `### Browser extension scope` subsection listing exactly the
  25 host patterns in the extension manifest, giving Chrome Web Store
  reviewers (and users) a source of truth that round-trips to
  `wxt.config.ts COVERED_SITES`.

### Fixed — browser extension behavior

- **Copilot empty-reply capture (P5B-PLAN P0 exit criterion, MCP6).**
  Copilot's React UI briefly shows the user turn before the assistant
  turn's `.ac-textBlock` populates. Two compounding issues let partial
  captures through:
  - `extractText` returned `""` when the rendered child existed but was
    empty (no fall-through to the whole-clone text).
  - Runtime was not configured with `requireBothRoles: true`, so the
    partial turn list made it past `capture-runtime.ts:306`.
  Both are now closed: `extractText` falls through when the rendered child
  is empty, and `requireBothRoles: true` is wired on the Copilot runtime
  (matching Zhipu, Poe, Grok, M365-Copilot). 2 new regression tests in
  `__tests__/copilot.content.test.ts`.
- **Copilot `/share/<id>` URL not skipped (MCP4).** Read-only shared
  conversations at `copilot.microsoft.com/share/<id>` were captured as if
  authored by the current user. `extractMessages` now takes a `pathname`
  argument and short-circuits to `[]` on `^/share/` (mirrors commit
  `702bf0e`, Gemini G8 + Claude C9). 3 new regression tests.

### Fixed — Chrome Web Store listing and metadata

The 2026-04-22 rejection triggered a pass across every reviewer-facing
surface for accuracy and consistency. Specifically:

- **Listing description (EN + ZH).** Removed the bullet list of 14 AI
  brand names with URLs that triggered the keyword-spam heuristic. The
  canonical list now lives in the public GitHub README (`#supported-ai-tools`
  anchor). Brand names that remain in the description appear in contextual
  prose, not as lists.
- **Listing description context-menu claim.** Was "right-click Save
  selection as snippet"; actual context menu at `background.ts:602` is
  "Capture This Page (PCE)". The "Save selection" feature is a floating
  DOM button on text selection (`text-collector.content.ts`), not a Chrome
  context menu. Description now describes both shortcuts accurately.
- **Listing description product form.** Was "PCE Core desktop app"; actual
  install flow per `README.md` is `python -m pce_core.server`. Replaced
  with "PCE Core companion server" / "local Python application" across
  listing.md, justification.md, and PRIVACY.md.
- **Privacy justifications `contextMenus` label.** Was "Save selection as
  snippet"; now "Capture This Page (PCE)" matching the code. Chrome Web
  Store reviewers compare justification strings to code verbatim — a
  mismatch triggers a ding.
- **Host-permissions justification count.** Was "14 hosts" in §3 and
  "17+ AI hosts" in the prose; the manifest actually declares 25 host
  patterns (17 F1 + 8 F2) covering ~14 AI services (multi-domain services:
  ChatGPT, Kimi, Notion, Figma). §3 now enumerates every manifest entry
  verbatim including previously-omitted `www.kimi.com`, `www.notion.so`,
  `www.figma.com`. `wxt.config.ts` comment updated to match.
- **Popup version label.** The toolbar popup's header showed `v0.3.0`
  while the manifest was `1.0.1`. Users reloading after install would see
  the mismatch. Now `v1.0.1`.
- **PRIVACY.md.** `Applies to: version 1.0.0 and later` → `1.0.1 and later`
  with a note that v1.0.0 was rejected. Removed the enumerated brand list
  in §2 (same keyword-spam pattern that got the listing rejected); now
  points to the manifest + README.
- **SECURITY.md contact.** Was `security@pce.example.com` with a
  "replace before public" placeholder note. RFC 2606 reserves example.com
  and reviewers / public reporters clicking through would see a
  non-functional contact. Replaced with GitHub's built-in Private
  Vulnerability Reporting URL, which routes privately to the repo owner
  without needing a separate mailbox. PRIVACY.md contact sections
  updated to match.

### Fixed — documentation hygiene

- 4 stale "⬜ OPEN" markers in coverage-diff docs synced to actual code
  state:
  - PX1 (Perplexity dedup slice-collapse) → CLOSED (commit predates v1.0.0)
  - PX2 (Perplexity isStreaming gate) → CLOSED
  - DS1 (DeepSeek isStreaming gate) → CLOSED
  - M365-P1 (M365 Copilot isStreaming gate) → CLOSED
- `SUBMISSION-PLAYBOOK.md` Step 0 rewritten: marks version bump + zip
  rebuild as DONE (with commit hashes), lists the 4 side-effect fixes
  shipped in v1.0.1, emphasizes the smoke test is now load-bearing (not
  metadata-only), and corrects stale "17 hosts" to "25 host patterns".
- All `Docs/store/*.md` titles refreshed from `(v1.0.0)` to `(v1.0.1)`.
- `Docs/stability/P5B-PLAN.md` top context updated with the rejection
  narrative and v1.0.1 rebuild summary.

### Tests

- 621/621 unit tests pass (up from 616 at the start of the resubmission
  audit; +5 regression tests: 2 for MCP6, 3 for MCP4).
- Webstore zip: `pce-browser-extension-wxt-1.0.1-chrome.zip` (1.01 MB).
  Manifest verified: 25 host patterns, 0 `<all_urls>` in `content_scripts`,
  clean description without brand enumeration, version 1.0.1.

## [1.0.0] - 2026-04-21 — rejected, never published

Submitted to the Chrome Web Store on 2026-04-21. Rejected on 2026-04-22
with transfer ID `FZSL` (Yellow Argon keyword-spam heuristic). Never
reached the Chrome Web Store front-end, therefore never installed by any
user. The rejection root cause and fix history are documented above and
in `Docs/store/listing.md` → "Rejection history".

The code shipped inside the v1.0.0 zip is otherwise equivalent to v1.0.1
minus the Copilot MCP4 + MCP6 fixes and the popup version label change.

## Unreleased

Planned for v1.0.2 (no commitments, subject to live-user evidence):

- Claude / Gemini / DeepSeek `requireBothRoles` defense (see
  `Docs/stability/REQUIREBOTHROLES-AUDIT.md`).
- Remaining open items in per-site coverage diffs:
  `COPILOT-COVERAGE-DIFF.md` MCP2 (SPA nav hook) and MCP5 (Bing citation
  structured extraction), `PERPLEXITY-COVERAGE-DIFF.md` PX3–PX6,
  `DEEPSEEK-COVERAGE-DIFF.md` DS2–DS6, `M365-COPILOT-COVERAGE-DIFF.md`
  M365-P2–P4.
- Live autopilot runs for S1 (ChatGPT / Claude / Gemini / GAS) and S2
  (Copilot / M365 / Perplexity / DeepSeek) once the user completes
  bootstrap + login.
