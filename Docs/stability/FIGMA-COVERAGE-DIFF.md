# Figma AI — Coverage Diff vs CHATGPT-FULL-COVERAGE

> ⚠ **NOT READY — scaffolding only.** This is an **SX** tier
> stub under the 2026-04-25 realignment. The content script
> exists but **no live-DOM probe has happened** against a real
> Figma account with First Draft / Jambot / Figma Make enabled.
> Figma is a canvas-heavy app with most of its UI rendered to
> WebGL / `<canvas>` — DOM extraction will recover only a subset
> of the AI exchange. Do not treat this spec as validated coverage.

**Tier:** **SX** — scaffolding (F2 SaaS-embedded AI,
canvas-rendered UI). Out-of-scope for the $50+/mo AI-native
power user persona's core capture obligations; relevant to the
design-professional slice. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**E2E depth:** none — blocked on DOM probe + canvas-extraction research
**Ship target:** v1.2 earliest (depends on either Figma exposing DOM-readable AI output, or PCE adopting L4c OCR for canvas regions)

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

Figma's AI is fragmented across several panels:

| ± | Surface | Notes |
|---|---|---|
| + | First Draft | Text-to-frame generation; prompt + generated frames |
| + | Figma Make | Prompt-to-code / prompt-to-component generation |
| + | Jambot (in FigJam) | Chat-style AI assistant for FigJam boards |
| + | AI rename / AI description | One-shot AI actions applied to selected layers |
| − | No standalone chat URL | All AI invocations live inside the design tool |
| − | No Custom GPTs / Projects / Canvas (ChatGPT sense) | Figma itself IS a canvas |
| ≠ | Session hint | File key from URL (`/file/<key>/...` or `/design/<key>/...`) |
| ≠ | Turn DOM | **Most of the output is rendered to `<canvas>`**, not DOM. Prompt text lives in DOM popovers that vanish after accept |

## 2. Share-able T-cases (aspirational — all need live probe)

- `T01` vanilla turn (First Draft prompt → frames)
- `T20` non-AI canvas editing silence

## 3. Site-specific T-cases (new, pending probe)

| ID | Description | Pass condition |
|---|---|---|
| `F1` | First Draft prompt capture | user prompt text captured; assistant "generated frame" represented as an attachment placeholder (DOM side won't have the frame contents) |
| `F2` | Figma Make prompt → component | user prompt + generated component code captured as `code_block` / `artifact` if DOM exposes code; otherwise prompt-only capture with assistant `canvas` placeholder |
| `F3` | Jambot chat turn | FigJam AI assistant chat captured as normal user/assistant turns |
| `F4` | AI rename / description | skipped — these are one-shot commands with no conversational turn; do NOT attempt to capture |
| `F5` | Non-AI canvas editing silence | zero captures during ordinary drawing / dragging |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/figma.content.ts` (scaffolding — ~6.6 KB)
- **E2E adapter:** ⬜ NOT CREATED
- **Host match:** `figma.com` / `*.figma.com`
- **What the script tries today:** DOM-only guess-selectors for
  First Draft / Jambot popovers. Cannot reach `<canvas>` content.

## 5. Known gaps (blocking ship)

- **F-P1.** Canvas-rendered AI output is unreachable from the
  DOM. Status: ⬜ BLOCKING — requires L4c OCR (Pro layer) for
  meaningful "generated frame" capture, or the prompt-only
  fallback documented in F1.
- **F-P2.** No live-DOM probe. Status: ⬜ BLOCKING.
- **F-P3.** No E2E adapter. Status: ⬜ BLOCKING.
- **F-P4.** Privacy: Figma design files can contain unreleased
  product concepts. Must confirm only the AI-invocation delta
  is captured, never a canvas snapshot of the full file.
  Status: ⬜ DESIGN DECISION PENDING — likely default-off with
  explicit opt-in.
- **F-P5.** Figma's multi-page tabs + iframe structure needs
  shadow-DOM / iframe-escape helpers. Status: ⬜ OPEN.

## 6. What must happen before this becomes S2 or S3

1. User with Figma account runs 15 minutes of First Draft +
   Jambot + Make, shares DOM snapshots and resulting captures.
2. **Honest decision point:** if DOM is not meaningful (high
   likelihood), drop Figma AI from v1.x browser-extension scope
   and document that "Figma AI capture requires Pro OCR layer
   (L4c), available in v1.3+".
3. Only if the DOM-only path is viable: pin selectors, write
   tests, promote to S3 / S2.

Until then, the Store listing must not imply Figma AI capture
works.
