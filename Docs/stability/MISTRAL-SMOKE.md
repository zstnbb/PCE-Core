# Mistral Le Chat — S3 Smoke Coverage

**Tier:** **S3** — breadth coverage. EU-niche chat front-end to
Mistral's own models (Le Chat, Codestral, Mistral Large). Used by
a small slice of the $50+/mo AI-native power user persona that
values EU data residency. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Vanilla chat. Some account tiers expose code interpreter and
  web search.
- URL shape: `chat.mistral.ai/chat` → `chat.mistral.ai/chat/<id>`.
- Turn DOM: bubble containers keyed by `[class*="assistant"]` /
  `[class*="message"]` — captured via the **generic extractor**
  (`generic.content.ts` `HOST_PROVIDER_MAP` entry
  `chat.mistral.ai → "mistral"`).
- File / image upload supported on most tiers.
- No Canvas / Artifacts / Projects / custom-GPT store.

## T-cases

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla turn | user + assistant captured under `provider="mistral"` |
| `T02` | Streaming | no partial captures mid-stream |
| `T05` | Code block | assistant turn has `code_block` attachment |
| `T20` | Settings silent | `/settings*` produces zero captures |

## Current implementation

- **Content script:** none — handled by
  `pce_browser_extension_wxt/entrypoints/generic.content.ts` via
  `HOST_PROVIDER_MAP`
- **E2E adapter:** `tests/e2e/sites/mistral.py` (smoke only)
- **E2E runner:** `tests/e2e/test_capture.py` (smoke matrix —
  added in the 2026-04-25 deployment)

## Known gaps

- No site-specific content script means rich attachments rely on
  the generic extractor; expect some under-capture of citations
  and code blocks when Mistral adds a DOM variant the generic
  extractor hasn't seen.
- If persona penetration rises (e.g., EU enterprise uptake after
  a data-residency mandate), promote to S2 with its own script.
