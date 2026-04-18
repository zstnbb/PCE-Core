"""PCE Browser E2E Capture Tests (Selenium).

Tests the full capture pipeline end-to-end:
  Chrome (user profile, logged in) + PCE extension
    → visit AI site → send message → wait for response
    → verify PCE Core captured the data

Usage:
    # 1. Close Chrome (Selenium needs the profile lock)
    # 2. Ensure PCE Core is running: python -m pce_app --no-tray --no-browser
    # 3. Run all sites:
    #      python -m pytest tests/e2e/test_capture.py -v -s
    # 4. Run specific site:
    #      python -m pytest tests/e2e/test_capture.py -v -s -k chatgpt
    # 5. Run without reset (keep existing data):
    #      PCE_E2E_NO_RESET=1 python -m pytest tests/e2e/test_capture.py -v -s

Environment variables:
    PCE_CHROME_PROFILE  - Override Chrome profile path
    PCE_E2E_NO_RESET    - Set to "1" to skip baseline reset before tests
    PCE_E2E_SITES       - Comma-separated list of sites to test (default: all)
"""

import os
import logging
import time
from pathlib import Path

import pytest

from .capture_verifier import (
    pce_is_running,
    get_stats,
    get_recent_captures,
    reset_baseline,
    wait_for_new_captures,
    wait_for_session_with_messages,
)
from .sites.chatgpt import ChatGPTAdapter
from .sites.claude import ClaudeAdapter
from .sites.deepseek import DeepSeekAdapter
from .sites.gemini import GeminiAdapter
from .sites.google_ai_studio import GoogleAIStudioAdapter
from .sites.grok import GrokAdapter
from .sites.kimi import KimiAdapter
from .sites.manus import ManusAdapter
from .sites.perplexity import PerplexityAdapter
from .sites.poe import PoeAdapter
from .sites.zhipu import ZhiPuAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pce.e2e.test")

# ---------------------------------------------------------------------------
# All available site adapters
# ---------------------------------------------------------------------------

ALL_SITES = {
    "chatgpt": ChatGPTAdapter(),
    "claude": ClaudeAdapter(),
    "deepseek": DeepSeekAdapter(),
    "zhipu": ZhiPuAdapter(),
    "gemini": GeminiAdapter(),
    "googleaistudio": GoogleAIStudioAdapter(),
    "grok": GrokAdapter(),
    "kimi": KimiAdapter(),
    "manus": ManusAdapter(),
    "perplexity": PerplexityAdapter(),
    "poe": PoeAdapter(),
}


def _selected_sites() -> list[str]:
    """Return the list of sites to test, filtered by env var."""
    env = os.environ.get("PCE_E2E_SITES", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip() in ALL_SITES]
    return list(ALL_SITES.keys())


# ---------------------------------------------------------------------------
# Session-level setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def ensure_pce_running():
    """Fail fast if PCE Core isn't running."""
    if not pce_is_running():
        pytest.fail(
            "PCE Core server not reachable at http://127.0.0.1:9800\n"
            "Start it with: python -m pce_app --no-tray --no-browser"
        )


@pytest.fixture(scope="session", autouse=True)
def reset_before_tests(ensure_pce_running):
    """Reset baseline before the test session (unless PCE_E2E_NO_RESET=1)."""
    if os.environ.get("PCE_E2E_NO_RESET", "").strip() == "1":
        logger.info("Skipping baseline reset (PCE_E2E_NO_RESET=1)")
        return

    result = reset_baseline()
    logger.info(
        "Baseline reset: %d captures, %d sessions, %d messages deleted",
        result.get("captures_deleted", 0),
        result.get("sessions_deleted", 0),
        result.get("messages_deleted", 0),
    )


# ---------------------------------------------------------------------------
# Parametrized test: one test case per site
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("site_name", _selected_sites())
def test_capture(site_name, driver):
    """End-to-end test: send a message on an AI site and verify PCE captured it."""
    adapter = ALL_SITES[site_name]
    logger.info("=" * 60)
    logger.info("TESTING: %s (%s)", adapter.name, adapter.url)
    logger.info("=" * 60)

    # Record initial capture count
    initial_stats = get_stats()
    initial_count = initial_stats["total_captures"]
    initial_provider_count = len(get_recent_captures(last=200, provider=adapter.provider))

    # Run the full interaction cycle (Selenium uses one window, navigate in-place)
    result = adapter.run_test(driver)

    logger.info(
        "[%s] Interaction: sent=%s response=%s error=%s (%.1fs)",
        site_name,
        result.message_sent,
        result.response_received,
        result.error,
        result.elapsed_s,
    )

    if not result.message_sent:
        pytest.fail(
            f"[{site_name}] Could not send message: {result.error}\n"
            f"Screenshot: {result.screenshot_path}"
        )

    # Give the capture pipeline time to process
    time.sleep(3)

    # Verify PCE captured the interaction
    verify = wait_for_new_captures(
        initial_count=initial_count,
        initial_provider_count=initial_provider_count,
        timeout_s=20,
        poll_interval=2,
        min_new=1,
        provider=adapter.provider,
    )

    logger.info(
        "[%s] Capture verify: success=%s new=%d (%.1fs)",
        site_name,
        verify["success"],
        verify.get("provider_new_count", verify["new_count"]),
        verify["elapsed_s"],
    )

    if not verify["success"]:
        adapter.take_screenshot(driver, "capture_failed")
        diag = (
            f"[{site_name}] PCE did not capture any data!\n"
            f"  Provider: {adapter.provider}\n"
            f"  Initial captures: {initial_count}\n"
            f"  Current captures: {verify['total']}\n"
            f"  Timeout: 20s\n"
            f"  Screenshot: {result.screenshot_path}"
        )
        pytest.fail(diag)

    # Deeper check: verify session has messages
    session_result = wait_for_session_with_messages(
        provider=adapter.provider,
        min_messages=2,
        timeout_s=15,
        poll_interval=2,
        required_roles={"user", "assistant"},
    )

    if session_result["success"]:
        msgs = session_result["messages"]
        roles = [m.get("role", "?") for m in msgs]
        if "unknown" in roles:
            adapter.take_screenshot(driver, "unknown_role")
            pytest.fail(
                f"[{site_name}] Session stored unknown-role messages.\n"
                f"  Provider: {adapter.provider}\n"
                f"  Roles: {roles}\n"
                f"  Screenshot: {result.screenshot_path}"
            )
        logger.info(
            "[%s] Session verified: %d messages, roles=%s",
            site_name,
            len(msgs),
            roles,
        )
    else:
        adapter.take_screenshot(driver, "session_failed")
        roles = [m.get("role", "?") for m in session_result.get("messages", [])]
        pytest.fail(
            f"[{site_name}] Captures arrived but no normalized session/messages were stored.\n"
            f"  Provider: {adapter.provider}\n"
            f"  Roles seen: {roles}\n"
            f"  Screenshot: {result.screenshot_path}"
        )


# ---------------------------------------------------------------------------
# Standalone runner (for running outside pytest)
# ---------------------------------------------------------------------------

def main():
    """Run all site tests sequentially with a simple report."""
    import platform
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    print("=" * 60)
    print("PCE Browser E2E Capture Test (Selenium)")
    print("=" * 60)

    if not pce_is_running():
        print("\nFATAL: PCE Core not running at http://127.0.0.1:9800")
        print("  Start it: python -m pce_app --no-tray --no-browser")
        return

    # Reset
    if os.environ.get("PCE_E2E_NO_RESET", "").strip() != "1":
        result = reset_baseline()
        print(f"Reset: {result}")

    sites = _selected_sites()
    print(f"\nSites to test: {', '.join(sites)}")

    # Detect Chrome profile
    system = platform.system()
    if system == "Windows":
        profile_dir = os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Google", "Chrome", "User Data",
        )
    elif system == "Darwin":
        profile_dir = os.path.expanduser(
            "~/Library/Application Support/Google/Chrome"
        )
    else:
        profile_dir = os.path.expanduser("~/.config/google-chrome")

    profile_dir = os.environ.get("PCE_CHROME_PROFILE", profile_dir)
    # Post-P2.5 Phase 4: WXT build output path.
    ext_dir = str(
        Path(__file__).resolve().parent.parent.parent
        / "pce_browser_extension_wxt"
        / ".output"
        / "chrome-mv3"
    )

    print(f"Chrome profile: {profile_dir}")
    print(f"Extension: {ext_dir}")
    print("\nMake sure Chrome is CLOSED before continuing!")
    input("Press Enter to start...")

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--enable-extensions")
    options.add_argument(f"--load-extension={ext_dir}")
    options.add_argument(f"--disable-extensions-except={ext_dir}")
    options.add_argument("--proxy-bypass-list=127.0.0.1;localhost")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,900")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    drv = webdriver.Chrome(options=options)
    results = []

    try:
        for site_name in sites:
            adapter = ALL_SITES[site_name]
            print(f"\n{'─' * 40}")
            print(f"Testing: {adapter.name} ({adapter.url})")
            print(f"{'─' * 40}")

            initial = get_stats()["total_captures"]

            try:
                site_result = adapter.run_test(drv)
                print(f"  Sent: {site_result.message_sent}  Response: {site_result.response_received}")

                if site_result.message_sent:
                    time.sleep(3)
                    verify = wait_for_new_captures(
                        initial_count=initial,
                        timeout_s=20,
                        provider=adapter.provider,
                    )
                    captured = verify["success"]
                    print(f"  Captured: {captured}  New: {verify['new_count']}")
                    results.append((site_name, captured, verify["new_count"]))
                else:
                    print(f"  SKIP: could not send ({site_result.error})")
                    results.append((site_name, False, 0))
            except Exception as e:
                print(f"  ERROR: {e}")
                results.append((site_name, False, 0))
    finally:
        try:
            drv.quit()
        except Exception:
            pass

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    passed = 0
    for name, success, count in results:
        icon = "+" if success else "-"
        print(f"  {icon} {name:15s} — captured={success}, new_captures={count}")
        if success:
            passed += 1
    print(f"\n{passed}/{len(results)} sites passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
