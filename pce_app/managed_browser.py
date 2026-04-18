# SPDX-License-Identifier: Apache-2.0
"""PCE Managed Browser – launch a pre-configured Chrome for AI capture.

Launches a dedicated Chrome instance with:
- Isolated user profile (~/.pce/chrome_profile)
- PCE browser extension pre-loaded
- proxy-bypass-list ensuring localhost/127.0.0.1 always reaches PCE Core
- User's existing system proxy (Clash/V2Ray/etc.) untouched for all other traffic

This gives zero-config capture: the user clicks one button, gets a browser
window where every AI conversation is automatically recorded.
"""

import logging
import os
import platform
import subprocess

import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.browser")


# ---------------------------------------------------------------------------
# System proxy detection (Windows-focused, with env-var fallback)
# ---------------------------------------------------------------------------

def detect_system_proxy() -> Optional[str]:
    """Detect the user's current system proxy setting.

    Returns a string like "127.0.0.1:7890" or None if no proxy is set.
    This is used for logging/diagnostics only – we do NOT alter the proxy.
    """
    # 1) Windows registry
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if enabled:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                winreg.CloseKey(key)
                return server
            winreg.CloseKey(key)
        except (OSError, FileNotFoundError):
            pass

    # 2) Environment variables
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        val = os.environ.get(var)
        if val:
            return val

    return None


def detect_proxy_bypass() -> str:
    """Read the existing proxy bypass list from Windows registry.

    Returns the raw bypass string (e.g. "localhost;127.*;10.*;...") or empty.
    """
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            bypass, _ = winreg.QueryValueEx(key, "ProxyOverride")
            winreg.CloseKey(key)
            return bypass or ""
        except (OSError, FileNotFoundError):
            pass
    return os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))


# ---------------------------------------------------------------------------
# Chrome discovery
# ---------------------------------------------------------------------------

_CHROME_CANDIDATES_WINDOWS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Google" / "Chrome" / "Application" / "chrome.exe",
]

_EDGE_CANDIDATES_WINDOWS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Microsoft" / "Edge" / "Application" / "msedge.exe",
]

_CHROME_CANDIDATES_MAC = [
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]

_CHROME_CANDIDATES_LINUX = [
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
]


def find_chrome() -> Optional[Path]:
    """Find a Chrome (or Chromium-based) browser executable.

    Returns the path or None. Prefers Chrome, falls back to Edge on Windows.
    """
    # Try Windows registry first
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
            )
            chrome_path, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            p = Path(chrome_path)
            if p.exists():
                return p
        except (OSError, FileNotFoundError):
            pass

    # Platform-specific candidate paths
    system = platform.system()
    if system == "Windows":
        candidates = _CHROME_CANDIDATES_WINDOWS + _EDGE_CANDIDATES_WINDOWS
    elif system == "Darwin":
        candidates = _CHROME_CANDIDATES_MAC
    else:
        candidates = _CHROME_CANDIDATES_LINUX

    for p in candidates:
        if p.exists():
            return p

    return None


# ---------------------------------------------------------------------------
# Managed browser launch
# ---------------------------------------------------------------------------

# Where we keep the isolated Chrome profile
_DEFAULT_PROFILE_DIR = Path.home() / ".pce" / "chrome_profile"

# Post-P2.5 Phase 4: the extension is built by WXT into
# ``pce_browser_extension_wxt/.output/{chrome,firefox}-mv3/``. The old
# ``pce_browser_extension/`` source directory no longer exists; Chrome
# loads the built bundle from the WXT output root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WXT_ROOT = _PROJECT_ROOT / "pce_browser_extension_wxt"
_WXT_CHROME_OUTPUT = _WXT_ROOT / ".output" / "chrome-mv3"
_WXT_FIREFOX_OUTPUT = _WXT_ROOT / ".output" / "firefox-mv3"


def _find_extension_dir() -> Path:
    """Resolve the bundled extension directory.

    Resolution order:
      1. ``PCE_EXTENSION_DIR`` environment override (absolute or
         repo-relative path).
      2. The WXT Chrome MV3 build output.
      3. The WXT Firefox MV3 build output.
      4. The Chrome output path as a default (triggers a clear error
         downstream when the directory is missing).
    """
    override = os.environ.get("PCE_EXTENSION_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if _WXT_CHROME_OUTPUT.exists():
        return _WXT_CHROME_OUTPUT
    if _WXT_FIREFOX_OUTPUT.exists():
        return _WXT_FIREFOX_OUTPUT
    return _WXT_CHROME_OUTPUT


_EXTENSION_DIR = _find_extension_dir()


class ManagedBrowser:
    """Manages the lifecycle of a PCE-controlled Chrome instance."""

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        extension_dir: Optional[Path] = None,
        start_urls: Optional[list[str]] = None,
    ):
        self.profile_dir = profile_dir or _DEFAULT_PROFILE_DIR
        self.extension_dir = extension_dir or _EXTENSION_DIR
        self.start_urls = start_urls or [
            "http://127.0.0.1:9800",   # PCE Dashboard
            "https://chatgpt.com",      # ChatGPT
        ]
        self._process: Optional[subprocess.Popen] = None
        self._chrome_path: Optional[Path] = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def launch(self) -> bool:
        """Launch the managed Chrome instance.

        Returns True on success, False on failure.
        """
        if self.is_running:
            logger.info("Managed browser already running (pid=%s)", self._process.pid)
            return True

        # Find Chrome
        chrome = find_chrome()
        if chrome is None:
            logger.error("Cannot find Chrome or Edge browser on this system")
            return False
        self._chrome_path = chrome

        # Ensure profile dir exists
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # Detect system proxy for logging
        sys_proxy = detect_system_proxy()
        if sys_proxy:
            logger.info("Detected system proxy: %s (will NOT be altered)", sys_proxy)
        else:
            logger.info("No system proxy detected")

        # Build Chrome launch arguments
        args = self._build_args()

        logger.info("Launching managed browser: %s", chrome.name)
        logger.info("  Profile: %s", self.profile_dir)
        logger.info("  Extension: %s", self.extension_dir)
        logger.info("  Start URLs: %s", self.start_urls)

        try:
            # Use CREATE_NEW_PROCESS_GROUP on Windows so Chrome doesn't die
            # when the parent console closes
            kwargs = {}
            if platform.system() == "Windows":
                kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                )

            self._process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
            logger.info("Managed browser started (pid=%s)", self._process.pid)
            return True
        except Exception:
            logger.exception("Failed to launch managed browser")
            return False

    def close(self):
        """Terminate the managed Chrome instance."""
        if self._process is None:
            return

        if self._process.poll() is None:
            logger.info("Closing managed browser (pid=%s)...", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                logger.warning("Managed browser killed after timeout")

        self._process = None
        logger.info("Managed browser closed")

    @property
    def pid(self) -> Optional[int]:
        if self._process and self._process.poll() is None:
            return self._process.pid
        return None

    def _build_args(self) -> list[str]:
        """Build the full Chrome command-line argument list."""
        chrome = str(self._chrome_path)

        args = [
            chrome,
            # Isolated profile – does NOT touch user's main Chrome data
            f"--user-data-dir={self.profile_dir}",
            # Explicitly enable extensions in this profile
            "--enable-extensions",
            # Pre-load PCE extension (no manual install needed)
            f"--load-extension={self.extension_dir}",
            # Only allow our extension (block others from profile)
            f"--disable-extensions-except={self.extension_dir}",
            # CRITICAL: bypass proxy for localhost so the extension can
            # reach PCE Core API at 127.0.0.1:9800. All other traffic
            # (including chatgpt.com) still goes through the user's
            # system proxy / VPN as usual.
            "--proxy-bypass-list=127.0.0.1;localhost",
            # Disable first-run experience
            "--no-first-run",
            "--no-default-browser-check",
            # Window title hint
            "--window-name=PCE AI Browser",
        ]

        # Start URLs (each becomes a tab)
        args.extend(self.start_urls)

        return args


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_instance: Optional[ManagedBrowser] = None


def get_managed_browser() -> ManagedBrowser:
    """Get or create the singleton ManagedBrowser instance."""
    global _instance
    if _instance is None:
        _instance = ManagedBrowser()
    return _instance


def launch_browser(start_urls: Optional[list[str]] = None) -> bool:
    """Launch the managed browser (convenience function)."""
    browser = get_managed_browser()
    if start_urls:
        browser.start_urls = start_urls
    return browser.launch()


def close_browser():
    """Close the managed browser (convenience function)."""
    if _instance is not None:
        _instance.close()
