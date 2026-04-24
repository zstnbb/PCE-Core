# Copilot — Coverage Diff vs CHATGPT-FULL-COVERAGE

**Tier:** **S2** — workflow-critical for the enterprise /
researcher slice of the $50+/mo AI-native power user persona
(Bing grounding + M365 ecosystem). Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.
**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**E2E depth:** S2 diff runner (pending) + smoke via `test_capture.py` (pending)
**Ship target:** v1.0.2

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

| ± | Surface | Notes |
|---|---|---|
| + | Bing search grounding | Copilot answers cite web results inline; `citation` attachment expected |
| + | "Notebook" / "Designer" modes | Alternate-workflow views |
| − | No Custom GPT Store / no Gems | Copilot doesn't have user-authored personas |
| − | No explicit "Canvas" side panel | Inline artifacts only |
| − | No projects route | |
| ≠ | Model badge | Uses `[class*="model"]`, `[class*="badge"][class*="gpt"]`, or `[data-model]` rather than a fixed testid |
| ≠ | Turn DOM | React + CIB (`cib-message-group[source=user\|bot]`), classnames like `.ac-textBlock` |

## 2. Share-able T-cases (reused from ChatGPT / parent)

- `T01` vanilla chat
- `T02` streaming
- `T03` stop mid-stream
- `T05` code blocks
- `T07` edit
- `T08` regenerate
- `T10` PDF upload (if file upload exposed)
- `T11` image upload (if file upload exposed)
- `T20` settings negative-capture

## 3. Site-specific T-cases (new)

| ID | Description | Pass condition |
|---|---|---|
| `MC01` | Bing grounding citations | assistant msg has ≥1 `citation` attachment with external URL |
| `MC02` | Notebook mode prompt | prompt + response captured under Notebook URL |
| `MC03` | `source="user"` vs `source="bot"` role mapping | user/assistant roles correctly assigned on `cib-message-group` nodes |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/copilot.content.ts` (181 lines)
- **Unit tests:** `pce_browser_extension_wxt/entrypoints/__tests__/copilot.content.test.ts` (18 tests)
- **E2E adapter:** `tests/e2e/sites/copilot.py` (minimal — ~60 lines)
- **Key helpers present:** `getModelName`, 6-strategy selector ladder, manual-capture bridge
- **Missing helpers:** `isStreaming` gate, explicit stop-button detection, `/share/` URL skip

## 5. Known gaps (short list)

- **MCP1.** `isStreaming` gate NOT wired. Partial mid-stream captures possible. Status: ✅ CLOSED — fixed with shared `isStreaming` helper + Stop/Cancel button detection (commit after `54ebf16`).
- **MCP2.** `hookHistoryApi: false`. SPA nav detected only after 5s polling. Status: ⬜ OPEN (acceptable for v1.0.1 — 5s lag beats a capture miss).
- **MCP3.** Dedupe key uses full content (good — no slice collapse like Gemini's G10). No action needed.
- **MCP4.** No `/share/` URL skip. Status: ✅ CLOSED — `extractMessages` now takes a `pathname` argument and short-circuits to `[]` when `^/share/` matches. Mirrors commit `702bf0e` (Gemini G8 + Claude C9). 3 regression tests added.
- **MCP5.** Bing citations extracted only as `[class*='citation']` STRIPPED by `extractText` — semantic citations go into `assistant` text as inline URLs rather than structured `citation` attachments. Status: ⬜ OPEN — needs live DOM probe.
- **MCP6.** *Empty-reply capture* (P5B-PLAN P0 exit criterion). Copilot's React UI briefly shows the user turn BEFORE the assistant turn's `.ac-textBlock` populates. Two compounding bugs let a partial capture through:
  1. `extractText` returned `""` when `.ac-textBlock` existed but was empty (no fall-through to the whole-clone text).
  2. Runtime was not configured with `requireBothRoles: true`, so the partial turn list (user-only) made it past `capture-runtime.ts:306`.
  Status: ✅ CLOSED — (a) `extractText` now falls through when the rendered child is empty; (b) `requireBothRoles: true` wired. Matches `zhipu`/`poe`/`grok`/`m365-copilot` pattern. Regression tests added in `__tests__/copilot.content.test.ts`.

## 6. Order of attack

```
Block 1 — smoke (5 min):            T01-equiv (MC vanilla), T20-equiv (/settings)
Block 2 — streaming + shared runtime (10 min): T02, T03 with MCP1 fix
Block 3 — site-specific (15 min):   MC01 (citations), MC02 (Notebook), MC03 (role mapping)
Block 4 — shared (10 min):          T05, T07, T08, T10/T11 (if uploads exposed)
```

Total ~40 min first pass.

## 7. Shared bug assumptions

After ChatGPT + S1 autopilots green:
- `isStreaming` fix for Copilot is 3-line change identical to G2/C2.
- URL `/share/` skip is identical pattern to G8/C9.
- Citation extraction likely needs attention in `pce-dom.ts` (shared — will benefit Perplexity + Gemini Extensions too).
