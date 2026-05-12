<!--
SPDX-License-Identifier: Apache-2.0
.github/PULL_REQUEST_TEMPLATE.md — P5.C.5.1 deliverable

Reviewer pre-flight: please confirm the boxes below were ticked by the
author before merging. The boxes are not "nice to have" — they're how
the Meta-Pipeline catches drift before it lands on `master`. CODEOWNERS
(top of repo) lists the reviewers auto-requested for each lane.
-->

## What does this PR change?

<!-- One-paragraph summary. Reference the ADR / handoff / issue this PR resolves. -->

Resolves #__

## Type of change

- [ ] **Bug fix** (non-breaking; fixes a broken adapter / capture pipeline issue)
- [ ] **New feature** (non-breaking; adds new lane / target / case coverage)
- [ ] **Breaking change** (changes a stable API surface — requires a `ARCHITECTURE-IMPACT.md` update)
- [ ] **Documentation** (README / CHANGELOG / ADR / handoff only)
- [ ] **Refactor** (no behaviour change; reduces complexity / debt)
- [ ] **Test / tooling** (CI / pytest infrastructure; no production code change)

## Lane / target affected

- [ ] browser (L3a — chatgpt / claude / gemini / 11 secondary sites)
- [ ] desktop (L3g — Claude Desktop chat / cowork / code)
- [ ] cli (L3h — Claude Code CLI)
- [ ] mcp (L3f — filesystem MCP reference)
- [ ] cross-lane / infrastructure (`pce_core` / `pce_test_conductor` / `tools/`)
- [ ] none (docs-only or governance change)

## Author checklist (required)

- [ ] **Tests pass locally**: ran the relevant `pytest` suite and pasted the summary line below.
- [ ] **Adapter changes touch YAML, not Python** (per P5.C.4.2): if I changed a selector / timeout / prompt for chatgpt / claude / gemini, I edited `pce_core/adapters/<site>.yaml`, NOT `tests/e2e_probe/sites/<site>.py`.
- [ ] **Canary updated** (per ADR-011 G3 + P5.C.2): if I added a new captured shape, I ran `python -m pce_test_conductor --tool diff_canary --args '{"...":"...", "update": true}'` and the resulting `pce_test_conductor/canaries/<target>/*.schema.json` is committed in this PR.
- [ ] **Health beacons still emit** (per P5.C.1): if I changed a lane hook site, the relevant `tests/test_health_beacon.py` block still passes.
- [ ] **Conductor tests pass** (per P5.C.2): `python -m pytest tests/test_conductor.py` is GREEN.
- [ ] **No new dependency** added without an ADR amendment (per ADR-013 "OSS attribution" + ADR-019 "maintenance debt").
- [ ] **CHANGELOG entry added** under `[Unreleased]` with the phase tag (e.g. `P5.C.5.x` / `P5.D.y`).
- [ ] **CODEOWNERS reviewer auto-requested** is the correct person for this surface.

### Pytest summary

<!-- Paste the last line of your test run here, e.g. "141 passed, 1 warning in 11.13s" -->

```
<paste pytest summary line>
```

## Reviewer checklist (filled out during review)

- [ ] **Diff scope matches type-of-change**: a `bug-fix` PR doesn't sneak in unrelated refactors
- [ ] **Test additions match production additions**: every new public function / endpoint has at least 1 test
- [ ] **No `print()` / debug logs left in production code**
- [ ] **No commented-out blocks of code** ("delete first, recover from git if needed")
- [ ] **Markdown / YAML lint clean** (workflow CI step `markdownlint` + `yamllint` GREEN — once those exist)
- [ ] **`tools/render_health_matrix.py` SVG diff reviewed** if this PR could shift a lane / target color

## Out-of-scope follow-ups

<!--
Anything you noticed but DIDN'T fix in this PR — file as a separate issue
linked here. Keeping a PR focused is more important than catching
everything; reviewers will thank you for the deferred-issue link.
-->

- [ ] (none) / Filed as #__
