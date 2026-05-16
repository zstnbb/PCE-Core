---
title: "Wave 7 Kickoff — F4 P2 ChatGPT Desktop UIA (Phase B Track 2)"
status: KICKOFF
date: 2026-05-19
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/07-wave7-chatgpt-desktop-uia.md
scope_authority: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md §3.2
estimated_hours: ~140h (≈3-4 weeks wall-clock, single owner)
adr_dependency: ADR-022 (UIA OSS boundary) — must be Adopted before W7 start
---

# Wave 7 — F4 P2 ChatGPT Desktop UIA (Phase B Track 2)

## TL;DR

**Goal**: bring ChatGPT Desktop from 1/3 V-GREEN to 3/3 by:

1. **L1 MITM** — already in place (1/3)
2. **A2 SSLKEYLOGFILE corroboration** — Wave 2 skeleton landed; live
   evidence completes here as part of W7-T1
3. **L4b Accessibility (UIA driver)** — the **new module** built in W7

L4b reads ChatGPT Desktop's native UI tree via Windows UI Automation
(UIAutomationCore.dll). Independent basis = `ui_automation_tree`,
distinct from L1's `chromium_net_stack`, so it counts as a real third
leg per REDUNDANCY-AUDIT §1.3.

Wall-clock: ~3-4 weeks. Code: ~2500 LOC + ~28 tests.

## Track owner

**Owner**: TBD (different from W6/W8 owners). Skill profile: comfortable
with Windows IPC / COM / pywinauto.

## Weekly rhythm

| Week | Milestone |
|---|---|
| W7.1 | UIA driver scaffolding (`pce_core/ui_drivers/uia/`); first read of ChatGPT Desktop tree |
| W7.2 | Selector library: turn-list / message-text / role-tags; redaction policy; 18 unit tests |
| W7.3 | Live capture + dedup proof against L1 leg |
| W7.4 | A2 SSLKEYLOGFILE corroboration completion + final 3/3 signoff |

## Acceptance (G1 contribution)

```
[ ] ADR-022 Adopted (UIA OSS boundary) BEFORE first commit
[ ] f4_p2_chatgpt_desktop scenarios.yaml row reaches 3/3 in /status
[ ] raw_captures has ≥ 1 row each from L1, A2 (corroboration), L4b
    in a single ChatGPT Desktop session
[ ] dedup proven (deduped_by JSON contains all 3 sources for one pair)
[ ] handoff `HANDOFF-W7-LIVE-CHATGPT-DESKTOP-3-LEGS-2026-MM-DD.md` written
[ ] REDUNDANCY-AUDIT-MATRIX §3 row "F4 P2 ChatGPT Desktop" updated
[ ] `phase_b: true` flag REMOVED from scenarios.yaml row
[ ] 28 new unit tests GREEN: 18 driver + 6 redact + 4 e2e (mock UIA tree)
```

## Risks (top 3)

| Risk | Mitigation |
|---|---|
| ChatGPT Desktop UI restructure mid-Wave (Electron → native?) | UIA reads platform-native names; resilient as long as the app exposes accessible tree (a11y is contractual on Windows). Lock Electron version in canary; fixture-based regression test |
| UIA performance: full-tree scan blocks app | scope reads to focus chain + last-N turn elements only; benchmark in W7.2 |
| ADR-022 not Adopted in time | block W7 start; do not begin coding until Adopted (per kickoff §5.4) |

## Reference

- Detailed task matrix: `Docs/stability/redundancy-sprint/07-wave7-chatgpt-desktop-uia.md`
- ADR-022 draft: `Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md`
- Wave 2 (A2 SSLKEYLOGFILE) prerequisite: `Docs/stability/redundancy-sprint/02-wave2-sslkeylogfile.md`
- Predecessor capture supervisor + 13 scenarios: see midgate handoff
