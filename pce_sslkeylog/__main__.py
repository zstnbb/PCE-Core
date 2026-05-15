# SPDX-License-Identifier: Apache-2.0
"""pce_sslkeylog — CLI entry point.

Usage::

    python -m pce_sslkeylog run                  # start the daemon
    python -m pce_sslkeylog probe                # check tshark + keylog file
    python -m pce_sslkeylog setup-env [--user|--machine]   # set SSLKEYLOGFILE

Exit codes:
- 0   success
- 2   usage error
- 3   tshark not found / not runnable
- 4   keylog file env var not set, refused to start
- 5   stopped by SIGINT
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pce.sslkeylog")


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )


def _resolve_keylog_path(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env_val = os.environ.get("SSLKEYLOGFILE")
    if env_val:
        return Path(env_val).expanduser().resolve()
    # Default: %LOCALAPPDATA%\pce\keylog.txt on Windows, ~/.pce/keylog.txt elsewhere
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "pce" / "keylog.txt"
    return Path.home() / ".pce" / "keylog.txt"


def _cmd_probe(args: argparse.Namespace) -> int:
    from .tshark_wrap import find_tshark, tshark_version

    tshark = find_tshark()
    keylog = _resolve_keylog_path(args.keylog)

    print(f"tshark binary:        {tshark or '(NOT FOUND — install Wireshark)'}")
    if tshark:
        print(f"tshark version:       {tshark_version(tshark) or '(unknown)'}")
    print(f"SSLKEYLOGFILE env:    {os.environ.get('SSLKEYLOGFILE') or '(not set)'}")
    print(f"keylog file path:     {keylog}")
    print(f"keylog file exists:   {keylog.is_file()}")
    if keylog.is_file():
        print(f"keylog file size:     {keylog.stat().st_size} bytes")
        # Last 3 labels in the file (helps verify Chromium is writing)
        with keylog.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-3:]
        for line in lines:
            label = line.split(" ", 1)[0] if line else ""
            print(f"  recent label: {label}")
    if not tshark:
        return 3
    if not args.allow_no_keylog and not keylog.is_file():
        print(
            "\nERROR: keylog file does not exist yet. Either:\n"
            "  - Set SSLKEYLOGFILE env var via `python -m pce_sslkeylog setup-env`\n"
            "    and restart a Chromium-based app, OR\n"
            "  - Pass --allow-no-keylog to skip this check.\n",
            file=sys.stderr,
        )
        return 4
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from pce_core.config import ALLOWED_HOSTS
    from pce_core.db import init_db
    from .capture import PairingCaptureSink
    from .tshark_wrap import TsharkConfig, TsharkRunner, find_tshark

    tshark = find_tshark()
    if tshark is None:
        print("ERROR: tshark not found. Install Wireshark.", file=sys.stderr)
        return 3
    keylog = _resolve_keylog_path(args.keylog)
    if not keylog.is_file():
        print(
            f"ERROR: keylog file {keylog} not present. Run setup-env + restart "
            f"a Chromium-based app first.",
            file=sys.stderr,
        )
        return 4

    init_db()  # ensure schema + sources up-to-date

    allowlist = frozenset(ALLOWED_HOSTS)
    sink = PairingCaptureSink(host_allowlist=allowlist)

    # On TUN/VPN/Clash adapters the BPF `host <name>` filter resolves to
    # the public IP but the wire shows rewritten internal IPs (e.g.
    # 198.18.0.x). Caller passes --no-bpf-filter; Python side allowlist
    # still constrains what lands in raw_captures.
    bpf_hosts = frozenset() if args.no_bpf_filter else allowlist
    config = TsharkConfig(
        tshark_path=tshark,
        keylog_file=keylog,
        interface=args.interface,
        allowed_hosts=bpf_hosts,
    )
    runner = TsharkRunner(config, on_line=sink.handle_line)

    stopping = False

    def _on_sigint(signum, frame):
        nonlocal stopping
        stopping = True
        logger.info("SIGINT received, stopping...")
        runner.stop(timeout=5.0)

    signal.signal(signal.SIGINT, _on_sigint)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _on_sigint)

    duration = float(getattr(args, "duration", 0.0) or 0.0)
    logger.info(
        "starting pce_sslkeylog daemon (tshark=%s, keylog=%s, interface=%s, "
        "hosts=%d, duration=%s)",
        tshark, keylog, args.interface, len(allowlist),
        f"{duration}s" if duration > 0 else "until SIGINT",
    )
    runner.start()
    start_ts = time.time()
    try:
        # Heartbeat / stats loop
        last_log = start_ts
        while not stopping and runner.running:
            time.sleep(1.0)
            now = time.time()
            if duration > 0 and (now - start_ts) >= duration:
                logger.info("duration %.1fs elapsed, stopping", duration)
                break
            if now - last_log >= 30.0:
                s = sink.stats
                logger.info(
                    "stats: lines=%d parsed=%d events=%d pairs=%d orphans=%d errors=%d",
                    s.lines_total, s.lines_parsed, s.events_total,
                    s.pairs_emitted, s.orphans_emitted, s.insert_errors,
                )
                last_log = now
    except KeyboardInterrupt:
        stopping = True
    finally:
        runner.stop(timeout=5.0)
        # Final stats line so smoke-test results show up even without 30s elapsed
        s = sink.stats
        logger.info(
            "final stats: lines=%d parsed=%d events=%d pairs=%d orphans=%d errors=%d",
            s.lines_total, s.lines_parsed, s.events_total,
            s.pairs_emitted, s.orphans_emitted, s.insert_errors,
        )
    return 5 if stopping else 0


def _cmd_setup_env(args: argparse.Namespace) -> int:
    """Set the SSLKEYLOGFILE env var (user or machine scope on Windows)."""
    keylog = _resolve_keylog_path(args.keylog)
    keylog.parent.mkdir(parents=True, exist_ok=True)
    # Touch the file so tshark probe doesn't fail on first run
    if not keylog.exists():
        keylog.touch()
    if sys.platform == "win32":
        scope = "Machine" if args.machine else "User"
        # PowerShell -Command for setEnvironmentVariable
        import subprocess
        ps_cmd = (
            f"[Environment]::SetEnvironmentVariable("
            f"'SSLKEYLOGFILE','{keylog}','{scope}')"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"ERROR: setEnvironmentVariable failed:\n{result.stderr}",
                  file=sys.stderr)
            return 1
        print(f"SSLKEYLOGFILE set in {scope} scope to: {keylog}")
        print(
            "\nIMPORTANT: this env var is read by Chromium at process start.\n"
            "  Restart any open Chromium-based AI apps (Chrome / Edge / Claude /\n"
            "  Cursor / Windsurf / VS Code / Electron AI apps) so they pick up\n"
            "  the new value. New processes will start writing TLS session\n"
            "  keys to this file.\n"
        )
    else:
        print(
            "On POSIX, add the following to your shell rc file (~/.bashrc, etc):\n"
            f"  export SSLKEYLOGFILE='{keylog}'\n"
            "Then restart your Chromium-based apps."
        )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pce-sslkeylog",
                                     description="A2 SSLKEYLOGFILE capture daemon")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--keylog", default=None,
                        help="Override the SSLKEYLOGFILE path "
                             "(defaults to $SSLKEYLOGFILE or "
                             "%%LOCALAPPDATA%%\\pce\\keylog.txt)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Start the capture daemon")
    run_p.add_argument("--interface", default="any",
                       help="Network interface (default: any). On Windows "
                            "use the alias from `tshark -D` (e.g. WLAN). "
                            "Pass multiple times to capture from several.")
    run_p.add_argument("--duration", type=float, default=0.0,
                       help="Auto-stop after N seconds (0 = run until "
                            "SIGINT; useful for smoke tests).")
    run_p.add_argument(
        "--no-bpf-filter", action="store_true",
        help="Skip the BPF host filter. Use this when capturing on a "
             "TUN/VPN/Clash adapter where IP addresses are rewritten so "
             "BPF `host <name>` resolves to the wrong addresses. Python "
             "side `host_allowlist` still applies post-decryption.",
    )

    probe_p = sub.add_parser("probe", help="Check tshark + keylog availability")
    probe_p.add_argument("--allow-no-keylog", action="store_true",
                         help="Don't fail if keylog file is missing")

    setup_p = sub.add_parser("setup-env", help="Set SSLKEYLOGFILE env var")
    setup_p.add_argument("--machine", action="store_true",
                         help="Set machine-wide (requires admin); "
                              "default is user-scope")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "probe":
        return _cmd_probe(args)
    if args.cmd == "setup-env":
        return _cmd_setup_env(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
