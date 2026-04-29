# PCE Probe Stability Loop — Agent Runbook

This is the single entry point for any agent (human or LLM) that needs
to run the probe-driven E2E matrix, read the result, decide what
broke, and ship a fix. If you're triaging an `e2e_probe` failure or a
"capture pipeline regressed" report, **start here**.

If you want to understand *what* the matrix is or *why* it exists,
read `@f:\INVENTION\You.Inc\PCE Core\Docs\testing\PCE-PROBE-USAGE.md`
and `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\PCE-PROBE-API.md`
first. This doc assumes that context and focuses on the **loop**.

---

## 0. One-time setup

Run these once per machine. The fix loop in §1 assumes all four are
already true.

1. **Build + sideload the extension**

   ```powershell
   cd "f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt"
   pnpm wxt build
   ```

   Then in `chrome://extensions`, enable Developer mode → Load
   unpacked → point at `pce_browser_extension_wxt\.output\chrome-mv3`.
   When you re-build, click **Reload** on that extension card.

2. **Log into the AI sites you want covered**

   In the same Chrome profile, sign in to ChatGPT, Claude, Gemini, etc.
   The matrix uses your live cookies; it does NOT log you in
   automatically. Sites you skip will surface as `SKIP` cells with
   reason `not logged in / input not reachable`.

3. **Start PCE Core**

   ```powershell
   python -m pce_core.server
   ```

   The matrix uses `http://127.0.0.1:9800` for round-trip verification.
   The `pce_core` fixture in `tests/e2e_probe/conftest.py` soft-skips
   the entire suite if the health endpoint is unreachable, so you'll
   know if you forgot.

4. **Confirm the probe path is alive**

   ```powershell
   pytest tests/test_pce_probe.py -q
   ```

   24/24 GREEN means the in-process probe stack is fine. If this fails,
   STOP — the matrix can't run; fix `pce_probe/` first.

---

## 1. The fix loop (5 steps)

The loop is `triage → decide → edit → replay → commit`. Each iteration
should converge to one fewer failing cell. Don't fight more than one
adapter at a time — failures across adapters often share a root cause
(extension reloaded, PCE Core restarted, etc.) and looking at one in
isolation is faster.

### Step 1 — Run

Pick one of three modes:

```powershell
# A) Smoke first: every site, no prompts (cheapest signal).
pytest tests/e2e_probe/test_matrix.py -k "T00" -v

# B) End-to-end matrix: every (site × case).
pytest tests/e2e_probe/test_matrix.py -v

# C) Just one cell.
pytest tests/e2e_probe/test_matrix.py -k "chatgpt-T01" -v -s
```

The session prints a one-line summary at end:

```
MATRIX SUMMARY: 28 cells, by_status={'pass': 18, 'skip': 8, 'fail': 2},
summary=tests/e2e_probe/reports/<UTC>/summary.json
```

### Step 2 — Triage (the agent's "perception")

```powershell
python -m pce_probe.triage
```

That picks up the latest summary, prints one block per FAIL cell:

```
[FAIL] chatgpt-T01   phase=send_prompt   code=selector_not_found   (4200ms)
────────────────────────────────────────────────────────────────────────
  summary:     send_prompt: selector_not_found (no element matched
               #prompt-textarea); agent_hint='did the page change?'
  agent_hint:  did the page change?
  edit:        F:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\chatgpt.py
  replay:      pytest tests/e2e_probe/test_matrix.py -k "chatgpt-T01" -v -s
  evidence:
    token: PROBE-CHATGPT-T01-a3f9b2
    tab_id: 12345
    dom_excerpt: <div class='wrap'><textarea id='new-prompt-textarea' …
```

Useful flags:

- `--include-skip` — also surface SKIP cells (login walls, unconfigured
  selectors). Default is FAIL only.
- `--json` — machine-readable output for an outer agent to ingest.
- `<path/to/summary.json>` — triage a specific run instead of the latest.

The triage exit code is **1** if any FAIL exists, **0** otherwise.
Useful in CI: `python -m pce_probe.triage && echo all-green`.

### Step 3 — Decide (root cause)

Map the **error code** (or `phase` for code-less failures) to one of
the patterns in §2 below. The `evidence` block is what you read to
choose which fix applies.

### Step 4 — Edit

Almost every selector / login / send-button fix lives in the **single
file** `tests/e2e_probe/sites/<site>.py`. The triage view prints that
path verbatim under `edit:` — copy-paste it. Only the four contracts
listed in
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py:53-93`
are public; everything else in that base class is internal.

If the failure is in the case logic itself (`cases/t01_basic_chat.py`
asserts the wrong thing), edit there. But that's rare — cases are
site-agnostic and a real case bug usually fails on **every** site, not
one.

### Step 5 — Replay

Use the exact `replay:` line from triage. It runs ONE cell and skips
the rest, so iteration is sub-30s on a fast site:

```powershell
pytest tests/e2e_probe/test_matrix.py -k "chatgpt-T01" -v -s
```

When it goes GREEN, do a full sweep to confirm you didn't break
anything else:

```powershell
pytest tests/e2e_probe/test_matrix.py -v
```

### Step 6 — Commit

```powershell
git add tests/e2e_probe/sites/chatgpt.py tests/e2e_probe/sites/_inventory.md
git commit -m "fix(e2e_probe): refresh chatgpt selectors after DOM rev"
```

Update the `_inventory.md` row for that site (`T00 verified <date>`,
`T01 verified <date>`) so future agents know which adapters are
known-good as of when.

---

## 2. Failure taxonomy → fix patterns

The probe RPC error codes are stable. Each one maps to one or two
likely fixes. Read the triage `evidence` block before applying — the
same code can have different root causes (e.g. `selector_not_found`
might mean a real DOM change OR a login wall the adapter didn't catch).

### `selector_not_found`

The selector tried by `dom.query` / `dom.click` / `dom.type` matched
zero elements. **Most common failure** because AI sites churn DOM
structure every few weeks.

**Fix**:
1. Read `evidence.dom_excerpt` — that's the live HTML at failure time.
2. Find the new selector for the input box / send button / etc.
3. Update the matching tuple in `sites/<site>.py`:
   - `input_selectors` for the prompt input
   - `send_button_selectors` for the submit button (empty tuple = press
     Enter instead)
   - `stop_button_selectors` for "still streaming" detection
   - `login_wall_selectors` for "user is not logged in"
4. Re-run that one cell.

**Tip**: the selector tuples are tried **in order**, first match wins.
Keep the new selector you just confirmed at the front; keep the old
one as a fallback in case ChatGPT A/B-tests two layouts.

### `capture_not_seen`

The probe typed the prompt fine, but no PCE_CAPTURE event matching the
unique token arrived within `response_timeout_ms` (default 90s).

**Possible root causes**, in order of likelihood:

1. **Content script didn't inject** — happens if the extension was
   updated but Chrome didn't reload it. Click "Reload" on the
   extension in `chrome://extensions`, re-run.
2. **Wrong site got the content script** — check `manifest.json`'s
   `content_scripts.matches` actually covers the URL pattern this site
   uses. Common gotcha: ChatGPT moved a feature to `chatgpt.com` from
   `chat.openai.com` but the manifest still lists only the old host.
3. **Token got truncated** — some sites strip very long prompts; check
   `evidence.recent_events` for events that match the host but DON'T
   contain the token. If you see them, the prompt arrived but the
   token was eaten upstream of capture. Shorten `PROMPT_TEMPLATE` in
   `cases/t01_basic_chat.py` or use a shorter token.
4. **`provider` filter mismatch** — `T01` passes the adapter's
   `provider` to `wait_for_token` so events from other tabs don't
   resolve the wrong waiter. If the content script normalizes the
   provider differently than the adapter expects (e.g. `"chatgpt"` vs
   `"openai"`), the waiter never matches even though events arrive.
   Compare `evidence.recent_events[*].provider` against
   `adapter.provider`.
5. **Capture pipeline disabled** — `capture.pipeline_state` should
   show `enabled: true, server_online: true`. T00 already covers this;
   if T00 passes but T01 hits this, look at the content script.

### `tab_not_found`

The probe tried `chrome.tabs.get(<id>)` and Chrome said no.

**Fix**: an earlier step closed the tab unexpectedly (the user, or a
race in the adapter). Re-run; it usually clears. If it persists for
one specific site, the adapter's `open()` is returning a stale tab id
from a redirect chain — override `open()` in that adapter and use
`probe.tab.find_by_url` to get the post-redirect tab.

### `login_wall_detected` (surfaced as SKIP, not FAIL)

The adapter's `login_wall_selectors` matched something on the page.
The matrix correctly skips, because there's nothing the agent can do
without your password.

**Fix**: log in manually in your daily Chrome, re-run.

If the adapter mis-detects (selector matches a non-login element):
update `login_wall_selectors` in the site adapter to be more specific,
or remove it (the adapter falls back to the
"input not appearing" path).

### `params_invalid`

The Python side sent a malformed verb request. **Almost always a bug
in the adapter or case**, not the site.

**Fix**: read the `message` field; the probe is explicit about which
field was wrong.

### `timeout`

A verb didn't return within `timeout_ms` (default 30s per request,
`response_timeout_ms` for `wait_for_token`). Usually means the page
hung mid-stream.

**Fix**: bump `response_timeout_ms` on the slow adapter, OR investigate
why the site is slow on your network. Some Cloudflare-fronted sites
(Claude, Perplexity) are slow from CN IPs; that's a network issue,
not an adapter issue.

### Case-specific failure phases (T01)

The `phase` field in `details` is set by `cases/t01_basic_chat.py` to
mark which step inside the case failed. The triage view prints it as
`phase=...`. Each phase implies a different fix scope:

| `phase` | Where the failure happened | Where to look first |
|---|---|---|
| `open` | `adapter.open(probe)` | Adapter's `url` or `open()` override |
| `login` | `check_logged_in` returned False | Manual login OR `login_wall_selectors` |
| `send_prompt` | `adapter.send_prompt` | `input_selectors` / `send_button_selectors` |
| `wait_for_token` | `probe.capture.wait_for_token` | Content script / manifest matches / provider key |
| `session_hint_missing` | Capture arrived but `session_hint=null` | `service_worker.js` URL→hint normalization for this provider |
| `sessions_request` / `sessions_status` | PCE Core HTTP API barked | PCE Core process; check `python -m pce_core.server` console |
| `session_match` | Token captured but `/api/v1/sessions` shows no row matching `session_hint` | Ingest path / dedup / pipeline normalization |
| `messages_status` | `/api/v1/sessions/<id>/messages` returned non-200 | PCE Core API surface |
| `token_in_messages` | Session has messages but none contain the token | Capture-to-DB content_text normalization (rich content stripping?) |

The further down this list a failure is, the deeper into the PCE
stack the bug lives. `session_match` and `token_in_messages` are
**not adapter problems** — they're PCE Core / extension content
script bugs, fix them in `pce_core/` or
`pce_browser_extension_wxt/entrypoints/<site>.content.ts`.

---

## 3. Common pitfalls

### "I rebuilt the extension but the failure didn't change"

You forgot to click **Reload** on the extension in `chrome://extensions`.
Building writes the new files; Chrome only re-reads them on reload.

### "T00 passes but T01 fails for every site"

Almost always means the capture pipeline isn't observing your tabs
even though `chrome.tabs.create` works. Top suspects, in order:

1. PCE Core isn't running on `127.0.0.1:9800` (you'll actually have
   seen this as a soft-skip earlier).
2. Service worker died after the last reload — open the extension's
   service worker console (`chrome://extensions` → "service worker")
   and look for errors. The `captureVerbs` TDZ regression (now fixed
   in commit `3d600ff`) used to do exactly this.
3. The extension is the **webstore** build, not the **sideload** build,
   so the probe is gated off and `capture.pipeline_state` returns
   `pipeline_state_provider_not_wired`. Build with the right command
   (`pnpm wxt build`, not `pnpm wxt build --mode webstore`).

### "I'm rerunning a single cell but the matrix summary keeps showing yesterday's failures"

Each pytest session writes its own
`tests/e2e_probe/reports/<UTC>/summary.json`, but `triage` picks the
**lex-newest** by directory name. After an interactive rerun, that's
the run you just did. If you've been running with weird PowerShell
redirects that may not produce a new report dir; explicitly pass the
path you want: `python -m pce_probe.triage tests/e2e_probe/reports/<ts>/summary.json`.

### "The dom_excerpt looks like a Cloudflare challenge"

You have a network-layer problem, not an adapter problem. Switch your
VPN node (avoid IDC ASNs), or run from a different IP. This bites
Claude and Perplexity hardest from CN networks.
See the existing notes in
`@f:\INVENTION\You.Inc\PCE Core\Docs\stability\` for the wider story.

### "I added a new site adapter and `--collect-only` doesn't show it"

You forgot to append it to `ALL_SITES` in
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\__init__.py:40-55`.
The matrix only sees adapters in that list, even if the file exists.

The same applies to new cases: append to `ALL_CASES` in
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\__init__.py`.

A conformance test (`tests/test_pce_probe_adapter_contract.py`)
catches "adapter scaffolded but `input_selectors` left empty" at
collect time — if you forgot to fill in the contract, that test fails
before the matrix even runs.

---

## 4. References

- Architecture & verb contract: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\PCE-PROBE-API.md`
- End-user usage of the probe: `@f:\INVENTION\You.Inc\PCE Core\Docs\testing\PCE-PROBE-USAGE.md`
- Site adapter base class: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py`
- Case base class: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\base.py`
- T01 reference case (the most-used): `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\t01_basic_chat.py`
- Adapter inventory & status: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\_inventory.md`
- Triage CLI: `@f:\INVENTION\You.Inc\PCE Core\pce_probe\triage.py`
- Triage tests (the runnable spec): `@f:\INVENTION\You.Inc\PCE Core\tests\test_pce_probe_triage.py`
- Conformance tests: `@f:\INVENTION\You.Inc\PCE Core\tests\test_pce_probe_adapter_contract.py`
