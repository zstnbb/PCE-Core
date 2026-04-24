# Poe — S3 Smoke Coverage

**Tier:** **S3** — breadth coverage. Multi-model aggregator with
shrinking relevance for the $50+/mo AI-native power user persona
(native ChatGPT / Claude / Gemini access beats Poe's pass-through).
Kept for diversity of DOM + backward compat with users who still
consolidate via Poe. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Multi-bot chat UI. Each bot is a GPT / Claude / Gemini / Llama /
  custom-creator persona reachable under a uniform chat shell.
- URL shape: `poe.com/<bot-handle>` → `poe.com/chat/<id>`.
- Turn DOM: `[class*="ChatMessage"]` bubbles, user vs bot
  distinguished by class keywords (`Human` / `Bot`).
- File / image upload supported on most bots.
- No first-class Canvas / Artifacts / Projects / custom-GPT store
  in the PCE capture sense (Poe's "bots" are simple system-prompt
  wrappers; capture them as plain chat).

## T-cases

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla turn on a GPT-family bot | user + assistant captured under `provider="poe"` |
| `T02` | Streaming | no partial captures mid-stream |
| `T05` | Code block | assistant turn has `code_block` attachment |
| `T10` | File upload (PDF) | user turn has `file` attachment |
| `T20` | Settings silent | `/settings*` navigation produces zero captures |

## Current implementation

- **Content script:** `pce_browser_extension_wxt/entrypoints/poe.content.ts`
- **E2E adapter:** `tests/e2e/sites/poe.py` (smoke only)
- **E2E runner:** `tests/e2e/test_capture.py` (smoke matrix)

## Known gaps

- Model-name attribution is approximate (Poe exposes the bot
  handle, not the upstream model); `conversation.model_name` is
  populated with the bot handle string. Acceptable for S3.
- No meaningful improvement target unless Poe materially grows
  persona penetration. If that happens, promote to S2.
