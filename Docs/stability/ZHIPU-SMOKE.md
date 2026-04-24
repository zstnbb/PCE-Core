# ZhiPu ChatGLM — S3 Smoke Coverage

**Tier:** **S3** — breadth coverage. CN-market research /
academic chat (GLM-4). Low overlap with the $50+/mo AI-native
power user persona outside the CN-market research slice.
Canonical tier definition: `Docs/stability/SITE-TIER-MATRIX.md`.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Vanilla chat with GLM-4 family models, plus code interpreter
  and web-search tools.
- URL shape: `chatglm.cn/main/alltoolsdetail?lang=zh` (landing) →
  `chatglm.cn/main/detail/<id>`.
- Turn DOM: bubble containers keyed by class keywords
  (`user` / `assistant`); Chinese-language copy.
- Image generation and file upload supported.
- No Canvas / Artifacts / Projects / custom-GPT store in the
  capture sense.

## T-cases

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla turn (Chinese prompt) | user + assistant captured under `provider="zhipu"` with no encoding artefacts |
| `T02` | Streaming | no partial captures mid-stream |
| `T05` | Code block | assistant turn has `code_block` attachment |
| `T20` | Settings silent | `/account*` / `/settings*` produces zero captures |

## Current implementation

- **Content script:** `pce_browser_extension_wxt/entrypoints/zhipu.content.ts`
- **E2E adapter:** `tests/e2e/sites/zhipu.py` (smoke only)
- **E2E runner:** `tests/e2e/test_capture.py` (smoke matrix)

## Known gaps

- Model-name attribution approximate; DOM does not always expose
  a stable model pill. `conversation.model_name` may fall back
  to `"GLM-4"` literal.
- Chinese content-encoding round-trip is the biggest real risk;
  the smoke T01 guards against regression. If T01 ever regresses,
  check the normalizer's UTF-8 handling before touching the
  content script.
