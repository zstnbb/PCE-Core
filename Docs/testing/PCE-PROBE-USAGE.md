# PCE Probe — Agent How-To

**Audience**: agents (Cascade, Claude Desktop, GPT-4 Operator, custom
runners) and humans driving PCE Core's E2E manually.

**TL;DR**:

```python
from pce_probe import connect

with connect() as probe:
    probe.tab.open("https://chatgpt.com/")
    probe.dom.wait_for_selector(tab_id, "#prompt-textarea")
    probe.dom.type(tab_id, "#prompt-textarea", "hello", submit=True)
    ev = probe.capture.wait_for_token("hello", timeout_ms=60_000)
    # then verify via PCE Core /api/v1/sessions
```

---

## 1. What problem this solves

Driving the AI sites that PCE captures from used to require Selenium +
chromedriver + remote debugging port + a small mountain of stealth
patches and human-behavior simulation just to make Cloudflare and
Anthropic / Microsoft / Google / etc. fingerprinters happy. That stack
is fragile on Windows in particular (profile lock interactions,
crash-marker recovery, TIME\_WAIT on the debug port). When it broke
nothing else worked.

PCE Probe replaces that brittle external driver with a **debug API
that lives inside the PCE browser extension itself**. Chrome stays
running with whatever profile and cookies the human user already has;
the extension exposes a small set of agent-facing verbs over a
loopback WebSocket. Agents drive AI sites by calling those verbs and
read failure context (DOM excerpt, capture log, screenshot) directly
from the wire response.

The agent never sees Chrome, Selenium, ports, profiles, locks, or
fingerprints. Those problems are owned by the tool.

## 2. Topology

```
agent (you)        ->  ProbeClient  (Python, sync or async)
                          │
                          ▼
                       ProbeServer  (asyncio, binds 127.0.0.1:9888)
                          ▲
                          │  ws (loopback only)
                          │
                       PCE extension probe-rpc client
                          │
                          ▼
                       Chrome tabs / DOM / capture pipeline
```

The Probe **does not own Chrome's lifecycle**. You are expected to
launch Chrome once, with the PCE extension installed, on whatever
profile makes sense. The probe-rpc client inside the extension polls
``ws://127.0.0.1:9888`` with exponential backoff: when a server is
listening it attaches; when not it sleeps. Tests / agents own the
server.

## 3. Pre-flight

You need (1) a Chrome window open with the PCE extension installed,
and (2) the PCE Core ingest server running (so capture round-trips
land somewhere queryable).

```powershell
# 1. PCE Core (in one terminal — leave running)
python -m pce_core.server

# 2. Chrome with the unpacked PCE extension. Any profile.
#    The extension auto-connects to ws://127.0.0.1:9888 once your
#    test/script binds the port. No flags required.
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' `
    --user-data-dir="$env:LOCALAPPDATA\Google\Chrome\User Data" `
    --profile-directory="Profile 1" `
    --load-extension="F:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\.output\chrome-mv3"
```

To verify the probe-rpc client is enabled inside the extension, open
``chrome://extensions``, enable Developer Mode, click the PCE service
worker's "Inspect" link, and confirm:

```
[PCE Probe] connected to ws://127.0.0.1:9888 (22 verbs registered)
```

If you see ``[PCE Probe] connected``, you're done with setup.

## 4. The verb catalog at a glance

Full schema in
`@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\PCE-PROBE-API.md`. Quick reference:

- **`system.*`** — `ping`, `version`. Useful for health checks and
  feature-detecting which verbs the attached extension supports.
- **`tab.*`** — `list`, `open`, `activate`, `close`, `navigate`,
  `wait_for_load`, `find_by_url`. Standard Chrome tab lifecycle.
- **`dom.*`** — `query`, `wait_for_selector`, `click`, `type`,
  `press_key`, `scroll_to`, `execute_js`. All run via
  `chrome.scripting.executeScript` in `world: "ISOLATED"`. `dom.type`
  is React-aware (uses the native value setter trick for controlled
  inputs and `document.execCommand("insertText")` for ProseMirror /
  contenteditable).
- **`page.*`** — `dump_state`, `screenshot`, `network_log`. Use
  `dump_state` first when something fails — its DOM excerpt is what
  tells you whether the selector you used is still right.
- **`capture.*`** — `wait_for_token`, `recent_events`,
  `pipeline_state`. The closing piece of the loop: type a unique
  token into the prompt, then `wait_for_token(token)` resolves the
  moment PCE's capture pipeline observes that exact substring in any
  outgoing capture body.

## 5. Common patterns

### 5.1 Drive ChatGPT, verify capture

```python
import time, httpx
from pce_probe import connect

with connect() as probe:
    tab = probe.tab.open("https://chatgpt.com/", active=True)
    tab_id = tab["tab_id"]
    probe.tab.wait_for_load(tab_id, timeout_ms=20_000)

    probe.dom.wait_for_selector(tab_id, "#prompt-textarea", timeout_ms=15_000)

    token = f"PROBE-{int(time.time())}"
    probe.dom.type(
        tab_id,
        "#prompt-textarea",
        f"Reply with the literal token {token}.",
        submit=True,
    )

    ev = probe.capture.wait_for_token(token, timeout_ms=60_000, provider="openai")

    pce = httpx.Client(base_url="http://127.0.0.1:9800", trust_env=False)
    sessions = pce.get(
        "/api/v1/sessions", params={"provider": "openai"}
    ).json()
    sess = next(s for s in sessions if ev["session_hint"] in s["session_key"])
    msgs = pce.get(f"/api/v1/sessions/{sess['id']}/messages").json()
    assert any(token in (m.get("content_text") or "") for m in msgs)
```

### 5.2 Diagnose a selector that broke

When a verb fails with `selector_not_found`, the exception's
`context["dom_excerpt"]` is the page's actual outerHTML at the moment
of failure. Read it before changing anything else.

```python
from pce_probe import SelectorNotFoundError

try:
    probe.dom.click(tab_id, "button.send-button")
except SelectorNotFoundError as e:
    print(e.context["dom_excerpt"][:2000])  # 2 KB of real DOM
    print(e.agent_hint)  # e.g. "selector likely changed; check chatgpt.content.ts"
```

This single error response replaces what used to be a Selenium
session, a hand-extracted screenshot, and a chrome devtools dive.

### 5.3 Take a screenshot before / after

```python
before = probe.page.screenshot(tab_id)["image_b64"]
probe.dom.click(tab_id, ".regenerate-button")
after = probe.page.screenshot(tab_id)["image_b64"]
# decode + diff for a regression visual; both b64 PNGs.
```

### 5.4 Pytest fixture

`pce_probe.pytest_plugin` is auto-registered when the package is
importable. To use it from any test file:

```python
# tests/conftest.py
pytest_plugins = ["pce_probe.pytest_plugin"]
```

Then:

```python
def test_chatgpt_send(pce_probe_fresh):
    tab = pce_probe_fresh.tab.open("https://chatgpt.com/")
    # ... pce_probe_fresh closes any new tabs on test exit
```

CLI knobs:

- `--pce-probe-host=127.0.0.1` — bind host (loopback only).
- `--pce-probe-port=9888` — bind port; use `0` for an ephemeral port
  in CI.
- `--pce-probe-attach-timeout=30` — seconds to wait for the extension
  to attach. Bump this for slow profiles.
- `--pce-probe-skip` — skip every `pce_probe`-fixtured test cleanly.
  Use in CI runs where no Chrome is available.

A live example is at
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\test_chatgpt_probe.py`.

### 5.5 Run the full-coverage matrix

`tests/e2e_probe/test_matrix.py` is the canonical entry point for
"run T01 across every site I have an adapter for". It generates one
pytest item per `(site, case)` pair from the registered list in
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\__init__.py:42-58` and
`@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\__init__.py:25-30`.

Quick reference:

```powershell
# Enumerate the matrix without executing (sanity check)
pytest tests/e2e_probe/ --collect-only

# Run everything against a logged-in attached browser
pytest tests/e2e_probe/test_matrix.py -v

# Run only chatgpt cells
pytest tests/e2e_probe/test_matrix.py -k "chatgpt" -v

# Run only T01 across all sites (= the basic-chat regression matrix)
pytest tests/e2e_probe/test_matrix.py -k "T01" -v

# Run a single cell
pytest tests/e2e_probe/test_matrix.py -k "claude-T01" -v

# CI-friendly run when no Chrome is attached: every cell SKIPs cleanly
pytest tests/e2e_probe/ --pce-probe-skip
```

Each session writes a JSON summary to
`tests/e2e_probe/reports/<UTC-timestamp>/summary.json`. The shape:

```json
{
  "started_at_utc": "20260429T024412",
  "duration_s": 187.4,
  "n_results": 28,
  "by_status": {"pass": 18, "skip": 8, "fail": 2},
  "by_site":   {"chatgpt": 2, "claude": 2, ...},
  "by_case":   {"T00": 14, "T01": 14},
  "results": [
    {
      "case_id": "T01",
      "site_name": "chatgpt",
      "status": "pass",
      "summary": "token PROBE-CHATGPT-T01-... round-tripped...",
      "duration_ms": 12431,
      "details": {"session_id": "...", "n_messages": 4}
    },
    ...
  ]
}
```

The `details` per row is whatever the case implementation chose to
attach. T00 attaches `pipeline` (probe's `capture.pipeline_state`).
T01 attaches `session_id`, `n_messages`, and on failure the recent
capture events surfaced by the probe — so a triager can read the
JSON and see exactly where in the pipeline the round-trip broke.

#### Adding a new site

1. Drop `tests/e2e_probe/sites/<name>.py` subclassing
   `BaseProbeSiteAdapter`. At minimum set `name`, `provider`, `url`,
   `input_selectors`. Most adapters are 30-50 lines.
2. Append the class to `ALL_SITES` in
   `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\__init__.py:42-58`.
3. Add a row to
   `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\_inventory.md`.
4. Run `pytest tests/e2e_probe/ -k "<name>-T00"` — T00 smoke validates
   the adapter without sending any prompt.

#### Adding a new case

1. Drop `tests/e2e_probe/cases/tNN_xxx.py` subclassing `BaseCase`.
   Implement `run(probe, pce_core, adapter) -> CaseResult`.
2. Append the class to `ALL_CASES` in
   `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\__init__.py:25-30`.
3. The matrix automatically expands to include the new case across
   every existing site — no other plumbing needed.

### 5.6 Triage a failing run

Once the matrix has produced a `summary.json`, the triage CLI turns it
into a fix-ready view per failing cell — what broke, where the
relevant adapter file lives, and the exact replay command:

```powershell
# Auto-locates the latest tests/e2e_probe/reports/<UTC>/summary.json
python -m pce_probe.triage

# Also surface SKIP cells (login walls, etc.)
python -m pce_probe.triage --include-skip

# Machine-readable JSON for an outer agent to ingest
python -m pce_probe.triage --json

# Triage a specific run instead of the latest
python -m pce_probe.triage tests/e2e_probe/reports/<UTC>/summary.json
```

Sample output (truncated):

```
[FAIL] chatgpt-T01   phase=send_prompt   code=selector_not_found   (4200ms)
────────────────────────────────────────────────────────────────────────
  summary:     send_prompt: selector_not_found (no element matched
               #prompt-textarea); agent_hint='did the page change?'
  agent_hint:  did the page change?
  edit:        F:\...\tests\e2e_probe\sites\chatgpt.py
  replay:      pytest tests/e2e_probe/test_matrix.py -k "chatgpt-T01" -v -s
  evidence:
    token: PROBE-CHATGPT-T01-a3f9b2
    tab_id: 12345
    dom_excerpt: <div class='wrap'><textarea id='new-prompt-textarea'…
```

Exit code is **1** if any cell failed, **0** otherwise — useful in CI:

```powershell
pytest tests/e2e_probe/test_matrix.py
python -m pce_probe.triage && echo "all green"
```

The full agent-facing fix loop (run → triage → decide → edit →
replay → commit), with a per-error-code fix taxonomy, lives in
`@f:\INVENTION\You.Inc\PCE Core\Docs\testing\PCE-PROBE-AGENT-LOOP.md`.

## 6. Failure model

Every wire failure carries:

- `code` — one of the taxonomy values (`selector_not_found`,
  `capture_not_seen`, `tab_not_found`, ...).
- `message` — short human-readable text.
- `context` — verb-specific extra fields. For DOM verbs this includes
  `dom_excerpt` and `url`. For `capture.wait_for_token` it includes
  `last_capture_events` (ring of the most recent ~20 events the
  observer saw).
- `agent_hint` — one-line suggestion the tool maintainers have left
  for you, updated as the extension evolves.

Every failure is also raised as a typed Python exception (subclass of
`pce_probe.ProbeError`) so you can pattern-match on the recovery
strategy:

```python
from pce_probe import (
    CaptureNotSeenError, SelectorNotFoundError, TabNotFoundError,
)

try:
    probe.capture.wait_for_token(token, timeout_ms=30_000)
except CaptureNotSeenError as e:
    # Capture pipeline never saw the token. Either the prompt didn't
    # actually go through, the content-script wasn't injected, or the
    # session_hint filter excluded it. e.context["last_capture_events"]
    # tells you which.
    ...
except SelectorNotFoundError as e:
    # Page DOM doesn't contain what we expected. e.context["dom_excerpt"]
    # is the live HTML.
    ...
```

## 7. What this is not

- Not a browser automation framework. It does not own Chrome's
  lifecycle and never will. Use Selenium or chromedriver if you want
  to spawn / kill Chrome programmatically.
- Not an autopilot. It does not decide what to test. Agents bring the
  policy.
- Not multi-tenant. The server accepts at most one extension client
  and serializes agent calls. For parallel CI you run multiple Chrome
  + multiple servers on different ports.
- Not headless-friendly out of the box. The AI sites this targets
  reject headless Chrome via fingerprinting. Run headed; use Xvfb on
  Linux servers.

## 8. Server deployment (later)

Stage 2 of the rollout (see PCE-PROBE-API.md) lifts this whole
vertical onto a dedicated Linux server with Xvfb + headed Chrome +
the PCE extension + the probe server, fronted by an HTTPS proxy with
a shared HMAC. That's not in this repo yet. The local-dev usage
above is the v1 contract; the cloud topology is the v1 contract plus
auth and process supervision.

## 9. Source of truth

- API surface + envelope: `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\PCE-PROBE-API.md`
- Wire types: `@f:\INVENTION\You.Inc\PCE Core\pce_browser_extension_wxt\utils\probe-protocol.ts`
- Python types: `@f:\INVENTION\You.Inc\PCE Core\pce_probe\types.py`
- Standalone reference test: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\test_chatgpt_probe.py`
- Full-coverage matrix: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\test_matrix.py`
- Site adapter contract: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\base.py`
- Case contract: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\cases\base.py`
- Site adapter inventory: `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\sites\_inventory.md`
- Triage CLI (matrix `summary.json` -> fix-ready view):
  `@f:\INVENTION\You.Inc\PCE Core\pce_probe\triage.py`
- Agent fix-loop runbook (run -> triage -> decide -> edit -> replay):
  `@f:\INVENTION\You.Inc\PCE Core\Docs\testing\PCE-PROBE-AGENT-LOOP.md`
- In-process integration tests (no Chrome required):
  `@f:\INVENTION\You.Inc\PCE Core\tests\test_pce_probe.py`
- Triage CLI tests:
  `@f:\INVENTION\You.Inc\PCE Core\tests\test_pce_probe_triage.py`
- Adapter / case contract conformance tests:
  `@f:\INVENTION\You.Inc\PCE Core\tests\test_pce_probe_adapter_contract.py`
