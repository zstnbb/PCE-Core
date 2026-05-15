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
    from .tshark_wrap import (
        TsharkConfig, TsharkRunner, detect_capture_interfaces, find_tshark,
    )

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

    # Resolve interface list:
    # - explicit `--interface X` (one or more): use as-is
    # - default: auto-detect (loopback + default-route on Windows; "any" elsewhere)
    raw_ifaces = args.interface or []
    if not raw_ifaces or raw_ifaces == ["any"]:
        if sys.platform == "win32":
            interfaces = detect_capture_interfaces(tshark)
            if not interfaces:
                print("ERROR: no capture interfaces detected. Use --interface "
                      "to specify one explicitly (see `tshark -D`).",
                      file=sys.stderr)
                return 3
            logger.info("auto-detected interfaces: %s", interfaces)
        else:
            interfaces = ["any"]
    else:
        interfaces = list(raw_ifaces)

    allowlist = frozenset(ALLOWED_HOSTS)
    sink = PairingCaptureSink(host_allowlist=allowlist)

    # On TUN/VPN/Clash adapters the BPF `host <name>` filter resolves to
    # the public IP but the wire shows rewritten internal IPs (e.g.
    # 198.18.0.x). Same situation for loopback. Caller passes
    # --no-bpf-filter; Python side allowlist still constrains what lands
    # in raw_captures. Auto-mode (multi-iface incl. loopback) implies
    # no BPF unless explicitly re-enabled.
    auto_no_bpf = (
        sys.platform == "win32"
        and any("loopback" in i.lower() for i in interfaces)
    )
    bpf_hosts = frozenset() if (args.no_bpf_filter or auto_no_bpf) else allowlist
    config = TsharkConfig(
        tshark_path=tshark,
        keylog_file=keylog,
        interfaces=interfaces,
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
        "starting pce_sslkeylog daemon (tshark=%s, keylog=%s, interfaces=%s, "
        "hosts=%d, bpf=%s, duration=%s)",
        tshark, keylog, interfaces, len(allowlist),
        "off" if not bpf_hosts else f"{len(bpf_hosts)} hosts",
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


def _safe_print(s: str) -> None:
    """print(s) that survives the case where stdout's encoding can't
    represent every character (common on non-UTF-8 Windows consoles
    where the system code page is CP936/GBK)."""
    try:
        print(s)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "utf-8")
        sys.stdout.buffer.write(s.encode(enc, errors="replace") + b"\n")


def _cmd_service(args: argparse.Namespace) -> int:
    """Manage the daemon as a background service.

    Windows (Scheduled Task):
      Registers a per-user task that auto-starts at logon, runs the
      daemon, and respawns it on exit. No admin rights needed.
    POSIX (systemd user unit):
      We don't auto-install (would need root or `loginctl enable-linger`);
      we just print a unit template ready to drop into
      ``~/.config/systemd/user/pce-sslkeylog.service``.
    """
    if sys.platform == "win32":
        return _service_windows(args)
    return _service_posix(args)


_WINDOWS_TASK_XML_TEMPLATE = r"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>PCE Core — SSLKEYLOGFILE-driven A2 capture daemon. Auto-starts at user logon. See pce_sslkeylog package docstring.</Description>
    <Author>PCE Core</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user_sid}</UserId>
      <Delay>PT15S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>10</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>-m pce_sslkeylog run</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _service_windows(args: argparse.Namespace) -> int:
    import subprocess
    import tempfile

    task_name = args.task_name
    action = args.action

    if action == "uninstall":
        result = subprocess.run(
            ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"task delete failed (already gone?): {result.stderr.strip() or result.stdout.strip()}",
                  file=sys.stderr)
            return result.returncode
        print(f"scheduled task {task_name!r} removed.")
        return 0

    if action == "status":
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"scheduled task {task_name!r}: NOT REGISTERED")
            return 1
        # decode best-effort
        for enc in ("utf-8", "mbcs"):
            try:
                txt = result.stdout.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            txt = result.stdout.decode("utf-8", errors="replace")
        _safe_print(txt)
        return 0

    # install or print-unit
    # Resolve the user's SID so the LogonTrigger fires for the right user.
    try:
        sid_proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value"],
            capture_output=True, timeout=10,
        )
        user_sid = sid_proc.stdout.decode("utf-8", errors="replace").strip()
    except (subprocess.SubprocessError, OSError):
        user_sid = ""
    if not user_sid:
        # Fallback to ``schtasks`` user form (less clean but functional)
        user_sid = os.environ.get("USERNAME") or "INTERACTIVE"

    python_exe = sys.executable
    working_dir = str(Path.cwd())

    xml = _WINDOWS_TASK_XML_TEMPLATE.format(
        user_sid=user_sid,
        python_exe=python_exe,
        working_dir=working_dir,
    )
    if action == "print-unit":
        print(xml)
        return 0

    # action == "install"
    # Write XML to a temp file and feed to schtasks /Create /XML.
    # Note: Windows task XML must be UTF-16-LE BOM.
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".xml", delete=False,
    ) as tmp:
        tmp.write(b"\xff\xfe")  # UTF-16-LE BOM
        tmp.write(xml.encode("utf-16-le"))
        xml_path = tmp.name
    try:
        # /F overwrites if exists; per-user scope via /RU current user
        username = os.environ.get("USERNAME", "")
        cmd = ["schtasks.exe", "/Create", "/TN", task_name, "/XML", xml_path, "/F"]
        if username:
            cmd.extend(["/RU", username])
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        if result.returncode != 0:
            print(f"schtasks /Create failed (rc={result.returncode}):\n"
                  f"  stdout: {out.strip()}\n  stderr: {err.strip()}",
                  file=sys.stderr)
            print(
                f"\nXML left at {xml_path} for inspection. "
                "Common fix: run from an elevated shell if /RU points "
                "at SYSTEM, or re-run with --task-name to use a fresh name.",
                file=sys.stderr,
            )
            return result.returncode
        _safe_print(out.strip())
        print(
            f"\npce_sslkeylog daemon registered as scheduled task {task_name!r}.\n"
            f"  Trigger: 15s after user logon (SID={user_sid})\n"
            f"  Action:  {python_exe} -m pce_sslkeylog run\n"
            f"  Restart: every 1min on failure (up to 10 times)\n"
            f"\nVerify with: python -m pce_sslkeylog service status\n"
            f"Start now:    schtasks /Run /TN \"{task_name}\""
        )
        return 0
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            pass


_POSIX_SYSTEMD_UNIT_TEMPLATE = """[Unit]
Description=PCE Core — SSLKEYLOGFILE-driven A2 capture daemon
After=network-online.target

[Service]
ExecStart={python_exe} -m pce_sslkeylog run
Restart=on-failure
RestartSec=30s
Environment=SSLKEYLOGFILE=%h/.pce/keylog.txt
WorkingDirectory={working_dir}

[Install]
WantedBy=default.target
"""


def _service_posix(args: argparse.Namespace) -> int:
    unit = _POSIX_SYSTEMD_UNIT_TEMPLATE.format(
        python_exe=sys.executable,
        working_dir=str(Path.cwd()),
    )
    if args.action == "print-unit":
        print(unit)
        return 0
    if args.action == "install":
        unit_dir = Path.home() / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_path = unit_dir / "pce-sslkeylog.service"
        unit_path.write_text(unit)
        print(f"Wrote systemd user unit to {unit_path}.")
        print(
            "\nEnable + start:\n"
            "  systemctl --user daemon-reload\n"
            "  systemctl --user enable --now pce-sslkeylog.service\n"
            "\nMake it persist after logout:\n"
            "  loginctl enable-linger $(whoami)\n"
            "\nVerify:\n"
            "  systemctl --user status pce-sslkeylog.service"
        )
        return 0
    if args.action == "uninstall":
        unit_path = Path.home() / ".config" / "systemd" / "user" / "pce-sslkeylog.service"
        if unit_path.exists():
            unit_path.unlink()
            print(f"Removed {unit_path}. Run "
                  f"`systemctl --user daemon-reload` to forget it.")
        else:
            print(f"No unit at {unit_path} (already gone).")
        return 0
    if args.action == "status":
        import subprocess
        r = subprocess.run(
            ["systemctl", "--user", "status", "pce-sslkeylog.service"],
            capture_output=False, timeout=10,
        )
        return r.returncode
    return 2


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
    run_p.add_argument(
        "--interface", action="append", default=None,
        help="Network interface to capture from. On Windows, use the "
             "alias shown by `tshark -D` (e.g. 'WLAN', '以太网', "
             "'Adapter for loopback traffic capture'). May be passed "
             "multiple times; tshark merges streams from all listed "
             "interfaces into one output. If omitted, auto-detects "
             "loopback + default-route iface on Windows, or 'any' on POSIX.")
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

    svc_p = sub.add_parser(
        "service",
        help="Manage the daemon as a per-user background service",
        description="Install / uninstall / status of the pce_sslkeylog "
                    "daemon as a background service that auto-starts at "
                    "user logon. Windows: registers a Scheduled Task "
                    "(no admin required). POSIX: prints a systemd-user "
                    "unit template you can save to "
                    "~/.config/systemd/user/.",
    )
    svc_p.add_argument(
        "action", choices=["install", "uninstall", "status", "print-unit"],
        help="install: register the scheduled task / service. "
             "uninstall: remove it. "
             "status: show whether it's registered. "
             "print-unit: dump systemd unit template (POSIX) or task XML "
             "(Windows) without registering anything.",
    )
    svc_p.add_argument(
        "--task-name", default="PCE-SSLKEYLOG-Capture",
        help="Scheduled Task name (Windows). Default: PCE-SSLKEYLOG-Capture.",
    )

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "probe":
        return _cmd_probe(args)
    if args.cmd == "setup-env":
        return _cmd_setup_env(args)
    if args.cmd == "service":
        return _cmd_service(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
