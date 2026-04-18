# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Electron App Auto-Proxy for AI Capture.

Provides utilities to route AI API traffic from Electron-based desktop apps
(Cursor, VS Code + Copilot, Windsurf, etc.) through the PCE mitmproxy.

Two approaches:
1. **Launcher mode**: Start an Electron app with proxy env vars pre-set.
2. **System proxy mode**: Generate platform-specific commands to configure
   the system proxy so ALL Electron apps route through PCE automatically.

Electron respects these env vars:
- HTTPS_PROXY / HTTP_PROXY  → routes traffic through proxy
- NODE_TLS_REJECT_UNAUTHORIZED=0  → accept PCE's CA cert (dev only)
- NODE_EXTRA_CA_CERTS=<path>  → trust PCE CA cert (production)

Usage:
    # Launch Cursor through PCE proxy
    python -m pce_core.electron_proxy launch cursor

    # Show env vars to set manually
    python -m pce_core.electron_proxy env

    # List detected Electron AI apps
    python -m pce_core.electron_proxy detect
"""

import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import PROXY_LISTEN_HOST, PROXY_LISTEN_PORT

logger = logging.getLogger("pce.electron_proxy")

# ---------------------------------------------------------------------------
# Known Electron AI apps and their typical install locations
# ---------------------------------------------------------------------------

@dataclass
class ElectronApp:
    name: str
    display_name: str
    exe_names: list[str]          # possible executable names
    win_paths: list[str]          # Windows install paths (relative to user home or absolute)
    mac_paths: list[str]          # macOS app bundle paths
    linux_paths: list[str]        # Linux executable paths
    ai_domains: list[str]         # AI API domains this app calls


KNOWN_APPS: list[ElectronApp] = [
    ElectronApp(
        name="cursor",
        display_name="Cursor",
        exe_names=["Cursor.exe", "cursor"],
        win_paths=[
            r"AppData\Local\Programs\cursor\Cursor.exe",
            r"AppData\Local\cursor\Cursor.exe",
        ],
        mac_paths=["/Applications/Cursor.app/Contents/MacOS/Cursor"],
        linux_paths=["/usr/bin/cursor", "/opt/cursor/cursor"],
        ai_domains=["api2.cursor.sh", "api.openai.com", "api.anthropic.com"],
    ),
    ElectronApp(
        name="vscode",
        display_name="VS Code",
        exe_names=["Code.exe", "code"],
        win_paths=[
            r"AppData\Local\Programs\Microsoft VS Code\Code.exe",
            r"Microsoft VS Code\Code.exe",
        ],
        mac_paths=["/Applications/Visual Studio Code.app/Contents/MacOS/Electron"],
        linux_paths=["/usr/bin/code", "/usr/share/code/code"],
        ai_domains=["copilot-proxy.githubusercontent.com", "api.githubcopilot.com"],
    ),
    ElectronApp(
        name="windsurf",
        display_name="Windsurf",
        exe_names=["Windsurf.exe", "windsurf"],
        win_paths=[
            r"AppData\Local\Programs\windsurf\Windsurf.exe",
        ],
        mac_paths=["/Applications/Windsurf.app/Contents/MacOS/Electron"],
        linux_paths=["/usr/bin/windsurf"],
        ai_domains=["api.codeium.com", "api.openai.com", "api.anthropic.com"],
    ),
    ElectronApp(
        name="claude-desktop",
        display_name="Claude Desktop",
        exe_names=["Claude.exe", "claude"],
        win_paths=[
            r"AppData\Local\Programs\claude-desktop\Claude.exe",
            r"AppData\Local\AnthropicClaude\Claude.exe",
        ],
        mac_paths=["/Applications/Claude.app/Contents/MacOS/Claude"],
        linux_paths=[],
        ai_domains=["api.anthropic.com", "claude.ai"],
    ),
    # ── P5.A-2: Tier-1 subscription targets (UCS §10.1) ────────────────
    # ChatGPT Desktop ships as an Electron bundle on Windows/macOS; Linux
    # is unsupported. Its background WebView hits the same ``chatgpt.com``
    # endpoints as the browser version, so the proxy allowlist already
    # covers it (see pce_core/config.py::ALLOWED_HOSTS).
    ElectronApp(
        name="chatgpt-desktop",
        display_name="ChatGPT Desktop",
        exe_names=["ChatGPT.exe", "ChatGPT"],
        win_paths=[
            r"AppData\Local\Programs\ChatGPT\ChatGPT.exe",
            r"AppData\Local\OpenAI\ChatGPT\ChatGPT.exe",
        ],
        mac_paths=["/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"],
        linux_paths=[],
        ai_domains=["chatgpt.com", "chat.openai.com", "api.openai.com"],
    ),
    # Codex CLI is a Node.js binary installed via ``npm i -g @openai/codex``.
    # It isn't Electron, but the KNOWN_APPS launcher works for any child
    # process since it just exports HTTP(S)_PROXY + NODE_EXTRA_CA_CERTS
    # before exec — which is exactly what a Node CLI needs. The install
    # paths below cover the two npm global prefixes seen in the wild;
    # ``exe_names`` is sufficient for the PATH-based detection fallback.
    ElectronApp(
        name="codex-cli",
        display_name="Codex CLI",
        exe_names=["codex.cmd", "codex.exe", "codex"],
        win_paths=[
            r"AppData\Roaming\npm\codex.cmd",
            r"AppData\Local\Programs\nodejs\codex.cmd",
        ],
        mac_paths=[
            "/usr/local/bin/codex",
            "/opt/homebrew/bin/codex",
        ],
        linux_paths=["/usr/bin/codex", "/usr/local/bin/codex"],
        ai_domains=["api.openai.com"],
    ),
]


# ---------------------------------------------------------------------------
# Proxy environment helpers
# ---------------------------------------------------------------------------

def get_proxy_url() -> str:
    """Return the PCE proxy URL."""
    return f"http://{PROXY_LISTEN_HOST}:{PROXY_LISTEN_PORT}"


def get_ca_cert_path() -> Optional[Path]:
    """Find the mitmproxy CA certificate path."""
    mitmproxy_dir = Path.home() / ".mitmproxy"
    cert_path = mitmproxy_dir / "mitmproxy-ca-cert.pem"
    if cert_path.exists():
        return cert_path
    # Also check common alternative locations
    for alt in [
        Path.home() / ".pce" / "ca-cert.pem",
        Path("/usr/local/share/ca-certificates/mitmproxy-ca-cert.crt"),
    ]:
        if alt.exists():
            return alt
    return None


def get_proxy_env(trust_mode: str = "ca_cert") -> dict[str, str]:
    """Return environment variables to proxy Electron apps through PCE.

    Args:
        trust_mode: "ca_cert" (use CA cert) or "insecure" (disable TLS verification)
    """
    proxy_url = get_proxy_url()
    env = {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
    }

    if trust_mode == "insecure":
        env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
    else:
        ca_path = get_ca_cert_path()
        if ca_path:
            env["NODE_EXTRA_CA_CERTS"] = str(ca_path)
        else:
            logger.warning(
                "mitmproxy CA cert not found. Set NODE_TLS_REJECT_UNAUTHORIZED=0 "
                "or install the CA cert first: mitmproxy --set confdir=~/.mitmproxy"
            )
            env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"

    return env


# ---------------------------------------------------------------------------
# App detection
# ---------------------------------------------------------------------------

def detect_installed_apps() -> list[dict]:
    """Detect which known Electron AI apps are installed."""
    system = platform.system()
    home = Path.home()
    results = []

    for app in KNOWN_APPS:
        found_path = None

        if system == "Windows":
            for rel_path in app.win_paths:
                full = home / rel_path
                if full.exists():
                    found_path = str(full)
                    break
            if not found_path:
                for name in app.exe_names:
                    which = shutil.which(name)
                    if which:
                        found_path = which
                        break

        elif system == "Darwin":
            for p in app.mac_paths:
                if Path(p).exists():
                    found_path = p
                    break

        elif system == "Linux":
            for p in app.linux_paths:
                if Path(p).exists():
                    found_path = p
                    break
            if not found_path:
                for name in app.exe_names:
                    which = shutil.which(name)
                    if which:
                        found_path = which
                        break

        if found_path:
            results.append({
                "name": app.name,
                "display_name": app.display_name,
                "path": found_path,
                "ai_domains": app.ai_domains,
            })

    return results


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

def launch_app(
    app_name: str,
    extra_args: Optional[list[str]] = None,
    trust_mode: str = "ca_cert",
) -> Optional[subprocess.Popen]:
    """Launch an Electron app with PCE proxy environment variables.

    Args:
        app_name: Name of the app (e.g. "cursor", "vscode")
        extra_args: Additional command-line arguments
        trust_mode: "ca_cert" or "insecure"

    Returns:
        The Popen process object, or None if app not found.
    """
    installed = detect_installed_apps()
    target = None
    for info in installed:
        if info["name"] == app_name:
            target = info
            break

    if not target:
        logger.error("App '%s' not found. Installed apps: %s",
                      app_name, [a["name"] for a in installed])
        return None

    exe_path = target["path"]
    proxy_env = get_proxy_env(trust_mode)

    # Merge with current environment
    env = dict(os.environ)
    env.update(proxy_env)

    cmd = [exe_path] + (extra_args or [])
    logger.info("Launching %s through PCE proxy: %s", target["display_name"], " ".join(cmd))
    logger.info("Proxy env: %s", {k: v for k, v in proxy_env.items() if k.isupper()})

    try:
        proc = subprocess.Popen(cmd, env=env)
        logger.info("Started %s (PID %d)", target["display_name"], proc.pid)
        return proc
    except Exception as e:
        logger.error("Failed to launch %s: %s", target["display_name"], e)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for electron_proxy module."""
    import argparse

    parser = argparse.ArgumentParser(
        description="PCE Electron App Auto-Proxy",
        prog="python -m pce_core.electron_proxy",
    )
    sub = parser.add_subparsers(dest="command")

    # detect
    sub.add_parser("detect", help="List detected Electron AI apps")

    # env
    env_parser = sub.add_parser("env", help="Print proxy environment variables")
    env_parser.add_argument("--insecure", action="store_true", help="Use insecure TLS mode")

    # launch
    launch_parser = sub.add_parser("launch", help="Launch an app through PCE proxy")
    launch_parser.add_argument("app", help="App name (cursor, vscode, windsurf, claude-desktop)")
    launch_parser.add_argument("--insecure", action="store_true", help="Use insecure TLS mode")
    launch_parser.add_argument("args", nargs="*", help="Extra args to pass to the app")

    args = parser.parse_args()

    if args.command == "detect":
        apps = detect_installed_apps()
        if not apps:
            print("No known Electron AI apps detected.")
        else:
            print(f"Found {len(apps)} Electron AI app(s):\n")
            for a in apps:
                print(f"  {a['display_name']}")
                print(f"    Path: {a['path']}")
                print(f"    AI domains: {', '.join(a['ai_domains'])}")
                print()

    elif args.command == "env":
        trust = "insecure" if args.insecure else "ca_cert"
        env = get_proxy_env(trust)
        print("# Add these to your shell profile or run before launching Electron apps:")
        print()
        system = platform.system()
        for k, v in env.items():
            if not k.isupper():
                continue
            if system == "Windows":
                print(f'$env:{k}="{v}"')
            else:
                print(f'export {k}="{v}"')
        print()
        ca = get_ca_cert_path()
        if ca:
            print(f"# CA certificate: {ca}")
        else:
            print("# WARNING: mitmproxy CA cert not found. Run mitmproxy once to generate it.")

    elif args.command == "launch":
        trust = "insecure" if args.insecure else "ca_cert"
        proc = launch_app(args.app, args.args or None, trust)
        if proc:
            print(f"Launched {args.app} (PID {proc.pid}) through PCE proxy")
        else:
            print(f"Failed to launch {args.app}")
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
