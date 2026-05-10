# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for ``pce_cli_wrapper`` (UCS layer L3h, ADR-018).

Hermetic — no real ``claude`` install needed:

- Discovery / install / status tests use synthesised ``ShimTarget``
  records or a tmp ``bin_dir``.
- Relay tests spawn ``sys.executable`` with a tiny in-tree fake-shim
  script as the first child arg, exercising the real
  ``subprocess.Popen`` + stdio-tee codepath without depending on the
  Anthropic CLI.

Cross-references:

- ``pce_cli_wrapper/`` — package under test.
- ``pce_core/migrations/0012_l3h_cli_wrapper_source.py`` — source row
  these tests assert on.
- ``Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md``
  §3.5 Axis 3 — strategic context.
"""
