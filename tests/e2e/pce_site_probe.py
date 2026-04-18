# SPDX-License-Identifier: Apache-2.0
"""Agent-driven PCE site probe — iterate AI sites one at a time.

This is the **Phase 4d** iteration driver. It reuses the existing
adapters (``tests/e2e/sites/*.py``), capture verifier helpers, and
Chrome-launch plumbing from ``conftest.py``, but is NOT pytest-based.
The agent runs it in a single long-lived Python session so the
Selenium Chrome instance stays up across all 14 sites, avoiding the
per-test-relaunch overhead of a parametrised pytest run.

Typical agent workflow::

    # 1. Start PCE core (background):
    #    python -m pce_app --no-tray --no-browser
    # 2. List what's pending:
    python -m tests.e2e.pce_site_probe --list
    # 3. Probe one site:
    python -m tests.e2e.pce_site_probe --site chatgpt
    # 4. After fixing a regression, force re-test just that site:
    python -m tests.e2e.pce_site_probe --site chatgpt --force
    # 5. Probe every untested site in sequence (interactive):
    python -m tests.e2e.pce_site_probe --all --scenario text
    # 6. Fully unattended probe + summary:
    python -m tests.e2e.pce_site_probe --all --scenario text --yes

State is shared with ``test_three_phase.py`` via ``e2e_state.json``,
so pytest runs and probe runs stay in sync.

Exit codes:
    0 — probe(s) succeeded (or were cleanly skipped due to login wall)
    1 — one or more probes failed
    2 — unrecoverable setup error (PCE core down, Chrome unavailable, …)

Environment variables honoured (shared with ``conftest.py``):
    PCE_EXTENSION_DIR      Override the WXT build output path
    PCE_CHROME_DEBUG_ADDRESS  Attach to an existing debugger
    PCE_CHROME_PROFILE_MODE   ``managed`` (default) / ``clone`` / ``direct``
    PCE_CHROME_PROFILE        Path to user data dir (for direct/clone modes)
    PCE_CHROME_PROFILE_DIR    Profile sub-directory name
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Make absolute imports work whether invoked as
# ``python -m tests.e2e.pce_site_probe`` or ``python pce_site_probe.py``.
if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent.parent))
    __package__ = "tests.e2e"

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options as ChromeOptions  # noqa: E402
from selenium.webdriver.chrome.service import Service as ChromeService  # noqa: E402
from selenium.webdriver.edge.options import Options as EdgeOptions  # noqa: E402
from selenium.webdriver.edge.service import Service as EdgeService  # noqa: E402

# Reuse adapters, scenarios, verifier, state persistence, and chrome
# launch helpers from the existing e2e framework. The private helpers
# in conftest are deliberately imported — this driver is internal test
# tooling, not application code.
from . import conftest as _cf  # noqa: E402
from .capture_verifier import (  # noqa: E402
    pce_is_running,
    get_stats,
    reset_baseline,
    wait_for_new_captures,
    wait_for_session_with_messages,
    verify_message_quality,
    verify_rich_content,
    verify_dashboard_sessions,
)
from .test_three_phase import (  # noqa: E402
    ALL_SITES,
    SCENARIOS,
    STATE_FILE,
    ScenarioState,
    _load_state,
    _save_state,
    _state_key,
    _site_supports_scenario,
)

logger = logging.getLogger("pce.e2e.probe")


# ---------------------------------------------------------------------------
# Driver lifecycle
# ---------------------------------------------------------------------------
#
# **Chrome 137+ blocker**: stable "Google Chrome" builds from 137 onwards
# hardcode-reject ``--load-extension`` ("not allowed in Google Chrome,
# ignoring"). Even ``--disable-features=DisableLoadExtensionCommandLineSwitch``
# no longer re-enables it. Microsoft Edge (also Chromium-based) still
# honours the flag, so the probe driver defaults to Edge. Users can
# force Chrome via ``--browser chrome`` but the extension must then be
# pre-installed in the profile via ``chrome://extensions``.

_EDGE_CANDIDATES_WIN = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def _find_edge_binary() -> Optional[Path]:
    for c in _EDGE_CANDIDATES_WIN:
        if c.is_file():
            return c
    return None


def _resolve_browser(choice: str) -> str:
    """Pick a browser given the user's --browser choice.

    'auto' prefers **Chrome** if the managed Chrome profile already has
    the WXT extension installed (detected by running the probe once and
    checking for captures) OR if the user has existing logins in it.
    Otherwise falls back to Edge, which still honours ``--load-extension``.

    Rationale: Chrome 137+ hardcode-rejects ``--load-extension``
    ("not allowed in Google Chrome, ignoring."). The workaround for
    Chrome is a one-time manual ``chrome://extensions`` install via
    ``--setup-chrome-extension``. Once installed, Chrome picks up the
    extension on every subsequent launch without the flag.
    """
    if choice == "chrome":
        return "chrome"
    if choice == "edge":
        return "edge"
    # 'auto' — prefer Chrome if its managed profile has user data
    # (cookies / logins). Falling back to Edge only if Chrome profile
    # is empty, so a brand-new user gets a working default.
    chrome_profile = _managed_profile_root("chrome")
    chrome_default = chrome_profile / "Default"
    if chrome_default.is_dir() and any(chrome_default.iterdir()):
        return "chrome"
    if _find_edge_binary() is not None:
        return "edge"
    return "chrome"


def _managed_profile_root(browser: str) -> Path:
    """Per-browser managed profile dir, so Chrome + Edge state stay separate."""
    if browser == "edge":
        return Path(
            os.environ.get(
                "PCE_EDGE_MANAGED_PROFILE",
                str(Path.home() / ".pce" / "edge_profile"),
            )
        )
    return Path(
        os.environ.get(
            "PCE_CHROME_MANAGED_PROFILE",
            str(Path.home() / ".pce" / "chrome_profile"),
        )
    )


def _cmd_setup_chrome_extension() -> int:
    """One-time helper: launch Chrome against the managed profile so the
    user can install the WXT build via ``chrome://extensions`` ->
    "Load unpacked".

    Needed because Chrome 137+ rejects ``--load-extension``. Once loaded
    manually, Chrome persists the unpacked extension in the profile and
    re-enables it on every subsequent launch (as long as "Developer mode"
    stays on in the same profile).
    """
    ext_dir = _cf._get_extension_dir()
    if not Path(ext_dir).is_dir():
        print(f"ERROR: Extension directory missing: {ext_dir}", file=sys.stderr)
        print("Run `pnpm --dir pce_browser_extension_wxt build` first.", file=sys.stderr)
        return 2

    chrome_bin = _cf._get_chrome_binary()
    if not chrome_bin or not Path(chrome_bin).is_file():
        print("ERROR: Chrome binary not found.", file=sys.stderr)
        return 2

    profile_root = _managed_profile_root("chrome")
    profile_root.mkdir(parents=True, exist_ok=True)

    if not _cf._check_chrome_not_running(str(profile_root)):
        print(
            "ERROR: A Chrome instance is already using this profile. "
            "Close it first (or kill all chrome.exe processes) and retry.",
            file=sys.stderr,
        )
        return 2

    # Put the extension path on the clipboard for easy paste in the
    # "Load unpacked" folder picker. Best-effort only.
    try:
        subprocess.run(
            ["clip.exe"],
            input=ext_dir.encode("utf-8"),
            timeout=3,
            check=False,
        )
        clip_hint = " (already copied to clipboard)"
    except Exception:
        clip_hint = ""

    print()
    print("=" * 72)
    print("  CHROME EXTENSION SETUP  (one-time, Chrome 137+ workaround)")
    print("=" * 72)
    print(f"  Extension: {ext_dir}{clip_hint}")
    print(f"  Profile:   {profile_root}")
    print()
    print("  Chrome 147 silently ignores --load-extension on the command")
    print("  line, so the WXT build must be loaded through the UI once.")
    print()
    print("  Steps:")
    print("    1. Chrome opens to chrome://extensions")
    print("    2. Toggle 'Developer mode' ON (top-right)")
    print("    3. Click 'Load unpacked' (top-left)")
    print("    4. Paste the path above and click 'Select Folder'")
    print("    5. Confirm 'PCE' or similar appears in the extension list")
    print("    6. Come back here and press Enter")
    print()
    print("  Your existing logins in this profile are preserved.")
    print("=" * 72)
    print()

    args = [
        str(chrome_bin),
        f"--user-data-dir={profile_root}",
        "--no-first-run",
        "--no-default-browser-check",
        "chrome://extensions/",
    ]
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        input("  Press Enter after you've loaded the extension... ")
    except (EOFError, KeyboardInterrupt):
        print("\n  aborted.")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print()
    print("  Chrome closed. Now run:")
    print("     python -m tests.e2e.pce_site_probe --all --browser chrome")
    print()
    return 0


def _launch_driver(
    browser: str,
) -> tuple[webdriver.Remote, Optional[subprocess.Popen]]:
    """Launch Chrome or Edge for the probe session.

    Only ``managed`` profile mode is supported for Edge; the debug-attach
    path + clone path remain Chrome-specific because they were written
    against ``conftest._launch_debug_chrome`` / the real Chrome user-data
    dir. Edge always uses its own managed profile at
    ``~/.pce/edge_profile``.
    """
    ext_dir = _cf._get_extension_dir()
    if not Path(ext_dir).is_dir():
        raise RuntimeError(
            f"Extension directory not found: {ext_dir}\n"
            "Run `pnpm --dir pce_browser_extension_wxt build` first."
        )

    if browser == "edge":
        return _launch_edge(ext_dir)
    return _launch_chrome(ext_dir)


def _launch_edge(
    ext_dir: str,
) -> tuple[webdriver.Edge, Optional[subprocess.Popen]]:
    edge_bin = _find_edge_binary()
    if edge_bin is None:
        raise RuntimeError(
            "Microsoft Edge not found. Either install Edge or run with "
            "--browser chrome and pre-install the extension via "
            "chrome://extensions."
        )

    profile_root = _managed_profile_root("edge")
    profile_root.mkdir(parents=True, exist_ok=True)

    logger.info("Edge user-data-dir:  %s", profile_root)
    logger.info("Edge binary:         %s", edge_bin)
    logger.info("Extension dir:       %s", ext_dir)

    options = EdgeOptions()
    options.binary_location = str(edge_bin)
    options.add_argument(f"--user-data-dir={profile_root}")
    for proxy_arg in _cf._get_chrome_proxy_args():
        options.add_argument(proxy_arg)
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,900")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--enable-extensions")
    options.add_argument(f"--load-extension={ext_dir}")
    # Harmless even on Edge; belts-and-braces for any branded rejection.
    options.add_argument("--disable-features=DisableLoadExtensionCommandLineSwitch")

    logger.info("Launching Edge via Selenium (driver auto-fetched by Selenium Manager)...")
    # Selenium 4.11+ auto-fetches msedgedriver via Selenium Manager.
    driver = webdriver.Edge(options=options)
    driver.implicitly_wait(0)
    logger.info(
        "Edge launched: %s",
        driver.capabilities.get("browserVersion", "?"),
    )
    return driver, None


def _launch_chrome(
    ext_dir: str,
) -> tuple[webdriver.Chrome, Optional[subprocess.Popen]]:
    """Original Chrome path — kept for completeness.

    On Chrome 137+ the extension MUST be pre-installed via
    ``chrome://extensions`` because ``--load-extension`` is rejected.
    """
    debugger_address = _cf._get_chrome_debug_address()
    profile_mode = _cf._get_profile_mode()
    if profile_mode == "managed":
        profile_root = str(_managed_profile_root("chrome"))
        Path(profile_root).mkdir(parents=True, exist_ok=True)
        profile_dir_name = None
    else:
        profile_root = os.environ.get(
            "PCE_CHROME_PROFILE", _cf._get_chrome_profile_dir()
        )
        profile_dir_name = _cf._get_profile_directory_name()

    use_profile_copy = profile_mode == "clone" and debugger_address is None
    attach_existing = debugger_address is not None
    extension_preinstalled = False

    logger.info("Chrome user-data-dir: %s", profile_root)
    logger.info("Chrome profile dir:   %s", profile_dir_name or "Default")
    logger.info(
        "Chrome profile mode:  %s",
        "debug(attach)" if attach_existing else profile_mode,
    )
    logger.info("Extension dir:        %s", ext_dir)

    if attach_existing:
        pass
    elif use_profile_copy:
        profile_root, profile_dir_name = _cf._build_isolated_user_data_dir(
            profile_root, profile_dir_name,
        )
        logger.info("Cloned profile root: %s", profile_root)
    else:
        if not _cf._check_chrome_not_running(profile_root):
            raise RuntimeError(
                "Chrome is already running against this profile. Close it first."
            )
        extension_preinstalled = _cf._profile_has_installed_extension(
            profile_root, profile_dir_name, ext_dir,
        )
        if not extension_preinstalled:
            logger.warning(
                "Chrome 137+ rejects --load-extension. "
                "Pre-install the WXT build via chrome://extensions -> "
                "'Load unpacked' -> select %s  (one-time setup).",
                ext_dir,
            )

    driver_path = _cf._get_chromedriver_path()
    service = ChromeService(driver_path) if driver_path else None
    chrome_proc: Optional[subprocess.Popen] = None

    if attach_existing:
        options = ChromeOptions()
        options.debugger_address = debugger_address
        drv = webdriver.Chrome(service=service, options=options)
    elif use_profile_copy or not _cf._is_default_chrome_user_data_dir(profile_root):
        options = ChromeOptions()
        options.add_argument(f"--user-data-dir={profile_root}")
        if profile_dir_name:
            options.add_argument(f"--profile-directory={profile_dir_name}")
        for proxy_arg in _cf._get_chrome_proxy_args():
            options.add_argument(proxy_arg)
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,900")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        if use_profile_copy or not extension_preinstalled:
            options.add_argument("--enable-extensions")
            options.add_argument(f"--load-extension={ext_dir}")
            options.add_argument(
                "--disable-features=DisableLoadExtensionCommandLineSwitch"
            )
        drv = webdriver.Chrome(service=service, options=options)
    else:
        chrome_proc, debugger_address = _cf._launch_debug_chrome(
            profile_root,
            profile_dir_name,
            None if extension_preinstalled else ext_dir,
        )
        options = ChromeOptions()
        options.debugger_address = debugger_address
        drv = webdriver.Chrome(service=service, options=options)

    drv.implicitly_wait(0)
    logger.info(
        "Chrome launched: %s",
        drv.capabilities.get("browserVersion", "?"),
    )
    return drv, chrome_proc


def _teardown_driver(
    drv: webdriver.Remote, chrome_proc: Optional[subprocess.Popen]
) -> None:
    try:
        drv.quit()
    except Exception:
        pass
    if chrome_proc is not None:
        try:
            chrome_proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Per-site probe
# ---------------------------------------------------------------------------


def _status_icon(val: Optional[bool]) -> str:
    if val is None:
        return "-"
    return "OK" if val else "XX"


def _probe_phase1(adapter, driver) -> tuple[bool, str]:
    """Quick connectivity + login check."""
    try:
        ok = adapter.check_logged_in(driver, timeout=10)
    except Exception as e:
        return False, f"check_logged_in crashed: {e!r}"
    if ok:
        return True, ""
    adapter.take_screenshot(driver, "probe_p1_not_logged_in")
    return False, "input not found / login wall"


_EXTRACTOR_FLAG_NAMES = [
    "__PCE_CHATGPT_ACTIVE",
    "__PCE_CLAUDE_ACTIVE",
    "__PCE_COPILOT_ACTIVE",
    "__PCE_DEEPSEEK_ACTIVE",
    "__PCE_GEMINI_ACTIVE",
    "__PCE_GOOGLE_AI_STUDIO_ACTIVE",
    "__PCE_GROK_ACTIVE",
    "__PCE_HUGGINGFACE_ACTIVE",
    "__PCE_MANUS_ACTIVE",
    "__PCE_PERPLEXITY_ACTIVE",
    "__PCE_POE_ACTIVE",
    "__PCE_ZHIPU_ACTIVE",
    "__PCE_GENERIC_ACTIVE",
    "__PCE_UNIVERSAL_EXTRACTOR_LOADED",
    "__PCE_DETECTOR_LOADED",
    "__PCE_AI_PAGE_CONFIRMED",
]


def _probe_extension_state(driver) -> dict:
    """Check which PCE globals the content scripts have set on the page.

    Useful diagnostic: if no flags are set, the content script didn't
    load (bad manifest match / build issue / CSP block). If a flag IS
    set but captures never arrive, the extractor loaded but its DOM
    walk failed (site DOM changed).
    """
    try:
        js = (
            "const out = {};"
            "const keys = arguments[0];"
            "for (const k of keys) {"
            "  try { out[k] = !!window[k]; } catch (e) { out[k] = 'ERR'; }"
            "}"
            "out.__data_pce_ai_confirmed = "
            "  document.documentElement.getAttribute('data-pce-ai-confirmed');"
            "return out;"
        )
        return driver.execute_script(js, _EXTRACTOR_FLAG_NAMES) or {}
    except Exception as e:
        return {"_error": repr(e)}


def _probe_phase2(adapter, driver, scenario_name: str) -> tuple[bool, str, dict]:
    """Send a probe message, verify capture + normalised session."""
    scenario = SCENARIOS[scenario_name]
    extra: dict = {}
    token = str(int(time.time()))

    try:
        initial = get_stats()
    except Exception as e:
        return False, f"stats API error: {e}", extra
    initial_count = initial["total_captures"]
    initial_provider_count = initial.get("by_provider", {}).get(adapter.provider, 0)

    if not adapter.navigate(driver):
        adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_nav_fail")
        return False, "navigate() returned False", extra

    if not adapter.find_input(driver):
        time.sleep(2)
        adapter.navigate(driver)
        if not adapter.find_input(driver):
            adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_no_input")
            return False, "input not found after navigate", extra

    # Diagnostic: are any PCE content-script flags set on the page?
    flags = _probe_extension_state(driver)
    active_flags = [k for k, v in flags.items() if v and not k.startswith("_")]
    logger.info("[%s] page PCE flags: %s", adapter.name, active_flags or "<none>")
    extra["page_flags"] = active_flags

    prompt = scenario["prompt_template"].format(site=adapter.name, token=token)
    adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_before_send")

    if scenario["file_paths"] or scenario["image_paths"]:
        sent = adapter.send_rich_message(
            driver,
            message=prompt,
            file_paths=scenario["file_paths"] or None,
            image_paths=scenario["image_paths"] or None,
        )
    else:
        sent = adapter.send_message(driver, message=prompt)

    if not sent:
        adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_send_fail")
        return False, f"send_{scenario_name}_failed", extra

    response_ok = adapter.wait_for_response(driver)
    adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_after_response")
    if not response_ok:
        logger.warning(
            "[%s] %s: response not detected (may still be captured)",
            adapter.name, scenario_name,
        )

    adapter.trigger_manual_capture(driver)
    time.sleep(3)

    verify = wait_for_new_captures(
        initial_count=initial_count,
        initial_provider_count=initial_provider_count,
        timeout_s=25,
        poll_interval=2,
        min_new=1,
        provider=adapter.provider,
    )
    if not verify["success"]:
        adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_no_capture")
        return False, (
            f"no new captures for provider={adapter.provider} "
            f"(baseline={initial_count}, current={verify['total']})"
        ), extra

    session = wait_for_session_with_messages(
        provider=adapter.provider,
        min_messages=2,
        timeout_s=20,
        poll_interval=2,
        required_roles={"user", "assistant"},
    )
    if not session["success"]:
        adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_no_session")
        roles = [m.get("role", "?") for m in session.get("messages", [])]
        return False, f"no user+assistant session (roles seen: {roles})", extra

    msgs = session["messages"]
    sess = session["session"]

    quality = verify_message_quality(msgs)
    if not quality["ok"]:
        adapter.take_screenshot(driver, f"probe_p2_{scenario_name}_quality_fail")
        return False, f"message quality failed: {quality['issues']}", extra

    rich = verify_rich_content(msgs)
    all_types = rich["types_found"]
    expected_user = scenario["expected_user_types"]
    expected_asst = scenario["expected_assistant_types"]
    missing_user = expected_user - all_types if expected_user else set()
    missing_asst = expected_asst - all_types if expected_asst else set()
    if missing_user or missing_asst:
        logger.warning(
            "[%s] %s: expected attachment types missing "
            "(user=%s, asst=%s; seen=%s)",
            adapter.name, scenario_name,
            sorted(missing_user), sorted(missing_asst), sorted(all_types),
        )

    extra["session_id"] = sess.get("id")
    extra["message_count"] = len(msgs)
    extra["attachment_types"] = sorted(all_types)
    extra["provider_new_count"] = verify.get("provider_new_count", verify["new_count"])
    return True, "", extra


def _probe_phase3(provider: str) -> tuple[bool, str]:
    res = verify_dashboard_sessions(provider=provider)
    if res["ok"]:
        return True, ""
    return False, (
        f"dashboard not renderable: "
        f"{res['renderable_sessions']}/{res['session_count']} sessions, "
        f"issues={res['issues']}"
    )


# ---------------------------------------------------------------------------
# Top-level probe
# ---------------------------------------------------------------------------


def probe_site(
    driver: webdriver.Chrome,
    site_name: str,
    scenario_name: str = "text",
) -> ScenarioState:
    """Run phases 1 → 3 for ``(site_name, scenario_name)``.

    The result is persisted to ``e2e_state.json`` (shared with
    ``test_three_phase.py``) and returned.
    """
    adapter = ALL_SITES[site_name]
    ss = ScenarioState(
        site_name=site_name,
        scenario=scenario_name,
        timestamp=time.time(),
    )

    print(f"\n{'=' * 72}")
    print(f"  {site_name} / {scenario_name}  ->  {adapter.url}")
    print(f"{'=' * 72}")

    print("  [P1] connectivity + login ...", end=" ", flush=True)
    p1_ok, p1_err = _probe_phase1(adapter, driver)
    ss.phase1_ok = p1_ok
    ss.phase1_error = p1_err or None
    print(f"{_status_icon(p1_ok)}  {p1_err}" if p1_err else _status_icon(p1_ok))
    if not p1_ok:
        _persist(ss)
        return ss

    print(f"  [P2] send {scenario_name} + verify capture + session ...", flush=True)
    p2_ok, p2_err, p2_extra = _probe_phase2(adapter, driver, scenario_name)
    ss.phase2_ok = p2_ok
    ss.phase2_error = p2_err or None
    ss.phase2_session_id = p2_extra.get("session_id")
    ss.phase2_message_count = p2_extra.get("message_count", 0)
    ss.phase2_attachment_types = p2_extra.get("attachment_types", [])
    if p2_ok:
        print(
            f"       {_status_icon(True)}  session={str(ss.phase2_session_id)[:8]}  "
            f"msgs={ss.phase2_message_count}  attachments={ss.phase2_attachment_types}"
        )
    else:
        print(f"       {_status_icon(False)}  {p2_err}")
        _persist(ss)
        return ss

    print("  [P3] dashboard render readable ...", end=" ", flush=True)
    p3_ok, p3_err = _probe_phase3(adapter.provider)
    ss.phase3_ok = p3_ok
    ss.phase3_error = p3_err or None
    print(f"{_status_icon(p3_ok)}  {p3_err}" if p3_err else _status_icon(p3_ok))

    ss.timestamp = time.time()
    _persist(ss)
    return ss


def _persist(ss: ScenarioState) -> None:
    state = _load_state()
    state[_state_key(ss.site_name, ss.scenario)] = ss
    _save_state(state)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(selected: Optional[list[str]] = None) -> None:
    state = _load_state()
    sites = selected or list(ALL_SITES.keys())

    print("\n" + "=" * 80)
    print("  PCE SITE PROBE SUMMARY")
    print("=" * 80)
    print(f"  {'site':<16} {'scenario':<10} {'P1':>3} {'P2':>3} {'P3':>3}  notes")
    print("  " + "-" * 74)

    pass_count = tested = 0
    for site in sites:
        adapter = ALL_SITES.get(site)
        if adapter is None:
            continue
        for scen in SCENARIOS:
            if not _site_supports_scenario(adapter, scen):
                continue
            key = _state_key(site, scen)
            s = state.get(key)
            if s is None:
                print(
                    f"  {site:<16} {scen:<10} "
                    f"{'-':>3} {'-':>3} {'-':>3}  (untested)"
                )
                continue
            tested += 1
            notes = []
            if not s.phase1_ok and s.phase1_error:
                notes.append(s.phase1_error[:40])
            elif not s.phase2_ok and s.phase2_error:
                notes.append(s.phase2_error[:40])
            elif not s.phase3_ok and s.phase3_error:
                notes.append(s.phase3_error[:40])
            elif s.phase2_ok:
                notes.append(f"{s.phase2_message_count}msgs")
                if s.phase2_attachment_types:
                    notes.append(",".join(s.phase2_attachment_types[:4]))
            if s.phase1_ok and s.phase2_ok and s.phase3_ok:
                pass_count += 1
            print(
                f"  {site:<16} {scen:<10} "
                f"{_status_icon(s.phase1_ok):>3} {_status_icon(s.phase2_ok):>3} "
                f"{_status_icon(s.phase3_ok):>3}  {' | '.join(notes)}"
            )

    print("  " + "-" * 74)
    print(f"  {pass_count}/{tested} scenario probes passed (state: {STATE_FILE})")
    print("=" * 80 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _interactive_prompt(site: str, scen: str) -> str:
    """Ask the agent/user whether to probe the next site.

    Returns:
        'y' — probe
        's' — skip
        'q' — quit
    """
    try:
        raw = input(
            f"\n-> next: {site}/{scen}   [Enter=probe, s=skip, q=quit]: "
        ).strip().lower()
    except EOFError:
        return "y"  # non-TTY → run unattended
    if raw == "q":
        return "q"
    if raw == "s":
        return "s"
    return "y"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="PCE real-site iterative probe driver (Phase 4d).",
    )
    parser.add_argument(
        "--site",
        help="Probe a single site (e.g. 'chatgpt'). Without this flag, "
             "behaviour depends on --all / --list.",
    )
    parser.add_argument(
        "--scenario",
        default="text",
        choices=list(SCENARIOS.keys()),
        help="Scenario to probe (default: text).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Probe every site that supports the scenario, in order.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print registered sites + current state, then exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-probe even if a previous run passed.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Unattended mode — don't prompt between sites.",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Skip the PCE DB reset before probing.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print summary table only, no probes.",
    )
    parser.add_argument(
        "--browser",
        default="auto",
        choices=("auto", "chrome", "edge"),
        help=(
            "Which browser to drive. 'auto' (default) prefers Chrome if "
            "its managed profile already has data; otherwise Edge. "
            "On Chrome 137+ the WXT extension must be pre-installed via "
            "chrome://extensions -> 'Load unpacked' (run "
            "--setup-chrome-extension once)."
        ),
    )
    parser.add_argument(
        "--setup-chrome-extension",
        action="store_true",
        help=(
            "One-time setup: launch Chrome against the managed profile so "
            "you can load the WXT extension via chrome://extensions. "
            "Required on Chrome 137+ because --load-extension is rejected."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # --- One-shot utilities that don't need a driver/server ---
    if args.setup_chrome_extension:
        return _cmd_setup_chrome_extension()
    if args.summary:
        print_summary()
        return 0

    args.browser = _resolve_browser(args.browser)

    # --- List short-circuit ---
    if args.list:
        state = _load_state()
        print("\nRegistered sites (14):")
        for name, adapter in ALL_SITES.items():
            key = _state_key(name, args.scenario)
            s = state.get(key)
            status = "untested"
            if s is not None:
                if s.phase1_ok and s.phase2_ok and s.phase3_ok:
                    status = "PASS"
                elif not s.phase1_ok:
                    status = "not-logged-in"
                elif s.phase2_ok is False:
                    status = "P2-FAIL"
                elif s.phase3_ok is False:
                    status = "P3-FAIL"
                else:
                    status = "partial"
            print(
                f"  {name:<16} provider={adapter.provider:<14} "
                f"url={adapter.url:<40} [{args.scenario}: {status}]"
            )
        print()
        return 0

    # --- Pre-flight: PCE core must be up ---
    if not pce_is_running():
        print(
            "ERROR: PCE Core is not reachable at http://127.0.0.1:9800/\n"
            "Start it first with: python -m pce_app --no-tray --no-browser",
            file=sys.stderr,
        )
        return 2

    # --- Resolve target list ---
    if args.all:
        targets = [
            name for name, adapter in ALL_SITES.items()
            if _site_supports_scenario(adapter, args.scenario)
        ]
    elif args.site:
        if args.site not in ALL_SITES:
            print(
                f"ERROR: unknown site '{args.site}'. "
                f"Known: {', '.join(ALL_SITES)}",
                file=sys.stderr,
            )
            return 2
        if not _site_supports_scenario(ALL_SITES[args.site], args.scenario):
            print(
                f"ERROR: site '{args.site}' does not support scenario "
                f"'{args.scenario}' (file/image requires upload support).",
                file=sys.stderr,
            )
            return 2
        targets = [args.site]
    else:
        parser.error("one of --site, --all, --list, --summary is required")
        return 2

    # --- Skip already-passed targets unless --force ---
    state = _load_state()
    if not args.force:
        pending = []
        for site in targets:
            key = _state_key(site, args.scenario)
            s = state.get(key)
            if s and s.phase1_ok and s.phase2_ok and s.phase3_ok:
                logger.info("[%s] already passed — skipping (use --force)", site)
                continue
            pending.append(site)
        targets = pending

    if not targets:
        print("Nothing pending. Use --force to re-probe.")
        print_summary()
        return 0

    # --- Optional baseline reset ---
    if not args.no_reset:
        try:
            r = reset_baseline()
            print(
                f"Baseline reset: "
                f"{r.get('captures_deleted', 0)} captures, "
                f"{r.get('sessions_deleted', 0)} sessions, "
                f"{r.get('messages_deleted', 0)} messages deleted."
            )
        except Exception as e:
            print(f"WARN: baseline reset failed: {e}", file=sys.stderr)

    # --- Launch browser once for the whole session ---
    print(f"Browser: {args.browser}")
    try:
        driver, chrome_proc = _launch_driver(args.browser)
    except Exception as e:
        print(f"ERROR: {args.browser} launch failed: {e}", file=sys.stderr)
        return 2

    any_failed = False
    try:
        for i, site in enumerate(targets):
            if len(targets) > 1 and not args.yes and i > 0:
                choice = _interactive_prompt(site, args.scenario)
                if choice == "q":
                    print("quit by user.")
                    break
                if choice == "s":
                    print(f"  skipping {site}")
                    continue

            ss = probe_site(driver, site, args.scenario)
            all_ok = bool(ss.phase1_ok and ss.phase2_ok and ss.phase3_ok)
            # Phase 1 fail (not-logged-in) is informational, not a hard fail.
            if not ss.phase1_ok:
                logger.warning(
                    "[%s] P1 fail — treated as skip (login needed)", site
                )
                continue
            if not all_ok:
                any_failed = True
    finally:
        _teardown_driver(driver, chrome_proc)

    print_summary(targets if args.site else None)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
