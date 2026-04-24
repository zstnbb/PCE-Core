# Kimi — S3 Smoke Coverage

**Tier:** **S3** — breadth coverage. CN-market long-context chat
(2M Chinese tokens). Low overlap with the $50+/mo AI-native power
user persona outside the CN-market slice, but the site is stable
and cheap to cover via the generic extractor. Canonical tier
definition: `Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Vanilla long-context chat with optional file grounding.
- URL shape: `kimi.com/chat/<id>` (new); legacy
  `kimi.moonshot.cn` redirects.
- Turn DOM: `.segment.segment-user` / `.segment.segment-assistant`
  — captured via the **generic extractor** (`generic.content.ts`
  `HOST_PROVIDER_MAP` entry `kimi.com → "moonshot"`).
- File upload supported (PDF / DOCX / TXT); image upload may be
  gated by account tier.
- No Canvas / Artifacts / Projects / custom-GPT store.

## T-cases

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla turn | user + assistant captured under `provider="moonshot"` |
| `T02` | Streaming | no partial captures mid-stream |
| `T10` | PDF upload | user turn has `file` attachment with name |
| `T20` | Settings silent | `/settings*` produces zero captures |

## Current implementation

- **Content script:** none — handled by
  `pce_browser_extension_wxt/entrypoints/generic.content.ts` via
  `HOST_PROVIDER_MAP` (`kimi.com` / `www.kimi.com` /
  `kimi.moonshot.cn`)
- **E2E adapter:** `tests/e2e/sites/kimi.py` (smoke only; uses
  `execute_script` soft-nav to avoid `ERR_CONNECTION_RESET` on
  repeated `driver.get`)
- **E2E runner:** `tests/e2e/test_capture.py` (smoke matrix)
- **Normalizer hosts:** `www.kimi.com` + `kimi.com` registered in
  `_COMPATIBLE_HOSTS` (openai.py) and `_CONVERSATION_HOSTS`
  (conversation.py)

## Known gaps

- Model-name attribution: `conversation.model_name` is populated
  from DOM heuristic and may return a literal "Kimi" fallback on
  unrelated pages. Acceptable for S3.
- Image-upload path on account tiers without vision is not
  covered; skip with evidence if UI hidden.
- Provider key is `"moonshot"` (Moonshot AI is the company) — do
  NOT regress to `"kimi"` / `"www"`; see memory on Kimi provider
  fix (Apr 2026).
