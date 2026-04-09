"""Convenience script to run PCE browser E2E capture tests.

Usage:
    python run_e2e.py                    # Test all sites
    python run_e2e.py chatgpt claude     # Test specific sites
    python run_e2e.py --list             # List available sites
    python run_e2e.py --no-reset         # Keep existing capture data

Prerequisites:
    1. Close Chrome (Playwright needs the profile lock)
    2. PCE Core running: python -m pce_app --no-tray --no-browser
"""

import os
import sys

AVAILABLE_SITES = [
    "chatgpt", "claude", "deepseek", "zhipu",
    "gemini", "googleaistudio", "grok", "kimi",
    "manus", "perplexity", "poe",
]


def main():
    args = sys.argv[1:]

    if "--list" in args:
        print("Available sites:")
        for s in AVAILABLE_SITES:
            print(f"  - {s}")
        return

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Parse flags
    no_reset = "--no-reset" in args
    sites = [a for a in args if not a.startswith("--")]

    if no_reset:
        os.environ["PCE_E2E_NO_RESET"] = "1"

    if sites:
        # Validate
        invalid = [s for s in sites if s not in AVAILABLE_SITES]
        if invalid:
            print(f"Unknown sites: {invalid}")
            print(f"Available: {AVAILABLE_SITES}")
            sys.exit(1)
        os.environ["PCE_E2E_SITES"] = ",".join(sites)

    # Run via pytest
    import pytest
    exit_code = pytest.main([
        "tests/e2e/test_capture.py",
        "-v",
        "-s",
        "--tb=short",
        "-x",  # Stop on first failure (easier for iterative debugging)
    ])
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
