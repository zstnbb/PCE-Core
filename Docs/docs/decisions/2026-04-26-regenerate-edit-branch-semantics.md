# ADR: Branch semantics for Regenerate / Edit-user-message

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-04-26 |
| **Authors** | PCE Core stability sweep |
| **Related** | `Docs/stability/CLAUDE-FULL-COVERAGE.md` C07/C08, follow-up `fu_recon_join`, `fu_branch` |
| **Supersedes** | — |
| **Superseded by** | — |

---

## 1. Context

PCE captures conversations from multiple AI providers via two parallel
channels:

- **DOM channel** — what the user actually sees in the page right now.
- **Network channel** — every `completion` request body / SSE response
  the page issues, regardless of whether the resulting message is
  still on screen.

For linear conversations these two channels agree and the session
reconciler trivially merges them into `[user, assistant, user, ...]`.

For **non-linear** turns the channels disagree. Two production-observed
cases on `claude.ai`:

### C08 — Regenerate (assistant retry)

User prompt → Claude reply v1 → user clicks ↻ Retry → Claude reply v2.

Network captures **two `completion` SSE streams**, each producing a
distinct assistant text. DOM ends up showing only v2 with a `1/2`
branch toggle that the user can navigate but the extractor does not
parse.

Current reconciler output:

```
[1] assistant : reply_v1   (from completion #1 SSE)
[2] user      : prompt     (deduped — same text in both completions)
[3] assistant : reply_v2   (from completion #2 SSE)
```

`session.message_count = 3`, ordering is wall-clock insertion order
(assistant_v1 happens to land before user because the network capture
beat the DOM capture). Looks superficially confused but no data is
lost.

### C07 — Edit user message

User sends prompt_v1 ("APPLES") → Claude replies asst_v1 ("APPLES") →
user edits the message to prompt_v2 ("BANANAS") → Claude replies
asst_v2 ("BANANAS").

Network captures both `completion` requests; each `request_body.prompt`
field carries the **distinct user text** (APPLES vs BANANAS).

Current reconciler output:

```
[1] assistant : APPLES_reply   (from completion #1)
[2] user      : APPLES_prompt  (only — BANANAS is silently dropped)
[3] assistant : BANANAS_reply  (orphan: no preceding BANANAS user msg)
```

`session.message_count = 3`. The user-facing session view now reads
"asked APPLES, got BANANAS reply" — internally inconsistent, even
though the **raw `raw_captures` table contains both prompts intact**
(they live in the `request_body` of each completion capture).

### Why both cases matter

| Concern | C08 (Regenerate) | C07 (Edit) |
|---|---|---|
| Forensic / audit completeness | ✅ raw layer complete | ✅ raw layer complete |
| Customer-facing session view | ⚠️ confusing (extra asst) | ❌ misleading (orphan asst) |
| Downstream RAG / search | ⚠️ duplicate content | ❌ broken pairing |
| UI navigation parity with provider | ❌ no `1/N` toggle | ❌ no `1/N` toggle |

C07 is strictly worse than C08: an apparent contradiction between the
user's question and the AI's answer is the kind of finding that erodes
customer trust the fastest, even when the underlying capture is
faithful.

---

## 2. Decision drivers

1. **Forensic fidelity is non-negotiable.** Whatever model we choose,
   `raw_captures` MUST keep every observed completion request /
   response intact. PCE's value proposition is "we have the receipts."
2. **Session view should never be self-contradictory.** A reader of
   the session list — auditor, customer, downstream automation —
   must be able to take what they see at face value, or be explicitly
   told "this is a branched/edited turn, click to expand."
3. **Reconciler must be cheap to reason about.** It runs on every
   ingest; subtle merge logic that only the original author understands
   is a long-term liability.
4. **Cross-provider portability.** Regenerate / Edit / Branch flip
   exist in some form on every covered provider (ChatGPT C08-equivalent,
   Gemini "show drafts", Copilot "redo", Perplexity "rewrite",
   Claude C07/C08, Grok regenerate, etc.). The model we pick must
   generalize, not bake in Anthropic-isms.
5. **Downstream tooling stability.** Dashboards, RAG indexers, the
   public favourites API — anything reading from `messages` —
   should not be silently broken by a schema change.

---

## 3. Considered options

### Option A — Status quo (flat append)

Keep current behaviour: every observed message text becomes a row in
`messages`, deduped only on **exact text match** within `(session_id,
role)`. Order is wall-clock insertion.

- ✅ Zero migration cost.
- ✅ Forensic fidelity at session level (nothing dropped).
- ❌ Self-contradictory session views (C07 orphan).
- ❌ Confusing message_count (C08 = 3 instead of 2).
- ❌ Downstream consumers cannot tell "branched" from "linear".

### Option B — Replace (keep-latest semantics)

When a new completion arrives whose `parent_message_uuid` matches an
existing assistant row, **overwrite** that row in place. Edit-user
similarly overwrites the user row.

- ✅ Session view always reflects "what the user sees now."
- ✅ message_count stays linear (= 2 for both C07 and C08).
- ❌ **Forensic loss at session layer** — v1 reply / v1 prompt
  vanish from `messages`, can only be reconstructed by manually
  joining `raw_captures`. This violates Driver #1's spirit (raw is
  intact, but the canonical "session" view is lossy).
- ❌ Hard to reverse if the customer / auditor wants to see the
  full edit history later — UI has to query raw layer.
- ❌ Edge case: if user navigates back to v1 in provider UI,
  reconciler would have to overwrite again, creating instability.

### Option C — Explicit branch model

Add `branch_id` and `branch_parent_id` columns to `messages`. Each
completion creates a new branch when its `parent_message_uuid` matches
a prior message that already has a child. Default view shows the
**rightmost** (latest) branch path; `?branch=N` query param exposes
others.

- ✅ Lossless at session layer (every reply preserved with provenance).
- ✅ UI parity with provider (`1/2`, `2/2` toggles natural).
- ✅ Generalizes to ChatGPT branches, Gemini drafts, etc.
- ⚠️ Schema migration: `messages` table + reconciler + dashboard +
  favourites all touched. Estimated 2-3 day refactor.
- ⚠️ Default-branch heuristic ("rightmost" / "latest user choice")
  is itself a small policy decision per provider.

### Option D — Two-tier: raw flat at capture, branch-aware at view

Keep `messages` table flat (Option A's semantics under the hood) but
introduce a **view layer** (`session_messages_view`) and an API
parameter `?branches=collapse|expand` (default `collapse`) that the
dashboard and public API call. `collapse` returns the latest branch
path only; `expand` returns all rows with `branch_id`.

Reconciler additionally stamps `branch_id` and `branch_parent_id` on
ingest using `parent_message_uuid` from the network capture, so the
flat table is sufficient to project either view.

- ✅ Lossless at storage (Option A's strength).
- ✅ Customer-facing default is linear (Option B's UX win) without
  destroying data.
- ✅ Power-users / auditors can opt into the full tree.
- ✅ Migration is additive: existing `messages` rows get
  `branch_id = 0` and stay visible by default. No reads break.
- ⚠️ Slightly more complex reconciler than A, slightly less
  ergonomic than B for naïve readers (have to understand the view).
- ⚠️ Two callers (dashboard + favourites) need to opt into
  `?branches=collapse` explicitly, or default-collapse becomes the
  contract.

---

## 4. Decision

**Adopt Option D — Two-tier with branch metadata + collapse-by-default
view.**

Rationale: it satisfies all five drivers without forcing any single
caller into a worse position than they are in today. Specifically:

- Forensic / raw layer (Driver #1): unchanged, `raw_captures` table
  is the source of truth, untouched.
- Self-consistency at view layer (Driver #2): default
  `branches=collapse` returns the latest branch path, which is what
  the user actually sees in the provider UI. C07's orphan goes away
  in the default view.
- Reconciler simplicity (Driver #3): the only new logic is "stamp
  `branch_id` from `parent_message_uuid`," which is one lookup per
  inserted row. The view layer is plain SQL.
- Cross-provider (Driver #4): every provider's network layer exposes
  parent-message identity in some form (`parent_message_uuid` on
  Anthropic, `conversation_id` + ordinal on OpenAI, etc.), so the
  same `branch_id` semantics generalise.
- Tooling stability (Driver #5): existing readers see the collapsed
  view by default; only opt-in `expand` queries return the full tree.
  Favourites continue to point to specific message rows by ID; if
  a favourited message is on a non-default branch it remains
  accessible via deep-link.

---

## 5. Consequences

### 5.1 Schema (additive)

```sql
ALTER TABLE messages ADD COLUMN branch_id        TEXT NOT NULL DEFAULT '0';
ALTER TABLE messages ADD COLUMN branch_parent_id TEXT;          -- FK to messages.id
ALTER TABLE messages ADD COLUMN turn_index       INTEGER;       -- 0-based within branch
CREATE INDEX idx_messages_session_branch ON messages(session_id, branch_id, turn_index);
```

`branch_id = '0'` is the default / canonical path. New branches get a
short ULID. Existing rows backfill to `'0'` so reads stay correct.

### 5.2 Reconciler

Pseudocode for the new ingest path:

```
on completion_capture:
  parent_uuid := request_body.parent_message_uuid OR null_uuid
  parent_msg  := find_message_by_provider_uuid(parent_uuid)

  if parent_msg has no children with this completion's text:
    branch_id := parent_msg.branch_id  (continue same branch)
  else:
    branch_id := mint_new_branch_id()  (regenerate / edit detected)

  insert message with (branch_id, branch_parent_id = parent_msg.id)

on edit_user_capture (request without parent_uuid → completion mints user_v2):
  same logic; user_v2 starts a new branch from the same parent as user_v1.
```

### 5.3 API surface

```
GET /api/v1/sessions/{id}/messages
    ?branches=collapse   (default — latest path only)
    ?branches=expand     (all rows + branch_id field exposed)
    ?branch={branch_id}  (specific branch path)
```

`message_count` reported on `Session` becomes the count under the
default branch. New field `branch_count` exposes total branches for
power users.

### 5.4 Dashboard

- Default render unchanged from today's UX.
- New affordance: `1/2 ▾` toggle on any message that has siblings
  (`branch_count > 1`). Click to switch the visible path.
- "View all branches" overflow menu links to `?branches=expand`.

### 5.5 Migration

- DDL is purely additive → zero-downtime.
- Reconciler change ships behind feature flag `BRANCHES_V2`; until
  flipped, all new inserts get `branch_id = '0'` (Option A
  behaviour preserved).
- Once enabled, a one-shot backfill job re-derives `branch_id` for
  existing `raw_captures` (best-effort; missing `parent_message_uuid`
  on legacy captures means those sessions stay linear).

### 5.6 Out of scope

- Cross-session branching (e.g. "fork conversation here") — explicitly
  not addressed; can layer on top later using the same `branch_id`
  mechanism.
- Provider-side branch identity (Anthropic's own UUIDs) — we **store**
  them in `provider_metadata` for traceability but the canonical
  `branch_id` is PCE-minted to keep cross-provider reasoning uniform.

---

## 6. Open questions

1. **What counts as "latest" for the default collapse?** Latest by
   wall-clock insertion (simple, what the network observed) vs.
   latest by `parent_message_uuid` chain depth (closer to provider
   intent). Recommend wall-clock for v1; revisit if provider UIs
   disagree.
2. **Should `messages.text` dedup span branches?** Today dedup is
   `(session_id, role, text)`. If `branch_id` is added to the dedup
   key we get cleaner branches but pay a small storage cost on
   regenerate-with-identical-output (rare). Recommend including
   `branch_id` in the dedup key.
3. **Public API breaking change?** Strictly no — `message_count`
   returns the collapsed count by default, which matches the
   provider's user-facing count. The `expand` query is opt-in.

---

## 7. Implementation tasks (informational)

Tracked separately as `fu_branch_v1` after this ADR is accepted:

- [ ] Schema migration (`002X_branches.sql`)
- [ ] Reconciler: `parent_message_uuid` extraction (Anthropic first;
      others adapt one-by-one)
- [ ] API: `?branches=` query param + `branch_count` on Session
- [ ] Dashboard: branch toggle UI
- [ ] Backfill job (best-effort)
- [ ] Test fixtures: C07 (edit) + C08 (regenerate) replay → assert
      `branch_count == 2`, default `messages` returns linear
- [ ] Update `Docs/stability/CLAUDE-FULL-COVERAGE.md` C07/C08 status
      from "raw PASS / session gap" to "PASS"

`fu_recon_join` (tool_use deltas + file uuid join) is **independent**
of this ADR and proceeds in parallel.
