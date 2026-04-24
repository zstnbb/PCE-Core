# Perplexity — Coverage Diff vs CHATGPT-FULL-COVERAGE

**Tier:** **S1** — promoted from S2 under the 2026-04-25
realignment. Perplexity Pro ($20/mo) is widely adopted by the
$50+/mo AI-native power user persona as a primary search
replacement. Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.

> ⚠ **Full-spec backfill pending.** S1 obligations call for a
> ChatGPT-parity Parts I-VI document with ~20 T-cases and a
> `test_perplexity_full.py` runner. Until those land, this diff
> doc continues to be the working reference. Do not treat
> absence of a full spec as permission to drop coverage.

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**E2E depth today:** smoke in `tests/e2e/test_capture.py`
**E2E target:** `tests/e2e/test_perplexity_full.py` (~20 T-cases, pending)
**Ship target:** v1.0.2 (diff), v1.1 (full-spec backfill)

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

Perplexity is search-first, not conversation-first. The "turn" unit is
a query + a cited answer.

| ± | Surface | Notes |
|---|---|---|
| + | Thread with inline citations | Every answer has numbered source footnotes; expected as `citation` attachments |
| + | Related questions panel | Suggested follow-ups rendered as clickable queries |
| + | Focus modes (Web / Academic / Writing / Wolfram / YouTube / Reddit) | Changes answer style; should land in `layer_meta.focus_mode` |
| + | Spaces (shared research collections) | URL: `/space/<id>` |
| + | Deep Research / Research mode | Long multi-source report |
| − | No editing user messages in-place | User re-asks, doesn't edit |
| − | No Custom GPT / Projects / Canvas / Artifacts | |
| − | No model selector visibility | Model name always `null` today (see P3) |
| ≠ | Thread URL | `/search/<id>` or `/thread/<id>` |

## 2. Share-able T-cases (reused from ChatGPT / parent)

- `T01` vanilla query (becomes PX01 vanilla search)
- `T02` streaming (Perplexity streams the answer progressively)
- `T05` code blocks (rare but possible in technical queries)
- `T10` PDF upload (Perplexity Pro supports document Q&A)
- `T11` image upload (Perplexity Pro supports vision)
- `T20` settings negative-capture

## 3. Site-specific T-cases (new)

| ID | Description | Pass condition |
|---|---|---|
| `PX01` | Single search with citations | user query + assistant answer with ≥2 `citation` attachments |
| `PX02` | Follow-up question in same thread | additional turn appended to existing session |
| `PX03` | Focus mode change | `layer_meta.focus_mode` reflects selection (deferred to v1.1 until CaptureEvent v3) |
| `PX04` | Space-scoped thread | `/space/<id>/thread/<id>` URL → session_hint extracts the thread id |
| `PX05` | Deep Research result | long report captured with many `citation` attachments |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/perplexity.content.ts` (245 lines)
- **Unit tests:** `pce_browser_extension_wxt/entrypoints/__tests__/perplexity.content.test.ts` (18 tests)
- **E2E adapter:** `tests/e2e/sites/perplexity.py` (minimal — ~70 lines)
- **Key helpers present:** 6-strategy selector ladder + index-pair fallback, manual-capture bridge
- **Missing helpers:** `getModelName` (returns null by design), `isStreaming` gate, `/share/` skip, `/space/<id>` URL support

## 5. Known gaps (short list)

- **PX1.** `dedupeMessages` uses `slice(0, 240)` key — same failure mode as Gemini's **G10** (long prefix collapse). Status: ✅ CLOSED — mirrored `955cfef`: dedup key now uses full content (`${role}:${content}`). See `perplexity.content.ts:137-148`.
- **PX2.** `isStreaming` gate NOT wired. Status: ✅ CLOSED — shared `isStreaming` helper + Stop/Cancel button detection wired; passed to `createCaptureRuntime`. See `perplexity.content.ts:63-74` + `isStreaming: () => isStreaming(document)` on line 248.
- **PX3.** `getModelName` always returns `null` (hard-coded). Perplexity does surface the model name in the Pro tier header; low-priority improvement. Status: ⬜ OPEN — probably ship in v1.1.
- **PX4.** No `/share/<id>` skip. Perplexity has public thread sharing at `/search/<id>?s=<share-token>`. Status: ⬜ OPEN — URL pattern needs live validation.
- **PX5.** Citations inside `extractText` are STRIPPED by the `[class*='citation']` selector. Semantic citation capture (structured attachments) depends on `pce-dom.ts:extractAttachments` picking them up — unverified. Status: ⬜ OPEN — shared with Copilot MCP5 and Gemini G7.
- **PX6.** `hookHistoryApi: false`. Transitioning between threads requires up to 5s polling. Status: ⬜ OPEN.

## 6. Order of attack

```
Block 1 — smoke (5 min):          PX01 (citations), T20 (settings silent)
Block 2 — shared runtime (10 min): T02 streaming with PX2 fix + PX1 dedupe fix
Block 3 — site-specific (10 min):  PX02 (follow-up), PX04 (Space thread)
Block 4 — heavy (15 min):          PX05 (Deep Research long report)
```

Total ~40 min first pass.

## 7. Shared bug assumptions

- PX1 dedup fix is a pure copy of Gemini G10 — 1 line + regression test.
- PX2 streaming is a pure copy of G2/C2.
- PX5 citation extraction is shared with Copilot MCP5 and Gemini G7 — solving one likely fixes all three.
