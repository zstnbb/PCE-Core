# HuggingFace Chat — S3 Smoke Coverage

**Tier:** **S3** — breadth coverage for the dev-niche slice. Low
weight per $50+/mo persona session; kept to exercise the generic
extractor on an OSS-model DOM. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Vanilla chat with community-hosted OSS models (Llama, Qwen,
  DeepSeek-R1, Mistral, etc.). No Custom GPTs, no Projects, no
  Canvas / Artifacts, no image generation.
- URL shape: `huggingface.co/chat/` → `huggingface.co/chat/conversation/<id>`.
- Turn DOM: `[data-message-role]` when exposed; otherwise
  `.chat-message` / `.prose` under a `[class*="chat-container"]`
  shell.
- File / image upload supported via the paperclip icon (Pro
  subscribers get vision).

## T-cases

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla turn | 1 user + 1 assistant message captured under `provider="huggingface"` |
| `T02` | Streaming | no partial captures mid-stream; final capture after stream settles |
| `T05` | Code block | assistant turn has a `code_block` attachment with language + code |
| `T10` | PDF / text file upload | user turn has `file` attachment with name + media_type |
| `T20` | Settings silent | navigating to `/chat/settings` produces zero new captures |

## Current implementation

- **Content script:** `pce_browser_extension_wxt/entrypoints/huggingface.content.ts`
- **E2E adapter:** `tests/e2e/sites/huggingface.py` (smoke only)
- **E2E runner:** `tests/e2e/test_capture.py` (smoke matrix)

## Known gaps

Typically none beyond shared-runtime items (streaming gate,
citation extraction). Any real bug surfaces during smoke run. A
single clean round-trip is enough for S3; upgrade to S2 diff
only if the persona's dev-niche penetration rises meaningfully.
