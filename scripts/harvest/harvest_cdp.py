# SPDX-License-Identifier: Apache-2.0
"""CDP-based harvester for Cursor / Windsurf / similar Electron AI apps.

What this script does:

1. Detects the target app's executable on Windows.
2. Spawns it with ``--remote-debugging-port=<port>`` (kills any existing
   instance first because Electron multi-window doesn't share the debug
   port).
3. Connects Playwright via ``connect_over_cdp`` and hooks
   ``page.on("response")`` on every existing + future page.
4. Filters URLs by app-specific patterns and writes matched
   ``(request, response)`` pairs to ``raw_captures`` via
   ``pce_core.db.insert_capture`` (source_id = SOURCE_CDP,
   app_name = "<app>").
5. On Ctrl+C or after ``--duration`` seconds, prints stats and exits
   cleanly. The spawned app is left running unless ``--terminate-app``
   is passed (user might want to keep using it).

Why a throwaway script and not a proper ``pce_app_launcher/cursor/``
module: the harvest is one-time-per-app evidence gathering — we want
the captured fixtures FIRST, then we can write the proper launcher
modules with full knowledge of the wire format. This script borrows
heavily from ``pce_app_launcher/claude_desktop/`` but reduces it to
"detect + spawn + bridge + write" with no abstraction overhead.

Usage::

    python scripts/harvest/harvest_cdp.py --app cursor --duration 600
    python scripts/harvest/harvest_cdp.py --app windsurf --port 9224

Output: rows in ``~/.pce/data/pce.db::raw_captures`` with
``source_id='cdp-embedded'`` and ``app_name='<app>'``.

After the session, run ``verify_harvest.py --since '<iso>'`` to see
coverage.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

# Make pce_core importable when this script runs from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pce_core.db import SOURCE_CDP, insert_capture  # noqa: E402
from pce_core.redact import redact_headers  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("harvest_cdp")


# ---------------------------------------------------------------------------
# App registry — detector + URL patterns per target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppSpec:
    name: str                         # short id used in CLI + DB
    display_name: str                 # for logs
    candidate_exe_paths: list[Path]   # ordered — first match wins
    registry_display_name_globs: list[str]  # glob patterns matched against HKCU/HKLM Uninstall DisplayName
    url_patterns: list[str]           # regex strings; matched against full URL
    default_debug_port: int
    process_name: str                 # for "is already running?" check


def _find_via_registry(name_globs: list[str]) -> Optional[Path]:
    """Scan HKCU + HKLM (incl. WOW6432Node) Uninstall keys for an
    install whose DisplayName matches any of ``name_globs``. Returns
    the path to the executable inferred from ``InstallLocation`` or
    ``DisplayIcon`` if found, else ``None``.

    Prefers the entry with the largest ``DisplayVersion`` lexicographically
    when multiple match — biases toward the newer of two installs.
    """
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    candidates: list[tuple[str, str]] = []  # (display_version, exe_path)
    hkeys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    import fnmatch
    for hkey, base in hkeys:
        try:
            root = winreg.OpenKey(hkey, base)
        except OSError:
            continue
        try:
            idx = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, idx)
                except OSError:
                    break
                idx += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        try:
                            disp_name, _ = winreg.QueryValueEx(k, "DisplayName")
                        except OSError:
                            continue
                        if not any(fnmatch.fnmatch(disp_name, g) for g in name_globs):
                            continue
                        try:
                            disp_ver, _ = winreg.QueryValueEx(k, "DisplayVersion")
                        except OSError:
                            disp_ver = ""
                        # Prefer InstallLocation\<basename>.exe, fall back to DisplayIcon
                        exe_path: Optional[str] = None
                        try:
                            install_loc, _ = winreg.QueryValueEx(k, "InstallLocation")
                            if install_loc:
                                for cand_name in ("Cursor.exe", "Windsurf.exe", "Code.exe"):
                                    p = Path(install_loc) / cand_name
                                    if p.is_file():
                                        exe_path = str(p)
                                        break
                        except OSError:
                            pass
                        if not exe_path:
                            try:
                                disp_icon, _ = winreg.QueryValueEx(k, "DisplayIcon")
                                # DisplayIcon is "C:\path\to\Cursor.exe,0"
                                cand = disp_icon.split(",")[0].strip()
                                if cand and Path(cand).is_file():
                                    exe_path = cand
                            except OSError:
                                pass
                        if exe_path:
                            candidates.append((disp_ver or "", exe_path))
                except OSError:
                    continue
        finally:
            winreg.CloseKey(root)
    if not candidates:
        return None
    # Sort by version string descending (lexicographic). For semver-ish
    # strings like "0.44.9", "1.2.3" this picks the higher one.
    candidates.sort(key=lambda kv: kv[0], reverse=True)
    return Path(candidates[0][1])


def _windows_app_specs() -> dict[str, AppSpec]:
    home = Path.home()
    localappdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    programs = localappdata / "Programs"
    return {
        "cursor": AppSpec(
            name="cursor",
            display_name="Cursor",
            candidate_exe_paths=[
                programs / "cursor" / "Cursor.exe",
                programs / "Cursor" / "Cursor.exe",
                home / "AppData" / "Local" / "Programs" / "cursor" / "Cursor.exe",
                Path("E:/cursor/Cursor.exe"),  # observed install location 2026-05-09
            ],
            registry_display_name_globs=["Cursor*", "Cursor (User)*"],
            url_patterns=[
                r"^https://api2?\.cursor\.sh/.*",
                r"^https://api2?\.cursor\.com/.*",
                r"^https://www\.cursor\.com/api/.*",
                r"^https://authenticator\.cursor\.sh/.*",
                r"^https://repo42\.cursor\.sh/.*",
            ],
            default_debug_port=9223,
            process_name="Cursor.exe",
        ),
        "windsurf": AppSpec(
            name="windsurf",
            display_name="Windsurf",
            candidate_exe_paths=[
                programs / "Windsurf" / "Windsurf.exe",
                programs / "windsurf" / "Windsurf.exe",
                home / "AppData" / "Local" / "Programs" / "Windsurf" / "Windsurf.exe",
            ],
            registry_display_name_globs=["Windsurf*", "Codeium - Windsurf*"],
            url_patterns=[
                r"^https://server\.codeium\.com/.*",
                r"^https://api\.codeium\.com/.*",
                r"^https://windsurf-server\.codeium\.com/.*",
                r"^https://web-backend\.codeium\.com/.*",
            ],
            default_debug_port=9224,
            process_name="Windsurf.exe",
        ),
    }


def get_app_spec(app_id: str) -> AppSpec:
    specs = _windows_app_specs()
    if app_id not in specs:
        raise SystemExit(
            f"Unknown app '{app_id}'. Available: {sorted(specs)}"
        )
    return specs[app_id]


def find_exe(spec: AppSpec) -> Path:
    """Locate the target app's exe.

    Resolution order:
      1. Registry Uninstall keys (prefers newer version when multiple installs)
      2. Hard-coded ``candidate_exe_paths`` (heuristic)
    """
    reg_hit = _find_via_registry(spec.registry_display_name_globs)
    if reg_hit and reg_hit.is_file():
        logger.info("registry-resolved exe: %s", reg_hit)
        return reg_hit
    for cand in spec.candidate_exe_paths:
        if cand.is_file():
            return cand
    raise SystemExit(
        f"Could not find {spec.display_name} executable.\n"
        f"  Registry globs tried: {spec.registry_display_name_globs}\n"
        f"  Candidate paths tried:\n"
        + "\n".join(f"    - {p}" for p in spec.candidate_exe_paths)
        + "\nIf installed elsewhere, edit `candidate_exe_paths` in this script."
    )


# ---------------------------------------------------------------------------
# Existing-instance handling
# ---------------------------------------------------------------------------


def kill_existing(process_name: str) -> int:
    """Best-effort kill of any running instances of the target app.

    Returns count killed. Uses ``taskkill`` on Windows.
    """
    if os.name != "nt":
        return 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        if process_name.lower() not in out.stdout.lower():
            return 0
        sub = subprocess.run(
            ["taskkill", "/F", "/IM", process_name],
            capture_output=True, text=True, timeout=15,
        )
        # Count is in stdout like "SUCCESS: ..." per instance.
        return sub.stdout.count("SUCCESS")
    except Exception as exc:
        logger.warning("kill_existing failed for %s: %s", process_name, exc)
        return 0


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def pick_port(preferred: int) -> int:
    if not port_in_use(preferred):
        return preferred
    # Probe upwards.
    for cand in range(preferred + 1, preferred + 100):
        if not port_in_use(cand):
            logger.info("port %d busy, falling back to %d", preferred, cand)
            return cand
    raise RuntimeError(f"no free port near {preferred}")


# ---------------------------------------------------------------------------
# Spawn + readiness poll
# ---------------------------------------------------------------------------


def spawn_app(exe: Path, debug_port: int, proxy_server: Optional[str] = None) -> subprocess.Popen:
    cmd = [str(exe), f"--remote-debugging-port={debug_port}"]
    if proxy_server:
        # Force all Electron / Chromium / Node.js networking through mitmdump.
        # `--proxy-server` covers Chromium renderer; HTTPS_PROXY env var
        # covers any Node http(s).request the main process might use that
        # bypasses Chromium net stack.
        cmd.append(f"--proxy-server={proxy_server}")
        cmd.append("--proxy-bypass-list=<-loopback>")
        # Some Electron builds set NODE_TLS_REJECT_UNAUTHORIZED=0 internally;
        # ours doesn't. Trust mitmproxy's CA system-wide if it's installed.
    env = dict(os.environ)
    if proxy_server:
        env.setdefault("HTTPS_PROXY", f"http://{proxy_server}")
        env.setdefault("HTTP_PROXY", f"http://{proxy_server}")
    logger.info("spawning: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if os.name == "nt"
        else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_cdp_ready(port: int, timeout: float = 30.0) -> str:
    """Poll http://127.0.0.1:<port>/json/version until it responds, return endpoint URL."""
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with urlopen(f"{url}/json/version", timeout=2.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                logger.info(
                    "CDP ready: Browser=%s, V8=%s",
                    payload.get("Browser", "?"),
                    payload.get("V8-Version", "?"),
                )
                return url
        except Exception as exc:
            last_err = exc
            time.sleep(0.5)
    raise TimeoutError(
        f"CDP endpoint {url} did not become ready within {timeout}s. "
        f"Last error: {last_err!r}"
    )


# ---------------------------------------------------------------------------
# Capture bridge — single-thread Playwright (the script is fully sync)
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    matched: int = 0
    written: int = 0
    failed: int = 0
    last_url: str = ""
    seen_hosts: dict[str, int] = field(default_factory=dict)
    started_at: float = 0.0


_MAX_BODY_BYTES = 5 * 1024 * 1024
_stop_evt = threading.Event()


def _truncate(body: str) -> str:
    if body is None:
        return ""
    if len(body) > _MAX_BODY_BYTES:
        return body[:_MAX_BODY_BYTES]
    return body


def _looks_like_json(body: str) -> str:
    s = (body or "").lstrip()
    return "json" if s[:1] in ("{", "[") else "text"


def _provider_from_host(host: str) -> str:
    h = (host or "").lower()
    if "cursor" in h:
        return "cursor"
    if "codeium" in h or "windsurf" in h:
        return "codeium"
    if "githubcopilot" in h or "copilot" in h:
        return "github-copilot"
    if "openai" in h or "chatgpt" in h:
        return "openai"
    if "anthropic" in h or "claude" in h:
        return "anthropic"
    if "google" in h:
        return "google"
    return "unknown"


def _safe_headers(obj: Any) -> dict[str, str]:
    if obj is None:
        return {}
    h = getattr(obj, "headers", None)
    if h is None:
        return {}
    if callable(h):
        try:
            h = h()
        except Exception:
            return {}
    try:
        return {str(k): str(v) for k, v in dict(h).items()}
    except Exception:
        return {}


def _safe_post_data(request: Any) -> str:
    if request is None:
        return ""
    pd = getattr(request, "post_data", "") or ""
    if callable(pd):
        try:
            pd = pd() or ""
        except Exception:
            return ""
    return _truncate(str(pd or ""))


def _safe_response_body(response: Any) -> str:
    if response is None:
        return ""
    try:
        text = response.text()
    except Exception as exc:
        logger.debug("response.text() raised: %s", exc)
        return ""
    return _truncate(text or "")


def make_on_response(
    spec: AppSpec, stats: Stats, patterns: list[re.Pattern]
) -> Callable[[Any], None]:
    """Build a Playwright response-event handler closure."""

    def handler(response: Any) -> None:
        try:
            url = getattr(response, "url", "") or ""
            if not any(p.search(url) for p in patterns):
                return

            stats.matched += 1
            stats.last_url = url

            parsed = urlparse(url)
            host = parsed.netloc or ""
            path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            stats.seen_hosts[host] = stats.seen_hosts.get(host, 0) + 1

            request = getattr(response, "request", None)
            method = (getattr(request, "method", "GET") or "GET") if request else "GET"
            req_headers = _safe_headers(request)
            res_headers = _safe_headers(response)
            req_body = _safe_post_data(request)
            res_body = _safe_response_body(response)
            try:
                status_code = int(getattr(response, "status", 0) or 0)
            except Exception:
                status_code = 0
            provider = _provider_from_host(host)

            pair_id = uuid.uuid4().hex
            meta = {
                "harvest.app": spec.name,
                "harvest.url": url,
            }
            meta_json = json.dumps(meta, ensure_ascii=False, sort_keys=True)

            # Request row.
            req_id = insert_capture(
                direction="request",
                pair_id=pair_id,
                host=host,
                path=path,
                method=method,
                provider=provider,
                headers_redacted_json=json.dumps(
                    redact_headers(req_headers), ensure_ascii=False
                ),
                body_text_or_json=req_body,
                body_format=_looks_like_json(req_body),
                source_id=SOURCE_CDP,
                app_name=spec.name,
                meta_json=meta_json,
            )

            # Response row.
            res_id = insert_capture(
                direction="response",
                pair_id=pair_id,
                host=host,
                path=path,
                method=method,
                provider=provider,
                status_code=status_code,
                headers_redacted_json=json.dumps(
                    redact_headers(res_headers), ensure_ascii=False
                ),
                body_text_or_json=res_body,
                body_format=_looks_like_json(res_body),
                source_id=SOURCE_CDP,
                app_name=spec.name,
                meta_json=meta_json,
            )

            if req_id and res_id:
                stats.written += 1
                logger.info(
                    "captured #%d: %s %s (%d) req=%d resp=%d",
                    stats.written, method, url[:80], status_code,
                    len(req_body), len(res_body),
                )
            else:
                stats.failed += 1
                logger.warning("insert_capture returned None (req=%s, res=%s)", req_id, res_id)
        except Exception:
            stats.failed += 1
            logger.exception("response handler crashed")

    return handler


def wire_page(page: Any, handler: Callable[[Any], None]) -> None:
    try:
        page.on("response", handler)
    except Exception:
        logger.exception("wire_page failed")


def wire_context(ctx: Any, handler: Callable[[Any], None]) -> None:
    try:
        for p in ctx.pages:
            wire_page(p, handler)
        ctx.on("page", lambda p: wire_page(p, handler))
    except Exception:
        logger.exception("wire_context failed")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--app",
        required=True,
        choices=sorted(_windows_app_specs().keys()),
        help="Target app to harvest.",
    )
    p.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Max harvest duration in seconds (default 600 = 10 min). Ctrl+C exits early.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="CDP debug port. Default = app-specific (Cursor=9223, Windsurf=9224).",
    )
    p.add_argument(
        "--no-kill-existing",
        action="store_true",
        help="Don't taskkill existing app instances. Default is to kill so the new instance gets a clean debug port.",
    )
    p.add_argument(
        "--terminate-app",
        action="store_true",
        help="Terminate the spawned app when the harvest ends (default leaves it running so you can keep using it).",
    )
    p.add_argument(
        "--proxy-server",
        default="127.0.0.1:8080",
        help="HTTPS proxy to force the spawned app through. Pass empty string to skip "
             "(default 127.0.0.1:8080 = mitmdump). Critical for Cursor / Windsurf because their main "
             "process Node networking otherwise bypasses the system proxy via Clash fake-IP.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if os.name != "nt":
        logger.error("This harvester only supports Windows currently.")
        return 2

    spec = get_app_spec(args.app)
    debug_port = args.port or spec.default_debug_port

    # --- detect ---
    exe = find_exe(spec)
    logger.info("found %s at %s", spec.display_name, exe)

    # --- kill existing ---
    if not args.no_kill_existing:
        n = kill_existing(spec.process_name)
        if n:
            logger.info("killed %d existing %s instance(s)", n, spec.process_name)
            time.sleep(1.5)  # let OS reap
        else:
            logger.info("no existing %s instances to kill", spec.process_name)

    # --- pick port ---
    debug_port = pick_port(debug_port)

    # --- spawn ---
    proxy_server = args.proxy_server.strip() or None
    proc = spawn_app(exe, debug_port, proxy_server=proxy_server)
    logger.info(
        "spawned PID=%d, debug_port=%d, proxy_server=%s",
        proc.pid, debug_port, proxy_server or "(none)",
    )
    time.sleep(0.5)  # let process init

    # --- wait CDP ready ---
    try:
        endpoint = wait_cdp_ready(debug_port, timeout=30.0)
    except TimeoutError as exc:
        logger.error("CDP not ready: %s", exc)
        if not args.no_kill_existing:
            try:
                proc.terminate()
            except Exception:
                pass
        return 3

    # --- Playwright connect ---
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.error(
            "Playwright not installed. Run: pip install playwright && python -m playwright install chromium"
        )
        return 4

    stats = Stats(started_at=time.time())
    patterns = [re.compile(p) for p in spec.url_patterns]
    on_response = make_on_response(spec, stats, patterns)

    # SIGINT handler — set the stop event so the main loop can clean up.
    def _on_sigint(signum, frame):
        logger.info("Ctrl+C received — finishing up...")
        _stop_evt.set()
    signal.signal(signal.SIGINT, _on_sigint)

    logger.info(
        "Attaching Playwright to %s — patterns:\n%s",
        endpoint,
        "\n".join(f"  {p}" for p in spec.url_patterns),
    )
    logger.info(
        "Now use %s normally. Press Ctrl+C when done, or wait %ds.",
        spec.display_name, args.duration,
    )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(endpoint)
            for ctx in browser.contexts:
                wire_context(ctx, on_response)
            browser.on("context", lambda c: wire_context(c, on_response))

            deadline = time.time() + args.duration
            while not _stop_evt.is_set() and time.time() < deadline:
                _stop_evt.wait(timeout=2.0)
                # Periodic heartbeat so user sees progress.
                if int((time.time() - stats.started_at)) % 30 == 0:
                    logger.info(
                        "alive — matched=%d written=%d failed=%d hosts=%s",
                        stats.matched, stats.written, stats.failed,
                        list(stats.seen_hosts.items())[:5],
                    )

            try:
                browser.close()
            except Exception:
                pass
    except Exception:
        logger.exception("Playwright session crashed")

    # --- final stats ---
    elapsed = time.time() - stats.started_at
    logger.info("=" * 60)
    logger.info("HARVEST COMPLETE for %s", spec.display_name)
    logger.info("  duration:      %.1f s", elapsed)
    logger.info("  matched:       %d", stats.matched)
    logger.info("  written:       %d (pairs)", stats.written)
    logger.info("  failed:        %d", stats.failed)
    logger.info("  hosts:")
    for host, n in sorted(stats.seen_hosts.items(), key=lambda kv: -kv[1]):
        logger.info("    %-40s %d", host, n)
    logger.info("=" * 60)
    logger.info("Captures saved to ~/.pce/data/pce.db (source_id=%s, app_name=%s)",
                SOURCE_CDP, spec.name)
    logger.info("Next: python scripts/harvest/verify_harvest.py --since '<iso>'")

    if args.terminate_app:
        try:
            logger.info("terminating spawned app PID=%d", proc.pid)
            proc.terminate()
            proc.wait(timeout=5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    else:
        logger.info("Leaving %s running (PID=%d). Close it manually when done.",
                    spec.display_name, proc.pid)

    return 0


if __name__ == "__main__":
    sys.exit(main())
