# Site Coverage — DIFF Template (S2 / S3 sites)

**Purpose:** this template captures just the **delta** between a new
site and `CHATGPT-FULL-COVERAGE.md`. Use it when you don't need a
full 200-300 line spec because the site is either structurally
similar to an already-specced site or is low-traffic (S2/S3 tier).

**Tier reminder (from P5B plan):**

- **S0 / S1** — use the full spec structure (`CHATGPT-FULL-COVERAGE.md`
  / `CLAUDE-FULL-COVERAGE.md` / `GEMINI-FULL-COVERAGE.md` /
  `GOOGLE-AI-STUDIO-FULL-COVERAGE.md`). ~20 T-cases per site.
- **S2 (this template, ~40 lines)** — 8-12 T-cases, diff-from-S0
  listing. Target: Copilot, M365-Copilot, Perplexity, DeepSeek.
- **S3 (this template, shorter, ~15 lines)** — 3-5 T-cases smoke. Target:
  Grok, Kimi, ZhiPu, Mistral, Manus, Poe, HuggingFace, Figma, Notion, Gmail.

---

## Template (copy and fill)

```markdown
# <Site Name> — Coverage Diff vs CHATGPT-FULL-COVERAGE

**Tier:** S2 / S3
**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited verbatim)
**Ship target:** v1.0.<x>

---

## 1. Surfaces diff (vs ChatGPT's 21 surfaces)

| ± | Surface | Notes |
|---|---|---|
| + | <site-specific feature> | e.g. "Copilot: GitHub repo context panel" |
| + | <site-specific feature 2> |  |
| − | <surface ChatGPT has but this site lacks> | e.g. "no Custom GPT Store" |
| ≠ | <surface rendered differently> | e.g. "Artifacts vs Canvas, side-panel DOM is outside chat turn" |

## 2. Share-able T-cases (reused from ChatGPT / parent)

List T-case IDs that apply with only selector substitutions:

- `T01` vanilla chat
- `T02` streaming
- `T05` code blocks
- `T07` edit
- `T08` regenerate
- `T10` PDF upload
- `T11` image upload
- `T20` settings negative-capture

## 3. Site-specific T-cases (new)

Each row is verifiable, deterministic, and has a one-line pass condition.

| ID | Description | Pass condition |
|---|---|---|
| `X01` | <e.g. Copilot GitHub repo context> | assistant message references repo name with valid URL citation |
| `X02` | <second site-specific case> | |

## 4. Current implementation audit

- **Content script:** `pce_browser_extension_wxt/entrypoints/<site>.content.ts` (<N> lines)
- **Unit tests:** `pce_browser_extension_wxt/entrypoints/__tests__/<site>.content.test.ts` (<M> tests)
- **E2E adapter:** `tests/e2e/sites/<site>.py` (<K> lines)
- **Missing helpers:** e.g. `getModelName`, `isStreaming` gate, manual-capture bridge

## 5. Known gaps (short list, ≤6 items)

Use IDs scoped to the site: `<Prefix><number>` (e.g. `P1` for Perplexity, `D1` for DeepSeek).

- **<prefix>1.** <one-line gap>. Status: ✅ CLOSED / 🔸 CLARIFIED / ⬜ OPEN — [commit hash or "needs autopilot"]
- **<prefix>2.** ...
- ...

## 6. Order of attack

```
Block 1 — smoke (5 min): T01, T20-equivalent
Block 2 — site-specific (10 min): X01, X02
Block 3 — shared (15 min): T05, T07, T08, T10, T11
```

Total ~30 min for first pass.

## 7. Shared bug assumptions

After ChatGPT + S1 autopilot is green:

- `pce-dom.ts` attachment extractors ≈ 70% coverage → expect ~70% of
  similar gaps to close "for free" on this site.
- `capture-runtime.ts` streaming gate / fingerprint / pushState hook →
  benefits any site that uses them.
- Site-specific residue is typically: selector drift, model name
  regex, site-specific DOM for canvas/artifacts/projects.
```

---

## S3 template (shorter — ~15 lines)

For truly-simple sites (basic chat only, rarely visited):

```markdown
# <Site Name> — S3 Smoke Coverage

**Parent spec:** `CHATGPT-FULL-COVERAGE.md` (Parts IV-VI inherited)

## Surfaces (abbreviated)

- Vanilla chat only. No Projects / Canvas / custom GPTs / image gen.
- URL shape: `<url-pattern>`.

## T-cases (4-5 only)

| ID | Description | Pass condition |
|---|---|---|
| `T01` | Vanilla | 1 user + 1 assistant captured |
| `T05` | Code block | assistant attachment has `code_block` |
| `T10` | Attachment (if supported) | user has `file` attachment |
| `T20` | Settings negative | 0 new captures |

## Known gaps

Typically none beyond shared-runtime items. Any real bug surfaces
during autopilot live run.
```

---

## Working rules for this template

1. **Never duplicate Parts IV-VI.** The collaboration protocol, order
   of attack logic, and between-rounds work are the same for every
   site. Link to the parent spec; don't copy.
2. **Keep the known-gaps section ≤ 6 items.** If you find more, the
   site probably deserves a full spec (promote to S1).
3. **Commit hash convention in status:** use the short hash (7 chars)
   that closed the gap. If open, write the blocker (`"needs autopilot"`
   or `"needs DOM probe"`).
4. **Reuse T-case IDs where possible** to make cross-site comparison
   trivial. A `T10` PDF upload case should mean the same thing on
   Claude and Perplexity, modulo selector differences.
5. **No spec drift.** When a gap is closed, update BOTH the gap list
   status AND the II.3 runtime table (if applicable), in the same
   commit as the code fix.
