# ADR-021: Capture Supervisor — OSS Boundary

- **Status:** Adopted (2026-05-15 sprint kickoff sign-off)
- **Date:** 2026-05-14 (Proposed) / 2026-05-15 (Adopted, P5.D.1 STRICT MODE)
- **Phase:** P5.D.1 Redundancy Sprint — Wave 3 implementation gate
- **Supersedes (partial):** `scripts/check_import_direction.py:39` —
  `pce_core.capture_supervisor` previously listed in `PRO_MODULES`,
  removed by this ADR.
- **Related:** ADR-009 (UCS canonical), ADR-010 (Open Core),
  ADR-018 (MSIX strategy), ADR-019 (maintenance as first-class concern)

---

## 1. 决议 (一句话)

**Capture Supervisor 的 3 个最小契约 — `dedup` (按 `pair_id +
fingerprint` 30s 滑窗去重) + `policy` (按 scenarios.yaml 决策当前走
哪条 leg + 自动降级) + `status` (`/api/v1/supervisor/status` 暴露每
场景 leg health) — 落在 OSS `pce_core.capture_supervisor` 包. Pro 渠
道 (Frida / L0 kernel) 通过 ADR-010 定义的 local HTTP 接口注册自己作
为额外 leg, 不改 OSS 仲裁逻辑.**

---

## 2. 背景

### 2.1 触发问题

`REDUNDANCY-AUDIT-MATRIX.md` §1 定义 V-GREEN 严格标准: 每场景需 ≥ 3
条不相互依赖的 V-GREEN 路线. 即使代码层有 5 条 leg, 如果运行时:

- 没有 dedup → 同一对话被 N leg 抓到, raw_captures 重复行
- 没有 status → 用户不知道当前哪条 leg 在跑、哪条 degraded
- 没有 policy → 一条 leg 挂了不会自动启用备份 leg

那"3 条腿"只是纸面冗余, 不是运行时冗余.

### 2.2 现状: 仲裁逻辑被锁在 Pro

`scripts/check_import_direction.py:38-41`:

```python
PRO_MODULES = (
    "pce_agent_electron",
    "pce_agent_ax",
    "pce_core.capture_supervisor",     # ← 当前禁止 OSS import
)
```

后果: OSS 用户即使本地装了 5 个 leg, 也拿不到 dedup / status / policy
任何一项. v1.x 永远困在"代码冗余 ≠ 运行时冗余"的状态.

### 2.3 ADR-010 的 Open Core 边界原意

ADR-010 §3 锁的是 "**捕获实现层**" (Frida hook 脚本 / L0 kernel driver
/ Electron preload)—这些有 Pro 价值因为它们能抓 OSS 抓不到的 (闭源
应用 / kernel 流量 / 进程内 TLS). 仲裁逻辑 (谁优先 / 怎么去重 / 谁挂了
报警) **不**是 Pro 价值, 它是基础工程能力, 没有它整个 UCS 设计无法
work.

---

## 3. 决议内容

### 3.1 三个 OSS 模块

```
pce_core/capture_supervisor/                     ← OSS (本 ADR)
├── dedup.py        — fingerprint LRU
├── policy.py       — scenarios.yaml + degrade 决策
├── status.py       — health by scenario × leg
├── scenarios.yaml  — P0 7 场景配置 (data only)
└── api.py          — /api/v1/supervisor/* 路由
```

### 3.2 Pro 注册点

Pro leg (L0 kernel / L2 Frida) 通过 ADR-010 已定义的 local HTTP 接口
向 OSS supervisor 注册自己:

```http
POST /api/v1/supervisor/legs/register     ← 新, ADR-021 §3.2
{
  "source": "L0_kernel",                  ← 来自 capture_event.py CaptureSource
  "scenario_ids": ["f4_p1_claude_desktop"],
  "independent_basis": "wfp_winsock_layer",
  "agent_pid": 12345,
  "agent_version": "pro-1.0.0"
}
```

OSS supervisor:
- 把这个 source 加到 status 输出
- dedup 时把 Pro leg 写的 raw_captures (经 ingest API) 一起处理
- policy 决策时把 Pro leg 视为合法 leg

### 3.3 不变式 (从 REDUNDANCY-AUDIT §1 继承, 本 ADR 锚定)

- "不相互依赖" 规则不松绑: L1 + A2 SSLKEYLOGFILE 仍算 1.5 条 (同
  Chromium net stack)
- 同场景内 leg 的 `independent_basis` 必须 unique, 否则
  `redundancy_target` 不计入
- raw_captures 永远先写, dedup 是后处理 (假阴优于假阳)

### 3.4 隔离: Pro 不可改 OSS 仲裁

- Pro 不能 fork dedup 算法 (如果 Pro 觉得 fingerprint 不够好, 走 ADR
  改 OSS, 不能私有化)
- Pro 不能旁路 supervisor (raw_captures 必须经 ingest API + dedup)
- Pro 可以扩展 leg 类型, 不能扩展 status 状态机

---

## 4. 后果

### 4.1 OSS 用户得到

- v1.2+ OSS 用户拿到运行时冗余 (本 ADR 不通过则永远拿不到)
- /api/v1/supervisor/status 在所有 release 渠道一致
- dashboard /redundancy 在 OSS 即可使用

### 4.2 Pro 渠道

- Pro 仍然有差异化价值: 抓 OSS 抓不到的 (闭源 / kernel / 进程内)
- Pro 通过 ADR-010 接口注册成为额外 leg
- Pro 不需要 fork supervisor

### 4.3 维护

- supervisor 是 OSS 一等公民, 维护负担分散到 OSS 贡献者
- ADR-019 maintenance-as-first-class 策略一致: 仲裁是基础能力, 不
  应该锁在 Pro

---

## 5. 实施 (P5.D.1 Wave 3)

落地清单见
`@Docs/stability/redundancy-sprint/03-wave3-supervisor-v1.md` §4.

测试矩阵 31 单测见同文件 §5.

时间预算: 17.2h, 1 工人日.

---

## 6. 决策依据 (为什么是 A 不是 B)

| 维度 | A: dedup/status/policy 落 OSS (本 ADR) | B: 全部留 Pro |
|---|---|---|
| OSS 用户拿到运行时冗余 | ✅ | ❌ |
| Pro 差异化价值是否被稀释 | ❌ (Pro 仍有 L0/L2/AX 独家 leg) | (无所谓) |
| ADR-010 边界一致性 | ✅ (仲裁是基础能力, 不是 Pro 价值) | 把仲裁标记成 Pro 价值, 与 ADR-010 §3 原意冲突 |
| ADR-019 maintenance 一致性 | ✅ (仲裁分散维护) | ❌ (Pro 维护者承担全部) |
| 实施复杂度 | 低 (在 OSS 仓库内, 单 PR 可完) | 高 (跨仓库 RPC, 故障域多) |
| 长期演进 | 健康 (仲裁随 OSS 演进) | 风险 (Pro 私有化容易导致仲裁逻辑漂移) |

---

## 7. 不做的事

- ❌ 不在 supervisor 里加 telemetry / 用户行为上报 (PROJECT.md §7.1
  local-first)
- ❌ 不让 Pro fork dedup 算法
- ❌ 不让 supervisor 决定走哪条 leg 是"快速通道" — 数据完整性永远优先
  (raw_captures 三 leg 都写, 然后 dedup, 不是只挑一条 leg 写)

---

## 8. 验收 (随 P5.D.1 Wave 3 同步)

- 31 单测 GREEN
- /api/v1/supervisor/status 暴露 7 P0 场景
- dedup 实战验证 (W4-T5 handoff)
- check_import_direction.py CI green
- ADR status: Proposed → Adopted (owner sign-off)

---

## 9. 一句话锚点

**冗余只有在仲裁可见的时候才是真冗余. 把仲裁锁在 Pro 等于把"≥3 条
腿"永远定格在纸面 — 这与 PCE 的 local-first + open-core 原则都冲突.**
