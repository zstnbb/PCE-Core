# Perplexity Coverage Diff vs CHATGPT-FULL-COVERAGE

**Status:** superseded.

Perplexity was promoted from S2 diff coverage to S1 full coverage on
2026-04-25. The canonical Perplexity coverage contract is now:

- `Docs/stability/PERPLEXITY-FULL-COVERAGE.md`
- `tests/e2e/test_perplexity_full.py`

This file remains only as a historical pointer for older tasks and
commit references that mention `PERPLEXITY-COVERAGE-DIFF.md`.

## Historical uplift summary

Perplexity differs from ChatGPT in these site-specific ways:

- Cited answers are the core unit; citations must survive as structured
  `citation` attachments.
- Related questions are clickable follow-up turns.
- Search/source modes and model choice affect answer shape and should be
  stored as metadata when visible.
- Spaces scope threads under `/space/<id>` or `/spaces/<id>`.
- Shared/read-only URLs and non-conversation pages must stay silent.
- Research mode and generated media are account/plan/usage gated and may
  skip with evidence.

See the FULL spec for the current P01-P24 case matrix.
