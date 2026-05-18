---
title: "D-Day B1 — CLI proxy injection (replaces transparent-proxy plan)"
status: PASS
date: 2026-05-18
session: B1 block, agent solo
predecessor: HANDOFF-D-DAY-PLAN-2026-05-18.md
canonical: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
artifacts:
  - pce_cli_wrapper/_proxy_env.py (114 LOC)
  - pce_cli_wrapper/_undici_proxy_inject.js (58 LOC)
  - pce_cli_wrapper/relay.py (+4 LOC env= wiring in two Popen call sites)
  - tests/e2e_cli/test_proxy_env.py (14 tests)
  - 94/94 e2e_cli regression GREEN
scope:
  enables_legs:
    - f6_p6_claude_code_cli__L1 (mitm-proxy auto when PCE_CLI_WRAPPER_PROXY set)
    - f6_p7_codex_cli__L1 (same — was blocked per old W4-T7-T8 deferred)
    - f6_p8_gemini_cli__L1 (same — was blocked per old W4-T8 deferred)
    - f6_p8_gemini_cli__A2 (NODE_OPTIONS=--tls-keylog injection)
---

# B1 · CLI Proxy Injection — strategy pivot from transparent proxy

## 1. TL;DR

P5.D.1 W4-T7-T8 deferred handoff (cited in REDUNDANCY-AUDIT-MATRIX
but file itself missing from repo, see §0.1) concluded **Codex CLI is
Rust and bypasses HTTPS_PROXY** + **Gemini CLI is Node 22+ undici and
bypasses HTTPS_PROXY**. The mitigation required either Rust frida
hooking or transparent proxy with WinDivert — both expensive Windows
engineering.

**Empirical recheck on the operator's machine 2026-05-18**:

```
where codex.cmd → C:\Users\ZST\AppData\Roaming\npm\codex.cmd
type codex.cmd  → "%dp0%\node.exe" "%dp0%\node_modules\@openai\codex\bin\codex.js"
```

`codex` is **Node**, not Rust. Same investigation for `gemini` (Node)
and `claude` (Anthropic's single-exec Node bundle wrapping the same
runtime). All three are Node-based.

This invalidates the W4-T7-T8 root-cause and unlocks a much simpler
fix: inject HTTPS_PROXY family + NODE_OPTIONS via the L3h shim
(`pce_cli_wrapper.relay`) before exec-ing the child. No transparent
proxy needed. No WinDivert. No Rust frida.

**Outcome**: L1 leg becomes reachable for all 3 CLIs whenever the
operator runs:

```powershell
$env:PCE_CLI_WRAPPER_PROXY = "http://127.0.0.1:8080"
$env:SSLKEYLOGFILE          = "C:\Users\ZST\.pce\sslkeys.log"
claude --print "hi"   # ← goes through mitmproxy + writes keys
codex exec "hi"       # ← same
gemini -p "hi"        # ← same (uses undici-shim for proxy)
```

## 2. What landed

### 2.1 `pce_cli_wrapper/_proxy_env.py`

Pure helper `augment_child_env(target_id, base_env)`:

| Trigger | Effect |
|---|---|
| `PCE_CLI_WRAPPER_PROXY=URL` (parent env) | Inject `HTTPS_PROXY`, `HTTP_PROXY`, `https_proxy`, `http_proxy` = URL into child (only if not already set — operator override wins) |
| `SSLKEYLOGFILE=PATH` + target is Node | Append `--tls-keylog=PATH` to `NODE_OPTIONS` (quoted if path has spaces) |
| `PCE_CLI_WRAPPER_PROXY` set + target is Node | Append `--require=<undici-shim>` to `NODE_OPTIONS` |

Properties:

- **Pure** — never mutates `base_env` or `os.environ`
- **Idempotent** — re-running on already-augmented env never duplicates
  flags
- **Opt-in only** — without `PCE_CLI_WRAPPER_PROXY` / `SSLKEYLOGFILE`
  set, env passes through unchanged
- **Operator override wins** — pre-set `HTTPS_PROXY` (e.g. corp proxy)
  is not overwritten

### 2.2 `pce_cli_wrapper/_undici_proxy_inject.js`

Node 22+ undici (used by `fetch()` and any modern HTTP client) does
not auto-honour `HTTPS_PROXY`. Even `--use-system-ca` doesn't help —
undici has its own dispatcher.

Solution: `NODE_OPTIONS=--require=<this-file>` runs at Node startup
before user code, calls `setGlobalDispatcher(new ProxyAgent(proxy))`.

Safety contract — wrapped in try/catch top-to-bottom. If undici is
missing or anything throws, the shim silently noops; the child CLI
never sees a confusing error.

Debug mode: `PCE_CLI_WRAPPER_PROXY_DEBUG=1` → prints one diagnostic
line to stderr at install time.

### 2.3 `pce_cli_wrapper/relay.py`

Two Popen call sites (`_run_with_capture` pipe-tee mode + `_run_passthrough`
TTY mode) now compute `child_env = augment_child_env(target_id, os.environ)`
and pass `env=child_env`. Previously both inherited the parent env via
`env=None` default — operator could only enable L1 by setting env vars
in the *parent shell* of the wrapper invocation. Now the relay enforces
the policy uniformly.

### 2.4 `tests/e2e_cli/test_proxy_env.py`

14 unit tests:

| Category | Count |
|---|:-:|
| Static catalogue invariants (NODE_TARGET_IDS / undici shim exists) | 2 |
| no-trigger / unchanged-env passthrough | 1 |
| HTTPS_PROXY family export | 2 (export + operator override) |
| SSLKEYLOGFILE → NODE_OPTIONS | 3 (Node-only + spaces-quoting + preserve existing) |
| undici-shim --require= | 2 (proxy-gated + idempotent) |
| Safety (target_id=None / dict purity / os.environ fallback) | 4 |

```
$ python -m pytest tests/e2e_cli/test_proxy_env.py -v
14 passed in 0.05s
```

Full e2e_cli regression: **94/94 GREEN** (80 baseline + 14 new), no
behaviour change in tests that don't set the trigger env vars.

## 3. Why we are not building "transparent proxy mode"

Original D-Day plan §B1 proposed:

1. Add `pce_proxy/run_proxy.py --mode transparent`
2. Add Clash TUN rules to route api.openai.com / api.anthropic.com /
   generativelanguage.googleapis.com to mitmproxy on port 8081
3. mitmproxy uses SO_ORIGINAL_DST or x-forwarded-for to recover
   destination

This is unnecessary now that all three CLIs are confirmed Node. The
HTTPS_PROXY env var route works for all of them once we inject it. The
transparent-proxy plan is preserved as a fallback in case some future
CLI ships an actually-Rust client that ignores both HTTPS_PROXY *and*
SSLKEYLOGFILE — see §6.

## 4. Acceptance criteria

- [x] `augment_child_env` lives at `pce_cli_wrapper/_proxy_env.py`
- [x] `NODE_TARGET_IDS == {claude-code, codex-cli, gemini-cli}` and
      this set is checked against `discovery.known_targets()` by a
      regression test
- [x] `_undici_proxy_inject.js` ships next to the module
- [x] Two relay.py Popen sites pass `env=child_env`
- [x] 14 unit tests pass
- [x] 94/94 e2e_cli regression GREEN
- [ ] Live evidence — deferred to B3 (3 CLI sweep) where we actually
      pipe a real `claude --print` / `codex exec` / `gemini -p` through
      mitmproxy with `PCE_CLI_WRAPPER_PROXY` set and confirm
      `raw_captures` rows land with source_id='proxy-default'.

## 5. Reproduction

```powershell
# 1. Start mitmproxy with PCE addon
mitmdump -s F:\INVENTION\You.Inc\PCE Core\run_proxy.py -p 8080

# 2. Set triggers (per-session is fine; install can do it once at
#    setup time too — see pce_sslkeylog setup-env)
$env:PCE_CLI_WRAPPER_PROXY = "http://127.0.0.1:8080"
$env:SSLKEYLOGFILE          = "$env:USERPROFILE\.pce\sslkeys.log"

# 3. Run any wrapped CLI; no further config required
claude --print "what is 2+2?"

# 4. Verify
sqlite3 $env:USERPROFILE\AppData\Local\PCE\pce.db `
    "SELECT source_id, host, COUNT(*) FROM raw_captures `
     WHERE created_at > strftime('%s','now') * 1000 - 60000 `
     GROUP BY source_id, host;"
# expect rows: proxy-default, api.anthropic.com, …
```

## 6. Fallback if a future CLI is actually Rust

If a future Codex / Cursor / etc. release switches back to Rust with
rustls and ignores both `HTTPS_PROXY` and `SSLKEYLOGFILE`:

1. The 14 unit tests still pass (they don't exercise child runtime)
2. Live evidence in B3 / nightly will fail to land L1 rows
3. Mitigation chain:
   - Try rustls' `SSLKEYLOGFILE` support (rustls 0.21+ honours it)
   - Try `--mode transparent` route via Clash TUN (original plan)
   - Last resort: Frida hook (ADR-018 §3.7 — Pro-only)

This handoff does not block any of those paths; it simply provides the
fast happy path for the current Node-everywhere reality.

## 7. Matrix impact

REDUNDANCY-AUDIT-MATRIX §3.6 F6 P6/P7/P8 verdict lines reference "B1
+ B3 待签字" for the L1 leg. B1 is now PASS. B3 still needs the live
mitmproxy sweep to land `raw_captures` rows. After B3:

| Scenario | Pre-B1 | Post-B1 | Post-B3 expected |
|---|:-:|:-:|:-:|
| F6 P6 Claude Code CLI | 0 | 0 (B1 alone is plumbing) | 3 (L1 + L3g + L3h) |
| F6 P7 Codex CLI | 0 | 0 | 3 (L1 + L3g + L3h) |
| F6 P8 Gemini CLI | 0 | 0 | 4 (L1 + L3g + L3h + A2) |

## 8. One-liner anchor

**Empirical recheck found all 3 CLIs are Node-installed → B1 ships
3-leg env injection via L3h shim instead of building transparent
proxy. 14 new unit tests + 94/94 e2e_cli regression GREEN. L1 leg
unblocked for all 3 CLIs pending B3 live sweep.**
