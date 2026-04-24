# Grok — Coverage Diff vs CHATGPT-FULL-COVERAGE

**Tier:** **S2** — workflow-critical for the X/Twitter-integrated
slice of the $50+/mo AI-native power user persona (SuperGrok
$30/mo, Grok Heavy $300/mo). Canonical tier definition:
`Docs/stability/SITE-TIER-MATRIX.md`.
**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**E2E depth:** S2 diff runner (pending) + smoke in `tests/e2e/test_capture.py`
**Ship target:** v1.0.2

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

Grok lives on two origins that share the same chat shell:
`grok.com` (standalone) and `x.com/i/grok` (embedded in X).
The persona primarily uses `grok.com` with a SuperGrok account.

| ± | Surface | Notes |
|---|---|---|
| + | DeepSearch / DeeperSearch | Real-time Twitter + web search with citations inline; expected `citation` attachments |
| + | Think mode / Big Brain | Longer reasoning; should surface as `<thinking>…</thinking>` prefix when DOM exposes it |
| + | Twitter / X context grounding | Posts cited inside replies as `citation` attachments with X URL |
| + | Image generation (Aurora / Grok Imagine) | Assistant inline renders generated images; expected `image_generation` attachment |
| + | Voice mode | Real-time voice conversation; transcript capture only, raw audio deferred |
| − | No Custom GPT Store / no Gems / no Projects | |
| − | No explicit Canvas / Artifacts side panel | Code / long outputs stay inline |
| ≠ | Message DOM | `div.tiptap.ProseMirror` input; assistant bubbles use generic `[class*="message"]` selectors |
| ≠ | Thread URL | `/chat/<uuid>` on `grok.com`; `/i/grok?conversation=<id>` on `x.com` |
| ≠ | Model selector | Grok 4 / Grok 4 Heavy / Grok 3 Mini; badge is a small pill near the input |

## 2. Share-able T-cases (reused from ChatGPT / parent)

- `T01` vanilla chat
- `T02` streaming
- `T05` code blocks
- `T07` edit user message (if UI exposes it — Grok frequently doesn't)
- `T08` regenerate
- `T10` PDF / file upload (supported on Pro accounts)
- `T11` image upload (vision)
- `T20` settings negative-capture

## 3. Site-specific T-cases (new)

| ID | Description | Pass condition |
|---|---|---|
| `GK01` | DeepSearch query with Twitter + web citations | assistant message has ≥1 `citation` attachment pointing to X.com **and** ≥1 pointing to an external URL |
| `GK02` | Think mode reasoning capture | assistant content contains `<thinking>…</thinking>` prefix OR thinking metadata on the turn |
| `GK03` | Aurora / Grok Imagine generated image | assistant turn has `image_generation` attachment (not `image_url`) with model = "aurora" / "grok-imagine" |
| `GK04` | `x.com/i/grok` embedded surface | capture fires under `x.com` host with `provider="xai"`; no duplicate capture when the user toggles back to `grok.com` |
| `GK05` | Voice-mode transcript | spoken turns captured as text turns with `source_type: "voice"` metadata if DOM exposes it; skip gracefully if not |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/grok.content.ts` (~5.6 KB)
- **Unit tests:** `pce_browser_extension_wxt/entrypoints/__tests__/grok.content.test.ts` (if present)
- **E2E adapter:** `tests/e2e/sites/grok.py` (~20 lines — needs expansion for GK01-GK05)
- **Host coverage:** `grok.com` + `x.com` (extension matches both)
- **Key helpers present:** input selector for tiptap ProseMirror; response container fallback `[class*="message"], [class*="response"], .prose`; paste-upload fallback
- **Missing helpers:** `getModelName` for Grok 4 / Grok 4 Heavy / Grok 3 Mini; `isStreaming` gate; `/share/` URL skip if Grok exposes one; DeepSearch citation extraction probe

## 5. Known gaps (short list)

- **GK1.** `isStreaming` gate not wired. Same failure mode as G2 / C2 on Claude / Gemini. Status: ⬜ OPEN — mirror the shared helper once the `grok.com` stop-button regex is confirmed.
- **GK2.** `getModelName` not implemented — `conversation.model_name` always empty. Grok's model badge is a small pill near the input; needs a live-DOM probe to pin the selector. Status: ⬜ OPEN.
- **GK3.** DeepSearch citation extraction path unverified. Current `extractAttachments` may strip inline citation chips via `[class*='citation']`. Shared gap with Copilot MCP5 + Perplexity PX5 + Gemini G7. Status: ⬜ OPEN.
- **GK4.** `x.com/i/grok` embedded surface: host is correctly matched, but the X shell wraps Grok in its own router and pushState; no pushState hook in `grok.content.ts` means up-to-5s lag on navigation. Status: ⬜ OPEN (acceptable for v1.0).
- **GK5.** Aurora / Grok Imagine images may get mis-classified as `image_url` uploads if the asset URL lands on a CDN without a generation marker. Needs a real Pro-account probe. Status: ⬜ OPEN.
- **GK6.** Voice mode is a separate Grok UI; no adapter path today. Status: ⬜ LOW PRIORITY (voice capture is a Pro-layer problem via L3d / L4c).

## 6. Order of attack

```
Block 1 — smoke (5 min):            T01 vanilla, T20 (/settings silent)
Block 2 — streaming + runtime (10 min): T02 with GK1 fix + getModelName (GK2)
Block 3 — DeepSearch (10 min):      GK01 (citations), GK03 (Aurora image)
Block 4 — X-embedded (10 min):      GK04 (x.com/i/grok host), verify no double-capture
Block 5 — shared (10 min):          T05, T08, T10, T11
```

Total ~45 min first pass.

## 7. Shared bug assumptions

After ChatGPT + S1 autopilots green:
- GK1 is a mirror of G2/C2 — 3-line change.
- GK3 citation extraction is shared with Copilot MCP5, Perplexity PX5, Gemini G7 — solving one fixes all.
- `getModelName` pattern (GK2) can reuse the shared 8-selector ladder from DeepSeek's `deepseek.content.ts`.
