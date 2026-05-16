# P5.D.1 Redundancy Sprint — Sub-document Index (STRICT MODE)

> **Canonical**: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md`
>
> **Scope authority**: `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
> (13 P0 场景锁定, 严格 ≥3 V-GREEN 才发 v1.1.6)
>
> 本目录是冲刺主文件的展开. 每个 wave 一份, 不可独立解读.

---

## 阅读顺序

1. **先读** [`SCOPE-LOCK-2026-05-15.md`](SCOPE-LOCK-2026-05-15.md)
   — 13 P0 场景范围锁定 + Tier-α/β 分层
2. **然后** [`../REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md`](../REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md)
   — 总目标 / 时序 (Phase A/B/C) / 风险 / 验收门
3. **顺序读** Phase A 6 份 wave doc (00 → 05)
4. **Phase B 三 track 并行** (06 / 07 / 08)
5. **Phase C** [`09-wave9-final-closure.md`](09-wave9-final-closure.md)
6. **最后** [`99-acceptance-gate.md`](99-acceptance-gate.md) — sprint
   关闭时 STRICT MODE 13/13 核对清单

---

## 文件清单

### Scope authority

| File | 角色 |
|---|---|
| [`SCOPE-LOCK-2026-05-15.md`](SCOPE-LOCK-2026-05-15.md) | 13 P0 场景范围权威 |

### Phase A — 核心冲刺 (Tier-α 10 场景, ~6 工日)

| File | Day | Wave | Estimated hours |
|---|:---:|---|:---:|
| [`00-pre-flight.md`](00-pre-flight.md) | 0 | Pre-flight (3+ 治理决策) | 1 |
| [`01-wave1-live-evidence.md`](01-wave1-live-evidence.md) | 1 | Live evidence (V-HERMETIC → V-GREEN) | 24 |
| [`02-wave2-sslkeylogfile.md`](02-wave2-sslkeylogfile.md) | 2 | A2 SSLKEYLOGFILE 生产链路 | 14.5 |
| [`03-wave3-supervisor-v1.md`](03-wave3-supervisor-v1.md) | 3 | Capture Supervisor v1 (13 场景) | 19 |
| [`04-wave4-third-leg.md`](04-wave4-third-leg.md) | 4 | Third leg per Tier-α | 18 |
| [`05-wave5-nightly-closure.md`](05-wave5-nightly-closure.md) | 5 | Phase A mid-gate + Phase B kickoff | 12 |

Phase A 合计 ~88h.

### Phase B — 结构性建设 (Tier-β 3 场景, 6 周 wall-clock 并行)

| File | Track | 场景 | Estimated hours |
|---|:---:|---|:---:|
| [`06-wave6-copilot-l3c.md`](06-wave6-copilot-l3c.md) | B-1 | F5 P5 GitHub Copilot | ~80 (~2 周) |
| [`07-wave7-chatgpt-desktop-uia.md`](07-wave7-chatgpt-desktop-uia.md) | B-2 | F4 P2 ChatGPT Desktop UIA | ~140 (~3-4 周) |
| [`08-wave8-cursor-protobuf.md`](08-wave8-cursor-protobuf.md) | B-3 | F5 P3 Cursor protobuf | ~200 (~4-6 周) |

Phase B wall-clock 由 Track B-3 决定: ~6 周.

### Phase C — 全量关闭 + tag

| File | Day | Wave | Estimated hours |
|---|:---:|---|:---:|
| [`09-wave9-final-closure.md`](09-wave9-final-closure.md) | 末周 | Final 13/13 verification + tag v1.1.6 | 16 |

### Closing checklist

| File | 角色 |
|---|---|
| [`99-acceptance-gate.md`](99-acceptance-gate.md) | STRICT MODE 13/13 核对清单 |

---

## 总时间窗

- **Phase A**: 6-7 工人日 (1 工作周, 多人并行 ~3 工日)
- **Phase B**: 6 周 wall-clock (3 track 必须不同人并行)
- **Phase C**: 2 工人日

**Sprint window: ~7-8 周** (2026-05-14 → ~2026-07-06).

---

## 关联资源

### 上承 (策略层)

- `@Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` — V-GREEN 严格标准 (§1)
  + 主表 (§3) + 不变式 (§6) + 更新协议 (§7)
- `@Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §2 — 8 desktop products
  P1-P8
- `@Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` §4 —
  Capture Supervisor 设计

### 上承 (决策层)

- `@Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md`
  §5.2 Phase 5 (SSLKEYLOGFILE)
- `@Docs/docs/engineering/adr/ADR-019-maintenance-as-first-class-concern.md`
  §3 (维护一等公民)
- `@Docs/docs/engineering/adr/ADR-021-capture-supervisor-oss-boundary.md`
- `@Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md`
  (本冲刺 Wave 7 落地)
- `@Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md`
  (本冲刺 Wave 8 落地)

### Handoff 入口

- Kickoff: `@Docs/handoff/HANDOFF-P5D1-KICKOFF-2026-05-14.md`
- Phase A mid-gate:
  `@Docs/handoff/HANDOFF-P5D1-PHASE-A-MIDGATE-2026-05-19.md` (待生成)
- Phase B kickoffs (3 份, 待生成):
  - `HANDOFF-W6-COPILOT-KICKOFF-2026-05-19.md`
  - `HANDOFF-W7-CHATGPT-DESKTOP-UIA-KICKOFF-2026-05-19.md`
  - `HANDOFF-W8-CURSOR-PROTOBUF-KICKOFF-2026-05-19.md`
- Final closing:
  `@Docs/handoff/HANDOFF-P5D1-FINAL-CLOSING-2026-07-06.md` (待生成)
- 30+ 份子 handoff: `@Docs/handoff/HANDOFF-W{1..9}-*-2026-*.md`

---

## 常用查询

### sprint 进度

```powershell
# 已完成的子 handoff 数 (status PASS)
Select-String -Path Docs/handoff/HANDOFF-W*-2026-*.md -Pattern "^status: PASS" | Measure-Object | Select-Object Count

# Phase A 11 + Phase B 8 + Phase C 1 = 20+ 子 handoff PASS 才到 v1.1.6
```

### REDUNDANCY-AUDIT §3 V-GREEN 数变化

```powershell
Select-String -Path Docs/stability/REDUNDANCY-AUDIT-MATRIX.md -Pattern "V-GREEN" -SimpleMatch | Measure-Object | Select-Object Count
```

### supervisor 健康度 (STRICT 验 13/13)

```powershell
$status = curl http://127.0.0.1:9800/api/v1/supervisor/status | ConvertFrom-Json
$status.scenarios | Format-Table id, status, color, legs_active
$redundant = ($status.scenarios | Where-Object { $_.status -eq "redundant" }).Count
"redundant: $redundant / 13"
```

---

## 维护

本目录文件**只在本冲刺内修改**. 冲刺关闭后:

- 标记所有 wave doc 头部 status: CLOSED
- 不删除, 作为后续审计参考
- 任何后续冲刺新建独立目录 `redundancy-sprint-N/` (N=2,3,...) 按
  本冲刺立的"3 leg + 独立 basis + dedup + status + nightly check"
  模板执行
