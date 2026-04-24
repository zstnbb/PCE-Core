# Notion AI — Coverage Diff vs CHATGPT-FULL-COVERAGE

> ⚠ **NOT READY — scaffolding only.** This is an **SX** tier
> stub under the 2026-04-25 realignment. The content script
> exists but **no live-DOM probe has happened** against a real
> Notion account with AI enabled. Selectors below are informed
> guesses. Do not treat this spec as validated coverage.

**Tier:** **SX** — scaffolding (F2 SaaS-embedded AI).
Out-of-scope for the $50+/mo AI-native power user persona's core
capture obligations; kept for breadth in the Store listing and
because the generic Notion embed is likely to bleed into real
power-user workflows via team wikis. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**E2E depth:** none — blocked on DOM probe
**Ship target:** v1.1 earliest (depends on probe + adapter)

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

Notion AI is NOT a chat site. It's a set of inline `/ai` block
invocations inside the Notion page editor, plus the "Ask AI"
side panel / modal. Coverage model differs.

| ± | Surface | Notes |
|---|---|---|
| + | Inline `/ai` block | User hits `/ai` → menu → picks a prompt ("Summarise", "Translate", …) → AI writes into the current block |
| + | Ask AI side panel / modal | Free-form Q&A across the current page / database / workspace |
| + | AI Autofill (database property) | Property filled by AI on row change |
| − | No standalone chat URL | Always embedded inside a page |
| − | No Custom GPTs / Projects / Canvas | |
| ≠ | Session hint | Page UUID from URL (`notion.so/<workspace>/<title>-<uuid>`) |
| ≠ | Turn DOM | Content is written into the page DOM (blocks) rather than a chat bubble; user prompt lives in a transient floating UI that disappears after acceptance |

## 2. Share-able T-cases (aspirational — all need live probe)

- `T01` vanilla turn (inline `/ai` summarise)
- `T02` streaming (AI writes into the block progressively)
- `T20` settings / non-AI editor silent

## 3. Site-specific T-cases (new, pending probe)

| ID | Description | Pass condition |
|---|---|---|
| `N1` | Inline `/ai` invocation captured as turn | user prompt (menu label + context) + assistant (written block text) captured once the user accepts the AI output |
| `N2` | Ask-AI side panel | free-form Q&A captured like a mini chat under the page UUID |
| `N3` | AI Autofill property | may or may not be captured as an AI turn — decision depends on how Notion surfaces it; document and test when probe data arrives |
| `N4` | Non-AI edit silence | ordinary typing / block manipulation must NOT produce captures |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/notion.content.ts` (scaffolding — ~7 KB)
- **E2E adapter:** ⬜ NOT CREATED
- **Host match:** `notion.so` / `www.notion.so`
- **What the script tries today:** guess-selectors for `/ai` result blocks + "Ask AI" modal. Likely misses in practice.

## 5. Known gaps (blocking ship)

- **N-P1.** No live-DOM probe. The entire selector set is a
  guess. Status: ⬜ BLOCKING — needs user with paid Notion
  account + 30-60 min inspection.
- **N-P2.** No E2E adapter. Status: ⬜ BLOCKING — depends on N-P1.
- **N-P3.** Session-hint policy for pages that are used as
  ongoing AI scratchpads: should each `/ai` invocation be its
  own session, or should the same page group them? Status:
  ⬜ DESIGN DECISION PENDING.
- **N-P4.** Privacy: Notion page content can be sensitive. Must
  confirm that only the AI-invocation delta (prompt + AI
  response) is captured, not the surrounding page body. Status:
  ⬜ DESIGN DECISION PENDING.
- **N-P5.** Canvas-style page writes: AI often writes multiple
  blocks in a row. Each block is a separate DOM insertion.
  Streaming / settle detection must handle multi-block writes.
  Status: ⬜ OPEN.

## 6. What must happen before this becomes S2 or S3

1. User signs in to Notion with AI enabled, records 15 minutes
   of `/ai`-block use + Ask-AI panel use, shares DOM snapshots
   and the resulting PCE captures (or absence of them).
2. Cascade turns those observations into a pinned selector set
   + `__tests__/notion.content.test.ts`.
3. User re-runs the same workflow; captures round-trip.
4. Only then: promote to S3 (smoke spec) or S2 (diff spec).

Until then, the Store listing must not imply Notion AI capture
works.
