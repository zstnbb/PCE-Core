# DeepSeek — Coverage Diff vs CHATGPT-FULL-COVERAGE

**Tier:** S2
**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**Ship target:** v1.0.2

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

DeepSeek is the most structurally fragile of the S2 sites because
DOM classnames are hashed (React CSS modules). The only stable
selector is `.ds-markdown` for assistant replies.

| ± | Surface | Notes |
|---|---|---|
| + | Thinking mode (DeepSeek-R1) | Reasoning panel above reply; should become `<thinking>` prefix |
| + | Search mode | DeepSeek Search with web results |
| − | No Custom GPT / Projects / Canvas / Artifacts | |
| − | No branch switcher | |
| − | No projects route | |
| ≠ | Assistant anchor | `.ds-markdown` block; user message found by walking up the DOM and scanning siblings |
| ≠ | Model selector | Multiple selector fallbacks (`.ds-markdown` for assistant; model badge uses 7 different selectors + title + body heuristics) |

## 2. Share-able T-cases (reused from ChatGPT / parent)

- `T01` vanilla chat
- `T02` streaming
- `T05` code blocks
- `T07` edit user message
- `T08` regenerate
- `T10/T11` file / image upload (if supported in the account)
- `T20` settings negative-capture

## 3. Site-specific T-cases (new)

| ID | Description | Pass condition |
|---|---|---|
| `DS01` | Thinking mode (R1) reasoning capture | assistant content begins with `<thinking>…</thinking>` |
| `DS02` | Search mode with citations | assistant msg has `citation` attachments |
| `DS03` | Chinese / mixed content | text extracted correctly with no encoding artefacts |
| `DS04` | `/chat/s/<id>` share-like URL | verify whether `/chat/s/` represents a shared thread (should skip) or a normal thread (should capture) |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/deepseek.content.ts` (329 lines — largest S2 by far)
- **Unit tests:** `pce_browser_extension_wxt/entrypoints/__tests__/deepseek.content.test.ts` (21 tests)
- **E2E adapter:** `tests/e2e/sites/deepseek.py` (minimal)
- **Key helpers present:** `getModelName` with 7 selector fallbacks + title/body heuristics, `extractThinking`, manual-capture bridge, `findTurnContainer` walk-up logic
- **Missing helpers:** `isStreaming` gate, `/share/` skip, `getSessionHint` for `/chat/s/<id>` semantics

## 5. Known gaps (short list)

- **DS1.** `isStreaming` gate NOT wired. Same as G2/C2. Status: ⬜ OPEN.
- **DS2.** `findTurnContainer` walks up 6 levels guessing siblings; may mis-pair user/assistant when layout uses grid / flex with non-sibling containers. Status: ⬜ OPEN — needs live DOM probe.
- **DS3.** Strategy 3 (large-text-block heuristic) can capture random sidebar/toolbar text as "user messages" if the heuristic misfires. Offset-height gating helps but isn't foolproof. Status: ⬜ OPEN.
- **DS4.** `/chat/s/<id>` URL semantics unclear. The current regex matches it and returns the id, but if `/chat/s/` is a SHARED thread surface, we'd be capturing it incorrectly. Status: ⬜ OPEN — needs live validation.
- **DS5.** Model name heuristic includes a `"DeepSeek"` fallback that returns the literal string when nothing matches — may pollute `conversation.model_name` with generic text on unrelated pages. Status: ⬜ LOW PRIORITY.
- **DS6.** `hookHistoryApi: false`. 5s polling only. Status: ⬜ OPEN.

## 6. Order of attack

```
Block 1 — smoke (5 min):      T01, T20
Block 2 — Thinking (10 min):  DS01 reasoning capture
Block 3 — streaming (5 min):  T02 with DS1 fix
Block 4 — structure (15 min): DS02 (search + citations), DS03 (Chinese content), DS04 (/chat/s/)
Block 5 — edit (5 min):       T07, T08 edit/regen
```

Total ~40 min first pass.

## 7. Shared bug assumptions

- DS1 streaming is a pure copy of G2/C2.
- `extractThinking` already works (same helper used by ChatGPT / Claude / GAS).
- DS2 (turn container walk-up) is the real differentiator — if that breaks, entire extractor fails. Needs live probe, not static fix.
