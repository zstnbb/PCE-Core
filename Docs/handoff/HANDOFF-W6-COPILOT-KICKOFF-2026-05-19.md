---
title: "Wave 6 Kickoff — F5 P5 GitHub Copilot (Phase B Track 1)"
status: KICKOFF
date: 2026-05-19
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/06-wave6-copilot-l3c.md
scope_authority: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md §3.2
estimated_hours: ~80h (≈2 weeks wall-clock, single owner)
---

# Wave 6 — F5 P5 GitHub Copilot (Phase B Track 1)

## TL;DR

**Goal**: bring GitHub Copilot from 0/3 V-GREEN to 3/3 by adding three
independent legs:

1. **L1 MITM + host-JSON normalizer** (`pce_core/normalizer/copilot.py`)
2. **L3c VS Code extension** (`pce_browser_extension_wxt/` analogue,
   targeting VS Code's extension host)
3. **L3f MCP proxy** *or* **L4a clipboard** as the third leg

Wall-clock: ~2 weeks. Code: ~1500 LOC + ~20 tests.

## Track owner

**Owner**: TBD (single owner per Phase B track per SCOPE-LOCK §5).
Cannot be the same person running W7 or W8 in parallel.

## Weekly rhythm

| Week | Milestone |
|---|---|
| W6.1 | L1 host-JSON normalizer + 12 unit tests; live Copilot session captures via mitmproxy |
| W6.2 | L3c VS Code extension scaffolding + first capture; pick L3f vs L4a for leg 3; final live signoff (3/3) |

## Acceptance (G1 contribution)

```
[ ] f5_p5_github_copilot scenarios.yaml row reaches 3/3 in
    /api/v1/supervisor/status (color flips grey → green)
[ ] raw_captures has ≥ 1 row from each of the 3 legs in a single
    Copilot session (independent_basis distinct: copilot_https_api +
    vscode_extension_host + mcp_jsonrpc OR user_action)
[ ] dedup proven on this scenario (W3 §9 acceptance: same pair
    deduped_by JSON array contains all 3 sources)
[ ] handoff `HANDOFF-W6-LIVE-COPILOT-3-LEGS-2026-MM-DD.md` written
[ ] REDUNDANCY-AUDIT-MATRIX §3 row "F5 P5 GitHub Copilot" updated
[ ] `phase_b: true` flag REMOVED from scenarios.yaml row
```

## Risks (top 3)

| Risk | Mitigation |
|---|---|
| Copilot's HTTPS API churns more than ChatGPT/Claude | host-JSON normalizer should be schema-driven (not regex); add to canary watch in `pce_test_conductor/canaries/` |
| VS Code extension manifest API change | pin vscode-engine version; add a manifest-shape unit test that reads the current `package.json` |
| L3f MCP support depends on Copilot exposing MCP — falls back to L4a clipboard | both paths in scope; pick whichever is reachable in W6.2 |

## Reference

- Detailed task matrix: `Docs/stability/redundancy-sprint/06-wave6-copilot-l3c.md`
- ADR-010 boundary: VS Code extension lands in OSS (`pce_browser_extension_wxt/`)
  same as the Chromium extension; Pro contribution is the MCP-side
  registration, not the extension itself
- Predecessor capture supervisor + 13 scenarios: see midgate handoff
