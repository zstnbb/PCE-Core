# 2026-05-11 — P1 Claude Desktop **inline Code tab** RECON findings

> Companion to `Docs/research/2026-05-11-cowork-recon-findings.md`.
> **Scope**: the third tab inside Claude Desktop (`Chat / Cowork / Code`),
> NOT the standalone npm-installed Claude Code CLI (that's **P6** in
> `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.6 and deferred).
> **Driver**: pce-mcp installed via `.mcpb`, mitmproxy + watcher up,
> user prompted "Run `cat /etc/os-release` and tell me what Linux
> distribution this VM is running" in a fresh `F:\test`-cwd Code-tab
> session at ~18:35 (UTC+08) 2026-05-11.

## TL;DR

| Axis | Verdict | Production role for Code-tab |
|------|---------|------------------------------|
| **H1 (`pce_cli_wrapper/` PATH shim)** | ❌ not applicable | Desktop spawns embedded `claude.exe` via absolute path, PATH shim cannot intercept — see `@/f:/INVENTION/You.Inc/PCE Core/pce_cli_wrapper/discovery.py:17-21` |
| **L1 (`pce_proxy/` mitm + system CA)** | ⚠️ partial | Captures heartbeats / settings probes / GitHub auth / **title generation (contains prompt original)** / telemetry / plugin lists. Does NOT capture conversation content — the actual SSE long-poll body on `/v1/sessions/watch` never lands in `raw_captures` |
| **L3g (`pce_persistence_watcher/` filesystem tail)** | ✅ **production path** | Full JSONL transcript at `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl`, identical schema to cowork's `local-agent-mode-sessions/.../<sess>.jsonl`. `entrypoint:"claude-desktop"` field discriminates Desktop Code tab from standalone CLI in the same file tree |
| **M (`pce_mcp_proxy/` + `pce_mcp` .mcpb)** | ✅ works | Code tab exposes user-installed MCP servers via `enabledMcpTools` in the session pointer; our 6 PCE tools (`pce_capture`/`pce_query`/`pce_stats`/`pce_sessions`/`pce_session_messages`/`pce_capture_pair`) are visible. **Opposite of cowork** (which has Anthropic-curated namespace and rejects user MCP packs) |

**Acceptance signature**: Code-tab cases should require **L3g JSONL row** with `entrypoint='claude-desktop'` AND optionally L1 row on `/dust/generate_title_and_branch` (carries the prompt original). The `/v1/sessions/watch` SSE is informational only — never expect body content there.

---

## Phase 0 misconception reconciliation

The initial Phase 0 hypothesis — *"Code tab runs in the Linux microVM
that `vm_bundles/claudevm.bundle/` provides"* — is **WRONG**. Empirical
evidence:

1. **Drive prompt**: `Run `cat /etc/os-release` and tell me what Linux
   distribution this VM is running.`
2. **Claude's verbatim response** (captured in JSONL transcript at
   `~/.claude/projects/F--test/f4cb2964-0a81-415f-8f9d-31acb3d9131e.jsonl`
   last line):
   > "This isn't a Linux VM — you're running **Windows 11 Pro** (build
   > 10.0.26200). The `/etc/os-release` file only exists on Linux
   > systems."
3. **The agent's `version: "2.1.128"`** matches the Desktop-embedded
   `claude.exe` exactly (`C:\Users\ZST\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.128\claude.exe`).
4. **The agent's `cwd: "F:\test"`** is a real Windows drive path — not
   a Linux mount.

So `vm_bundles/claudevm.bundle/` is **cowork's** sandbox; `smol-bin.vhdx`
mtime updating during a Code-tab drive is keep-alive activity by the
shared Desktop process, **not** Code-tab execution. The Code tab spawns
the bundled `claude.exe` as a **Windows-native child process** that
operates on the real host filesystem.

This realignment means three things:

- **`vm_bundles/`** is NOT a Code-tab capture surface (still cowork's)
- **No `local-agent-mode-sessions/...` writes** during a Code-tab drive
  (delta confirmed: `local_agent_mode: +0` in `Docs/research/2026-05-11-code-recon-artefacts/_code_snap_after.json`)
- **The Code tab IS Claude Code CLI** — just spawned with a Desktop UI
  instead of a terminal. Same Node.js agent, same `claude.exe`, same
  `~/.claude/` data directory, same JSONL transcript schema.

---

## Q1 — Where does the prompt original go on the network?

**Empirical finding**: the prompt text is **not** sent in any single
request body where you'd expect it (no `POST /completion`, no `POST
/v1/sessions`, no `POST /v1/code/*`). It IS sent — once — to the
auto-title endpoint:

`@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_inspect3_out.txt:9-20`:
```
POST /api/organizations/<org>/dust/generate_title_and_branch
  request body 133 bytes:
    {"first_session_message":"Run `cat /etc/os-release` and tell me what Linux distribution this VM is running.","title_style":"default"}
  response body 90 bytes:
    {"title":"Identify Linux distribution on VM","branch_name":"claude/identify-linux-distro"}
```

So **claude.ai DOES receive the prompt text** (for auto-titling), but
that's the only place the L1 proxy sees it. The actual prompt+response
exchange between the Desktop Code tab and Anthropic's model server goes
through a different channel (Q4).

**Implication for E-case design**: the `dust/generate_title_and_branch`
POST request body is a clean L1 fingerprint of "a Code-tab session
started with this prompt". It's a useful **detection signal** (case E00)
even though it's not the full conversation.

---

## Q2 — Where does the model's reply land? (the L3g answer)

`%USERPROFILE%\.claude\projects\F--test\f4cb2964-0a81-415f-8f9d-31acb3d9131e.jsonl`
**16,783 bytes, mtime 2026-05-11 18:36:56**.

The cwd `F:\test` is encoded as `F--test` (drive letter colon stripped,
backslash→dash). The filename is the `cliSessionId` (NOT the
Desktop-side `sessionId` — see Q6).

**Schema** (identical to cowork's JSONL — see Q5 of
`@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-cowork-recon-findings.md`):
each line is a JSON object with `type` ∈ {`user`, `assistant`,
`tool_use`, `tool_result`, …}, a `message` block carrying the standard
Anthropic Messages content shape, plus correlation metadata
(`sessionId`, `parentUuid`, `requestId`, `timestamp`, `cwd`,
`version`, `gitBranch`, `userType`, `entrypoint`).

**Last-line sample (assistant turn)**:
```json
{
  "type": "assistant",
  "uuid": "0871b20e-0f3f-4d69-9476-13ff0d78563b",
  "parentUuid": "9b868162-ad6d-4ea0-8182-582a95ace24c",
  "isSidechain": false,
  "timestamp": "2026-05-11T10:36:56.174Z",
  "userType": "external",
  "entrypoint": "claude-desktop",
  "cwd": "F:\\test",
  "sessionId": "f4cb2964-0a81-415f-8f9d-31acb3d9131e",
  "version": "2.1.128",
  "gitBranch": "HEAD",
  "requestId": "req_011Cavdn9Qgahdx5Lmbjjowd",
  "message": {
    "model": "claude-haiku-4-5-20251001",
    "id": "msg_01MnPKDZhm6wGeqimS6t2MWd",
    "type": "message",
    "role": "assistant",
    "content": [{
      "type": "text",
      "text": "This isn't a Linux VM — you're running **Windows 11 Pro** (build 10.0.26200). ..."
    }],
    "stop_reason": "end_turn",
    "usage": {
      "input_tokens": 8,
      "cache_creation_input_tokens": 394,
      "cache_read_input_tokens": 42998,
      "output_tokens": 162,
      "service_tier": "standard"
    }
  }
}
```

**Implication**: the existing `pce_persistence_watcher` (cowork
edition) already understands this row shape. It needs:

1. **A new source root**: `%USERPROFILE%\.claude\projects\` (in
   addition to the existing
   `%LOCALAPPDATA%\...\Roaming\Claude\local-agent-mode-sessions\`).
2. **A new `source_id`**: `l3g-claude-code-tab-default` (parallel to
   `l3g-local-persistence-default`) to tag rows distinctly.
3. **An `entrypoint` discriminator** inside the structural normaliser
   so `entrypoint:"claude-desktop"` rows become
   `tool_family='claude-desktop-code'` and `entrypoint:"cli"` (or
   absent) rows become `tool_family='claude-code-cli'` (deferred to P6).

---

## Q3 — What does the Code-tab session pointer look like?

Parallel to cowork's `local-agent-mode-sessions/<user>/<org>/local_<sess>.json`,
Code tab writes:

`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code-sessions\<user_uuid>\<org_uuid>\local_<sessionId>.json`

For the drive session, the path was:

```
claude-code-sessions/
  e323c763-c6b3-4761-a126-8d80870ee7c3/                    ← user_uuid
    8a522742-17e5-43c6-b1aa-b28bf3d7ad32/                  ← org_uuid
      local_69b85915-deab-488c-97cc-65f372c7ebb8.json      ← 946 B pointer
```

**Full pointer content** (this is structured, NOT a transcript):
```json
{
  "sessionId":      "local_69b85915-deab-488c-97cc-65f372c7ebb8",
  "cliSessionId":   "f4cb2964-0a81-415f-8f9d-31acb3d9131e",
  "cwd":            "F:\\test",
  "originCwd":      "F:\\test",
  "createdAt":      1778495789349,
  "lastActivityAt": 1778495801285,
  "model":          "claude-haiku-4-5-20251001",
  "isArchived":     false,
  "title":          "Identify Linux distribution on VM",
  "titleSource":    "auto",
  "permissionMode": "acceptEdits",
  "enabledMcpTools": {
    "local:PCE — Personal Cognitive Engine:pce_capture":          true,
    "local:PCE — Personal Cognitive Engine:pce_query":            true,
    "local:PCE — Personal Cognitive Engine:pce_stats":            true,
    "local:PCE — Personal Cognitive Engine:pce_sessions":         true,
    "local:PCE — Personal Cognitive Engine:pce_session_messages": true,
    "local:PCE — Personal Cognitive Engine:pce_capture_pair":     true
  },
  "remoteMcpServersConfig":     [],
  "alwaysAllowedReasons":       [],
  "sessionPermissionUpdates": [{
    "type":        "addRules",
    "rules":      [{"toolName": "Read", "ruleContent": "//etc/**"}],
    "behavior":    "allow",
    "destination": "session"
  }]
}
```

**Field interpretations**:

- `sessionId` (`local_<uuid>`) = Desktop UI's handle for the session.
  Appears in the Recents sidebar.
- `cliSessionId` (different UUID) = the claude-code agent's native
  session id. Equals the filename stem of the JSONL transcript.
- `cwd` / `originCwd` = workspace root the agent operates in. Drives
  the JSONL parent dir name encoding.
- `permissionMode: acceptEdits` = matches the "Accept edits" toggle in
  the bottom toolbar of the Code-tab UI.
- `enabledMcpTools` = **which user-installed MCP tools the agent can
  reach for this session**. PCE-mcp's 6 tools are here because the
  `.mcpb` was installed via Settings → Extensions.
- `sessionPermissionUpdates` = explicit user grants for tool-use
  permission, in chronological order. The single entry shows the user
  accepting Claude's request to read `//etc/**` (which is what
  triggered the "Ran Check what OS/distro is running" toast in the
  screenshot).

**Implication for L3g**: the pointer file is **structured session
metadata** that complements the JSONL transcript. The normaliser
should pair them via `cliSessionId` and write a single richer
`sessions` row that joins:
- Title, model, permission_mode, created_at, last_activity_at, cwd
  (from the pointer)
- Message turns, tool calls, content (from the JSONL)

---

## Q4 — Why does L1 miss the conversation body?

`/v1/sessions/watch` is the channel where the actual prompt+response
exchange flows. Its handshake-time request headers
(`@/f:/INVENTION/You.Inc/PCE Core/_inspect4_out.txt:4-33`) reveal it:

```
GET /v1/sessions/watch HTTP/2
  accept: text/event-stream, text/event-stream            ← SSE
  anthropic-beta: ccr-byoc-2025-07-29                     ← "CCR" internal codename
  anthropic-client-app: com.anthropic.claudefordesktop
  anthropic-client-feature: ccr                           ← short form
  anthropic-client-version: 1.6608.2
  anthropic-client-platform: desktop_app
  anthropic-client-os-platform: win32
  anthropic-client-os-version: 10.0.26200
  referer: https://claude.ai/epitaxy                      ← shared cowork prefix
  ...
```

**`ccr`** appears to mean "**Claude Code Remote**". `byoc` is probably
"**Bring Your Own Code**" (i.e., the user's own files / repo). The
referer being `claude.ai/epitaxy` is interesting because `epitaxy` was
cowork's internal codename — Code tab inherits part of cowork's UI
shell.

**Why the body isn't captured**: mitmproxy is configured with
`stream_large_bodies=1m` in
`@/f:/INVENTION/You.Inc/PCE Core/run_proxy.py`. For SSE long-polls
the response body is open-ended (events flow over minutes or hours)
and mitmproxy's `response` hook only fires when the response is
complete. So a `/v1/sessions/watch` request that's still streaming at
snapshot time will have a `request` row but no paired `response` row
in `raw_captures`. After the drive we found exactly that pattern: 1
request, 0 paired response (`@/f:/INVENTION/You.Inc/PCE Core/_inspect4_out.txt:35-36`).

**Three potential fixes**, ranked by cost:

1. **(cheapest, recommended)**: do nothing on L1 — rely on L3g for
   the conversation content. The L1 watch handshake is enough as a
   detection signal.
2. **(medium)**: add an `event_message` / `responseheaders` hook in
   `pce_proxy/addon.py` (parallel to the WebSocket hooks shipped in
   the cowork sub-run) that flushes SSE event lines to `raw_captures`
   as they arrive instead of waiting for response completion.
3. **(expensive)**: switch `stream_large_bodies` off for
   `claude.ai/v1/sessions/watch` specifically. Would buffer the entire
   long-poll in memory — fragile, leaks RAM.

For P5.B.7 we recommend option (1). Option (2) is a v1.1 polish task
to add a redundant network capture path for resilience.

---

## Q5 — Distinguishing Desktop Code tab vs standalone CLI in the
same JSONL store

The Desktop-embedded `claude.exe` and the standalone npm `claude` both
write to `%USERPROFILE%\.claude\projects\<encoded-cwd>\<sess>.jsonl`.
That's by design — they're the same agent binary spawned by different
parents.

**Discriminator**: the `entrypoint` field on every JSONL row:

| `entrypoint` value | Origin | Maps to PCE `tool_family` |
|--------------------|--------|---------------------------|
| `"claude-desktop"` | Spawned by Claude Desktop's Code tab | `claude-desktop-code` |
| `"cli"` (or absent) | User typed `claude` in a terminal | `claude-code-cli` (P6 scope) |

Confirmed by the drive: every row in
`f4cb2964-0a81-415f-8f9d-31acb3d9131e.jsonl` has
`"entrypoint":"claude-desktop"`. Older sessions in adjacent
`projects/F--INVENTION-...` directories (from April standalone-CLI
usage) lack this field or have `"entrypoint":"cli"` — to be verified
when P6 work resumes.

**Implication for the structural normaliser**:
```python
# pce_core/normalizer/local_persistence.py (or new cli_jsonl.py)
def _tool_family_from_row(row: dict) -> str:
    ep = row.get("entrypoint") or "cli"
    return {
        "claude-desktop": "claude-desktop-code",
        "cli":            "claude-code-cli",
    }.get(ep, "claude-code-cli")
```

Sweep-runner case spec can require `tool_family='claude-desktop-code'`
to scope E-cases to the inline Code tab only.

---

## Q6 — M axis: are user-installed MCP servers visible?

**Yes, fully** — confirmed by the pointer file's `enabledMcpTools`
block (Q3). All 6 PCE tools are listed:

```
local:PCE — Personal Cognitive Engine:pce_capture          true
local:PCE — Personal Cognitive Engine:pce_query            true
local:PCE — Personal Cognitive Engine:pce_stats            true
local:PCE — Personal Cognitive Engine:pce_sessions         true
local:PCE — Personal Cognitive Engine:pce_session_messages true
local:PCE — Personal Cognitive Engine:pce_capture_pair     true
```

This is the **opposite** of the cowork architectural finding (memory
ref `76de9e16`): cowork has Anthropic-curated `mcp__<server>__*` tool
families and rejects user `.mcpb` installs. **Code tab DOES respect
user MCP packs.**

**Implication for E-cases**: an E16-equivalent ("invoke a PCE MCP
tool from Code tab and verify the round-trip lands a `messages` row
with `source_type='pce_mcp'`") is **expected to PASS**, unlike
C16-cowork which is documented SKIP.

---

## Q7 — What else does L1 catch around a Code-tab drive?

Full catalog of NEW endpoint families seen during the
2026-05-11T10:34-10:36 UTC drive (FLOOR_TS = 1778495670.7,
`@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_inspect3_out.txt`):

### `claude.ai` (+65 rows total)

| Path family | Count | Body shape | Use |
|-------------|-------|------------|-----|
| `/v1/environment_providers/private/organizations/<org>/environments` | 16 | small JSON | heartbeat poll (every ~7s) — same family as cowork's heartbeat, **without** the `?included_worker_types=cowork` filter |
| `/v1/sessions` (GET) | 12 | `{"data":[],"has_more":false}` | session list — **always empty** in our drive (active sessions live elsewhere) |
| `/v1/sessions/watch` (GET, SSE) | 1 | open long-poll, no body row | actual conversation channel (see Q4) |
| `/api/bootstrap/<org>/current_user_access` | 4 | 1787 B JSON | per-tab access check |
| `/api/accounts/<acct>/invites` | 4 | 2 B | trivial |
| `/api/organizations/<org>/plugins/list-plugins?installation_preference=auto_install&installation_preference=...` | 4 | **23,140 B** | full plugin catalogue — equivalent of cowork's `/skills/list-skills` |
| `/api/organizations/<org>/memory/settings` | 2 | small JSON | memory feature settings |
| `/api/organizations/<org>/code/repos?skip_status=true` | 2 | 237 B | **Code-tab-specific** GitHub repo list — currently `{"type":"error","error":{"type":"authentication_error","message":"You must authenticate with GitHub.","error_code":"auth_required"}}` because the user hasn't connected GitHub |
| `/api/organizations/<org>/marketplaces/list-account-marketplaces` | 4 | 19 B | marketplace presence |
| `/api/organizations/<org>/usage` | 2 | 2136 B | quota |
| `/api/organizations/<org>/dust/generate_title_and_branch` | 4 | **prompt original ⇄ title** (see Q1) | auto-titling — **the one L1 row that carries the prompt verbatim** |
| `/api/organizations/<org>/sync/settings` | 2 | small JSON | preferences sync |
| `/api/event_logging/v2/batch` (POST) | 4 | 8K-18K binary | Anthropic telemetry — large, opaque, **probably ignore** |

### `api.anthropic.com` (+18 rows, **NEW host** for Desktop)

| Path | Count | Status | Body |
|------|-------|--------|------|
| `/api/claude_code/settings` | 18 (9 req + 9 resp) | **all 404** | `{"type":"error","error":{"type":"not_found_error","message":"Not found."}}` |

This is a 30-second polling probe Code tab makes against
`api.anthropic.com` (not `claude.ai`!) looking for a settings
endpoint that doesn't exist yet — feature-gated or future API. **Safe
to ignore** in capture pipeline but record as evidence Code tab is
active.

### `chromium-indexeddb` (+19 L3g summaries)

All `/claude-desktop/indexeddb-summary/https://claude.ai`, 2845 B
each, content **redacted** by L3g policy
(`"composer_drafts_redacted":true`, text values replaced with
`"[redacted]"`). This is the existing L3g IndexedDB summariser — does
NOT carry conversation content. Object stores observed:
`composer-drafts`, `claude-notifications`, `keyval-store`. None are
Code-tab specific.

---

## Q8 — Filesystem deltas during the drive

`@/f:/INVENTION/You.Inc/PCE Core/_code_fs_scan.out:13-35` lists 22
files modified in the `Claude_*\LocalCache\Roaming\Claude\` tree
since FLOOR_TS. Filtered to the structurally interesting ones:

```
mt=18:36:44  size=  946  claude-code-sessions/e323c763-c6b3-4761-a126-8d80870ee7c3/8a522742-17e5-43c6-b1aa-b28bf3d7ad32/local_69b85915-deab-488c-97cc-65f372c7ebb8.json   ← pointer (Q3)
mt=18:46:43  size=    7  claude-code-vm/.sdk-version                                                                                                                       ← keep-alive marker (touched but tiny)
mt=18:46:44  size= 396799 logs/cowork_vm_node.log                                                                                                                          ← cowork VM keep-alive (NOT Code-tab)
mt=18:46:44  size=37748736 vm_bundles/claudevm.bundle/smol-bin.vhdx                                                                                                        ← cowork VM keep-alive (NOT Code-tab)
mt=18:46:09  size= 1365194 IndexedDB/https_claude.ai_0.indexeddb.leveldb/000004.log                                                                                        ← Electron IDB log (binary leveldb)
mt=18:46:09  size=  973276 IndexedDB/https_claude.ai_0.indexeddb.blob/1/09/92c                                                                                              ← Electron IDB blob — could contain UI state (binary)
mt=18:47:18  size=  632661 logs/main.log                                                                                                                                   ← main process log
mt=18:47:17  size=   18226 sentry/scope_v3.json                                                                                                                            ← Sentry telemetry
```

PLUS — outside `LocalCache` — at `%USERPROFILE%\.claude\projects\F--test\`:

```
mt=18:36:56  size= 16783   f4cb2964-0a81-415f-8f9d-31acb3d9131e.jsonl   ⭐ TRANSCRIPT (Q2)
```

This is the JSONL the structural normaliser will read. Note the path
is **outside** the MSIX package's LocalCache — it's in the user's
home directory, accessible without UWP-sandbox elevation.

---

## Open architectural questions (RECON-resolvable in next pass)

1. **Tool-call lines in JSONL**: this drive's prompt triggered "Ran
   Check what OS/distro is running" in the UI (visible in the screenshot)
   but we only sampled the last (assistant text) line. Need to dump
   middle lines to confirm `type:"tool_use"` and `type:"tool_result"`
   row shapes match cowork's. Likely identical given same agent code
   but verify before writing the normaliser.

2. **Subagent JSONL files**: cowork RECON Q5 mentioned sub-bundle
   files (`.claude/projects/.../subagents/agent-*.jsonl`). For Code
   tab, do these appear when the agent invokes the `Agent` tool?
   Needs a follow-up drive with a multi-step task.

3. **PCE MCP tool invocation from Code tab**: with the 6 PCE tools
   listed in `enabledMcpTools`, what does a `pce_stats` call look
   like in the JSONL? Expected shape: `tool_use` row with
   `name:"pce_stats"` + `tool_result` row with `pce_capture_pair` MCP
   response. This is the E16-equivalent acceptance signal.

4. **GitHub-authenticated `code/repos`**: the current 401 response
   skips the repo-listing path. After user connects GitHub, what does
   the response shape look like, and does Code tab make any new
   endpoint calls? Probably not in P5.B.7 scope but worth a one-line
   note in MATRIX §4.1.C.

5. **`/v1/sessions/watch` SSE event types**: even if we don't capture
   the body for production, knowing the event names (`session.created`,
   `message.delta`, `tool_use.start`, etc.) would help cross-validate
   L3g rows against L1 envelope rows. Resolvable by adding a temporary
   mitmproxy SSE-dump hook for one drive.

6. **`anthropic-beta: ccr-byoc-2025-07-29`**: this beta gate could
   move or expire. Track the date string — if Anthropic ships v1 of
   Code tab to GA the beta header changes and we need to update any
   detection logic that keys off it.

---

## Recommendations for Phase 2 (doc 落盘) and Phase 3 (impl)

### Phase 2 — doc alignment (no code)

1. **Fix MATRIX line 244**
   `@/f:/INVENTION/You.Inc/PCE Core/Docs/stability/DESKTOP-PRODUCT-MATRIX.md:244`
   change "Code-region | H1 CLI wrap" to:
   > "**Code-region (inline)** | **L1 (N axis)** for shell endpoints +
   > **L3g** for JSONL transcript at `~/.claude/projects/`
   > (`entrypoint:"claude-desktop"`-discriminated) + **M (MCP)** —
   > H1 PATH shim NOT applicable (Desktop spawns embedded
   > `claude-code/<ver>/claude.exe` via absolute path). Standalone
   > Claude Code CLI lives in P6 §4.6."

2. **Fix MATRIX line 248** — split the lumped "Run Trace (cowork +
   code-region CLI sessions)" into:
   > "Tool Tape (cowork) + Run Trace (Code-tab inline) + (P6 Run
   > Trace for standalone CLI, deferred)."

3. **Fix MATRIX line 265** in the cowork prose
   (`@/f:/INVENTION/You.Inc/PCE Core/Docs/stability/DESKTOP-PRODUCT-MATRIX.md:265`)
   to clarify Code-region(inline) is host-native, not VM.

4. **Add MATRIX §4.1.C** — a new sub-section for inline Code region,
   structured like §4.1.B (cowork). Include:
   - Product positioning (Desktop UI wraps host-native claude-code agent)
   - Three-axis breakdown (L1 / L3g / M, no H1)
   - Open questions list (the 6 above)
   - **E-case namespace E00-E0X** acceptance bar (≥80% PASS)

5. **Add MATRIX §5.C** — full E-case spec table, modelled after §5.B
   for cowork. Draft scope:
   - **E00** detection: switching to Code tab triggers
     `/v1/environment_providers/.../environments` (without cowork
     filter) + `/api/claude_code/settings` 404 + `/v1/sessions/watch`
     handshake within 5s
   - **E01** single prompt: prompt → JSONL line with role=user;
     response → JSONL line with role=assistant; both with
     `entrypoint="claude-desktop"`
   - **E02** streaming complete: multi-paragraph reply ends with
     `stop_reason="end_turn"`, all text in `content[].text`
   - **E03** multi-step tool use: prompt that triggers ≥1 tool_use
     turn → JSONL has `type:"tool_use"` rows correlated by
     `parentUuid` chain
   - **E04** prompt cancel: stop mid-stream, JSONL has terminal
     event without phantom assistant row
   - **E05** file context: open Code tab in a cwd containing files,
     ask the agent to read one → tool_use(Read) → tool_result with
     actual file content in `message.content`
   - **E06** `permissionMode` propagation: switching "Accept edits"
     toggle in UI → next JSONL turn has updated
     `sessionPermissionUpdates` block
   - **E07** auto-title: prompt first_session_message → L1 captures
     `/dust/generate_title_and_branch` request body matching the
     prompt
   - **E08** session list visibility: Code-tab Recents sidebar entry
     appears within 1s of session creation → pointer
     `local_<sess>.json` exists on disk
   - **E09** PCE MCP tool: prompt "use pce_stats" → JSONL has
     tool_use(pce_stats) → tool_result with our schema. **Expected
     PASS** (cowork's C16 was SKIP because user MCP rejected; Code
     tab DOES accept them)
   - **E10** GitHub auth (SKIP unless user connects): `/code/repos`
     responds 200 instead of 401, repos appear in UI
   - **E11** model selection: switching model in UI → next JSONL line
     has the new `message.model`
   - **E12** workspace switch: closing and reopening Code tab in
     different cwd → new `~/.claude/projects/<new-encoded>/<new-sess>.jsonl`
     file created
   - **E13** idle silence: 5min no activity → 0 new JSONL lines, ≤8
     heartbeat polls
   - **E14** L3g backstop: capture-pipeline kill mitm, run prompt,
     restart — JSONL transcript still flushed correctly to disk
   - **E15** title source: `titleSource:"auto"` from
     `dust/generate_title_and_branch` matches the displayed Recents
     entry text

   Target acceptance: **≥12/16 PASS, ≤4 SKIP, 0 FAIL** (mirrors
   cowork P5.B.5.5c 12/5/0 target).

### Phase 3 — implementation

1. **Watcher**: extend `pce_persistence_watcher/`:
   - New source `~/.claude/projects/<encoded-cwd>/*.jsonl` with
     `source_id='l3g-claude-code-tab-default'`
   - New source `claude-code-sessions/<user>/<org>/local_<sess>.json`
     with `source_id='l3g-claude-code-session-pointer-default'`
   - Discovery probe checks for both roots existence; gracefully
     no-op if absent
   - Pair pointer↔JSONL by `cliSessionId` ↔ filename stem

2. **Normaliser**: extend `pce_core/normalizer/local_persistence.py`
   (or split out `cli_jsonl.py`) to:
   - Map JSONL row `type` → PCE `messages` row shape (reuses cowork
     normaliser's `_extract_rich_blocks` logic)
   - Set `tool_family` from `entrypoint` discriminator
   - Set `provider='anthropic'`, `model_names=[message.model]`,
     `title_hint=<pointer.title>`,
     `oi_attributes_json={cliSessionId, cwd, permissionMode, version,
     gitBranch, sessionPermissionUpdates}`
   - One sessions row per `cliSessionId`, messages rows per JSONL line

3. **Driver**: extend `tests/e2e_desktop_ui/drivers/claude_desktop.py`:
   - `open_code_tab()` — click third sidebar tab, wait for
     "/v1/sessions/watch" handshake in DB
   - `new_code_session(cwd: Optional[Path])` — click "New session"
     in left sidebar, optionally set cwd via UIA file picker
   - `send_code_prompt(text: str)` — type into composer, press Enter
   - `wait_for_code_response(timeout: int)` — poll for new assistant
     line in the JSONL transcript file matching the active session
   - `accept_edit_permission(rule_substring: str)` — when Claude pops
     up a Read/Bash permission dialog, click "Allow once"

4. **Sweep runner**: new
   `tests/e2e_desktop_ui/run_p1_code_sweep.py` modelled after
   `run_p1_cowork_sweep.py` — same dual `--mode static / live`
   architecture, 16 E-cases per Phase 2 spec, same verdict aggregator
   shape.

### Phase 4 — sweep + tag

Same shape as P5.B.5.5c. Tag candidate: **`v1.1.0-alpha.12-code-p1`**
once the sweep passes ≥12/16 with 0 FAIL.

---

## Evidence files (archived in-tree under `Docs/research/2026-05-11-code-recon-artefacts/`)

| File | Role |
|------|------|
| `@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_code_snap_before.json` | Pre-drive DB+FS snapshot, FLOOR_TS for diffs |
| `@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_code_snap_after.json` | Post-drive snapshot |
| `@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_inspect3_out.txt` | Endpoint catalog + body samples for the drive window |
| `@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_inspect4_out.txt` | `/v1/sessions/watch` header dump + paired-row check |
| `@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_code_fs_scan.out` | Filesystem delta scan during drive |
| `@/f:/INVENTION/You.Inc/PCE Core/Docs/research/2026-05-11-code-recon-artefacts/_code_jsonl_hunt.out` | Discovery of `~/.claude/projects/F--test/<sess>.jsonl` |

The RECON scripts that produced these (`_code_phase1_snap.py`,
`_code_drive_inspect{,2,3,4}.py`, `_code_fs_scan.py`,
`_code_jsonl_hunt.py`, `_code_tab_recon.py`) were one-shots and were
removed at P5.B.7 P2.1 cleanup (mirroring cowork's discipline). The
six data artefacts above are kept in-tree because they are the
empirical anchors cited throughout this findings doc; future Code-tab
RECON drives should write into a new dated `code-recon-artefacts/`
subdirectory rather than reviving repo-root scratch files
(`/_code_*` is now in `.gitignore`).
