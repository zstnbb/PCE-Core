# Human-like E2E SOP

**Status**: Active operational guide
**Last update**: 2026-04-29

Runbook for executing PCE's E2E live-site coverage without tripping
Cloudflare Turnstile / xAI Super-Bot-Fight / Anthropic Auth0 / OpenAI
bot scoring.

> If you are validating PCE in CI / sandbox / no live sites, the
> older `_open_login_tabs.py` + managed-profile path still works and
> this doc does not apply. Use this doc when you specifically need to
> drive the test runner against the real ChatGPT / Grok / Claude /
> Perplexity, and your VPN exit IP has any history at all.

---

## TL;DR

```powershell
# 0. Quit ALL Chrome windows (Ctrl+Shift+Q saves the session).

# 1. Print the per-environment Chrome launch command:
python -m tests.e2e._attach_to_daily_chrome --print-launch

# 2. Switch VPN to a fresh, clean exit (US recommended).

# 3. Paste the printed Start-Process line in a NEW PowerShell. Chrome
#    relaunches with --remote-debugging-port=9222 + PCE extension.

# 4. Bulk-open AI tabs with humanlike pacing + warmup:
python -m tests.e2e._attach_to_daily_chrome --warmup --open-tabs

# 5. Manually log into every tab.

# 6. In another PowerShell, point pytest at the same Chrome:
$env:PCE_CHROME_DEBUG_ADDRESS = "127.0.0.1:9222"
pytest tests/e2e/<your_test>.py -v
```

---

## Bot signals neutralised

The legacy E2E pattern (managed profile + cookie clone) emits eight
distinct bot signals that compound on a flagged VPN IP:

| # | Signal | Mitigation in this SOP |
|---|---|---|
| 1 | Cold browser process (uptime ~seconds) | Attach to daily Chrome (long-running) |
| 2 | No `document.referrer` on first AI request | `--warmup` visits Wikipedia / GitHub / HN first |
| 3 | No prior browsing history vector | Daily Chrome already has months of history |
| 4 | `navigator.webdriver=true` legacy | `--disable-blink-features=AutomationControlled` + `_stealth.py` |
| 5 | WebGL spoof divergence vs TLS-JA3 | Daily Chrome's REAL WebGL already earned cf_clearance; consider `PCE_E2E_STEALTH=0` |
| 6 | Empty `mousemove` event stream | `MouseJiggler` daemon emits 8-22s ambient mousemoves session-wide |
| 7 | Millisecond first-input gap | `humanizer.read_pause(2.5, 7.0)` between nav and interaction |
| 8 | Repeated identical session hash | Every run reuses the same long-lived browser; no fresh hash |

Plus affirmative protections:

- **Risk-sorted tab opening** (`SITES_BY_RISK` in `_attach_to_daily_chrome.py`):
  Gmail / Notion / Kimi / Zhipu first; Claude / Perplexity / Grok
  last.
- **Progressive pacing** (`pace_between_sites`): 8-22s on early tabs,
  26-72s on late tabs.
- **CDP-injected clicks** (`human_click`): `Input.dispatchMouseEvent`
  produces `isTrusted=true` events matching real OS input.
- **Log-normal typing cadence** (`human_type`): per-char delays from
  log-normal distribution + occasional typo+backspace cycles.

---

## Pre-flight checklist (one-time per VPN switch)

1. **Pick a clean VPN exit.**
   - From your daily Chrome, visit `https://www.cloudflare.com/cdn-cgi/trace`
     and copy the `ip=` value.
   - Lookup on `https://scamalytics.com/ip/<ip>`.
   - **Acceptable**: Fraud Score < 50, no "Recent Abuse: Yes".
   - **Reject**: Score > 75, or Proxy:Yes + Recent Abuse:Yes.
   - Prefer **US** exits.

2. **Quit Chrome completely** (`Ctrl+Shift+Q`). Confirm with
   `(Get-Process chrome -ErrorAction SilentlyContinue).Count` returns 0.

3. **Print + paste the launch command:**
   ```powershell
   python -m tests.e2e._attach_to_daily_chrome --print-launch
   ```

4. **Verify the extension loaded** in `chrome://extensions`. Yellow
   "developer mode" tag is normal.

5. **Open AI tabs with humanlike pacing**:
   ```powershell
   python -m tests.e2e._attach_to_daily_chrome --warmup --open-tabs
   ```

6. **Manually log into every tab.** CF may still challenge the
   high-risk three; it should now be a Turnstile checkbox, not a
   permanent spinner.

7. **Wait 24-72h before heavy E2E.** CF reputation has memory. Even
   after switching to a clean IP and human-like driving, the
   account-IP-fingerprint cluster needs time to accumulate "looks
   normal" signal before high-volume runs.

---

## Daily run flow

Once daily Chrome is up + logged in:

```powershell
$env:PCE_CHROME_DEBUG_ADDRESS = "127.0.0.1:9222"
pytest tests/e2e/test_<site>.py -v
```

`conftest` detects the env var, takes the attach branch, does NOT
spawn a new Chrome. The autouse `mouse_jiggler` fixture starts on
first `driver` acquisition and runs for the entire pytest session;
tests that drive interactions pull the `humanizer` fixture:

```python
def test_send_message(driver, humanizer):
    humanizer.read_pause(3.0, 6.0)
    humanizer.human_click(driver, send_btn)
    humanizer.human_type(driver, prompt_box, "hello world")
    humanizer.gentle_scroll(driver, 400)
```

---

## Knobs

| Env var | Default | Effect |
|---|---|---|
| `PCE_CHROME_DEBUG_ADDRESS` | unset | Attach to running Chrome instead of spawning |
| `PCE_E2E_HUMANIZE` | `1` | `0` disables MouseJiggler + log-normal delays + bezier paths |
| `PCE_E2E_STEALTH` | `1` | `0` disables `_stealth.py` JS injection (try this if cf_clearance keeps invalidating on daily Chrome) |
| `PCE_CHROME_PROFILE_MODE` | `managed` | Set to `clone` for the legacy isolated-profile path |

---

## Recovery: when CF still flags after switching

In order of escalating cost:

1. **Wait 24-72h.** CF risk score decays. Solves 80% of cases.
2. **Don't spam runs.** Don't run the same test 5x in 30 min.
   Shuffle test order, take coffee breaks.
3. **Manually click through Turnstile.** Future requests on that
   domain inherit the fresh `cf_clearance`.
4. **`PCE_E2E_STEALTH=0`** on daily Chrome attach mode. The
   `_stealth.py` payload was designed for headless Chrome; on a real
   headed Chrome it over-spoofs (forces WebGL=Intel UHD,
   hardwareConcurrency=8, ...) and creates a fingerprint divergence
   from the natural one that originally earned `cf_clearance`.
5. **Switch VPN node again** if the fresh node started accumulating
   bot reputation from your testing.
6. **Buy a real ISP-residential static IP** ($40-80/mo: Bright Data
   ISP / Rayobyte ISP / IPRoyal ISP). Chain through your existing
   GFW-bypass tunnel via Clash relay rule.
7. **Use `_warmup_login_via_proxy.py`** (already in the repo) for a
   one-shot residential-proxy login that earns `cf_clearance` against
   a clean IP and persists cookies into a dest profile.

---

## What NOT to do

- **Don't run pytest 10x in a row** when CF is already challenging.
  Each fresh Selenium session generates a new identical fingerprint
  hash; CF clusters them as one bot doing many runs.
- **Don't close + reopen Chrome between test runs.** Resets process
  uptime to seconds; signal #1 fires every time.
- **Don't set `--window-size`** on attach mode -- inherit the user's
  natural viewport.
- **Don't run E2E from a coffee shop / mobile data** where the
  ASN reputation is unknown -- residential ISP only.
- **Don't fight Cloudflare with bigger stealth payloads.** If
  level-1 stealth (`_stealth.py`) keeps failing, the problem is
  IP / behaviour / session-age, NOT JS-level fingerprint.

---

## Module references

| Module | Role |
|---|---|
| `tests/e2e/_attach_to_daily_chrome.py` | Attach helper: poll 9222, Selenium attach, warmup, paced bulk-open AI tabs |
| `tests/e2e/_humanizer.py` | Primitives: `read_pause`, `pace_between_sites`, `human_click`, `human_type`, `gentle_scroll`, `MouseJiggler`, `warmup_browse` |
| `tests/e2e/_stealth.py` | JS-level fingerprint patches (webdriver, cdc, plugins, WebGL, ...) |
| `tests/e2e/conftest.py` | Selenium driver fixture (auto-detects attach mode), autouse `mouse_jiggler` fixture, `humanizer` fixture |
| `tests/e2e/_open_login_tabs.py` | Legacy managed-profile launcher (kept for non-live-site contexts) |
| `tests/e2e/_clone_login_state.py` | Legacy cookie-clone helper (kept for fallback / advanced use) |
