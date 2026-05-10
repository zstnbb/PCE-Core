# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper – PATH-priority stdio interceptor for CLI AI agents.

This package implements the ADR-018 Phase 4 H1 path: a wrapper that
sits in front of user-installed CLI AI agents (canonical case: the
``@anthropic-ai/claude-code`` npm shim, distributed as ``claude.cmd`` /
``claude.ps1`` / ``claude``) and tees their stdin / stdout / stderr to
PCE while passing every byte through to the real child process
unchanged.

Architecture (see ``Docs/docs/engineering/adr/ADR-018-msix-store-app-
capture-strategy.md`` §3.5 Axis 3):

1. **Discover** the real CLI shim under ``%APPDATA%\\npm\\`` (Windows)
   or ``$(npm prefix -g)/bin/`` (POSIX). The wrapper deliberately does
   NOT inject itself into ``node_modules`` — we only manipulate the
   thin npm shim layer so an ``npm install -g @anthropic-ai/claude-
   code`` upgrade still works without re-running our installer.

2. **Install** generates a wrapper shim in a PCE-owned bin directory
   (default ``%LOCALAPPDATA%\\PCE\\bin\\`` on Windows, ``~/.pce/bin``
   on POSIX) and prepends that directory to ``PATH`` for the user's
   shell. The wrapper shim's job is one ``python -m pce_cli_wrapper
   relay -- <args>`` call that re-execs the real shim under stdio
   tee.

3. **Relay** spawns the real child via ``subprocess.Popen`` with
   captured stdio pipes, then concurrently:
     - Forwards stdin from our parent stdin to child stdin (binary,
       byte-for-byte).
     - Forwards child stdout to our parent stdout while accumulating
       a transcript buffer.
     - Forwards child stderr to our parent stderr while accumulating
       a separate transcript buffer.
   On child exit the relay flushes one ``(request, response)`` pair
   into the PCE database via ``pce_core.db.insert_capture``, tagged
   ``source_id = SOURCE_L3H_CLI_WRAPPER``.

4. **Status** prints what the discovery + install state looks like
   right now (PATH ordering, where the real shim lives, whether our
   wrapper is in front, last-N capture row count). This is the
   diagnostic equivalent of ``pce-persistence-watcher discover``.

Boundary contract (see ADR-013 OSS / Pro split rules):

- This package is **OSS**. It contains no Anthropic-proprietary
  protocol knowledge — the relay treats stdin / stdout / stderr as
  opaque byte streams. Higher-tier semantic parsing (e.g. JSON-RPC
  frame extraction from stdout) lives in the ``pce_core.normalizer``
  layer and runs offline against the captured transcript.
- The package writes through the same ``pce_core.db.insert_capture``
  API that ``pce_proxy``, ``pce_mcp_proxy``, and ``pce_persistence_
  watcher`` use, with its own ``source_id`` constant
  (``SOURCE_L3H_CLI_WRAPPER``). Dashboard + stats / query endpoints
  treat L3h rows uniformly with all other capture rows.
- TTY interactive sessions (e.g. user runs ``claude`` with no piped
  input and starts a REPL) fall back to a passthrough mode that
  records args + exit code + duration but does NOT capture the
  interactive transcript. Capturing a live PTY without breaking the
  user-facing terminal experience is out of scope for v0.

Public API surface (the only stable entry-points):

- ``pce_cli_wrapper.discovery.discover()`` → list of ``ShimTarget``
- ``pce_cli_wrapper.relay.relay()`` → spawns child + writes capture
- ``pce_cli_wrapper.install.install()`` / ``uninstall()`` → manage
  the PCE-owned ``bin/`` directory and PATH guidance
- ``python -m pce_cli_wrapper {install|uninstall|status|relay}``
"""
from __future__ import annotations

__version__ = "0.1.0"
