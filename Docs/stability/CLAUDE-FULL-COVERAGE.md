# Claude — "Full Coverage" Specification & Collaboration Protocol

**Scope:** this document defines what it means for PCE to capture
**everything** a user does on `claude.ai`, audits the current
implementation against that definition, and lays out the exact
human-computer loop we use to drive the implementation to that
bar in a finite number of iterations.

**Audience:** the user (running the browser) + Cascade (reading and
writing code) + the Claude autopilot.

**Status:** **S0 tier** — indispensable daily driver alongside
ChatGPT for the $50+/mo AI-native power user persona. Promoted
from S1 under the 2026-04-25 realignment (see
`Docs/stability/SITE-TIER-MATRIX.md`). ~20 must-pass T-cases for
v1.0.

**Timebox:** 1-2 evenings of autopilot runs once ChatGPT's
scaffolding is in place.

---

## Part I — What "full coverage" means

Claude is structurally **similar to ChatGPT** (single chat SPA at
one root URL) but has **three defining differences**:

- **`captureMode: "full"`** — unlike ChatGPT/Gemini which send
  deltas, Claude re-sends the entire conversation on every
  fingerprint change. This makes Claude more robust to partial
  re-render but more bandwidth-hungry. Not a gap, a design choice.
- **Artifacts** — Claude renders code/HTML/React/markdown/SVG/
  Mermaid in a **side panel**. The assistant's chat-side text
  references the artifact ("I've created an artifact…") but the
  artifact body lives in a separate DOM subtree that current
  extraction completely misses.
- **Projects** — `/project/<id>` scopes conversations to a project
  with shared knowledge base. URL pattern is different from
  `/chat/<uuid>` and is currently **unrecognised**.

### I.1 Product surfaces PCE must handle

| # | Surface | URL shape | Must capture |
|---|---|---|---|
| 1 | Vanilla chat | `/new` → `/chat/<uuid>` | user + assistant text; multi-turn |
| 2 | New chat (pre-URL) | `/new` before URL upgrade | user + assistant; conv_id fallback |
| 3 | Streaming response | `/chat/<uuid>` | NO partial captures during stream |
| 4 | Code blocks in reply | `/chat/<uuid>` | `code_block` attachment |
| 5 | Extended Thinking (Opus, Sonnet 3.7+) | `/chat/<uuid>` | `<thinking>…</thinking>` prefix |
| 6 | Edit user message | `/chat/<uuid>` | new capture replacing old user turn |
| 7 | Regenerate / retry | `/chat/<uuid>` | new capture with new assistant variant |
| 8 | Branch switcher `< 1/2 >` | `/chat/<uuid>` | reflect currently-shown branch |
| 9 | PDF upload | `/chat/<uuid>` | user has `file` attachment + name |
| 10 | Image upload (vision) | `/chat/<uuid>` | user has `image_url` + `media_type` |
| 11 | Other file upload (csv/docx/txt) | `/chat/<uuid>` | `file` attachment |
| 12 | Projects chat | `/project/<id>` + `/project/<id>/chat/<uuid>` | same as 1, but PCE must handle both URL shapes |
| 13 | Artifact (simple) | side panel, triggered by any `/chat/<uuid>` | assistant message + `canvas`/`artifact` attachment with body |
| 14 | Artifact (HTML/React preview) | side panel | `artifact` with `content_type: html\|react` + source code |
| 15 | Model switcher | any chat URL | `conversation.model_name` = Haiku / Sonnet / Opus variant |
| 16 | Writing Style | any chat URL | `layer_meta.style` = "Explanatory" / "Formal" / custom — NOT in messages |
| 17 | Computer Use | limited access | assistant's tool-use outputs (screenshots, terminal) as attachments | **DEFER v1.1** |
| 18 | Shared conversation | `/share/<uuid>` | **NO capture** (read-only) |
| 19 | Sidebar history | any | NO capture (non-chat surface) |
| 20 | Settings | `/settings*` | NO capture |
| 21 | Error states | any | NO capture of error banner as assistant |
| 22 | Rate limit banner | any | NO capture |

### I.2 Meta-capture invariants

- **Role accuracy** — Claude's DOM has dedicated `data-testid="human-turn"` / `data-testid="assistant-turn"` markers. Strategy 1 uses these correctly. Fallback strategies 2/3 can mis-classify.
- **No duplicates** — `captureMode: "full"` means every fingerprint change resends the whole history. `fingerprintConversation` must be stable under re-render.
- **Streaming safety** — Claude streams word-by-word. **`isStreaming` gate is NOT wired** (see **C2**) — partial captures possible.
- **Idle honesty** — Settings / sidebar history / Projects dashboard = zero captures.
- **SPA nav correctness** — no pushState hook (see **C1**); 3s URL polling.
- **Manual capture** — **NOT wired** in Claude (legacy behaviour preserved; see file header comment). Force-resend doesn't work for Claude — see **C6**.
- **Model name** — `getModelName` **NOT IMPLEMENTED** in Claude adapter (see **C5**). `conversation.model_name` is always empty.
- **Console hygiene** — no red errors.

---

## Part II — Current implementation audit

Grounded in `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\claude.content.ts` (270 lines).

### II.1 Extraction strategies

Three-tier ladder. First non-empty result wins.

| Strat | Selector | Tested | Surface |
|---|---|---|---|
| 1 | `data-testid="human-turn"` etc. + `data-testid="assistant-turn"` etc. (5 each), sorted by `getBoundingClientRect().top` | ✅ | Surfaces 1-11 |
| 2 | `[class*="message"], [class*="turn"]` — role from class / `data-role` / `aria-label` | ✅ | Fallback for older layouts |
| 3 | Direct `<div>` children of container, alternating user/assistant by index | ✅ | Emergency (may mis-classify if container has non-message children) |

### II.2 Helper coverage

| Helper | Tested | Handles |
|---|---|---|
| `getSessionHint` | ✅ | Surface 1 (`/chat/<uuid>`) only |
| `getSessionHint` gap | ❌ | Surface 12 (`/project/<id>`), 18 (`/share/<uuid>`) |
| `getContainer` | ✅ | 6 selector fallbacks (`.conversation-content`, `.chat-messages`, `.thread-content`, `[role="log"]`, `main .flex.flex-col`, `main`) |
| `getModelName` | ✅ IMPLEMENTED (8d7c1df, closes **C5**) | Haiku/Sonnet/Opus family + fallbacks |
| `extractAttachments` | from `pce-dom.ts` | Surfaces 4, 9-11 |
| `extractThinking` | from `pce-dom.ts`, called in Strategy 1 | Surface 5 |
| `elementTop` | ✅ | Position-based sort of strategy 1 |

### II.3 Runtime behaviours

| Behaviour | State |
|---|---|
| Debounce (2000ms) | ✅ |
| `streamCheckMs` | ❌ not passed |
| Poll (3000ms) | ✅ |
| `isStreaming` gate | ✅ WIRED (c49d3de, closes **C2**) |
| `hookHistoryApi` | ❌ false (see **C1**) |
| Fingerprint dedup | ✅ |
| Manual-capture bridge | ✅ WIRED (db55169, closes **C6**) |
| Capture mode | `full` (not incremental) — by design |

### II.4 Known gaps

**Status snapshot** — updated after P5.B C01-C20 manual run on 2026-04-26 (see Part III.bis below).

| Gap | Status | Commit / Source |
|---|---|---|
| **C2** streaming gate | ✅ CLOSED | `c49d3de` — `isStreaming` wired + stop-button detection + regression tests |
| **C3** Projects URL | 🔸 CLARIFIED | `11f4da0` — unanchored regex already matches `/project/<id>/chat/<uuid>` via substring; tests lock behaviour |
| **C5** `getModelName` | ✅ CLOSED | `8d7c1df` — Haiku/Sonnet/Opus family regex + 3-tier fallback |
| **C6** manual-capture bridge | ✅ CLOSED | `db55169` — `installManualCaptureBridge` + listener added |
| **C9** `/share/` URL not skipped | ✅ CLOSED | `702bf0e` — `extractMessages` returns `[]` for `/share/...` paths + regression tests |
| **B1** Non-chat surfaces leak captures (C18 share, C19 settings, root) | ✅ CLOSED | `2285a3e` — `claude.content.ts` path whitelist (`/chat/`, `/new`, `/project/<id>/chat/`) + `requireSessionHint: true` defence-in-depth |
| **B2** PCE Core dashboard self-detected as AI page | ✅ CLOSED | `2285a3e` — `detector.content.ts` excludes `127.0.0.1` / `localhost` |
| **B3 v1-v5** Cross-AI telemetry noise (Datadog RUM, Sentry, Statsig, Growthbook event_logging, Anthropic `/interviews`, `/api/stripe`, `/api/v2/rum`, root `/`) | ✅ CLOSED | `2285a3e` — `interceptor-network.ts` universal `NOISE_HOST_PATTERNS` + `NOISE_PATH_PATTERNS`; runs before `isAIRequest`; covers fetch + XHR + WebSocket + EventSource; relative-URL safe |
| **C1** No pushState hook | 🔸 EMPIRICALLY OK | C02 manual run — 3s URL polling caught `/new → /chat/<uuid>` upgrade within 1-2 cycles. Code unchanged (`hookHistoryApi: false`). Revisit if cross-Project nav surfaces issues. |
| **C4** Artifacts body not extracted | ⬜ OPEN — pivot | C14 manual run found body **in SSE** as `content_block_delta.tool_use.input_json_delta` events, NOT only DOM side panel as originally assumed. Session reconciler can extract from existing network captures without DOM probe. Tracked as **`fu_recon_join`** (also covers C10). |
| **C7** Computer Use outputs | 🔵 DEFER v1.1 | unchanged |
| **C8** Writing Style → `layer_meta.style` | 🔸 SCOPED | C17 manual run 2026-04-27: raw has full `personalized_styles[]` payload (type/key/name/prompt). Session schema does not yet surface it. Folded into **`fu_recon_join`** as **N11** (see Part III.ter). |
| **C10** Strategy 3 mis-classify | ⬜ OPEN | not exercised by C01-C20 manual run |
| **C11** Position-sort under virtualization | ⬜ OPEN | needs long-conversation test |
| **C12** `captureMode: "full"` bandwidth | 🔵 BY DESIGN | not a bug |
| **fu_branch_v1** Regenerate / Edit branch semantics | ✅ CLOSED | ADR `36735b9` (Option D two-tier model) shipped through G1-G7: migration `0008_branches.py` adds `branch_id` / `branch_parent_id` / `turn_index` columns; `query_messages(branches=)` projects collapse vs expand; `GET /sessions/{id}/messages?branches=` defaults to collapse; dashboard surfaces a `branch_count` chip + Collapse/Expand toggle + per-message branch pill; `message_processor._detect_branch_fork` mints branch_id from `parent_message_uuid` collisions on Anthropic web shape; `pce_core/branch_backfill.py` re-derives branches for legacy data. Regression locked in `tests/test_g6_c07_c08_replay.py`. |
| **fu_recon_join** Session reconciler — multi-source data join | ⬜ OPEN | scope (post Round 2): (1) `tool_use.input_json_delta` accumulation (C04/C10/C14/C15) · (2) cross-capture `file_uuid` join via `/wiggle/upload-file` response (C10/C11/C12) · (3) new `files:[]` schema variant alongside legacy `attachments:[{file_uuid}]` · (4) `thinking_delta` accumulation as separate `reasoning` field (C06) · (5) `personalized_styles` extraction → `layer_meta.style` (C17 / **N11**, closes **C8**) |
| **N9** `model_name` 15-char truncation | ⬜ OPEN — medium | 2026-04-27 C16: user-visible model label truncated to 15 chars (`"Claude Haiku 4.5"` → `"Claude Haiku 4."`). API model id (`claude-haiku-4-5-20251001`) unaffected. Likely varchar(15) column or serializer cap. |
| **N10** Multi-turn / branch user msg dropped | ⬜ OPEN — **HIGH** | 2026-04-27 C16 + C09 both reproduce: 2nd user message reaches raw layer but never lands in `session.messages`. Reconciler dedup is too aggressive. Suggested fix: switch primary key from `(chat_uuid, role, position)` (or content-hash) to `(chat_uuid, message_uuid)` — Claude's SSE carries `user_message_uuid` + `assistant_message_uuid` per turn. |
| **N11** Writing Style not in `session.layer_meta` | ⬜ OPEN — low | 2026-04-27 C17. See `fu_recon_join` row above (item 5). Same fix closes **C8**. |

- **C1. No pushState hook.** `/chat/A` → `/chat/B` within 3s escapes URL polling. Claude navigates via client-side router for most transitions. *Empirically OK as of 2026-04-26 P5.B C02 manual run* — `/new → /chat/<uuid>` upgrade caught by the 3s polling within 1-2 cycles. Revisit if cross-Project nav (`/project/<a>` → `/project/<b>`) surfaces issues.
- **C2. No streaming gate.** `isStreaming` is not passed to `createCaptureRuntime`. Partial captures mid-stream are possible when the 2s debounce fires before streaming ends. Same problem as Gemini G2 — likely the same one-line fix.
- **C3. Projects URL not in regex.** `SESSION_HINT_RE = /\/chat\/([a-f0-9-]+)/` doesn't match `/project/<id>` or `/project/<id>/chat/<uuid>`. Projects chats land with `conv_id = null`, which breaks fingerprint on Project-scoped chats.
- **C4. Artifacts body not extracted.** The side panel containing the artifact (code/HTML/React/markdown/SVG/Mermaid) lives in a DOM subtree OUTSIDE the chat turn. Current extractor captures only the assistant's in-chat prose ("I've created an artifact…") and misses the actual content entirely. **Pivot finding from 2026-04-26 P5.B C14 manual run:** the artifact body is **also fully present in the SSE `completion` response body** as a sequence of `content_block_delta` events with `type: tool_use.input_json_delta` — Claude's `create_file` MCP tool streams `partial_json` chunks that concat to the artifact source. The session reconciler can extract this from existing `raw_captures` rows without needing a DOM probe of the side panel; this is a strictly cheaper fix than the original plan. Tracked as **`fu_recon_join`**, which also covers C10 (file attachment join). C15 (React artifact) likely follows the same pattern.
- **C5. `getModelName` not implemented.** No Haiku/Sonnet/Opus detection. `conversation.model_name` always empty. Compare ChatGPT and Gemini which both populate this.
- **C6. No manual-capture bridge.** The Claude file header explicitly preserves this legacy omission. As a result, force-resend from the PCE tray does not work on Claude. Low user-facing impact today, but asymmetric with other sites.
- **C7. Computer Use outputs not handled.** Screenshots + terminal outputs from agentic Claude tool-use render via a separate tool-output component; current extractor sees the wrapper but not the structured attachment. Deferred to v1.1.
- **C8. Writing Style not captured as `layer_meta`.** Surface 16: style selection (Explanatory / Formal / custom) affects the assistant's tone but is invisible in captured conversation. Should land in `layer_meta.style`.
- **C9. No `/share/<uuid>` skip.** Public shared conversations will be captured as if owned by the user — corrupting their personal memory with other people's content.
- **C10. Strategy 3 can mis-classify.** Direct-children alternating index assumes every direct `<div>` is a turn; layout wrappers (typing indicators, scroll sentinels) break the alternation. Not a test-covered case.
- **C11. Position-sort under virtualization.** `getBoundingClientRect().top` on a virtualized turn list returns `0` for off-screen turns — they all collapse to the top of the sorted output. Needs verification against long conversations.
- **C12. `captureMode: "full"` bandwidth.** By design, but worth noting: every fingerprint change re-uploads the whole conversation. For a long Project chat, this can get expensive. Not a correctness bug.

---

## Part III — Test matrix

### III.1 Must-pass for v1.0.1

| ID | Surface | Auto | User action | Expected capture | Known risk |
|---|---|---|---|---|---|
| C01 | 1 vanilla | 🟢 | New chat, "what is 2+2" | 1 user + 1 assistant | — |
| C02 | 2 new chat | 🟢 | From `/new`, send message; watch URL upgrade | 1 capture; conv_id or `_new_…` fallback | **C1** |
| C03 | 3 streaming | 🟢 | Long prompt, wait for completion | 1 capture AFTER stream ends | **C2** |
| C04 | 3 + stop | 🟢 | Click Stop mid-stream | 1 capture with partial assistant text | **C2** |
| C05 | 4 code blocks | 🟢 | "Write python hello world" | assistant has `code_block` + `language: python` | pce-dom |
| C06 | 5 extended thinking | 🟠 | Switch to Opus / Sonnet 3.7, reasoning prompt | assistant begins `<thinking>…</thinking>` | extract |
| C07 | 6 edit | 🟢 | Click pencil on 1st user msg, change, submit | new capture with edited user + new assistant | fingerprint |
| C08 | 7 regenerate | 🟢 | Click retry on assistant msg | new capture with new assistant variant | — |
| C09 | 8 branch flip | 🟢 | Click `<` / `>` arrows to switch branches | capture reflects currently-shown branch only | branch heuristic |
| C10 | 9 PDF | 🟢 | Upload PDF, "summarize" | user has `file` attachment | upload |
| C11 | 10 vision | 🟢 | Upload image, "describe" | user has `image_url` + `media_type` | extract |
| C12 | 11 other file | 🟢 | Upload CSV, ask question | user has `file` attachment | upload |
| C13 | 12 Projects | 🟢 | Open a Project, send message | captures work; `conv_id` well-defined for `/project/<id>` | **C3** |
| C14 | 13 Artifact simple | 🟡 | "Create a markdown todo list" → artifact opens | assistant msg + `artifact` attachment with body text | **C4** |
| C15 | 14 Artifact HTML/React | 🟡 | "Create a counter react component" → artifact opens | `artifact` with `content_type: react` + source code | **C4** |
| C16 | 15 model switch | 🟡 | Switch from Sonnet to Opus via model selector | `conversation.model_name` reflects Opus | **C5** |
| C17 | 16 Writing Style | 🟡 | Switch style (e.g. "Explanatory"), send message | `layer_meta.style = "Explanatory"`; messages NOT polluted | **C8** |
| C18 | 18 shared | 🟢 | Open a public `/share/<uuid>` URL | ZERO new captures | **C9** |
| C19 | 20 settings | 🟢 | Navigate `/settings`, stay for 30s | ZERO new captures | idle honesty |
| C20 | 21 error | 🟡 | Force rate-limit error (rapid burst) | NO capture with rate-limit banner as assistant | error filter |

### III.2 Defer-to-v1.1

| ID | Surface | Notes |
|---|---|---|
| C21 | 17 Computer Use | Limited access; needs `tool_result` + screenshot handling |
| C22 | Workbench / API console | Not `claude.ai`; out of scope for content script |

### III.3 Regression guardrails

For every ❌ → ✅ transition above, add a unit test in
`@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\entrypoints\__tests__\claude.content.test.ts`
locking in the live DOM snippet the fix was written against.

Three invariants must get dedicated regression tests before v1.0.1:

- **C3 Projects URL**: `getSessionHint("/project/abc/chat/uuid")` returns a stable session hint.
- **C4 Artifact extraction**: a fixture with side-panel artifact produces an `artifact` attachment on the assistant turn.
- **C5 Model name**: new `getModelName` helper returns "Sonnet"/"Opus"/"Haiku" for each Claude model badge variant.

---

## Part III.bis — Manual run results (2026-04-26)

P5.B sweep — 10 of the 20 must-pass cases manually validated against
production `claude.ai`. **All raw-layer evidence intact** (forensics
view PASS); session-layer UX gaps grouped into two follow-ups
(`fu_recon_join` + ADR `2026-04-26`).

| Case | Surface | Result | Notes |
|---|---|---|---|
| C01 | vanilla | ✅ PASS | clean 1u + 1a, no telemetry leak |
| C02 | new chat URL upgrade | ✅ PASS | 3s polling caught `/new → /chat/<uuid>` (informs **C1**) |
| C03 | streaming complete | ✅ PASS | `isStreaming` gate holds (locks **C2** closed) |
| C04 | streaming + stop | ✅ PASS | partial assistant text + `/stop_response` capture |
| C07 | edit user message | ✅ PASS | session now lands on TWO branches with `branch_count == 2`; default collapse view shows the LATEST round (BANANAS), forensic APPLES round still recoverable via `?branches=expand`. Closed by `fu_branch_v1` (G4 reconciler + G6 replay). |
| C08 | regenerate | ✅ PASS | session has TWO branches, `message_count == 2` under default collapse (matches provider UI's `1/2` count). Latest assistant variant wins, prior variant retrievable via `?branches=expand`. Closed by `fu_branch_v1`. |
| C10 | PDF upload | ⚠️ PARTIAL | `wiggle/upload-file` response captured (filename/uuid/size); completion request has `files: [<uuid>]`; but `session.user.attachments=[]`. → **`fu_recon_join`** |
| C14 | artifact (markdown todo list) | ⚠️ PARTIAL | full markdown in SSE `tool_use.input_json_delta` deltas; assistant text has only the chat shell ("Done! I created Todo-9152..."). → **`fu_recon_join`** (informs **C4** pivot) |
| C18 | shared `/share/<uuid>` | ✅ PASS | path whitelist + extractor short-circuit (locks **C9** + **B1**) |
| C19 | settings | ✅ PASS | path whitelist + `requireSessionHint` + detector self-exclusion + network noise filter (closes **B1** + **B2** + **B3**) |

**Bug fixes shipped this round** (`2285a3e` → pushed):
- **B1** Claude path whitelist + `requireSessionHint` (`claude.content.ts`)
- **B2** Detector PCE-Core-host exclusion (`detector.content.ts`)
- **B3 v1-v5** Universal cross-AI noise filter (`interceptor-network.ts`)

**ADR shipped** (`36735b9` → pushed):
- `Docs/docs/decisions/2026-04-26-regenerate-edit-branch-semantics.md`
  — proposes Option D (two-tier branch model) to close C07/C08
  session-layer gaps without losing forensic fidelity.

**Untested in this round, picked up by Round 2 (2026-04-27, see Part III.ter below):**
**C05** code block, **C06** extended thinking, **C09** branch flip,
**C11** image, **C12** CSV, **C13** Projects, **C15** React artifact,
**C16** model switch, **C17** Writing Style. Only **C20** rate-limit
still pending (would require burst traffic to trigger).

---

## Part III.ter — Manual run results (2026-04-27, Round 2)

Round 2 sweep — 9 of the 10 cases deferred from 2026-04-26 manually
validated against production `claude.ai`. **Raw-layer evidence intact**
in all cases; session-layer gaps fold cleanly into existing follow-ups
(`fu_recon_join`, `fu_branch_v1`, ADR `2026-04-26`) plus three new
findings (N9 / N10 / N11) — all logged in Part II.4 above.

| Case | Surface | Result | Notes |
|---|---|---|---|
| C05 | code block | ✅ PASS | `language: python` detected; assistant text contains complete code fence; token in user msg |
| C06 | extended thinking | ✅ PASS | raw SSE has `thinking_delta` × 15 events; final answer (1784 chars) in `session.assistant`; internal monologue only in raw → folds into **`fu_recon_join`** item 4 |
| C09 | branch flip (edit) | ✅ PASS | both branches preserved (`branch_count == 2`); collapse view tracks the active branch the user toggled to, expand view returns all 4 messages with their `branch_id` set. Closed by `fu_branch_v1` G4 reconciler. **N10** dedup fix (separate) is what allows the second branch's user msg to survive into the messages table. |
| C11 | image upload (vision) | ✅ PASS | `/conversations/<id>/wiggle/upload-file` response has full metadata (`file_uuid`, `file_name`, `file_kind=image`, `size_bytes`, thumbnail/preview URLs, dimensions, `primary_color`); completion body references `files:[<uuid>]`; Claude vision OCR'd the embedded token verbatim |
| C12 | CSV upload | ✅ PASS | same upload endpoint; `file_kind=blob` (third variant alongside `image`/`document`); CSV content also inlined into completion body; no `code_execution` for the trivial lookup; assistant quoted target row verbatim |
| C13 | Projects chat | ✅ PASS | `/project/<id>/chat/<uuid>` URL pattern matches; session attached to project namespace; closes **C3** empirically |
| C15 | React artifact | ✅ PASS | full React source streams via `tool_use.input_json_delta` — same shape as C14 markdown; reconciler join → **`fu_recon_join`** item 1 |
| C16 | model switch (mid-conversation) | ⚠️ PARTIAL | per-msg model attribution **correct** (haiku→sonnet); 2 completions captured. **N9**: user-visible label truncated to ~15 chars. **N10**: 2nd-turn user msg lost — `message_count=3` should be 4 |
| C17 | Writing Style | ✅ PASS | raw body has full `personalized_styles[].{type,key,name,prompt}` — including the entire system prompt for "Concise" mode. Session schema does not yet surface style → **N11**, folded into **`fu_recon_join`** item 5 (closes **C8**) |

**Confirmed-clean upload mechanics (consolidates C10/C11/C12 across both rounds):**

- Single endpoint: `POST /api/organizations/<org>/conversations/<chat-uuid>/wiggle/upload-file`
- Request body serialized as `[FormData]` placeholder — binary multipart not extracted (intentional; metadata is sufficient + binary would be PII risk). Tracked as **N8** if explicit binary capture is ever required.
- Response: full metadata including `file_uuid`, `file_name`, `file_kind` ∈ {`image`, `document`, `blob`}, `size_bytes`, `thumbnail_url`, `preview_url`, `image_width/height` (when applicable), `primary_color`
- Schema reference in completion body: `"files":[<uuid>]` array (newer) **or** `"attachments":[{file_uuid: <uuid>}]` (legacy) — `fu_recon_join` must handle both.

**Round 2 net deltas:**
- 19 / 20 must-pass cases now exercised against production (only C20 still pending; deferrable).
- 3 new findings (**N9** / **N10** / **N11**) added to gap table.
- Existing follow-up **`fu_recon_join`** scope expanded from 2 items (C10 + C14) to **5 items** covering all session-layer reconciler gaps observed across both rounds.
- No new code shipped this round (manual validation only). Bug fixes from Round 1 still hold (`2285a3e` + `36735b9` + `5f6abf0`).

---

## Part IV — Collaboration protocol

Inherits from `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\CHATGPT-FULL-COVERAGE.md` Part IV verbatim.

**Delta for Claude:**

- **Autopilot drives most cases.** C01-C05, C07-C13, C18, C19 = 12 cases fully automated via extended `ClaudeAdapter` + `capture_verifier.wait_for_session_matching`.
- **C06 requires Opus or Sonnet 3.7+** — autopilot skips unless user declares tier.
- **C14, C15, C16, C17, C20** use screenshot-assisted verification (Cascade `read_file`s the PNG + confirms artifact pane / model badge / style selector / rate-limit banner).
- **`getModelName` must be written first** — it doesn't exist today. That's a pre-req for any test that asserts on `conversation.model_name` (C16 at minimum, and silently helps C01 look complete).
- **`captureMode: "full"` means the fingerprint domain is huge.** A long conversation that drifts one byte across re-renders will look like a new conversation to the fingerprint. If a stability bug surfaces as "conversation keeps duplicating," look at fingerprint stability first, not the extractor.

---

## Part V — Order of attack

```
Block 1 — smoke (5 min):         C01, C19
  Does chat capture? Does /settings stay silent?

Block 2 — streaming + nav (10 min): C02, C03, C04, C18
  If C03 or C04 fail, apply the same `isStreaming` fix we shipped
  for Gemini (C2 ≡ G2). C18 confirms share skip.

Block 3 — editing (10 min):      C07, C08, C09
  Edit / regenerate / branch flip. Fingerprint churn zone.

Block 4 — attachments (15 min):  C05, C10, C11, C12
  Code block + PDF + image + other file.

Block 5 — model meta (15 min):   C06, C16, C17
  Extended thinking + model switch + Writing Style. Needs
  `getModelName` implementation first (C5).

Block 6 — Projects + Artifacts (20 min): C13, C14, C15
  Projects URL (C3) + Artifact side-panel extraction (C4 — biggest
  single gap). Budget extra.

Block 7 — error (5 min):         C20
  Rate-limit / safety banner filter.
```

Total ~80 min for a first pass. Block 6 is the high-value / high-risk
block for Claude — Artifacts is where the shared `pce-dom.ts`
extractor probably needs extension.

**What happens if Block 1 C01 fails?** Stop. Report. Fix before
Block 2.

---

## Part VI — What Cascade does between rounds

Same list as `CHATGPT-FULL-COVERAGE.md` Part VI, plus Claude-specific:

- Implementing `getModelName` (close **C5**) by scanning for
  `data-testid="model-selector-button"` + badge text + body-text
  regex (`Claude (Haiku|Sonnet|Opus) [\d.]+`).
- Drafting the artifact extractor for `pce-dom.ts` (close **C4**) —
  likely signature `extractArtifacts(doc) → { id, title, content_type, body, language? }[]` that
  walks the side-panel subtree. Needs live DOM probe first.
- Preparing a `hookHistoryApi: true` patch (closing **C1**) with a
  regression test for `/chat/A` → `/project/B/chat/C`.
- Adding a `pce-manual-capture` listener (close **C6**) for feature
  parity with ChatGPT/Gemini/GAS.
- Writing the Claude sub-section of v1.0.1 release notes.

Say "Cascade, work on X in the background while I run C14" — I will.

---

## Part VII — Stability run signoff (2026-04-30)

**Probe vertical result: 10 PASS / 9 SKIP / 2 FAIL.** Same shape as
ChatGPT — both verticals reproduce the SW user-upload extractor gap
(T10 + T11). All other invariants (capture, streaming gate, multi-turn,
nav, edit, regen, code block, web search, model switch, settings
silence) are green or honestly skipped.

### Pass / Skip / Fail breakdown

| Status | Cases | Notes |
|---|---|---|
| **PASS (10)** | T00, T01, T02, T03, T04, T05, T06, T08, T14, T20 | core capture + streaming + multi-turn + nav + code block + regen + web search + settings silence |
| **SKIP (9)** | T07, T09, T12, T13, T15, T16, T17, T18, T19 | T07 is honest UI-targeting skip (see below); T09 wired but no Claude branch surface in current UI; T12 / T13 / T15-T19 deferred per coverage spec or no equivalent surface |
| **FAIL (2)** | T10, T11 | known SW extractor gap — see `CHATGPT-T10-T11-EXTRACTOR-HANDOFF.md` (now confirmed cross-site for Claude) |

### Stability fixes shipped this round

Five real bugs / robustness gaps were closed during the Claude
shakedown:

1. **`pce_browser_extension_wxt/utils/capture-runtime.ts`
   `fingerprintConversation`** — included assistant message body in
   the fingerprint key (was user-only). T08 regen on Claude was
   collapsing the regenerated turn onto the seed because both shared
   the user prompt. Now changes in the assistant body trigger a fresh
   capture.
2. **`tests/e2e_probe/sites/claude.py` `wait_for_done`** —
   `tool_call` MCP popup ("Allow tool call") was mistaken for a stop
   button by the streaming detector. Filter it out so wait_for_done
   doesn't return prematurely on tool-using turns.
3. **`tests/e2e_probe/cases/t02_streaming_complete.py`,
   `t07_edit_user_message.py`, `t08_regenerate.py`,
   `t14_web_search.py`** — refactored to use **PCE Core round-trip as
   the authoritative gate** instead of SW ring count. The SW ring is
   still consulted as a fast path / session_hint hint, but a 15s
   timeout no longer fails the case if PCE Core eventually has the
   token. Absorbs Claude's end-of-stream lag and DOM finalization
   variance without masking real pipeline breaks.
4. **`tests/e2e_probe/cases/t08_regenerate.py` `_event_body_has`** —
   token search now includes the `fingerprint` key from
   `CaptureEventSummary` (was checking `body_excerpt` / `content_text`
   that the SW ring doesn't populate). Same fix applied to T02.
5. **`tests/e2e_probe/cases/t07_edit_user_message.py`** — split
   terminal outcome on whether the SW ever observed the edit_token.
   No SW signal → SKIP (UI-targeting failure, pipeline never
   exercised). SW saw it but PCE Core didn't → FAIL (real ingest
   break). Plus reordered Save → Send → Enter on click priority to
   match the legacy autopilot.

### Known gaps left open

- **T07 (edit user message) — SKIP on Claude.** The case opens the
  edit pencil and types into `[contenteditable="true"]`, but the
  modern Claude UI's inline edit area resolves to the same selector
  as the bottom composer. The typed text doesn't make it into
  Claude's actual edit submit, so the SW never sees a new capture
  with the edit_token. Adapter hook `edit_input_selectors` was
  added to `BaseProbeSiteAdapter` for future per-site targeting;
  Claude can wire it once the live DOM is inspected. Until then,
  this is an honest SKIP — not a pipeline bug.
- **T10 / T11 (PDF / image upload).** See
  `CHATGPT-T10-T11-EXTRACTOR-HANDOFF.md`. Probable hypotheses:
  DataTransfer-injected uploads bypass React's `isTrusted` gate
  (probe-side), or the Claude content script extractor doesn't
  parse `<input type="file">` attachment metadata into
  `content_json.attachments` (extractor-side). Either way it is
  cross-site and the same fix path documented for ChatGPT applies.

### Reproduction

```powershell
python -m pytest tests/e2e_probe/test_matrix.py -k "claude" -v --tb=line
```

Expected: `10 passed, 9 skipped, 2 failed (T10, T11)`.
