# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper.relay – spawn a child CLI shim under stdio tee.

The relay is the in-band capture engine. It is invoked by the
generated wrapper shim files (``install.py``) when the user types the
shim's command name (e.g. ``claude``) on their shell. The relay's
contract is::

    parent argv + parent stdin
            │
            ▼
    ┌─────────────────────────────────────┐
    │ pce_cli_wrapper.relay.relay()       │
    │  ├─ spawn real shim with same argv  │
    │  ├─ tee stdin → child / capture     │
    │  ├─ tee child stdout → parent / cap │
    │  ├─ tee child stderr → parent / cap │
    │  └─ on exit: build RelayResult      │
    └─────────────────────────────────────┘
            │
            ├─ exit code  → propagated to parent shell
            └─ RelayResult → CliWrapperObserver → pce_core.db

Two operating modes (auto-selected from ``sys.stdin.isatty()``):

- **Pipe tee mode** (non-TTY parent stdin): stdin / stdout / stderr
  all run through ``subprocess.PIPE`` and three pump threads. Full
  byte-for-byte capture with a per-stream byte cap.

- **TTY passthrough mode** (parent stdin is a terminal — interactive
  REPL): we hand the child the parent's stdin / stdout / stderr file
  descriptors directly, so terminal control codes and colour stay
  intact. We capture *only* args + duration + exit code; the
  transcript is not recorded. ``RelayResult.tty_passthrough = True``.

This split is intentional: a v0 relay that tries to interpose a PTY
between a real terminal and an interactive Claude Code REPL would
either drop input characters or break colour rendering. Capturing the
metadata-only summary in TTY mode is honest and never breaks the user.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO, Optional

from .capture import CliWrapperObserver, RelayResult
from .discovery import discover

logger = logging.getLogger("pce.cli_wrapper.relay")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def relay(
    *,
    target_path: Path,
    child_args: list[str],
    target_id: Optional[str] = None,
    capture_label: Optional[str] = None,
    max_body_bytes: int = 1_000_000,
    timeout_s: Optional[float] = None,
    db_path: Optional[Path] = None,
    dry_run: bool = False,
    parent_stdin: Optional[BinaryIO] = None,
    parent_stdout: Optional[BinaryIO] = None,
    parent_stderr: Optional[BinaryIO] = None,
) -> int:
    """Run ``target_path child_args``, mirror its stdio, write a capture row.

    Returns the child's exit code so the caller (typically a Windows
    ``.cmd`` / ``.ps1`` shim or a POSIX shell wrapper) can ``exit
    $LASTEXITCODE`` and not change the user-observable behaviour of
    the wrapped command.

    All stdio mirroring runs through binary file objects so byte-exact
    fidelity is preserved (no UTF-8 round-tripping, no newline
    translation). Decoding for storage happens in
    ``capture._encode_stream``, not here.
    """
    if not target_path.exists():
        # The wrapper shim should never have been invoked if the real
        # shim went away mid-session, but if it did, fail soft and pass
        # through ENOENT to the parent.
        msg = f"pce-cli-wrapper: target shim missing: {target_path}\n"
        (parent_stderr or sys.stderr.buffer).write(msg.encode("utf-8"))
        return 127

    # Fall back to discovery if caller didn't supply target_id /
    # capture_label / version metadata.
    target_id, provider, version = _augment_target_meta(target_path, target_id)

    parent_stdin = parent_stdin or _binary(sys.stdin)
    parent_stdout = parent_stdout or _binary(sys.stdout)
    parent_stderr = parent_stderr or _binary(sys.stderr)

    is_tty = _stdin_is_tty(parent_stdin)
    started_at_ns = time.time_ns()

    if is_tty:
        result = _run_passthrough(
            target_path=target_path,
            child_args=child_args,
            target_id=target_id,
            provider=provider,
            target_version=version,
            capture_label=capture_label,
            timeout_s=timeout_s,
            started_at_ns=started_at_ns,
        )
    else:
        result = _run_pipe_tee(
            target_path=target_path,
            child_args=child_args,
            target_id=target_id,
            provider=provider,
            target_version=version,
            capture_label=capture_label,
            max_body_bytes=max_body_bytes,
            timeout_s=timeout_s,
            started_at_ns=started_at_ns,
            parent_stdin=parent_stdin,
            parent_stdout=parent_stdout,
            parent_stderr=parent_stderr,
        )

    # Best-effort capture write — never fail the user's exit code.
    try:
        observer = CliWrapperObserver(db_path=db_path, dry_run=dry_run)
        observer.emit(result)
    except Exception as exc:  # pragma: no cover — defensive guard
        logger.warning("relay observer write skipped: %s", exc)

    return result.exit_code


# ---------------------------------------------------------------------------
# Pipe tee mode
# ---------------------------------------------------------------------------


def _run_pipe_tee(
    *,
    target_path: Path,
    child_args: list[str],
    target_id: str,
    provider: str,
    target_version: Optional[str],
    capture_label: Optional[str],
    max_body_bytes: int,
    timeout_s: Optional[float],
    started_at_ns: int,
    parent_stdin: BinaryIO,
    parent_stdout: BinaryIO,
    parent_stderr: BinaryIO,
) -> RelayResult:
    cmd = _build_command(target_path, child_args)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # On Windows .cmd / .ps1 shims need the shell semantics of CreateProcess
        # via the shell=False + explicit cmd.exe path baked into _build_command.
        shell=False,
        bufsize=0,
        cwd=os.getcwd(),
    )

    stdin_buf = _CapBuffer(max_body_bytes)
    stdout_buf = _CapBuffer(max_body_bytes)
    stderr_buf = _CapBuffer(max_body_bytes)

    threads = [
        threading.Thread(
            target=_pump_stdin,
            name="pce-cli-wrapper-stdin",
            args=(parent_stdin, proc.stdin, stdin_buf),
            daemon=True,
        ),
        threading.Thread(
            target=_pump_stream,
            name="pce-cli-wrapper-stdout",
            args=(proc.stdout, parent_stdout, stdout_buf),
            daemon=True,
        ),
        threading.Thread(
            target=_pump_stream,
            name="pce-cli-wrapper-stderr",
            args=(proc.stderr, parent_stderr, stderr_buf),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_proc(proc)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Output threads may still have buffered bytes — give them a moment
    # to drain after the child exits.
    for t in threads:
        t.join(timeout=2.0)

    # Flush the parent stdout/stderr so the user sees output before the
    # process control returns to the shell prompt.
    try:
        parent_stdout.flush()
    except Exception:  # pragma: no cover — defensive
        pass
    try:
        parent_stderr.flush()
    except Exception:  # pragma: no cover — defensive
        pass

    finished_at_ns = time.time_ns()
    return RelayResult(
        target_id=target_id,
        command_name=target_path.stem,
        provider=provider,
        target_path=target_path,
        args=list(child_args),
        cwd=Path(os.getcwd()),
        started_at_ns=started_at_ns,
        finished_at_ns=finished_at_ns,
        exit_code=int(proc.returncode if proc.returncode is not None else -1),
        timed_out=timed_out,
        tty_passthrough=False,
        stdin_bytes=stdin_buf.value(),
        stdout_bytes=stdout_buf.value(),
        stderr_bytes=stderr_buf.value(),
        stdin_truncated=stdin_buf.truncated,
        stdout_truncated=stdout_buf.truncated,
        stderr_truncated=stderr_buf.truncated,
        target_version=target_version,
        capture_label=capture_label,
    )


# ---------------------------------------------------------------------------
# TTY passthrough mode
# ---------------------------------------------------------------------------


def _run_passthrough(
    *,
    target_path: Path,
    child_args: list[str],
    target_id: str,
    provider: str,
    target_version: Optional[str],
    capture_label: Optional[str],
    timeout_s: Optional[float],
    started_at_ns: int,
) -> RelayResult:
    cmd = _build_command(target_path, child_args)

    proc = subprocess.Popen(
        cmd,
        # None ⇒ inherit parent's stdin/stdout/stderr fds verbatim.
        stdin=None,
        stdout=None,
        stderr=None,
        shell=False,
        cwd=os.getcwd(),
    )

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_proc(proc)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    finished_at_ns = time.time_ns()
    return RelayResult(
        target_id=target_id,
        command_name=target_path.stem,
        provider=provider,
        target_path=target_path,
        args=list(child_args),
        cwd=Path(os.getcwd()),
        started_at_ns=started_at_ns,
        finished_at_ns=finished_at_ns,
        exit_code=int(proc.returncode if proc.returncode is not None else -1),
        timed_out=timed_out,
        tty_passthrough=True,
        target_version=target_version,
        capture_label=capture_label,
    )


# ---------------------------------------------------------------------------
# Helpers — command construction
# ---------------------------------------------------------------------------


def _build_command(target_path: Path, child_args: list[str]) -> list[str]:
    """Compose the argv list for ``Popen`` from a shim path and child args.

    npm-on-Windows ships three variants of every global bin
    (``foo.cmd`` / ``foo.ps1`` / ``foo``). We always invoke ``.cmd``
    via cmd.exe, ``.ps1`` via pwsh / powershell, and bare-name files
    directly via the OS loader.
    """
    suffix = target_path.suffix.lower()
    if suffix == ".cmd" or suffix == ".bat":
        comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        return [comspec, "/d", "/s", "/c", str(target_path), *child_args]
    if suffix == ".ps1":
        ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(target_path), *child_args]
    return [str(target_path), *child_args]


def _augment_target_meta(
    target_path: Path,
    target_id: Optional[str],
) -> tuple[str, str, Optional[str]]:
    """Resolve target_id / provider / version when not supplied by caller.

    Falls back to discovery when the caller (rare path) only handed us
    a path. The wrapper shim ALWAYS supplies these explicitly so this
    branch is mostly for tests + ``python -m pce_cli_wrapper relay``
    invoked by hand for debugging.
    """
    if target_id is not None:
        # Best-effort lookup of provider / version from discovery.
        for t in discover():
            if t.target_id == target_id:
                return target_id, t.provider, t.version
        return target_id, "unknown", None

    # Lookup by absolute path.
    for t in discover():
        for v in t.shim_variants:
            try:
                if v.resolve() == target_path.resolve():
                    return t.target_id, t.provider, t.version
            except OSError:
                continue
    # Last-resort defaults — the row is still recorded, just less labelled.
    return "unknown", "unknown", None


# ---------------------------------------------------------------------------
# Helpers — stream pumps
# ---------------------------------------------------------------------------


class _CapBuffer:
    """Append-only byte buffer with a hard size cap.

    Once the cap is reached, additional writes are discarded and
    ``truncated`` flips to True. A per-instance lock guards the
    buffer so concurrent reader threads can write without races.
    """

    __slots__ = ("_buf", "_cap", "truncated", "_lock")

    def __init__(self, cap: int) -> None:
        self._buf = bytearray()
        self._cap = max(0, int(cap))
        self.truncated = False
        self._lock = threading.Lock()

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            if len(self._buf) >= self._cap:
                self.truncated = True
                return
            remaining = self._cap - len(self._buf)
            if len(chunk) <= remaining:
                self._buf.extend(chunk)
            else:
                self._buf.extend(chunk[:remaining])
                self.truncated = True

    def value(self) -> bytes:
        with self._lock:
            return bytes(self._buf)


_CHUNK = 4096


def _pump_stream(src: Optional[IO[bytes]], dst: Optional[BinaryIO], buf: _CapBuffer) -> None:
    """Read all bytes from ``src`` to ``dst`` while teeing into ``buf``."""
    if src is None:
        return
    try:
        while True:
            data = src.read(_CHUNK)
            if not data:
                break
            if dst is not None:
                try:
                    dst.write(data)
                except Exception:  # pragma: no cover — defensive
                    pass
            buf.write(data)
    except Exception:  # pragma: no cover — defensive
        return
    finally:
        try:
            if src is not None:
                src.close()
        except Exception:  # pragma: no cover — defensive
            pass


def _pump_stdin(src: Optional[BinaryIO], dst: Optional[IO[bytes]], buf: _CapBuffer) -> None:
    """Read all bytes from parent stdin and forward to child stdin."""
    if src is None or dst is None:
        if dst is not None:
            try:
                dst.close()
            except Exception:  # pragma: no cover — defensive
                pass
        return
    try:
        while True:
            data = src.read(_CHUNK)
            if not data:
                break
            try:
                dst.write(data)
                dst.flush()
            except (BrokenPipeError, OSError):
                break
            buf.write(data)
    except Exception:  # pragma: no cover — defensive
        return
    finally:
        try:
            dst.close()
        except Exception:  # pragma: no cover — defensive
            pass


def _stdin_is_tty(stream: BinaryIO) -> bool:
    """Best-effort ``isatty`` check on a binary parent stdin."""
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return False


def _binary(stream) -> BinaryIO:
    """Coerce a text/buffered stream to its binary buffer where possible."""
    return getattr(stream, "buffer", stream)


def _terminate_proc(proc: subprocess.Popen) -> None:
    """Send a graceful terminate; let caller follow up with kill if needed."""
    try:
        if sys.platform == "win32":
            # On Windows, terminate is a hard CTRL_BREAK-equivalent via
            # TerminateProcess. There's no SIGTERM concept — Popen.terminate()
            # is already the right call.
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
    except OSError:  # pragma: no cover — defensive
        pass
