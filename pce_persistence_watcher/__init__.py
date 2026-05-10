# SPDX-License-Identifier: Apache-2.0
"""PCE Local Persistence Watcher (UCS layer L3g / ADR-018).

A read-only filesystem observer that extracts AI interaction records
from what closed-source Electron AI applications persist to the user
filesystem. The ADR-018 three-axis MSIX implementation model names this
package the "persistence axis" for P1 Claude Desktop and P2 ChatGPT
Desktop — the route that remains viable when L3b (Electron preload)
and L3d (CDP launcher) are blocked by Windows MSIX distribution.

Boundary contract — what this package MUST do and MUST NOT do:

- MUST be read-only. We open application files with ``O_RDONLY`` (or
  the Windows equivalent) and never modify, rename, or delete anything
  the target app owns. Filesystem writes are restricted to PCE's own
  data directory (``PCE_DATA_DIR``).

- MUST handle concurrent locks gracefully. Chromium LevelDB holds
  an exclusive ``LOCK`` file while the app runs. We side-step this by
  copying the LDB files to a temp directory (content bytes, not the
  LOCK itself) before any parser touches them, so the running app is
  never disturbed.

- MUST dedupe emitted captures. The scanner runs repeatedly; a session-
  metadata file that was emitted last pass must NOT be re-emitted this
  pass unless its content changed. Dedup uses ``(source_path, key,
  content_hash)`` in ``~/.pce/persistence_watcher_state.json``.

- MUST treat parse failures as soft. A single malformed ``manifest.json``
  or un-decodable LevelDB block must not crash the watcher; it is
  logged and skipped so the remaining records still flow.

- MUST tolerate missing installs. On a machine where Claude Desktop
  was never installed, ``python -m pce_persistence_watcher scan``
  exits 0 after emitting a "no install found" stderr line.

- MUST stay OS-agnostic. Primary target is Windows MSIX (ADR-018's
  motivating case); macOS Squirrel + Linux paths are added in
  ``discovery.py`` with the same abstraction so future platforms are
  cheap to support.

- MUST NOT depend on C extensions that require local builds. Optional
  dependencies (e.g. ``plyvel-ci`` for fast LevelDB reads) are
  ``try/except ImportError``-gated; the package degrades to file-based
  sources (JSON manifests, skill catalogues) when binary readers are
  absent.

OSS classification: Apache-2.0, shipping in the same boundary as
``pce_mcp_proxy/`` and ``pce_mcp/`` per ADR-018 §3.9 — the 13 retained
capture paths all live in OSS so adoption is not gated by a paywall.

Cross-references:

- ``Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md``
  §3.4 — L3g sub-layer definition + persistence-source catalogue.
- ``Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`` §3 — the
  five-layer UCS this package sits in.
- ``pce_core/migrations/0011_l3g_local_persistence_source.py`` —
  registers the ``l3g-local-persistence-default`` source id.
- ``pce_core/db.py::SOURCE_L3G_LOCAL_PERSISTENCE`` — the constant the
  observer writes against.
- ``tests/e2e_l3g/`` — migration + discovery + observer tests.
- ``tests/manual/method_g_capture_feasibility.ps1`` — ADR-018 Phase 1
  feasibility probe this package is downstream of.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
