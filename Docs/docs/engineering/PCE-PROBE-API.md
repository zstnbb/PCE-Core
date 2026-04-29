# PCE Probe — Agent-Facing Debug API

**Status**: design / Stage 1 in progress (2026-04-29)
**Working name**: PCE Probe
**Schema version**: `1`

## 1. What this is

PCE Probe is an **agent-friendly debugging instrument** for the AI-native
sites that PCE Core captures from. It is **not** an autopilot, not a test
runner with policy logic, and not an automation framework. It is a
narrow, high-quality set of verbs that any agent (Cascade, Claude
Desktop, GPT-4 Operator, custom scripts) can call to:

- drive a real, logged-in browser session
- inspect what the page is doing (DOM / network / capture pipeline)
- verify that a capture round-tripped to PCE Core
- get rich, agent-readable failure context when something breaks

The Probe lives **inside** the existing PCE browser extension. When the
agent-facing WebSocket endpoint is reachable, the extension upgrades from
"passive capture" to "passive capture + agent-driven probe". When it's
not reachable, the extension behaves identically to today — no behavioral
regression for end users.

## 2. Why this exists

The Selenium / chromedriver / `--remote-debugging-port` path has proven
brittle on Windows in particular: profile launch interacts badly with
crash markers, singleton locks, enterprise policy, TIME\_WAIT sockets.
None of that complexity is the agent's problem; it should be the
**tool's** problem, and it should be solved exactly once.

The Probe approach replaces a fragile, multi-layered launch dance
(`selenium → chromedriver → chrome.exe → remote debugging port`) with a
trivially observable channel (`agent → ws → already-running-extension →
chrome APIs`). The browser is the host. The extension is the driver.
The agent only sees a clean RPC.

## 3. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│ Long-running Chrome (any profile, manually launched or         │
│ auto-launched, headed)                                         │
│                                                                │
│   ┌──────────────────────────┐      ┌──────────────────────┐  │
│   │ PCE Browser Extension    │  L3a │ AI tab(s)            │  │
│   │   - capture pipeline     │ ──── │  navigates / types / │  │
│   │     (already shipping)   │      │  reads streamed      │  │
│   │   - probe-rpc module     │      │  reply               │  │
│   │     (NEW)                │      └──────────────────────┘  │
│   └──────────┬───────────────┘                                 │
│              │ ws://127.0.0.1:9888  (only when reachable)      │
└──────────────┼─────────────────────────────────────────────────┘
               │
               ▼
       ┌─────────────────────┐    HTTP    ┌───────────────────┐
       │ pce_probe ws server │◄──────────►│ PCE Core          │
       │ (Python)            │            │ /api/v1/...       │
       └──────────┬──────────┘            └───────────────────┘
                  │
                  ▼
          ┌───────────────┐
          │ Agent / pytest│
          └───────────────┘
```

### Two stable surfaces

1. **Extension WS client** (TS, ships in the extension)
   - Tries `ws://127.0.0.1:9888` on every service-worker startup.
   - Reconnects with exponential backoff up to 30 s.
   - When connected, exposes the verb catalog below.
   - When not connected, sleeps; capture pipeline runs as today.

2. **Probe WS server** (Python, `pce_probe.server`)
   - Binds `127.0.0.1:9888` (loopback only, never external).
   - Accepts at most one extension client at a time.
   - Multiplexes incoming agent calls (`pce_probe.client`) onto that one
     extension client and routes responses back by request id.
   - Exposes a synchronous Python API for pytest fixtures + a thin HTTP
     facade for non-Python agents.

## 4. RPC envelope

### Request (agent → extension)

```json
{
  "v": 1,
  "id": "req-<uuid4>",
  "verb": "tab.open",
  "params": { "url": "https://chatgpt.com/" },
  "timeout_ms": 30000
}
```

| Field        | Required | Notes                                              |
|--------------|----------|----------------------------------------------------|
| `v`          | yes      | Schema version. Mismatch → `error: schema_mismatch`|
| `id`         | yes      | Unique per request. Echoed in response.            |
| `verb`       | yes      | See §5.                                            |
| `params`     | yes      | Object. Empty `{}` if verb takes no params.        |
| `timeout_ms` | no       | Default 30 000. Hard cap per verb (see §5).        |

### Response (extension → agent)

Success:

```json
{
  "v": 1,
  "id": "req-<uuid4>",
  "ok": true,
  "result": { "tab_id": 1234, "url": "https://chatgpt.com/" },
  "elapsed_ms": 472
}
```

Failure:

```json
{
  "v": 1,
  "id": "req-<uuid4>",
  "ok": false,
  "error": {
    "code": "selector_not_found",
    "message": "querySelector('#prompt-textarea') matched 0 nodes",
    "context": {
      "tab_id": 1234,
      "url": "https://chatgpt.com/c/abc-123",
      "title": "ChatGPT",
      "dom_excerpt": "<main><div class='..'>...",
      "last_capture_events": [
        { "ts": 1714382912.4, "verb": "PCE_CAPTURE", "host": "chatgpt.com" }
      ],
      "screenshot_b64": null
    },
    "agent_hint": "selector likely changed; check chatgpt.content.ts"
  },
  "elapsed_ms": 30001
}
```

**Critical**: every failure carries enough state for the calling agent
to decide its next action without a follow-up round trip. If the
selector is missing, include a DOM excerpt; if a capture didn't arrive,
include the recent capture-pipeline log; if the page navigated away,
include the new URL. `agent_hint` is a short suggestion the tool
maintainers can update as the extension evolves.

### Error code taxonomy

| Code                 | Meaning                                         |
|----------------------|-------------------------------------------------|
| `schema_mismatch`    | `v` differs from server-supported version       |
| `unknown_verb`       | verb name not registered                        |
| `params_invalid`     | required field missing / wrong type             |
| `tab_not_found`      | `tab_id` doesn't exist or is closed             |
| `selector_not_found` | DOM `querySelector` matched 0 nodes             |
| `selector_ambiguous` | `requireUnique` set and >1 match                |
| `navigation_failed`  | `chrome.tabs.update` rejected                   |
| `script_threw`       | injected script raised; inner stack in `context`|
| `timeout`            | verb's `timeout_ms` exceeded                    |
| `capture_not_seen`   | `capture.wait_for_token` window expired         |
| `host_blocked`       | extension host\_permissions don't cover URL     |
| `extension_internal` | bug in probe-rpc code (please report)           |

Agents are expected to handle `selector_not_found` and `timeout` as
"site changed, investigate" signals, not as test infrastructure bugs.

## 5. Verb catalog (v1)

All verbs are namespaced. `params` and `result` schemas are listed
inline. Defaults shown in `()`.

### `system.*` — meta

| Verb               | Params | Result                                          |
|--------------------|--------|-------------------------------------------------|
| `system.ping`      | `{}`   | `{ pong: true, extension_version: string }`     |
| `system.version`   | `{}`   | `{ schema_version: 1, extension_version: ... }` |

### `tab.*` — Chrome tab lifecycle

| Verb                  | Params                                  | Result                            |
|-----------------------|-----------------------------------------|-----------------------------------|
| `tab.list`            | `{}`                                    | `{ tabs: [{id,url,title,active}] }` |
| `tab.open`            | `{ url, active? (true) }`               | `{ tab_id, url }`                 |
| `tab.activate`        | `{ tab_id }`                            | `{ ok: true }`                    |
| `tab.close`           | `{ tab_id }`                            | `{ ok: true }`                    |
| `tab.navigate`        | `{ tab_id, url }`                       | `{ tab_id, url }`                 |
| `tab.wait_for_load`   | `{ tab_id, timeout_ms? (15000) }`       | `{ tab_id, url, title }`          |
| `tab.find_by_url`     | `{ url_pattern }` (regex)               | `{ tabs: [...] }`                 |

### `dom.*` — DOM queries + simulated input (executed via `chrome.scripting.executeScript`)

| Verb                  | Params                                                         | Result                                                  |
|-----------------------|----------------------------------------------------------------|---------------------------------------------------------|
| `dom.query`           | `{ tab_id, selector, all? (false), require_unique? (false) }` | `{ matches: [{outer_html_excerpt,text_excerpt,visible}] }` |
| `dom.wait_for_selector`| `{ tab_id, selector, timeout_ms? (15000), visible? (true) }`  | `{ matched: 1 }`                                        |
| `dom.click`           | `{ tab_id, selector, scroll_into_view? (true) }`               | `{ ok: true }`                                          |
| `dom.type`            | `{ tab_id, selector, text, clear? (true), submit? (false) }`   | `{ ok: true }`                                          |
| `dom.press_key`       | `{ tab_id, selector?, key, modifiers? }`                       | `{ ok: true }`                                          |
| `dom.scroll_to`       | `{ tab_id, selector? \| y? }`                                  | `{ ok: true }`                                          |
| `dom.execute_js`      | `{ tab_id, code, args? }` (sandboxed, world: ISOLATED)         | `{ value: <serializable> }`                             |

`dom.type` simulates real keyboard events (`input` + `change` +
`InputEvent`). It does not bypass React-controlled inputs by writing
`value` directly; it dispatches the events that the framework listens
for. `submit: true` additionally dispatches Enter on the focused
element after typing.

### `page.*` — observability

| Verb                  | Params                                | Result                                                                 |
|-----------------------|---------------------------------------|------------------------------------------------------------------------|
| `page.dump_state`     | `{ tab_id, dom_max_chars? (8000) }`   | `{ url, title, dom_excerpt, ready_state, location_search, cookies_count }` |
| `page.screenshot`     | `{ tab_id, format? ('png') }`         | `{ image_b64, width, height }`                                         |
| `page.network_log`    | `{ tab_id, last_n? (50) }`            | `{ requests: [{url,method,status,t}] }` (best effort)                  |

`page.network_log` is best-effort because the extension can only see
what the existing `interceptor-network.ts` patched (fetch, XHR, WS,
EventSource). Requests issued by other extensions or by Chrome itself
aren't visible. This is documented as a known limitation; agents should
not rely on it for low-level networking debugging.

### `capture.*` — PCE capture-pipeline observation

| Verb                       | Params                                                                              | Result                                                |
|----------------------------|-------------------------------------------------------------------------------------|-------------------------------------------------------|
| `capture.wait_for_token`   | `{ token, timeout_ms? (60000), provider? }`                                         | `{ matched: true, capture_id, session_hint, ... }`    |
| `capture.recent_events`    | `{ last_n? (20), provider? }`                                                       | `{ events: [...] }`                                   |
| `capture.pipeline_state`   | `{}`                                                                                | `{ enabled, capture_count, last_error, server_online, queued }` |

`capture.wait_for_token` is the verb that closes the loop. The agent
types a unique token (e.g. `T01-1714382912`) into the AI tab; the
content script captures the conversation and sends it via
`PCE_CAPTURE`; the probe-rpc module observes the message before the
background worker forwards it to the ingest API; if the captured body
contains the token (substring match in any message's `content`), the
verb resolves with the capture metadata. Otherwise the timeout fires
with `error.code = "capture_not_seen"` and the failure context includes
`capture.recent_events` so the agent sees what _did_ get captured.

### Verbs explicitly NOT in v1

- No keyboard or mouse simulation outside an element. Use `dom.click` /
  `dom.type` — they take a selector. This avoids the entire class of
  "where is the cursor?" bugs.
- No browser-process control (close, restart, switch profile). The
  Probe does not own the browser lifecycle. If the browser dies, the
  extension's WS reconnects when it comes back.
- No file upload. (Likely v2.)
- No iframe traversal helpers. (Likely v2.)
- No multi-tab transactions. (Each tab\_id is independent.)

## 6. Connection model

```
extension boots
   │
   ▼
ws://127.0.0.1:9888 reachable?  ── no ──► sleep, retry in 5s..30s (cap)
   │ yes
   ▼
send `{ v:1, hello: { extension_version, schema_version } }`
   │
   ▼
server replies `{ v:1, ok: true, server_version }`
   │
   ▼
ready: route incoming RPCs
```

If the schema version doesn't match, both sides log + close the
connection. The server SHOULD support last-N versions for graceful
upgrades.

The server enforces **single-client semantics**: at most one extension
WS client and at most one agent caller. Two pytest sessions hitting the
same server will be serialized at the agent-callable layer (Python
`asyncio.Lock`).

## 7. Security

- Server **binds 127.0.0.1 only**. Never `0.0.0.0`.
- No authentication is required for v1 because the threat model is
  "everything on this loopback is trusted" (single dev machine or
  isolated dedicated host). For server-deployed multi-tenant later,
  add a shared HMAC secret via env `PCE_PROBE_HMAC_KEY`.
- The server **MUST refuse** the connection if the upgrade `Origin`
  header is set and not `null` / `chrome-extension://...`. (Defends
  against a malicious page connecting from JavaScript.)
- `dom.execute_js` runs in `world: 'ISOLATED'`. It cannot read page
  variables but cannot be tampered with by page scripts. If the agent
  needs MAIN-world access for unblocking a specific case, that becomes
  an explicit verb in v2 with extra warnings.

## 8. Versioning policy

- `v: 1` is **stable** once Step 4 of Stage 1 ships green.
- Adding a new verb / a new optional param: minor change, same `v`.
- Removing a verb / changing a param's required-ness / changing a
  return shape: bump `v`. Both extension and server announce supported
  versions in the hello message.
- ADR for any `v` bump goes in `Docs/docs/decisions/`.

## 9. Reference flow — one ChatGPT case end-to-end

```python
# pytest fixture / agent code
probe = pce_probe.connect()  # ws://127.0.0.1:9888 already running

token = f"PROBE-T01-{int(time.time())}"

probe.tab.open("https://chatgpt.com/")
probe.tab.wait_for_load()
probe.dom.wait_for_selector("#prompt-textarea")
probe.dom.type(
    "#prompt-textarea",
    f"Say the literal word 'hello' and the token {token}.",
    submit=True,
)
result = probe.capture.wait_for_token(token, timeout_ms=60_000)

# Server-side verification via PCE Core API (HTTP, not via probe)
session = httpx.get(
    f"http://127.0.0.1:9800/api/v1/sessions/{result['session_hint']}/messages"
).json()
assert any(token in m["content_text"] for m in session["messages"])
```

If `dom.wait_for_selector("#prompt-textarea")` fails, the response
contains a DOM excerpt the agent can read to discover ChatGPT renamed
the selector. If `capture.wait_for_token` fails, the response includes
the last 20 capture events so the agent sees whether the issue is
"capture didn't fire" vs "capture fired but token wasn't in content"
vs "wrong session\_hint".

## 10. File layout

```
pce_browser_extension_wxt/
  entrypoints/
    background/
      probe-rpc.ts         ← NEW: WS client + verb dispatcher
      probe-rpc-tab.ts     ← NEW: tab.* verbs
      probe-rpc-dom.ts     ← NEW: dom.* verbs
      probe-rpc-page.ts    ← NEW: page.* verbs
      probe-rpc-capture.ts ← NEW: capture.* verbs (taps PCE_CAPTURE)
    background.ts          ← MOD: import + boot probe-rpc
  utils/
    probe-protocol.ts      ← NEW: shared types (envelope, errors)

pce_probe/                  ← NEW Python package
  __init__.py
  server.py                ← WS server, agent-side multiplexer
  client.py                ← Python client lib (sync + async)
  types.py                 ← request/response dataclasses (mirrors TS)
  errors.py                ← exception types one-to-one with error codes
  pytest_plugin.py         ← pytest fixture `pce_probe`
  __tests__/
    test_protocol.py
    test_server.py
    test_client.py

tests/
  e2e_probe/                ← probe-driven E2E (replaces selenium suite over time)
    conftest.py             ← pce_core fixture + matrix summary writer
    test_chatgpt_probe.py   ← T01 standalone proof (predates matrix)
    test_matrix.py          ← canonical entry: site x case parametrize
    sites/                  ← per-site adapters (selectors + login check)
      __init__.py           ← ALL_SITES registry
      base.py               ← BaseProbeSiteAdapter + LoginCheckResult
      _inventory.md         ← porting status tracker
      chatgpt.py  claude.py  gemini.py  perplexity.py
      googleaistudio.py  copilot.py  deepseek.py  kimi.py
      grok.py  manus.py  mistral.py  huggingface.py
      poe.py  zhipu.py      ← 14 sites
    cases/                  ← cross-site case templates
      __init__.py           ← ALL_CASES registry
      base.py               ← BaseCase + CaseResult + CaseStatus
      t00_smoke.py          ← adapter health (open + login + pipeline)
      t01_basic_chat.py     ← token round-trip through capture pipeline
    reports/                ← gitignored; per-run JSON summaries
      <UTC-timestamp>/
        summary.json
```

`pce_probe/` is its own top-level Python package, not nested under
`pce_core/`. Rationale: it's a tool (like `pce_proxy`), independent
release cadence, importable by tests + by external agents.

## 11. Build-mode policy — webstore vs sideload

The probe is an **internal developer tool**. It must never enter the
public Chrome Web Store bundle, both because (a) its `dom.execute_js`
verb is indistinguishable from a remote-code-execution backdoor for
automated CWS review, and (b) shipping it would require new
permission grants (`alarms`) and new privacy disclosures that
contradict the published listing.

The build is therefore split:

| | sideload (default) | webstore |
|---|---|---|
| invoked by | `pnpm build` / `pnpm dev` | `pnpm build --mode webstore` |
| `$probe-rpc` resolves to | `entrypoints/background/probe-rpc.ts` | `entrypoints/background/probe-rpc.stub.ts` |
| `$probe-rpc-capture` resolves to | `…/probe-rpc-capture.ts` | `…/probe-rpc-capture.stub.ts` |
| `__PCE_PROBE_ENABLED__` constant | `true` | `false` |
| manifest `permissions` | `…, alarms` | (no `alarms`) |
| manifest `host_permissions` | `<all_urls>` | explicit COVERED_SITES |
| `background.js` size | ~92 KB | ~58 KB |
| probe identifiers in bundle | present | **0** |

The pieces that enforce this:

1. **Virtual imports** in `entrypoints/background.ts`:
   ```ts
   import { observeCaptureMessage, setPipelineStateProvider } from "$probe-rpc-capture";
   import { startProbeRpc } from "$probe-rpc";
   ```
   `$probe-rpc` and `$probe-rpc-capture` are not real modules — they
   are aliases configured per build mode in `wxt.config.ts`
   `vite.resolve.alias`. The sideload alias points at the real
   files; the webstore alias points at no-op stubs
   (`probe-rpc.stub.ts`, `probe-rpc-capture.stub.ts`) whose function
   bodies are empty.

2. **Compile-time constant** `__PCE_PROBE_ENABLED__`, injected by Vite
   `define` in `wxt.config.ts`:
   ```ts
   define: {
     __PCE_PROBE_ENABLED__: JSON.stringify(!isWebstore),
   }
   ```
   Every probe call site in `background.ts` is wrapped:
   ```ts
   if (__PCE_PROBE_ENABLED__) {
     observeCaptureMessage({...});
   }
   ```
   In the webstore build the constant is replaced with the literal
   `false`, so Rollup's dead-code eliminator drops the entire
   branch. With no remaining call sites, the imports are unreachable
   and Rollup tree-shakes the (already no-op) stub modules out of
   the bundle as well.

3. **Conditional manifest permissions**: the `alarms` permission
   used by the probe MV3 service-worker keepalive is only declared
   in the sideload manifest. The webstore manifest's permissions
   list is unchanged from the pre-probe baseline, so the install
   warning shown to users is identical to the previously-published
   version.

4. **Type alignment**: `tsconfig.json paths` map both virtual
   identifiers to the real files so TypeScript checks against the
   real surface in every build. `vitest.config.ts resolve.alias`
   does the same so unit tests run against real probe code with
   `__PCE_PROBE_ENABLED__ = true`.

### How to verify

The webstore audit is two PowerShell calls:

```powershell
pnpm build --mode webstore

# Bundle markers — every count below should be 0 except
# captureVisibleTab (1, from the webextension-polyfill API metadata
# table; pre-existing, not probe).
$bg = ".output/chrome-mv3/background.js"
foreach ($m in @(
  "PCE Probe","ws://127.0.0.1:9888","probe-rpc","chrome.alarms",
  "startProbeRpc","observeCaptureMessage","setPipelineStateProvider",
  "ProbeException","KEEPALIVE_ALARM","wait_for_token","new Function("
)) {
  $hits = (Select-String -Path $bg -SimpleMatch -Pattern $m).Count
  "{0,-30} hits: {1}" -f $m, $hits
}

# Manifest permissions — should NOT include "alarms".
(Get-Content .output/chrome-mv3/manifest.json -Raw |
  ConvertFrom-Json).permissions -join ", "
```

If any probe marker is non-zero, the build is contaminated; do not
upload to CWS until it is zero.

### How to extend

Adding a new probe verb that uses a chrome API not currently in the
webstore manifest's `permissions`:

1. Add the verb to the appropriate `probe-rpc-*.ts` file.
2. Add a corresponding no-op export to the matching stub file
   (`probe-rpc.stub.ts` if it lives in the dispatcher; otherwise the
   namespace stub).
3. **Do not** add the new chrome permission to the webstore branch
   in `wxt.config.ts`. Sideload-only.
4. Re-run the audit above; confirm zero hits.
5. Bump `Docs/docs/engineering/PCE-PROBE-API.md` §5 + this §11 if
   the verb introduces a new visible-bundle pattern (e.g. a new
   compile-time gate).

A regression test for the audit lives at
`pce_browser_extension_wxt/entrypoints/__tests__/probe-rpc-system.test.ts`
("verb registry consistency"). Adding a new verb without updating
`__systemTesting.ALL_KNOWN_VERBS` will fail that test, which is the
tripwire for the agent-facing capability list.

## 12. Full-coverage matrix

The probe vertical (sections 1-9) is the **plumbing**. Section 10 lays
out the file structure; this section describes the coverage matrix
that runs on top.

### Architecture

```
              ┌──────────────────────────────────┐
              │ tests/e2e_probe/test_matrix.py   │
              │   parametrize site x case        │
              │   one pytest item per pair       │
              └────────┬─────────────────────────┘
                       │
              ┌────────▼─────────┐    ┌────────────────────┐
              │   sites/         │    │   cases/           │
              │  ALL_SITES = […] │    │  ALL_CASES = […]   │
              │                  │    │                    │
              │  BaseProbeSite-  │    │  BaseCase          │
              │  Adapter         │    │   .run(probe,      │
              │   .open()        │    │        pce_core,   │
              │   .check_logged_ │    │        adapter)    │
              │     in()         │    │     -> CaseResult  │
              │   .send_prompt() │    │                    │
              │   .wait_for_done │    │  T00 = smoke       │
              │   ()             │    │  T01 = basic chat  │
              │   selectors[]    │    │  ...               │
              └──────────────────┘    └────────────────────┘
                       │                       │
                       └───────────┬───────────┘
                                   │
                       ┌───────────▼────────────┐
                       │  pce_probe.ProbeClient │
                       │  (verb RPC over WS)    │
                       └────────────────────────┘
```

**Two orthogonal axes**:

* **Sites** (axis 1) hold *site-specific knowledge* — selectors, login
  detection, send-button vs Enter, conversation-URL parsing. Adding a
  new site means writing one ~50-line adapter; the new site is then
  automatically covered by every case in the matrix.
* **Cases** (axis 2) hold *behaviour* — basic chat, regenerate, image
  upload, branch flip. Cases are written once, against the abstract
  adapter contract, and run unchanged across every site. Adding a new
  case extends the matrix by `len(ALL_SITES)` cells with zero
  per-site work.

This is the same pattern as the legacy Selenium suite at
`tests/e2e/sites/`, but lifted onto the probe RPC so the suite no
longer owns Chrome, no longer needs a remote-debugging port, and no
longer fights chromedriver.

### Sites currently covered

14 (see `tests/e2e_probe/sites/_inventory.md` for the live table):
ChatGPT, Claude, Gemini, Perplexity, Google AI Studio, Microsoft
Copilot, DeepSeek, Kimi, Grok, Manus, Mistral, HuggingChat, Poe,
Zhipu / Z.AI.

The browser extension's content scripts cover a few additional hosts
(Notion, Figma, Gmail, M365 Copilot Cloud, GitHub Copilot Chat in
the IDE) which are not chat-style and are not currently in the
matrix. They are tracked in `_inventory.md` as TODO.

### Cases currently covered

* **T00** (`t00_smoke`): adapter health. Open the entry URL, confirm
  the chat input is reachable, confirm `capture.pipeline_state`
  reports `server_online`. No prompt sent. Always runs first per
  site so a borked adapter fails fast.
* **T01** (`t01_basic_chat`): unique-token round trip through the
  capture pipeline. Type `PROBE-<SITE>-T01-<rand>` in a prompt;
  observe `capture.wait_for_token` resolve; assert the token is in
  the captured `content_text` reachable via PCE Core's
  `/api/v1/sessions/{id}/messages`.

T02-T20 (image upload, regenerate, branch flip, file attachments,
custom GPT URL patterns, etc.) are not yet ported. The legacy
Selenium variants live in `tests/e2e/test_*.py` and translate to
the probe path with mostly mechanical changes.

### Result reporting

Every session writes
`tests/e2e_probe/reports/<UTC-timestamp>/summary.json` with a
machine-readable view of every cell:

```json
{
  "started_at_utc": "20260429T024412",
  "duration_s": 187.4,
  "n_results": 28,
  "by_status": {"pass": 18, "skip": 8, "fail": 2},
  "by_site":   {"chatgpt": 2, "claude": 2, …},
  "by_case":   {"T00": 14, "T01": 14},
  "results":   [{"case_id":"T01","site_name":"chatgpt","status":"pass",
                 "summary":"…","duration_ms":12431,"details":{…}}, …]
}
```

The same triage script that aggregates the legacy Selenium reports
(`tests/e2e/reports/chatgpt/<ts>/summary.json`) can read these — the
schema is intentionally the same.

### How to extend

* New site: subclass `BaseProbeSiteAdapter`, append to `ALL_SITES`.
  See §5.5 of `Docs/testing/PCE-PROBE-USAGE.md` for the recipe.
* New case: subclass `BaseCase`, append to `ALL_CASES`. The matrix
  expands automatically by `len(ALL_SITES)` cells.

A regression test for matrix integrity (every site adapter loads,
every case has the required `id`/`name` fields) is implicit in
`pytest --collect-only`: any breakage prints an `ImportError` /
`KeyError` instead of enumerating cells.
