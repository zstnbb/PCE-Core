# SPDX-License-Identifier: Apache-2.0
"""pce_core.adapters — YAML-driven site adapter configuration (P5.C.4.1).

This package replaces the per-site Python class attributes in
``tests/e2e_probe/sites/<site>.py`` with declarative YAML manifests.

Phase ladder (per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.4):

- **P5.C.4.1 (this commit)**: foundation only. ``adapter_loader.py``
  loads + validates YAML; ``chatgpt.yaml`` / ``claude.yaml`` /
  ``gemini.yaml`` mirror the existing class-attribute values 1:1.
  The 3 site adapter classes are NOT modified — backward-compat is
  preserved while the new mechanism stabilises.

- **P5.C.4.2 (next commit)**: the 3 site adapter classes get refactored
  to call ``adapter_loader.apply_to_class()`` at module load time, so
  selectors disappear from the Python source. Regression bar: every
  existing ``tests/e2e_probe`` test must remain GREEN.

- **P5.C.4.3 (next commit)**: ``pce_test_conductor.llm_repair`` +
  ``tools/repair_adapter.py`` use the same loader to ingest the
  current YAML, propose patches as YAML diffs (not Python diffs),
  and keep selectors-as-data end-to-end.

The split keeps each commit small + reviewable, per the rule that
"a single commit shouldn't ship both a load mechanism and a refactor
that depends on it" (lesson from P5.B.7 P2.1 audit-gap closure where
walker + cases shipped together and audit found 3 missing surfaces).
"""

__all__: list[str] = []
