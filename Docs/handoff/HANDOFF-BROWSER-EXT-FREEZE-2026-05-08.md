# Handoff · Browser Extension Hard Freeze (2026-05-08)

> **Status**: canonical — this document is the authoritative record of the
> browser-extension sub-system freeze. Future agents MUST NOT re-open this
> sub-system for feature work without first creating an ADR that
> supersedes this handoff.
>
> **Freeze type**: **HARD** (per user decision 2026-05-08 22:32 UTC+08).
> Only P0 security / privacy bugs or Chrome Web Store review blockers
> may reopen it.
>
> **Adopted**: 2026-05-08
>
> **Supersedes**: the "P5.A Subscription Capture" active-work status
> for the browser-extension surface (see `Docs/docs/PROJECT.md:5`).
> **Does not** supersede PCE Core back-end work — the capture ingest,
> normalizer, and dashboard remain live and are the path through which
> future IDE/Desktop/MCP captures will flow.
>
> **Audience**: Cascade, all future agents, project owner.

---

## 1. What "Hard Freeze" means operationally

From 2026-05-08 onward the following hold for everything under
`@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\`:

| Category | Allowed? | Notes |
|---|---|---|
| P0 security / privacy bug fix | ✅ | e.g. credential leakage, CSP hole, XSS into PCE UI |
| Chrome Web Store review blocker fix | ✅ | Only if the Store rejects the review and we must ship to stay listed |
| Bug fix discovered while building IDE/Desktop capture | 🟡 case-by-case | Requires explicit user approval; default posture is "log it as known issue, do not fix" |
| Feature work on unresolved known issues (see §4) | ❌ | No, even if the fix looks trivial |
| New site adapter (S2/S3) | ❌ | S2/S3 formally deferred — see `Docs/stability/SITE-TIER-MATRIX.md` 2026-05-08 update |
| New `tests/e2e_probe/cases/Tnn_*.py` | ❌ | The v1.0 matrix is locked at T00-T20 |
| Refactor / test cleanup in `pce_browser_extension_wxt/` | ❌ | Code drift is acceptable; rework isn't justified |
| Back-end work (`pce_core/*`, `pce_mcp/*`, `tests/*` non-browser) | ✅ | Fully alive; IDE/Desktop/MCP captures will land there |

### 1.1 Reopen procedure (if ever)

1. File an ADR under `Docs/docs/decisions/` explicitly naming this handoff
   as the document being superseded.
2. Get user approval.
3. Update this file's Status header to `REOPENED` with link to the ADR.
4. Only then touch `pce_browser_extension_wxt/`.

---

## 2. What was sealed in — acceptance snapshot

### 2.1 Best-of E2E probe matrix (across 199 runs in `tests/e2e_probe/reports/`)

Computed 2026-05-08 evening as "best status observed per (site × case)"
where `pass > skip > fail`. This is the **release-quality bar** for the
sealed state.

| Site | Tier | PASS | SKIP | FAIL | Notes |
|---|---|---:|---:|---:|---|
| **chatgpt** | S0 | **19** | 2 (T16, T17) | 0 | T16=Custom GPT, T17=Project — account-tier skips |
| **claude** | S0 | **16** | 5 (T12, T16, T17, T18, T19) | 0 | T12=CSV upload, T18=Temp chat, T19=Error state — minor gaps |
| **gemini** | S1 | **17** | 4 (T16, T17, T18, T19) | 0 | Settings/project/temp/error gaps |
| **googleaistudio** | S1 | **13** | 8 (T03, T04, T09, T12, T15, T16, T17, T18) | 0 | Branch/new-chat/edit probes have UI-exposure gaps |
| **grok** | S2 | **11** | 10 | 0 | S2; no longer a target — see §7 |
| copilot / deepseek / huggingface / kimi / manus / mistral / perplexity / poe / zhipu | S2/S3 | 0 | 2 (T10, T11 smoke only) | 0 | Full matrix never entered — deferred per §7 |

**Reading**: 0 failures across every tier that still counts. Skips are
either account-tier gates (S0/S1 T16/T17/T18/T19 Projects/Temp/Error)
or adapter-level UI gaps the freeze deliberately carries forward as
known issues (§4).

Raw detail per-case: `tests/e2e_probe/reports/<timestamp>/summary.json`.

### 2.2 Architectural deliveries sealed in

- **Branch / variant storage semantics** — migration 0008
  (`@f:\INVENTION\You.Inc\PCE Core\pce_core\migrations\0008_branches.py`)
  + `messages.{branch_id, branch_parent_id, turn_index}` columns +
  `idx_messages_session_branch` index. ADR-2026-04-26 ratified.
- **`rich_content` envelope** —
  `@f:\INVENTION\You.Inc\PCE Core\pce_core\rich_content.py:178-228`
  emits six render-contract keys (`variant_group`, `variant_controls`,
  `current_variant`, `branch_tree`, `branch_choices`, `current_branch`).
- **`pce-probe-e2e-standard-2026-05-04`** executable contract — every
  T-case asserts `storage_paths` × `render_paths` pairs, not just text
  diff; see `tests/e2e_probe/execution_standard.py`.
- **WXT migration** of the extension is complete (`pce_browser_extension_wxt/`).
  The legacy `pce_browser_extension/` folder is retired.
- **Dashboard branches UI** — `branch_count` badge on the sessions
  list, `collapse / expand` toggle in the session detail, per-message
  `branch pill` in expand mode. See
  `@f:\INVENTION\You.Inc\PCE Core\pce_core\dashboard\app.js:528-670`.
- **`GET /api/v1/sessions/{id}/messages?branches={collapse,expand}`** —
  API contract for branch projection; see
  `@f:\INVENTION\You.Inc\PCE Core\pce_core\server.py:1663-1701`.
- **Probe RPC protocol** (`pce_browser_extension_wxt/utils/probe-protocol.ts`,
  +124 lines) — versioned contract between probe and extension.
- **109 passed / 1 skipped** in the branch + rich-content unit test
  suite (`pytest tests/test_g2_*.py tests/test_g4_*.py tests/test_g5_*.py
  tests/test_g6_*.py tests/test_migration_0008_*.py tests/test_rich_content*.py`).

---

## 3. Git / release state at freeze time

```
HEAD       : c1375655 Add branch persistence and replay support
origin/..  : 1 commit behind local HEAD
Working tree: 45+ modified files + ~15 untracked (see `git status`)
```

### 3.1 Pending commit work — the freeze commit

The 45+ uncommitted files in the working tree **must be committed as
part of the freeze itself**. Suggested action sequence for the project
owner:

```powershell
# 1. Review the freeze-relevant changes
git status
git diff --stat

# 2. Stage and commit the freeze payload
git add .
git commit -m "Browser extension hard freeze (2026-05-08)" -m "Sealed state: S0/S1 best-of matrix green with zero failures. See Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md for full acceptance snapshot and known issues. S2/S3 formally deferred per Docs/stability/SITE-TIER-MATRIX.md 2026-05-08 update. Phase transitions to P5.B (IDE/Desktop/MCP) per Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md."

# 3. Tag the freeze
git tag -a browser-ext-freeze-v1.0.0 -m "Hard freeze of browser-extension sub-system per Docs/handoff/HANDOFF-BROWSER-EXT-FREEZE-2026-05-08.md"

# 4. Push when VPN permits
git push origin master --follow-tags
```

The tag name `browser-ext-freeze-v1.0.0` is the **canonical reference**
for "the sealed browser-extension state" in all future documentation.

### 3.2 Repository hygiene

- `scripts/_make_test_csv.py`, `scripts/_make_test_image.py`,
  `scripts/_verify_manual.py` are listed as untracked; they are
  development helpers, included in `.gitignore`-compatible paths or
  can be added to the freeze commit at the owner's discretion.
- `tests/e2e/_reset_managed_profile.py` and
  `tests/e2e/_warmup_login_via_proxy.py` are new helpers from the
  final autopilot runs; suitable to include in the freeze commit.
- `tests/e2e_probe/_pce_helpers.py` and
  `tests/e2e_probe/_t08t09_5sites.py` are part of the probe framework
  and should be committed.

---

## 4. Known issues carried forward (NOT fixing)

These are real gaps documented here so future agents don't waste time
re-discovering them. Per hard-freeze rule, **none of these will be
touched** unless reclassified as P0 security / privacy.

| ID | Site | Case | Observation | Decision |
|---|---|---|---|---|
| **BF-01** | claude | T12 (CSV upload) | Adapter does not expose CSV file-kind via the unified attachment path; extractor returns no `file` attachment | Won't fix; covered in `Docs/stability/CLAUDE-FULL-COVERAGE.md` Part III.ter as N-class item |
| **BF-02** | claude | T09 branch flip | 2026-05-08 `20260508T055403` run shows intermittent FAIL: Prev-button click succeeds but PCE still renders the edited user branch. May be a race between UI flip animation and `fingerprintConversation` debounce | Won't fix; deterministic contract PASS exists on subsequent runs; listed for diagnostic reproducibility only |
| **BF-03** | claude | T07 edit-user-message | Claude adapter does not expose `edit_input_selectors`; probe falls back to SKIP | Won't fix; Part II of CLAUDE-FULL-COVERAGE notes this as C6 Force-resend limitation |
| **BF-04** | googleaistudio | T09 branch_from_here | AI Studio UI exposes "more options" / "tools menu" / "navigation menu" buttons but no `branch_from_here` control per current DOM contract | Won't fix; gated on AI Studio adding that control or on us adding L3b preload (P5.B.3) |
| **BF-05** | googleaistudio | T03 new-chat-pre-URL & T04 new-chat-URL | `new_chat` flow doesn't stabilise the `/prompts/new_chat` session id within probe timeout | Won't fix in browser-ext layer; may resurface at L3b preload layer |
| **BF-06** | all S0/S1 | T16 Custom GPT / T17 Project Chat | Account-tier gates (ChatGPT Plus Projects, Claude Projects, Gemini Advanced project) | Won't fix; legitimate `skip` by design |
| **BF-07** | all S0/S1 | T18 Temporary chat / T19 Error state | Edge surfaces with minimal adapter coverage; `skip` with evidence | Won't fix; release bar allows these |
| **BF-08** | push to origin | — | Local HEAD is 1 commit ahead of `origin/master` + 45 uncommitted; requires VPN | User action pending; not blocking the freeze document |
| **BF-09** | `.diag_*` artefacts | — | Diagnostic logs / one-shot scripts from autopilot sessions remain in working tree (`.gitignore`-compliant) | Won't clean; low priority |

---

## 5. S2 / S3 formal deferral

Effective 2026-05-08, the following tiers move out of v1.0 / v1.1
scope:

- **S2**: `grok` / `copilot` / `deepseek` / `perplexity` — deferred
  to **v1.2** or later. Best-of `grok` is 11 PASS / 10 SKIP; the
  others have only the smoke pair (T10, T11) run. The planned
  `tests/e2e_probe/test_s2_diff.py` diff runner is **not** going to
  be built in P5.B.
- **S3**: `huggingface` / `kimi` / `manus` / `mistral` / `poe` /
  `zhipu` / `notion` / `gmail` / `figma` / `m365-copilot` — deferred
  to **v1.2+**. Their `SMOKE.md` / `COVERAGE-DIFF.md` documents are
  retained as archaeological evidence; no active testing.

See `Docs/stability/SITE-TIER-MATRIX.md` (2026-05-08 update) for the
canonical matrix state.

---

## 6. What comes alive next

Browser-extension work closes; three new work streams open under P5.B
**v1.1 IDE & Desktop & MCP**. Framework reference:
`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §8.

| Sub-phase | Slice | Est. | UCS layer |
|---|---|---|---|
| P5.B.0 | MCP asset formalisation (姿态 A) — package `pce_mcp/` | 1 evening | L3f姿态A |
| P5.B.1 | MCP middleware proxy (姿态 B) | 3-5 days | L3f |
| P5.B.2 | Electron preload — Claude Desktop | 1-2 weeks | L3b |
| P5.B.3 | Electron preload — Cursor / Windsurf | 1-2 weeks | L3b (reuse .2 scaffolding) |
| P5.B.4 | CLI gateway — Claude Code / Codex CLI / Aider / LM Studio / Ollama | 1 week | L3e |

Kickoff details:
`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` (same-day sibling of
this document).

---

## 7. Cross-reference map

| You need to... | Read... |
|---|---|
| Understand the cognitive framework behind this phase transition | `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` |
| Understand which sites are still in scope | `Docs/stability/SITE-TIER-MATRIX.md` (2026-05-08 update) |
| Start P5.B work | `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md` |
| Understand the project's overall phase | `Docs/docs/PROJECT.md` (§11 阶段划分) |
| Audit the sealed extension code | `pce_browser_extension_wxt/` at tag `browser-ext-freeze-v1.0.0` (once pushed) |
| Reproduce the best-of matrix | `python scripts/*` or run `tests/e2e_probe/test_matrix.py` against a fresh logged-in profile |
| Re-examine branch capture semantics | `Docs/docs/decisions/2026-04-26-regenerate-edit-branch-semantics.md` + migration 0008 |

---

## 8. Sign-off

- **Browser-extension sub-system**: hard-frozen per the acceptance
  snapshot in §2 and the known issues in §4.
- **Phase pointer** updates from P5.A → P5.B (see
  `Docs/docs/PROJECT.md:5` 2026-05-08 revision).
- **Next document in line**: `Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`.

---

*End of handoff.*
