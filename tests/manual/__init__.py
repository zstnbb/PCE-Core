# SPDX-License-Identifier: Apache-2.0
"""One-off manual diagnostic / reconnaissance scripts.

These are NOT automated tests. They are ad-hoc tools used during
investigation phases (e.g. Phase 4.0 Claude Desktop super-app
surface recon) to gather facts about real-world third-party
applications before we expand normalizer / capture coverage.

Files in this package:

* ``recon_claude_desktop.py`` — Phase 4.0 live recon tool.
* ``analyze_recon.py``        — post-run JSONL analyzer.
* ``RECON-CHECKLIST.md``      — user-facing click-through script.

Outputs land in ``tests/manual/recon_<ts>/`` and are gitignored.
"""
