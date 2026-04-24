# Site Tier Matrix — $50+/mo AI-Native Power User Alignment

**Status:** canonical source of truth for site tiering, coverage-doc
format, and E2E test depth. Supersedes the volume-first tiering
previously implied by `P5B-PLAN.md §Inventory` (Tier 1 P0/P1/P2).

**Adopted:** 2026-04-25.
**Reviewer target:** every agent / contributor touching `Docs/stability/*`,
`pce_browser_extension_wxt/entrypoints/*.content.ts`, or
`tests/e2e/sites/*.py`.

---

## 1. Target persona — why this matrix exists

PCE's v1.x target is **not** the mass consumer. It is the
**$50+/mo AI-native power user**:

- Pays for **multiple** AI subscriptions simultaneously
  (ChatGPT Plus/Pro + Claude Pro/Max + Perplexity Pro, plus often
  Cursor / Copilot / Gemini Advanced).
- Uses AI as a **daily indispensable tool** — coding, research,
  writing, planning, not as a toy.
- Spreads across platforms: **no single vendor lock-in**; the
  same question might hit ChatGPT for reasoning, Claude for long
  context, Perplexity for sources, Gemini for Drive / Docs
  integration, GAS for API tuning.
- Tolerates installation friction (CA + proxy + extension) in
  exchange for genuine capability.
- Aligns with the developer-first release-strategy decision
  (`D-3 = A` in `Docs/docs/decisions/2026-04-18-ucs-and-release-strategy.md`).

### What this persona is NOT

- **Not** a mass ChatGPT-only consumer.
- **Not** a Chinese consumer-market user whose daily tool is
  豆包 / Yuanbao / Tongyi. Those surfaces have enormous raw MAU
  but almost zero overlap with the $50+/mo persona and add no
  $ to the captured value-per-session.
- **Not** a casual user evaluating free tiers of everything.

### Implication for tiering

Ranking by **raw global MAU** mis-weights the matrix. A site
with 300M casual MAU but zero paid penetration in our persona
is worth **less** than a site with 20M users where >40% of our
persona visits daily. The tier bands below explicitly collapse
MAU and instead weight by:

- `P(power_user_uses_daily)`
- `P(power_user_pays_$20+/mo)`
- `workflow_indispensability` (1-5)
- `capture_distinctness` (does capturing here add value the
  other tiers already cover? e.g. "yet another GPT-4 wrapper"
  scores low)

---

## 2. Tier definitions

Each tier defines a **coverage-doc format**, an **E2E test depth**,
and a **release gate**.

| Tier | Label | Coverage-doc format | E2E depth | Release gate |
|---|---|---|---|---|
| **S0** | Indispensable daily | FULL (ChatGPT-parity, Parts I-VI, ~20 T-cases, ~250-350 lines) | Full autopilot runner (`test_<site>_full.py`, 20 cases) | ≥95% T-case pass rate before any tag |
| **S1** | High-value frequent | FULL spec (same format) | Full autopilot runner | ≥90% T-case pass rate before v1.0 |
| **S2** | Workflow-critical for a segment | DIFF vs ChatGPT (~8-12 T-cases, Parts IV-VI inherited) | Diff runner — shared T01/T02/T05/T07/T08/T10/T20 + 2-5 site-specific X-cases | ≥80% before v1.1 |
| **S3** | Breadth coverage | SMOKE (~15 lines, 3-5 T-cases) | Single-message smoke in `test_capture.py` | 1 capture round-trip |
| **SX** | Scaffolding (DOM-unverified) | STUB + action-item list | None — live-DOM probe required first | Honest "not ready" label in the Store listing |

### S0 — Indispensable daily drivers

Every $50+/mo AI power user uses these **daily**. First-minute
impression of the extension is made on these two.

- `ChatGPT` — web (`chatgpt.com`)
- `Claude` — web (`claude.ai`)

If the extension captures these badly, nothing else rescues it.

### S1 — High-value frequent use

The majority of the persona uses **at least one** of these
several times per week. Subscription overlap with S0 is common
(Gemini Advanced / Perplexity Pro bundled alongside ChatGPT Plus).

- `Gemini` — web (`gemini.google.com`), **including NotebookLM
  in-app** (see §4)
- `Google AI Studio` — (`aistudio.google.com`) — developer-
  tuning front-end to Gemini API; distinct from Gemini web
- `Perplexity` — (`perplexity.ai`) — power-user search replacement

Perplexity is **promoted** from S2 (was P1/MEDIUM in P5B volume
tiering) because of Perplexity Pro $20/mo adoption in the
persona.

### S2 — Workflow-critical for a segment

Not daily for everyone, but daily for a well-defined persona
slice. Capturing these meaningfully differentiates PCE from a
"just ChatGPT" logger.

- `Copilot` — Microsoft Copilot (`copilot.microsoft.com`) —
  Bing-grounded, enterprise / researcher segment
- `Grok` — (`grok.com`, `x.com`) — Twitter-integrated power
  users, SuperGrok $30/mo segment
- `DeepSeek` — (`chat.deepseek.com`) — OSS-devs / R1-thinking
  researchers / CN market overlap

### S3 — Breadth coverage

Low weight per power-user session, kept for store-listing breadth
and to exercise the generic extractor on diverse DOMs. A capture
failure on any of these should not block a release.

- `HuggingFace Chat` — (`huggingface.co/chat`) — dev niche
- `Poe` — (`poe.com`) — multi-model aggregator, shrinking
- `Kimi` — (`kimi.com`) — CN long-context
- `ZhiPu ChatGLM` — (`chatglm.cn`) — CN research
- `Mistral Le Chat` — (`chat.mistral.ai`) — EU niche
- `Manus` — (`manus.ai`) — emerging agent

### SX — Scaffolding (DOM-unverified, account-gated)

Content scripts exist but no live-DOM probe has happened. Each
of these is honestly labelled NOT READY in its spec and in the
Store listing until a user with the right account validates the
selectors.

- `M365 Copilot` — (`m365.cloud.microsoft`) — paid M365 only
- `Notion AI` — (`notion.so`) — `/ai` inline block
- `Gmail "Help me write"` — (`mail.google.com`) — Workspace only
- `Figma AI` — (`figma.com`) — First Draft / Jambot / Make

### Explicitly OUT of scope for v1.x

These are deliberate non-goals for the $50+/mo persona and must
not be smuggled back in via a "more sites = better" instinct.

- **豆包 / Doubao** — consumer CN, zero overlap with persona.
- **Yuanbao / Tongyi Qianwen / Baichuan consumer apps** —
  same reasoning.
- **Midjourney** — Discord-only primary surface; browser
  extension layer cannot reach it. Covered by L3d / L3e in later
  phases.
- **Character.ai / Replika** — entertainment-first, not the
  $50+/mo workflow persona.
- **Standalone mobile-first apps with no web UI** — Perplexity
  mobile is tracked via the web page; native apps are a Pro
  capture problem (L0 / L4b).

---

## 3. Full ranking table

| Rank | Site | Tier | Persona penetration¹ | Indispensability² | Coverage doc | E2E runner |
|---:|---|---|---:|---:|---|---|
| 1 | ChatGPT | S0 | ~95% | 5 | `CHATGPT-FULL-COVERAGE.md` | `test_chatgpt_full.py` (20 cases) |
| 2 | Claude | S0 | ~80% | 5 | `CLAUDE-FULL-COVERAGE.md` | `test_claude_full.py` (20 cases) |
| 3 | Gemini | S1 | ~65% | 4 | `GEMINI-FULL-COVERAGE.md` (inc. NotebookLM §4) | `test_gemini_full.py` (20 cases) |
| 4 | Google AI Studio | S1 | ~45% (dev slice) | 4 | `GOOGLE-AI-STUDIO-FULL-COVERAGE.md` | `test_googleaistudio_full.py` (20 cases) |
| 5 | Perplexity | S1 | ~55% | 4 | `PERPLEXITY-COVERAGE-DIFF.md` (upgrade-to-full pending) | diff runner pending; smoke in `test_capture.py` |
| 6 | Copilot (MS) | S2 | ~25% (enterprise slice) | 3 | `COPILOT-COVERAGE-DIFF.md` | diff runner pending; smoke pending |
| 7 | Grok | S2 | ~20% (X/Twitter slice) | 3 | `GROK-COVERAGE-DIFF.md` | diff runner pending; smoke in `test_capture.py` |
| 8 | DeepSeek | S2 | ~25% (OSS-dev slice) | 3 | `DEEPSEEK-COVERAGE-DIFF.md` | diff runner pending; smoke in `test_capture.py` |
| 9 | HuggingFace | S3 | ~10% | 2 | `HUGGINGFACE-SMOKE.md` | smoke in `test_capture.py` |
| 10 | Poe | S3 | ~8% | 2 | `POE-SMOKE.md` | smoke in `test_capture.py` |
| 11 | Kimi | S3 | ~15% (CN slice) | 2 | `KIMI-SMOKE.md` | smoke in `test_capture.py` |
| 12 | ZhiPu | S3 | ~8% (CN slice) | 2 | `ZHIPU-SMOKE.md` | smoke in `test_capture.py` |
| 13 | Mistral | S3 | ~12% (EU slice) | 2 | `MISTRAL-SMOKE.md` | smoke in `test_capture.py` |
| 14 | Manus | S3 | ~5% | 2 | `MANUS-SMOKE.md` | smoke in `test_capture.py` |
| 15 | M365 Copilot | SX | gated | — | `M365-COPILOT-COVERAGE-DIFF.md` (scaffolding label) | blocked by account |
| 16 | Notion AI | SX | gated | — | `NOTION-COVERAGE-DIFF.md` (stub) | blocked by DOM probe |
| 17 | Gmail Help-me-write | SX | gated | — | `GMAIL-COVERAGE-DIFF.md` (stub) | blocked by Workspace |
| 18 | Figma AI | SX | gated | — | `FIGMA-COVERAGE-DIFF.md` (stub) | blocked by DOM probe |

¹ Penetration = `P(persona visits this site in a typical week)`, subjective estimate.
² Indispensability = 1 (trivial) … 5 (cannot do job without it).

---

## 4. NotebookLM — folded into Gemini

**Decision (2026-04-25):** NotebookLM (`notebooklm.google.com`) does
**not** get a standalone content script or adapter. Google has
integrated NotebookLM's notebook-sources UX into the main Gemini
web app: selecting a notebook as a chat source is a flow
**inside `gemini.google.com`**, not a navigation to a separate
origin.

### Coverage implication

- `GEMINI-FULL-COVERAGE.md` already lists NotebookLM-notebook
  source as **Surface 28**: "NotebookLM notebook source".
- Test case for NotebookLM-as-source is covered by Gemini's
  attachment / Drive-source T-cases (G14, G29, G30 family).
- If/when Google re-separates NotebookLM as a distinct origin or
  ships workspace-scoped chat that can't be reached via Gemini,
  promote to S1 with its own spec.

### What happens at `notebooklm.google.com`

The notebook-editor surface exists for creating/editing
notebooks. This is **not a chat surface** for our purposes —
no user/assistant turn exchange happens there; the AI surfaces
back inside the Gemini chat shell. Therefore:

- **No** capture on `notebooklm.google.com` is desired today.
- If we observe ambient noise from the notebook editor in Pro
  capture traces, silence it at the host-exclusion layer, not by
  inventing a placeholder adapter.

### Review trigger

Re-evaluate this decision if any of the following becomes true:

1. NotebookLM launches a **first-class chat mode** at
   `notebooklm.google.com/chat/<id>` that Gemini web does not
   mirror.
2. Workspace admins **disable Gemini integration** but keep
   NotebookLM; captures would otherwise be lost for that slice.
3. NotebookLM exposes a dedicated **audio-overview stream** at
   its own origin before Gemini exposes it. (Already a v1.2
   Gemini surface per Gemini spec §I.1 #15.)

---

## 5. E2E test-level matrix

| Tier | Runner | Cases | Runtime | Owner |
|---|---|---:|---|---|
| S0 | `tests/e2e/test_<site>_full.py` | ~20 T-cases per site | 30-60 min | autopilot + user |
| S1 | `tests/e2e/test_<site>_full.py` | ~20 T-cases per site | 30-60 min | autopilot + user |
| S2 | `tests/e2e/test_<site>_diff.py` (scaffold pending) | 8-12 | 15-30 min | autopilot |
| S3 | `tests/e2e/test_capture.py` (smoke) | 1 | <5 min | autopilot |
| SX | none — blocked on account or DOM probe | — | — | user |

### S0 / S1 full runner — present state

| Site | Runner file | # cases | Latest best-of status |
|---|---|---:|---|
| ChatGPT | `tests/e2e/test_chatgpt_full.py` | 20 | 19 ✅ / 1 ⏸ (T09 UI-blocked) |
| Claude | `tests/e2e/test_claude_full.py` | 20 | runner exists, autopilot pending |
| Gemini | `tests/e2e/test_gemini_full.py` | 20 | runner exists, autopilot pending |
| Google AI Studio | `tests/e2e/test_googleaistudio_full.py` | 20 | runner exists, autopilot pending |
| Perplexity | **pending** — upgrade from smoke | 20 | needs scaffold |

### S2 diff runner — design

Each S2 site shares a common core of T-cases with ChatGPT and
adds 2-5 site-specific X-cases. The proposed runner is:

```
tests/e2e/test_s2_diff.py          # shared shell, parametrised by site
tests/e2e/sites/copilot.py         # adapter (exists, ~60 lines — expand)
tests/e2e/sites/grok.py            # adapter (exists, ~20 lines — expand)
tests/e2e/sites/deepseek.py        # adapter (exists, ~200 lines)
```

Shared T-cases to always run for S2:
`T01 vanilla`, `T02 streaming`, `T05 code-block`, `T07 edit`,
`T08 regenerate`, `T10 PDF upload` (if the site supports it),
`T20 settings-silent`.

Site-specific X-cases come from the per-site `COVERAGE-DIFF.md`
§3 table.

### S3 smoke runner — present state

`tests/e2e/test_capture.py` parametrises one capture round-trip
per site. Currently wired: chatgpt, claude, deepseek, zhipu,
gemini, googleaistudio, grok, kimi, manus, perplexity, poe.

Missing from the smoke matrix: **mistral, huggingface**. Added
in this deployment (see §8).

---

## 6. Coverage-doc format obligations

Every site must have a doc in `Docs/stability/`. Obligations by
tier:

### S0 / S1 — FULL spec

Parts I-VI, following `CHATGPT-FULL-COVERAGE.md`. Specifically:

- **Part I — What "full coverage" means** (product surfaces
  table, official source map, meta-capture invariants).
- **Part II — Current implementation audit** (extraction
  strategies, helpers, runtime behaviours, known gaps with IDs
  prefixed by site-letter — G1..Gn for Gemini, C1..Cn for
  Claude, A1..An for AI Studio).
- **Part III — Live validation matrix** (T-case table with
  Status column + evidence directory + best-of run timestamp).
- **Parts IV-VI — inherited** from `CHATGPT-FULL-COVERAGE.md`
  (collaboration protocol, order-of-attack, between-rounds
  work). Do not duplicate.

### S2 — DIFF spec

Use `SITE-COVERAGE-DIFF-TEMPLATE.md`:

1. Surfaces diff (+/−/≠ vs ChatGPT's 21 surfaces).
2. Share-able T-cases (list IDs only).
3. Site-specific T-cases (2-5 X-cases).
4. Current implementation audit (script/tests/adapter paths + helpers present/missing).
5. Known gaps (≤6 items, ID prefix by site-letter).
6. Order of attack (10-40 min).
7. Shared bug assumptions (link back to sibling sites).

### S3 — SMOKE spec

~15 lines. Exists mainly to pin down:

- URL shape + what's in-scope vs out-of-scope.
- 3-5 T-cases (usually `T01`, `T05`, `T10` if supported, `T20`).
- Known gaps section (almost always "none beyond shared-runtime
  items").

### SX — STUB + action-item

Marked NOT READY at the top. Lists what's blocking (account,
DOM probe, paid tier) and the concrete next step. Prevents this
surface from being treated as "done" by anyone scanning the doc
index.

---

## 7. Acceptance criteria mapped to tiers

Folded into the v1.0 criteria from
`Docs/tasks/TASK-006-P5A-subscription-capture.md`:

| v1.0 criterion | Tier required |
|---|---|
| ChatGPT web capture success ≥ 98% | S0 |
| New user "install → first capture" P95 ≤ 5 min | S0 (ChatGPT) |
| Core S0+S1 sites pass their full runner ≥ 90% | S0 + S1 |
| S2 sites pass their diff runner ≥ 80% | S2 |
| S3 sites pass single-message smoke | S3 |
| SX sites honestly labelled "not ready" in Store | SX |

---

## 8. Deployment of this matrix — what gets committed

This is an index of the files created/updated to enforce this
matrix. Cross-check against the git diff of the landing commit.

| File | Action |
|---|---|
| `Docs/stability/SITE-TIER-MATRIX.md` (this file) | NEW |
| `Docs/stability/CHATGPT-FULL-COVERAGE.md` | tier label = S0 in header |
| `Docs/stability/CLAUDE-FULL-COVERAGE.md` | tier label = S0 in header |
| `Docs/stability/GEMINI-FULL-COVERAGE.md` | tier label = S1 + NotebookLM §I.4 addendum |
| `Docs/stability/GOOGLE-AI-STUDIO-FULL-COVERAGE.md` | tier label = S1 in header |
| `Docs/stability/PERPLEXITY-COVERAGE-DIFF.md` | tier promoted to S1 (diff preserved with "full-spec backfill pending" note) |
| `Docs/stability/COPILOT-COVERAGE-DIFF.md` | tier label = S2 |
| `Docs/stability/DEEPSEEK-COVERAGE-DIFF.md` | tier label = S2 |
| `Docs/stability/GROK-COVERAGE-DIFF.md` | NEW (S2) |
| `Docs/stability/HUGGINGFACE-SMOKE.md` | NEW (S3) |
| `Docs/stability/POE-SMOKE.md` | NEW (S3) |
| `Docs/stability/KIMI-SMOKE.md` | NEW (S3) |
| `Docs/stability/ZHIPU-SMOKE.md` | NEW (S3) |
| `Docs/stability/MISTRAL-SMOKE.md` | NEW (S3) |
| `Docs/stability/MANUS-SMOKE.md` | NEW (S3) |
| `Docs/stability/M365-COPILOT-COVERAGE-DIFF.md` | tier label = SX + NOT READY banner |
| `Docs/stability/NOTION-COVERAGE-DIFF.md` | NEW (SX stub) |
| `Docs/stability/GMAIL-COVERAGE-DIFF.md` | NEW (SX stub) |
| `Docs/stability/FIGMA-COVERAGE-DIFF.md` | NEW (SX stub) |
| `Docs/stability/P5B-PLAN.md` | add supersession note at top pointing here |
| `Docs/stability/SITE-COVERAGE-DIFF-TEMPLATE.md` | tier section updated to match S0-SX |
| `tests/e2e/test_capture.py` | add Mistral + HuggingFace adapters to smoke matrix |

Follow-up tasks (NOT in the landing commit — tracked separately):

- `test_s2_diff.py` scaffold + parametrised runner (S2 uplift).
- `test_perplexity_full.py` scaffold (S1 uplift from diff to full).
- Promote Perplexity diff → full spec when the full runner lands.
- Live-DOM probes on SX sites (M365 / Notion / Gmail / Figma).

---

## 9. Working rules (don't drift)

1. **Tier is declared in the spec header** — `**Status:** S<n> tier.`
   Missing or stale tier labels make the matrix meaningless.
2. **Changing a site's tier requires updating this doc**, not
   just the per-site spec. This doc is the index.
3. **Volume ≠ tier.** If you ever feel tempted to promote a
   site because "but it has 200M MAU", re-read §1. MAU outside
   our persona is noise.
4. **Persona checkbox for new sites:** before adding a new
   content script, answer:
   - Does the $50+/mo persona use this weekly?
   - What fraction pays a recurring subscription?
   - Is the capture distinct from sites we already cover?
   If all three score low, park it in SX or drop it entirely.
5. **NotebookLM-style "folded-into-parent" decisions** belong in
   the parent site's spec, not as ghost standalone specs.
