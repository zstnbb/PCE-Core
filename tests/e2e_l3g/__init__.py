# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for ``pce_persistence_watcher`` (UCS layer L3g).

These tests are hermetic: they build a synthetic ``local-agent-mode-sessions/``
tree under a tmp dir, hand-construct an ``AppInstall`` pointing at it, and
exercise the discovery / parser / observer / CLI paths against an isolated
PCE sqlite DB.

No real Claude Desktop / ChatGPT Desktop install is required — the tests run
the same on a CI worker that has neither app installed and on a developer
laptop that has both.

Cross-references:

- ``pce_persistence_watcher/`` — the package under test (ADR-018 §3.4).
- ``pce_core/migrations/0011_l3g_local_persistence_source.py`` — adds the
  source row these tests assert on.
- ``Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`` —
  the strategic context.
"""
