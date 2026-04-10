"""Selenium-based fixtures for PCE E2E capture tests.

Launches Chrome with:
  - The user's existing profile (logged-in sessions)
  - The PCE browser extension pre-loaded
  - Selenium WebDriver control

Requirements:
    - PCE Core running at http://127.0.0.1:9800
    - pip install selenium webdriver-manager

Profile modes:
    - default clone mode copies the selected Chrome profile into a temp dir.
    - set PCE_CHROME_DEBUG_ADDRESS=127.0.0.1:<port> to attach to an
      already-launched debugging Chrome without copying profile files.
    - set PCE_CHROME_PROFILE_MODE=managed to use ~/.pce/chrome_profile as a
      dedicated live-test browser profile. Log in once there, then reuse it.
    - set PCE_CHROME_PROFILE_MODE=direct or PCE_CHROME_PROFILE_COPY=0 to use
      the real profile directly; Chrome must be closed in that mode.
    - set PCE_CHROME_PROXY=host:port, http://host:port, or direct to make
      browser networking deterministic for live external sites.
"""

import json
import logging
import os
import platform
import socket
import subprocess
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger("pce.e2e")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXTENSION_DIR = PROJECT_ROOT / "pce_browser_extension"
SCREENSHOTS_DIR = PROJECT_ROOT / "tests" / "e2e" / "screenshots"


def _get_chrome_profile_dir() -> str:
    """Detect the default Chrome user data directory."""
    system = platform.system()
    if system == "Windows":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Google", "Chrome", "User Data",
        )
    elif system == "Darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Google/Chrome"
        )
    else:
        return os.path.expanduser("~/.config/google-chrome")


def _get_profile_directory_name() -> str | None:
    """Return the Chrome profile directory name, if explicitly configured."""
    return os.environ.get("PCE_CHROME_PROFILE_DIR", "").strip() or None


def _get_chrome_debug_address() -> str | None:
    """Return an existing Chrome DevTools address to attach to, if configured."""
    return (
        os.environ.get("PCE_CHROME_DEBUG_ADDRESS", "").strip()
        or os.environ.get("PCE_CHROME_REMOTE_DEBUGGING_ADDRESS", "").strip()
        or None
    )


def _get_profile_mode() -> str:
    """Return the configured E2E profile mode."""
    explicit = os.environ.get("PCE_CHROME_PROFILE_MODE", "").strip().lower()
    if explicit:
        return explicit
    # Backward compatibility with the old boolean switch.
    if os.environ.get("PCE_CHROME_PROFILE_COPY", "1").strip() == "0":
        return "direct"
    return "managed"


def _get_chrome_proxy_args() -> list[str]:
    """Return Chrome proxy flags for deterministic live-site navigation."""
    proxy = os.environ.get("PCE_CHROME_PROXY", "").strip()
    bypass = os.environ.get(
        "PCE_CHROME_PROXY_BYPASS",
        "127.0.0.1;localhost",
    ).strip()
    args: list[str] = []

    if proxy:
        proxy_mode = proxy.lower()
        if proxy_mode in {"direct", "none", "off"}:
            args.append("--no-proxy-server")
        elif proxy_mode != "system":
            if "://" not in proxy:
                proxy = f"http://{proxy}"
            args.append(f"--proxy-server={proxy}")

    if bypass:
        args.append(f"--proxy-bypass-list={bypass}")
    return args


def _get_extension_dir() -> str:
    """Get the path to the PCE browser extension."""
    return str(EXTENSION_DIR)


def _is_default_chrome_user_data_dir(profile_root: str) -> bool:
    """Return True when the path points at the user's default Chrome data dir."""
    return os.path.normcase(os.path.abspath(profile_root)) == os.path.normcase(
        os.path.abspath(_get_chrome_profile_dir())
    )


def _profile_has_installed_extension(
    profile_root: str,
    profile_dir_name: str | None,
    ext_dir: str,
) -> bool:
    """Check whether the unpacked PCE extension is already installed in the profile."""
    profile_name = profile_dir_name or "Default"
    ext_path = os.path.normcase(os.path.abspath(ext_dir))
    pref_files = [
        Path(profile_root) / profile_name / "Secure Preferences",
        Path(profile_root) / profile_name / "Preferences",
    ]

    for pref_file in pref_files:
        if not pref_file.is_file():
            continue
        try:
            data = json.loads(pref_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        settings = data.get("extensions", {}).get("settings", {})
        for meta in settings.values():
            stored = meta.get("path")
            if not stored:
                continue
            if os.path.normcase(os.path.abspath(stored)) == ext_path:
                return True
    return False


def _get_chrome_binary() -> str:
    """Best-effort Chrome binary path for launching a debuggable browser."""
    system = platform.system()
    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError("Chrome binary not found")


def _get_chromedriver_path() -> str | None:
    """Return a usable chromedriver path without relying on Selenium Manager."""
    cached = (
        Path.home()
        / ".wdm"
        / "drivers"
        / "chromedriver"
        / "win64"
        / "146.0.7680.165"
        / "chromedriver-win32"
        / "chromedriver.exe"
    )
    if cached.is_file():
        return str(cached)

    try:
        from webdriver_manager.chrome import ChromeDriverManager

        return ChromeDriverManager().install()
    except Exception as exc:
        logger.warning("Unable to resolve chromedriver via webdriver_manager: %s", exc)
        return None


def _find_free_port() -> int:
    """Allocate a local TCP port for Chrome remote debugging."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _launch_debug_chrome(
    profile_root: str,
    profile_dir_name: str | None,
    ext_dir: str | None,
) -> tuple[subprocess.Popen, str]:
    """Launch Chrome with remote debugging and return the process + address."""
    chrome_binary = _get_chrome_binary()
    port = _find_free_port()
    args = [
        chrome_binary,
        f"--user-data-dir={profile_root}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--remote-allow-origins=*",
        "--window-size=1280,900",
        "about:blank",
    ]
    args.extend(_get_chrome_proxy_args())
    if profile_dir_name:
        args.insert(2, f"--profile-directory={profile_dir_name}")
    if ext_dir:
        args[3:3] = [
            "--enable-extensions",
            f"--load-extension={ext_dir}",
        ]

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urllib.request.urlopen(endpoint, timeout=2)
            return proc, f"127.0.0.1:{port}"
        except Exception:
            time.sleep(0.5)

    try:
        proc.terminate()
    except Exception:
        pass
    raise RuntimeError(f"Chrome debugger endpoint did not come up: {endpoint}")


def _build_isolated_user_data_dir(
    source_root: str,
    profile_dir: str | None,
) -> tuple[str, str | None]:
    """Copy the selected Chrome profile into a temp user-data-dir.

    This avoids profile lock issues when the user's real Chrome is open while
    still preserving logged-in cookies and local storage for E2E validation.
    """
    src_root = Path(source_root)
    temp_root = Path(tempfile.mkdtemp(prefix="pce-chrome-profile-"))

    # Copying Local State from a live/default Chrome profile can crash the
    # isolated Selenium browser because it carries machine/profile-level
    # feature and extension state. Leave it out unless explicitly requested.
    if os.environ.get("PCE_CHROME_PROFILE_COPY_LOCAL_STATE", "").strip() == "1":
        local_state = src_root / "Local State"
        if local_state.is_file():
            shutil.copy2(local_state, temp_root / "Local State")

    # Copy the chosen profile only. This keeps the clone relatively small and
    # avoids locking the original user-data-dir.
    profile_name = profile_dir or "Default"
    src_profile = src_root / profile_name
    dst_profile = temp_root / profile_name
    if not src_profile.is_dir():
        raise FileNotFoundError(f"Chrome profile directory not found: {src_profile}")

    ignore = shutil.ignore_patterns(
        "Cache",
        "Code Cache",
        "GPUCache",
        "Service Worker",
        "ShaderCache",
        "GrShaderCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "VideoDecodeStats",
        "blob_storage",
        "Shared Dictionary",
        "OptimizationGuidePredictionModels",
        "Safe Browsing Network",
        # Do not clone arbitrary user-installed extensions into Selenium. They
        # can crash or hijack the test browser; this fixture explicitly loads
        # only the unpacked PCE extension after the clone is built.
        "Extensions",
        "Extension State",
        "Local Extension Settings",
        "Managed Extension Settings",
        "Sync Extension Settings",
        # NOTE: Cookies are intentionally kept so clone mode preserves login
        # state. Use 'managed' mode if Cookies cause profile lock issues.
        "Sessions",
        "Session_*",
        "Tabs_*",
    )
    try:
        shutil.copytree(src_profile, dst_profile, ignore=ignore)
    except shutil.Error as exc:
        if os.environ.get("PCE_CHROME_PROFILE_COPY_STRICT", "").strip() == "1":
            raise
        logger.warning(
            "Chrome profile copy skipped locked/unavailable files and will continue: %s",
            exc,
        )
        if not dst_profile.exists():
            raise
    return str(temp_root), profile_dir


def _check_chrome_not_running(profile_root: str | None = None) -> bool:
    """Warn if Chrome is already running against the same user-data-dir."""
    if platform.system() == "Windows":
        if profile_root:
            escaped = profile_root.replace("'", "''")
            ps = (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -eq 'chrome.exe' -and "
                f"$_.CommandLine -like '*{escaped}*' }} | "
                "Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                logger.warning(
                    "Chrome is already running for profile %s; close it first.",
                    profile_root,
                )
                return False
        else:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                capture_output=True, text=True,
            )
            if "chrome.exe" in result.stdout.lower():
                logger.warning(
                    "Chrome is running! Close it first to avoid profile lock issues."
                )
                return False
    return True


@pytest.fixture(scope="session")
def driver():
    """Session-scoped Selenium WebDriver: Chrome with user profile + PCE extension."""
    debugger_address = _get_chrome_debug_address()
    profile_mode = _get_profile_mode()
    if profile_mode == "managed":
        profile_root = os.environ.get(
            "PCE_CHROME_MANAGED_PROFILE",
            str(Path.home() / ".pce" / "chrome_profile"),
        )
        Path(profile_root).mkdir(parents=True, exist_ok=True)
        profile_dir_name = None
    else:
        profile_root = os.environ.get("PCE_CHROME_PROFILE", _get_chrome_profile_dir())
        profile_dir_name = _get_profile_directory_name()
    ext_dir = _get_extension_dir()
    use_profile_copy = profile_mode == "clone" and debugger_address is None
    attach_existing = debugger_address is not None
    extension_preinstalled = False

    logger.info("Chrome user-data-dir: %s", profile_root)
    logger.info("Chrome profile directory: %s", profile_dir_name or "Default")
    logger.info("Chrome profile mode: %s", "debug" if attach_existing else profile_mode)
    logger.info("Extension: %s", ext_dir)

    if attach_existing:
        logger.info("Attaching to existing Chrome debugger: %s", debugger_address)
    elif use_profile_copy:
        profile_root, profile_dir_name = _build_isolated_user_data_dir(
            profile_root, profile_dir_name,
        )
        logger.info("Using isolated Chrome profile copy: %s", profile_root)
    else:
        if not _check_chrome_not_running(profile_root):
            raise RuntimeError(
                "Chrome must be closed before running E2E against the real profile"
            )
        extension_preinstalled = _profile_has_installed_extension(
            profile_root, profile_dir_name, ext_dir,
        )
        logger.info(
            "Profile-managed PCE extension detected: %s",
            extension_preinstalled,
        )

    driver_path = _get_chromedriver_path()
    service = Service(driver_path) if driver_path else None
    chrome_proc = None

    if attach_existing:
        options = Options()
        options.debugger_address = debugger_address
        chrome_driver = webdriver.Chrome(service=service, options=options)
    elif use_profile_copy or not _is_default_chrome_user_data_dir(profile_root):
        options = Options()
        options.add_argument(f"--user-data-dir={profile_root}")
        if profile_dir_name:
            options.add_argument(f"--profile-directory={profile_dir_name}")
        for proxy_arg in _get_chrome_proxy_args():
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

        logger.info("Launching Chrome via Selenium...")
        chrome_driver = webdriver.Chrome(service=service, options=options)
    else:
        logger.info("Launching Chrome with remote debugging against real profile...")
        chrome_proc, debugger_address = _launch_debug_chrome(
            profile_root,
            profile_dir_name,
            None if extension_preinstalled else ext_dir,
        )
        options = Options()
        options.debugger_address = debugger_address
        chrome_driver = webdriver.Chrome(service=service, options=options)

    chrome_driver.implicitly_wait(0)  # We handle waits explicitly

    logger.info(
        "Chrome launched: %s",
        chrome_driver.capabilities.get("browserVersion", "?"),
    )

    yield chrome_driver

    # Cleanup
    try:
        if attach_existing:
            # Do not close a user-managed debugging browser. Stop the local
            # chromedriver service when available and leave Chrome untouched.
            service_obj = getattr(chrome_driver, "service", None)
            if service_obj is not None:
                service_obj.stop()
        else:
            chrome_driver.quit()
    except Exception:
        pass
    if chrome_proc is not None:
        try:
            chrome_proc.terminate()
        except Exception:
            pass
    if use_profile_copy:
        try:
            shutil.rmtree(profile_root, ignore_errors=True)
        except Exception:
            pass
    logger.info("Chrome closed")
