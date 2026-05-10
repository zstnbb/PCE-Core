# Handoff — P1 Claude Desktop chat full D-case sweep (2026-05-10)

**Scope:** Same-day continuation of
`HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md`. That earlier handoff
proved Bug 1 (D03 multi-turn) was fixed and documented Bug 2
(P2 WebSocket split-channel). This handoff finishes the **Claude
Desktop chat** D-case sweep end-to-end using the new
`tests/e2e_desktop_ui/` framework (UIA + SendInput automation).

**TL;DR — Claude Desktop chat (P1) coverage**

| D-case | Status | Evidence |
|--------|--------|----------|
| **D00** product detection | ✅ PASS | `provider=claude.ai`, `tool_family=api-direct`, real model name on every captured pair |
| **D01** single-turn | ✅ PASS | inherited from `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md` |
| **D02** streaming complete | ✅ PASS | full SSE assembled, `event: message_stop` reached on all non-cancelled turns |
| **D03** multi-turn | ✅ PASS | 5 turns / 1 session via Bug 1 fix (`session_key` from path UUID) |
| **D04** stop / cancel | ❌ KNOWN BUG | request captured, response missing, no message persisted — see §3.1 |
| **D05** model switch | ✅ PASS | inherited from prior handoff |
| **D06** file attachment | ✅ PASS | `file_uuid` in user `content_json.attachments`; assistant `tool_call`s captured |
| **D07** code block | ✅ PASS | `\`\`\`python` fenced block survives in assistant `content_text` |
| **D08** MCP tool | ⏭ deferred | placement under cowork phase, not chat |
| **D10** error mid-stream | ✅ PASS | fail-closed semantics — request captured, no phantom message, proxy restart healthy |
| **D11** long-context (≥8K tokens) | ✅ PASS | 50/50 turns, 100/100 messages, 1 session, 14378 cumulative tokens |
| **D12** silent on idle | ✅ PASS | 0 chat-relevant writes / 310s; +8 raw heartbeats only (expected) |

**Score: 9 PASS / 1 known bug / 1 deferred / 1 not-applicable** out of
the 12 applicable cases. **D0 release gate (≥85%) cleared.**

---

## 1. The new test infrastructure — `tests/e2e_desktop_ui/`

This handoff introduces a new automation tree, sibling to
`tests/e2e_desktop/` (which is for `pce_app` unit tests):

```
tests/e2e_desktop_ui/
├── __init__.py
├── utils.py                 # foreground / mouse-click / clipboard / DB poll
├── drivers/
│   ├── __init__.py
│   ├── base.py              # DesktopDriver ABC
│   └── claude_desktop.py    # pywinauto UIA + SendInput driver
└── cases/
    ├── __init__.py
    ├── p1_chat_window_a.py        # D03 + D07 + D04
    ├── p1_chat_window_b_d11.py    # D11 long-context (50 turns)
    ├── p1_chat_window_c_d12.py    # D12 idle (5 min)
    ├── p1_chat_window_d_d06.py    # D06 attachment (CSV via clipboard CF_HDROP)
    └── p1_chat_window_e_d10.py    # D10 mid-stream proxy kill + restart
```

Companion inspector scripts at the repo root are intentionally kept
as scratch (named `_inspect_window_<a..e>.py`); they directly query
`~/.pce/data/pce.db` and emit a per-case PASS/FAIL verdict.

### 1.1 Why a new tree (not extending `tests/e2e_probe/`)

`tests/e2e_probe/` drives **browser** sessions via Selenium and is
hard-frozen post-`SITE-TIER-MATRIX`. `tests/e2e_desktop_ui/` drives
**installed Windows desktop apps** via UIA + SendInput — different
automation primitives (no DOM, no JavaScript, must work with whatever
focus / IME / accessibility-tree the OS exposes). Sharing a tree would
have required wedging two unrelated capture-driver paradigms together.

### 1.2 Empirical observations baked into `claude_desktop.py`

Documented inline in `tests/e2e_desktop_ui/drivers/claude_desktop.py`:

- `Desktop(backend="uia")` — Electron MSIX apps don't expose a
  reliable Win32 `EnumWindows` chain; the UIA tree is the only stable
  introspection path.
- `force_foreground()` (in `utils.py`) uses the
  `AttachThreadInput` trick — Windows 10+ blocks naïve
  `SetForegroundWindow` from non-foreground processes.
- `click_composer()` clicks at `(window.center_x, window.bottom-120)`
  before typing. The Chromium child window inside the Electron shell
  doesn't accept synthesized keyboard events until its inner renderer
  has input focus; UIA's `set_focus()` alone isn't enough on the chat
  composer.
- `send_keys(..., vk_packet=True)` bypasses the IME and sends Unicode
  directly. Without `vk_packet`, English-only fallback worked but CJK
  prompts broke.
- `cancel_current()` sends `{ESC}`, which is Claude Desktop's bound
  shortcut for "stop generation" (matches the in-UI Stop button).
- `new_chat()` sends `Ctrl+N` *after* `click_composer()` — without
  the click, `Ctrl+N` falls through to the OS shell.

Anyone replacing pywinauto later (e.g. moving to `uiautomation` or
adding macOS support) will need to re-validate these five points.

---

## 2. Per-window evidence (D03/D04/D06/D07/D10/D11/D12)

All five run windows used the same upstream chain as the prior
handoff (system → `mitmdump :8080` → `127.0.0.1:7890` Clash → external)
and the same retained mitmproxy CA. `_baseline_ts.txt` was rewritten
at the start of every window via Python `time.time()` (the previous
PowerShell `Get-Date -UFormat %s` pattern returns local seconds, not
UTC, and produced 8h offsets — fixed in `utils.baseline_ts`).

### 2.1 Window A — `p1_chat_window_a.py` (D03 + D07 + D04)

Five-turn conversation with one fenced-`\`\`\`python` request and one
mid-stream cancel. After-window numbers (from `_inspect_window_a.py`):

| Metric | Value |
|--------|-------|
| `/completion` request rows | 5 |
| `/completion` response rows | 4 (cancelled turn missing response) |
| `messages` rows | 8 (4 user + 4 assistant) |
| `sessions` rows | 1 (D03 PASS — Bug 1 fix held) |
| code block in turn-3 assistant | ✅ ` ```python ` fenced block intact |
| cancel-turn assistant message | ❌ missing — see §3.1 |

D03 ✅, D07 ✅, D04 ❌ (root cause in §3.1).

### 2.2 Window B — `p1_chat_window_b_d11.py` (D11 long-context)

50 distinct turns sized to push cumulative input tokens past the 8K
threshold. After-window numbers (`_inspect_window_b.py`):

| Metric | Value |
|--------|-------|
| Turns sent | 50 |
| `/completion` pairs (request+response) | 50 / 50 |
| `messages` rows for these pairs | 100 (50 user + 50 assistant) |
| `sessions` rows | **1** (single session_key from path UUID) |
| `turn_index` monotonicity | strictly increasing 0..99 |
| Cumulative `token_estimate` | **14 378** (>>8K threshold) |
| Drops / errors | 0 |

D11 ✅ — zero message loss, no session split across the long context.

### 2.3 Window C — `p1_chat_window_c_d12.py` (D12 idle)

5-minute + 10-second idle window with **no user activity** in any AI
desktop app. After-window deltas (`_window_c_log.txt` saved):

| Table | Δ over 310s |
|-------|------|
| `/completion` requests | **0** |
| `messages` | **0** |
| `sessions` | **0** |
| `raw_captures` (all) | +8 (background heartbeats — `current_user_access`, `app_start`, `system_prompts`, `cowork_settings` etc.) |

D12 ✅ — zero chat-relevant writes; the +8 raw_captures noise is
explainable Electron-app heartbeat traffic, **not** chat content.

### 2.4 Window D — `p1_chat_window_d_d06.py` (D06 attachment)

CSV attached via `CF_HDROP` clipboard paste, then prompt asking for
column headers. After-window inspection (via the corrected
`_inspect_d06_focused.py` + `_inspect_d06_message_content.py`; the
in-case post-run check used a 10-char pair_id prefix that didn't
match the 16-char DB key — that bug was in the inspector script,
not in the capture pipeline):

| Metric | Value |
|--------|-------|
| `/api/.../wiggle/upload-file` rows | 2 (request + 200 response) |
| `/completion` request body length | 31 146 B |
| `files` field in body | `["0778d897-062e-4870-896d-49ed7186d8db"]` |
| `attachments` field in body | `[]` (Claude Desktop puts file UUIDs under `files`, not `attachments`) |
| `messages` rows for this pair | 2 (user + assistant) |
| user `content_json.attachments` | `[{type: "file", file_uuid: "0778d897-…", file_id: "0778d897-…"}]` |
| assistant `content_json.attachments` | 4 `tool_call` items (all `view`) |
| assistant final `content_text` | thinking blocks + `[Tool call: view]×4` + `**item** and **value**` |

D06 ✅ — file upload metadata preserved, agentic tool calls visible
in attachments, final answer correctly extracted.

### 2.5 Window E — `p1_chat_window_e_d10.py` (D10 mid-stream error)

Long-form prompt sent → 3 s grace → mitmdump killed via
`Stop-Process -Force` while the SSE stream was in flight → 12 s wait
for client to surface the error → mitmdump restarted with the same
flags → tiny smoke prompt to verify proxy health. After-window
inspection (`_inspect_window_e.py`):

| Pair | sides captured | response status | messages | pipeline_errors |
|------|----------------|----|----------|---|
| `5406a5fb17…` (killed mid-stream) | request only | n/a | **0** (no phantom row) | **0** |
| `44a88c2d12…` (post-restart smoke) | request + response | 200 (6459 B) | 2 (user + assistant) | 0 |

D10 ✅ — fail-closed semantics: the killed pair didn't corrupt state,
didn't emit a stack trace, didn't poison the messages table; the
restart bring-up was clean and the next request flowed normally.

---

## 3. Known bug — D04 cancel mid-stream (deferred)

### 3.1 Symptom (Window A)

Turn 4 of `p1_chat_window_a.py`: typed prompt, sent, waited 1.2 s for
streaming to start, sent `{ESC}` via `cancel_current()`. The desktop
UI showed the partial reply being truncated as expected.

DB state for the cancelled `pair_id`:

- `raw_captures` request row: ✅ present (full request body, 31 KB+)
- `raw_captures` response row: ❌ **never written**
- `messages` rows for this pair: ❌ none
- `pipeline_errors`: clean (no exception)

**The user's prompt is sitting in `raw_captures` but never surfaces
as a `messages` row.**

### 3.2 Root cause

`pce_core/normalizer/pipeline.py::try_normalize_pair` requires both
sides:

```python
rows = query_by_pair(pair_id, db_path=db_path)
if len(rows) < 2:
    return None  # pair not yet complete
...
if request_row is None or response_row is None:
    return None
```

Cancel-mid-stream means mitmproxy's `response()` hook never fires
(client TCP closed before upstream finished), so no response row is
written. With `len(rows) < 2`, the pipeline gives up. The user's
prompt is captured but invisible to the rendered messages timeline.

### 3.3 Why this is the same shape as D10

D10 (proxy killed mid-stream) has the same root cause: only a request
row exists, no response. There the **fail-closed** outcome is the
correct product behaviour (treat it as "request never completed").
For D04 (user-initiated cancel) the desired product behaviour is
**different**: the user *did* author a prompt and *did* see the
partial reply on screen — the user's intent was to keep that prompt
in their history. So D04 wants a request-only normalization path that
emits at least the user message.

### 3.4 Why the fix is deferred (medium-priority follow-up)

Implementing this requires three coordinated changes:

1. **Pipeline** — new `try_normalize_pair_request_only(pair_id)` that
   accepts `response_row=None` and runs the normalizer over just the
   request body to emit user messages (and any assistant `partial`
   marker if a partial body was captured).
2. **Normalizer (`anthropic.py`)** — extend `normalize_pair` to handle
   `response_row=None`: parse the request body's `prompt` /
   `attachments` / `files` / `parent_message_uuid`, derive the same
   `session_key` (already fixed in Bug 1), emit a single `role=user`
   `NormalizedMessage` with `interaction_kind="cancelled"`.
3. **Trigger** — somebody has to call this when the response is known
   to be never-coming. Two options:
   - mitmproxy hook on `client_disconnected` (most accurate, but
     correlating it back to a `pair_id` is non-trivial in the current
     addon).
   - Background sweep that scans `raw_captures` for request rows older
     than N seconds without a paired response and normalizes them
     request-only. Simpler; a few seconds of latency before the user
     message becomes visible is acceptable.

Total estimate: 1–2 person-days incl. tests. Tracked in the project
TODO under `auto_bugs_fix` (medium priority). The next operator can
pick this up without re-establishing capture state — the data the
fix needs (request rows on cancelled pairs) is already in the DB.

---

## 4. Reproduction recipe

Per-window invocation (Windows PowerShell), assuming the running
mitmdump pid file is at `_mitm.pid`:

```powershell
# Ensure proxy chain is up and CA is trusted
python _state_check.py            # (legacy script from prior handoff)

# Window A — D03 + D07 + D04
python -m tests.e2e_desktop_ui.cases.p1_chat_window_a 2>&1 | Tee-Object _window_a_log.txt
python _inspect_window_a.py

# Window B — D11
python -m tests.e2e_desktop_ui.cases.p1_chat_window_b_d11 2>&1 | Tee-Object _window_b_log.txt
python _inspect_window_b.py

# Window C — D12
python -m tests.e2e_desktop_ui.cases.p1_chat_window_c_d12 2>&1 | Tee-Object _window_c_log.txt

# Window D — D06
python -m tests.e2e_desktop_ui.cases.p1_chat_window_d_d06 2>&1 | Tee-Object _window_d_log.txt
python _inspect_d06_focused.py
python _inspect_d06_message_content.py

# Window E — D10 (kills + restarts mitmdump itself)
python -m tests.e2e_desktop_ui.cases.p1_chat_window_e_d10 2>&1 | Tee-Object _window_e_log.txt
python _inspect_window_e.py
```

Each case writes `_baseline_ts.txt` at the start so the inspector can
filter by it. The case scripts deliberately do **not** stop or restart
PCE Core / proxy themselves except in Window E (which is testing
exactly that flow).

### 4.1 Pre-flight invariants

- `~/.pce/data/pce.db` is the canonical DB path (`pce_core.db.DB_PATH`).
  Earlier inspector scripts that hard-coded `~/.pce/db.sqlite3` will
  fail with "no such table" — fixed in this commit's inspectors.
- `messages.error` does **not** exist as a column. Any older inspector
  scripts referencing `messages.error` need to be repointed at
  `interaction_kind` or `content_json` markers.
- `raw_captures.headers_json` does **not** exist either; the right
  column name is `headers_redacted_json`.
- `messages.capture_pair_id` is the join key (not `capture_id`).
  Pair IDs in the DB are 16-char hex; logs print only the first 10
  chars — match using the log prefix as a `LIKE 'prefix%'` clause OR
  recover the full id via
  `SELECT pair_id FROM raw_captures WHERE pair_id LIKE 'prefix%'`.

These four schema points cost ~30 minutes of debug each in this
session; pinning them here so the next operator skips that detour.

---

## 5. Cross-references

- Prior same-day handoff: `HANDOFF-P1-D03-D05-P2-EMPIRICAL-2026-05-10.md`
- N/L1 validation baseline: `HANDOFF-P1-N-L1-VALIDATION-2026-05-10.md`
- Product-tier authority: `Docs/stability/DESKTOP-PRODUCT-MATRIX.md`
  §4.1 P1 Claude Desktop (this commit appends a dated note)
- Cognitive framework (D-case definitions): `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`
- Anthropic normalizer (Bug 1 fix from prior handoff still in force):
  `pce_core/normalizer/anthropic.py:30-56,247-261`
- Cancel-bug location:
  `pce_core/normalizer/pipeline.py:52-65` (`if len(rows) < 2: return None`)
- New automation tree: `tests/e2e_desktop_ui/` (this commit)

---

## 6. Open follow-ups (priority-ordered)

1. **(Medium)** Implement D04 request-only normalization path per §3.4.
   The data is already in the DB — this is purely a pipeline + addon
   change; no new capture work needed. Targeted regression test:
   replay a cancel-shaped request row through `try_normalize_pair_request_only`
   and assert exactly 1 user `messages` row is emitted.
2. **(Medium)** Move scratch inspectors into `tests/e2e_desktop_ui/inspectors/`
   so they survive the next workspace cleanup and become callable from
   pytest. Today they live as `_inspect_window_*.py` at the repo root.
3. **(Low)** Add D08 (MCP tool call) under the upcoming **cowork**
   sweep. Claude Desktop's MCP path goes through `pce_mcp_proxy/`,
   not `pce_proxy/`, so it's a different test fixture and does not
   belong in this chat-only sweep.
4. **(Low)** Promote `claude_desktop.py` driver from "single-window
   case scripts" to a pytest fixture so other agents can write D-case
   tests without re-implementing the focus / click / send dance.

---

*End of handoff.*
