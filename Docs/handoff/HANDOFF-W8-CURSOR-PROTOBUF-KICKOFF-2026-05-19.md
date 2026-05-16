---
title: "Wave 8 Kickoff — F5 P3 Cursor protobuf (Phase B Track 3 / longest pole)"
status: KICKOFF
date: 2026-05-19
predecessor: HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md
canonical: Docs/stability/redundancy-sprint/08-wave8-cursor-protobuf.md
scope_authority: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md §3.2
estimated_hours: ~200h (≈4-6 weeks wall-clock, single owner — release-blocker)
adr_dependency: ADR-023 (gRPC-web protobuf decoder) — must be Adopted before W8 start
---

# Wave 8 — F5 P3 Cursor protobuf decoder (Phase B Track 3)

## TL;DR

**Goal**: bring Cursor from 0/3 V-GREEN to 3/3.

1. **L1 MITM + protobuf decoder** — Cursor speaks gRPC-web framed
   protobuf instead of plain JSON. Need a protobuf framing/decoder
   (the ADR-023 module) so the L1 leg actually produces decoded
   `raw_captures` rows
2. **L3f MCP proxy** — Cursor exposes MCP for tools; capture there
3. **L4a clipboard** *or* **L3b Electron preload** — third leg

This is the **longest pole** in v1.1.6. Slip risk on this track shifts
the release. Track owner cadence is more rigorous than W6/W7.

Wall-clock: 4-6 weeks. Code: ~3500 LOC + ~60 tests.

## Track owner

**Owner**: TBD (different from W6/W7 owners). Skill profile: comfortable
with protobuf wire format, gRPC-web framing, mitmproxy custom addon.

## Weekly rhythm

| Week | Milestone |
|---|---|
| W8.1 | gRPC-web framing parser; 22 unit tests for length-prefix + flag bytes |
| W8.2 | Protobuf descriptor pipeline (Cursor schema lifted from `.proto` files in their public repo / observed traffic); 18 unit tests |
| W8.3 | `pce_core/normalizer/cursor.py` + 14 unit tests; first decoded pair |
| W8.4 | L3f MCP integration; second leg signoff |
| W8.5 | L3b preload OR L4a clipboard pick + third leg signoff |
| W8.6 | Live dedup proof + final 3/3 + mid-check vs Cursor upgrade window |

**Mid-check at W8.5**: 8.A (gRPC framing) + 8.B (decoder) MUST be
complete. If not, raise to owner immediately — release window slips.

## Acceptance (G1 contribution)

```
[ ] ADR-023 Adopted (gRPC-web protobuf decoder) BEFORE first commit
[ ] f5_p3_cursor scenarios.yaml row reaches 3/3 in /status
[ ] raw_captures has ≥ 1 row each from L1 (decoded), L3f, L3b/L4a
[ ] dedup proven (deduped_by JSON contains all 3 sources for one pair)
[ ] handoff `HANDOFF-W8-LIVE-CURSOR-3-LEGS-2026-MM-DD.md` written
[ ] REDUNDANCY-AUDIT-MATRIX §3 row "F5 P3 Cursor" updated
[ ] `phase_b: true` flag REMOVED from scenarios.yaml row
[ ] 60+ new unit tests GREEN: 22 framing + 18 decoder + 14 normalizer + 6 L3b/L4a
```

## Risks (top 3 — Phase B critical track)

| Risk | Mitigation |
|---|---|
| Cursor schema changes mid-Wave (protobuf field renumber) | fixture-lock the `.proto` snapshot per release; canary test compares wire format weekly; if breaking change → escalate |
| Decoder edge cases (varint / sint zigzag / repeated fields) corrupt body | property-based tests with hypothesis on framing parser; reference round-trip via known-good protoc binary |
| ADR-023 not Adopted in time | block W8 start; do not begin coding until Adopted |
| **STRICT MODE: this track determines v1.1.6 release date** | weekly status to owner; mid-check at W8.5 is hard gate for ≤6 week wall-clock |

## Reference

- Detailed task matrix: `Docs/stability/redundancy-sprint/08-wave8-cursor-protobuf.md`
- ADR-023 draft: `Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md`
- Predecessor capture supervisor + 13 scenarios: see midgate handoff
- Cursor MCP context: `Docs/research/CURSOR-MCP-INTEGRATION-NOTES.md` (if exists)
