---
title: "P5.D.1 Redundancy Sprint — KICKOFF (STRICT MODE)"
status: KICKOFF
date: 2026-05-14
re_scoped: 2026-05-15 (STRICT MODE — 13 P0)
predecessor: HANDOFF-P5C-COMPLETION-2026-05-12.md
canonical: Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md
scope_authority: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
sprint_window: 2026-05-14 → ~2026-07-06 (~7-8 周 wall-clock; Phase A 1 周 + Phase B 6 周 + Phase C 紧贴)
operator: <name>
artifacts:
  - canonical plan: Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md
  - scope lock: Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md
  - 10 wave docs: Docs/stability/redundancy-sprint/0{0..9}-*.md + 99-acceptance-gate.md
  - ADR-021 draft: Docs/docs/engineering/adr/ADR-021-capture-supervisor-oss-boundary.md
  - ADR-022 draft: Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md (Wave 7 前置)
  - ADR-023 draft: Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md (Wave 8 前置)
---

# P5.D.1 Redundancy Sprint — KICKOFF (STRICT MODE)

## TL;DR

P5.C Meta-Pipeline closed (`HANDOFF-P5C-COMPLETION-2026-05-12.md`).
Successor sprint P5.D.1 — **Redundancy Sprint, STRICT MODE** —
2026-05-15 锁定 **13 个 P0 场景** (5 Web + 8 Desktop), 严格 ≥3 V-GREEN
不相互依赖路线. 13/13 全绿才发 v1.1.6 — 不接受 12/13 妥协.

时序拆 Phase A (Tier-α 10 场景, 1 周) + Phase B (Tier-β 3 场景, 6 周
wall-clock 并行) + Phase C (16h 关闭).

The plan is fully spec'd in
`Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` +
`Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`.

This kickoff handoff bootstraps the sprint:
- ✅ Plan landed (this commit)
- ✅ STRICT MODE re-scope landed (2026-05-15 SCOPE-LOCK)
- ⏳ Pre-flight 5 decisions pending owner approval
- ⏳ ADR-021 + ADR-022 + ADR-023 drafts pending Adopted

---

## 1 · Why this sprint exists

`Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` (canonical, 2026-05-13)
showed 0/~20 P0 scenarios meeting the strict "≥3 V-GREEN legs" bar.
Even the strongest scenario (P1 Claude Desktop) had only 2 V-GREEN
legs. Without runtime supervisor, even those V-GREEN paths are
paper redundancy — duplicate raw_captures rows when 2 legs both fire,
no degrade detection, no user visibility.

P5.D.1 closes that gap. **STRICT MODE (2026-05-15 owner 决策)**: 锁定
13 个 P0 场景作为 v1.1 标杆, 13/13 全严格 ≥3 V-GREEN 才发 v1.1.6.

---

## 2 · 3 goals (G1/G2/G3) — STRICT MODE

| Goal | Description | Acceptance |
|---|---|---|
| **G1 Coverage** | 13 P0 scenarios each ≥ 3 V-GREEN legs | REDUNDANCY-AUDIT §3 main table 13 行 V-GREEN |
| **G2 Runtime** | dedup + status + degrade alive | /api/v1/supervisor/status returns 13 scenarios |
| **G3 Visibility** | nightly auto-redundancy-check | REDUNDANCY-MATRIX.svg (13 cards) + auto-issue channel |

13 P0 scenarios (per SCOPE-LOCK §2):

**Web 5**: F1 ChatGPT Web · F1 Claude Web · F1 Gemini Web ·
F1 GAS · F1 Grok Web

**Desktop 8** (= `DESKTOP-PRODUCT-MATRIX.md` §2 P1-P8):
F4 P1 Claude Desktop · F4 P2 ChatGPT Desktop · F5 P3 Cursor ·
F5 P4 Windsurf · F5 P5 GitHub Copilot · F6 P6 Claude Code CLI ·
F6 P7 Codex CLI · F6 P8 Gemini CLI

---

## 3 · Phase A + B + C timeline

### Phase A — Tier-α 10 场景 (1 周, ~88h)

| Day | Wave | Sub-doc | Estimated hours |
|---|---|---|:---:|
| 0 | Pre-flight (5 决策) | [`00-pre-flight.md`](../stability/redundancy-sprint/00-pre-flight.md) | 1 |
| 1 | Wave 1 — Live Evidence (V-HERMETIC → V-GREEN) | [`01-wave1-live-evidence.md`](../stability/redundancy-sprint/01-wave1-live-evidence.md) | 24 |
| 2 | Wave 2 — A2 SSLKEYLOGFILE | [`02-wave2-sslkeylogfile.md`](../stability/redundancy-sprint/02-wave2-sslkeylogfile.md) | 14.5 |
| 3 | Wave 3 — Capture Supervisor v1 (13 场景) | [`03-wave3-supervisor-v1.md`](../stability/redundancy-sprint/03-wave3-supervisor-v1.md) | 19 |
| 4 | Wave 4 — Third Leg per Tier-α | [`04-wave4-third-leg.md`](../stability/redundancy-sprint/04-wave4-third-leg.md) | 18 |
| 5 | Wave 5 — Phase A mid-gate + Phase B kickoff | [`05-wave5-nightly-closure.md`](../stability/redundancy-sprint/05-wave5-nightly-closure.md) | 12 |

### Phase B — Tier-β 3 场景 (~6 周 wall-clock 并行)

| Track | Wave | 场景 | 工时 | Sub-doc |
|---|---|---|:---:|---|
| B-1 | Wave 6 | F5 P5 GitHub Copilot | ~80h (~2 周) | [`06-wave6-copilot-l3c.md`](../stability/redundancy-sprint/06-wave6-copilot-l3c.md) |
| B-2 | Wave 7 | F4 P2 ChatGPT Desktop UIA | ~140h (~3-4 周) | [`07-wave7-chatgpt-desktop-uia.md`](../stability/redundancy-sprint/07-wave7-chatgpt-desktop-uia.md) |
| B-3 | Wave 8 | F5 P3 Cursor protobuf | ~200h (~4-6 周) | [`08-wave8-cursor-protobuf.md`](../stability/redundancy-sprint/08-wave8-cursor-protobuf.md) |

Track B-3 是 longest pole, 决定 v1.1.6 release 日.

### Phase C — Final Closure (~16h, 2 工日)

| Day | Wave | Sub-doc |
|---|---|---|
| 末周 | Wave 9 — 13/13 verification + tag v1.1.6 | [`09-wave9-final-closure.md`](../stability/redundancy-sprint/09-wave9-final-closure.md) |

**Total: ~7-8 周 wall-clock**, 2026-05-14 → ~2026-07-06.

---

## 4 · Deliverables (cumulative)

- 30+ new handoff files (W1×11, W2×1, W3×1, W4×8, W5×1, W6×3, W7×2,
  W8×3, W9×1, midgate, kickoff×4)
- ~5000 LOC new code
- 70+ new unit tests
- 13 P0 scenarios reach G1 acceptance (STRICT)
- 4 ADR closures: ADR-018 Phase 5 + ADR-021/022/023 Adopted
- 1 nightly auto-issue channel: `redundancy-degraded`
- git tag v1.1.6 (only after 13/13 全绿)

---

## 5 · Pre-flight blockers (must resolve before Wave 1)

These 5 don't write code but shape everything that follows.
**~1h total** if owner is available.

### 5.1 ADR-021 OSS boundary decision (BLOCKER)

`pce_core.capture_supervisor` is currently `PRO_MODULES` per
`scripts/check_import_direction.py:39`. The plan needs it in OSS.

ADR-021 draft is at
`Docs/docs/engineering/adr/ADR-021-capture-supervisor-oss-boundary.md`.

**Action**: owner reviews + Adopts (or rejects → R4 fallback path).

### 5.2 V-GREEN strict criterion lock

UCS appendix B "design level coverage" annotations need a header note
pointing to REDUNDANCY-AUDIT §3 as authoritative.

**Action**: 5min editorial change; ride along with ADR-021 PR.

### 5.3 Isolation rule reaffirmation

L1 + A2 SSLKEYLOGFILE = 1.5 legs (not 2). To prevent any future
"squeeze 3 legs out of paper" attempts, ADR-021 §3.3 reaffirms.

**Action**: owner approves; lands with ADR-021.

### 5.4 ADR-022 (UIA OSS boundary) — Wave 7 前置 (STRICT MODE)

Wave 7 F4 P2 ChatGPT Desktop UIA driver 落 OSS, 与 ADR-021 同模板.

**Action**: draft 进 review 队列; Wave 7 开工前 Adopted.

### 5.5 ADR-023 (gRPC-web protobuf decoder) — Wave 8 前置 (STRICT MODE)

Wave 8 F5 P3 Cursor protobuf decoder 落 OSS.

**Action**: draft 进 review 队列; Wave 8 开工前 Adopted.

### 5.6 SCOPE-LOCK 签字

owner 在 `Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
确认 13 P0 场景锁定 + STRICT 验收门.

**Action**: 5 min review.

---

## 6 · Risks (top 5, STRICT MODE)

| Risk | Mitigation |
|---|---|
| ADR-021/022/023 not all Adopted in time | R4 fallback for ADR-021: capture_supervisor lands in PCE-pro repo, OSS exposes read-only proxy. Sprint scope unchanged. ADR-022/023 不 Adopted → Wave 7/8 不能开工, release 顺移 |
| Live evidence (W1, W4) blocked by Claude Desktop / browser session / CLI | Reschedule to days when owner available; W3 supervisor work is independent of live tests |
| dedup false positive (different requests sharing fingerprint) | fingerprint includes 5min ts bucket (W3 §3.1.1); `--no-dedup` debug flag. raw_captures all rows preserved (just tagged). |
| **Wave 8 (Cursor) longest pole 6 周 wall-clock** | 5 周 mid-check 必须 8.A/B 完成; 不允许妥协 leg 数 (STRICT). 如 Cursor 期间升级改 protobuf schema → fixture 锁版本, release 前再 RECON |
| **STRICT MODE 12/13 不接受** | Wave 6/7/8 任一 track 滑点 → release 顺移; 不开 carry-forward 妥协通道 |

Full risk register: `REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §4.

---

## 7 · Sprint state at kickoff (2026-05-14, re-scoped 2026-05-15)

```
Pre-sprint V-GREEN counts (from REDUNDANCY-AUDIT-MATRIX §4 +
SCOPE-LOCK §2):

# Web 5 (Tier-α)
Scenario                      legs   gap
─────────────────────────────────────────
F1 ChatGPT Web                 2      1
F1 Claude Web                  2      1
F1 Gemini Web                  1      2
F1 GAS                         1      2
F1 Grok Web                    1      2

# Desktop 8 / Tier-α
F4 P1 Claude Desktop           2      1
F5 P4 Windsurf                 1      2
F6 P6 Claude Code CLI          1      2
F6 P7 Codex CLI                0      3
F6 P8 Gemini CLI               0      3

# Desktop 8 / Tier-β (Phase B)
F4 P2 ChatGPT Desktop          1      2 (L4b UIA)
F5 P3 Cursor                   0      3 (protobuf + L3f + L3b/L4a)
F5 P5 GitHub Copilot           0      3 (L1+L3c+L3f)
─────────────────────────────────────────
                              14     26

Sprint target: all 13 reach 3 → +26 V-GREEN legs gained.
```

---

## 8 · How to start tomorrow morning

1. Open
   [`SCOPE-LOCK-2026-05-15.md`](../stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md)
   — confirm 13 scope
2. Open
   [`00-pre-flight.md`](../stability/redundancy-sprint/00-pre-flight.md)
3. Resolve 5 decisions with owner — 1h
4. Open
   [`01-wave1-live-evidence.md`](../stability/redundancy-sprint/01-wave1-live-evidence.md)
5. Pick a parallelism mode (1 person or 3 people) per §4
6. Start with W1-T1 SOP — handoff template at
   `Docs/handoff/HANDOFF-W1-T1-MCP-PROXY-LIVE-2026-05-14.md`
7. As each Wave closes, update REDUNDANCY-AUDIT-MATRIX §3 in the
   same commit (per §7.2 update protocol)
8. **Phase A 末尾不发 v1.1.6 tag** — STRICT MODE 13/13 才发
9. Phase B 三 track 并行 (W6 / W7 / W8); 各 track owner 独立
10. Phase B 全闭 → Phase C Wave 9 全量验收 → tag v1.1.6

---

## 9 · Reference

- **Scope authority**: `Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
- **Canonical plan**: `Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md`
- **Phase A mid-gate handoff**: `Docs/handoff/HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md` (待生成 W5-T6)
- **Phase B kickoff handoffs (3 份)**: 待 W5-T6 生成
- **Final closing handoff**: `Docs/handoff/HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md` (template at end of `09-wave9-final-closure.md`)
- **Acceptance checklist**: `Docs/stability/redundancy-sprint/99-acceptance-gate.md`
- **Predecessor**: `HANDOFF-P5C-COMPLETION-2026-05-12.md`

---

## 10 · One-liner anchor

**13 个场景, 5 + 8 = 标杆. Tier-α 1 周内做完, Tier-β 6 周内并行做完,
Phase C 同步关闭. 13 全绿才发 v1.1.6 tag — STRICT MODE. 完成后, 任何
一条腿挂掉, 用户感知不到; 任何一条腿降级, dashboard 立刻知道; 后续
每个新场景接入按本冲刺立的"3 leg + 独立 basis + dedup + status +
nightly check"模板执行.**
