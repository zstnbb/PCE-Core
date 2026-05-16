# Pre-flight — 5 治理决策 (Day 0, ~1h, STRICT MODE)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md`
>
> **不写代码, 但是不澄清后面写出来的代码方向会跑偏.**
>
> STRICT MODE 把决策从 3 项增到 5 项: 加 ADR-022 (UIA OSS) draft 启动 +
> ADR-023 (protobuf decoder) draft 启动. 两者 Wave 7/8 前置.

---

## 1 · `pce_core/capture_supervisor/` 的 OSS/Pro 边界 ⚠️ Blocker

### 1.1 现状

`scripts/check_import_direction.py:39` 把 `pce_core.capture_supervisor`
列为 OSS 禁止 import 的 Pro-only 模块. 但 UCS §4 + 本冲刺的 G2 都需要
它在 OSS 跑.

```python
# scripts/check_import_direction.py:38-41
PRO_MODULES = (
    "pce_agent_electron",
    "pce_agent_ax",
    "pce_core.capture_supervisor",     # ← 当前禁止 OSS import
)
```

### 1.2 决策选项

#### A (推荐)

Dedup + status + policy 三件搬到 OSS (基础能力), 把 L0 kernel / Frida
的 Pro 插件机制留 Pro.

需要:
- 更新 `check_import_direction.py` allowlist (移除 `pce_core.capture_supervisor`)
- 新增一条 ADR-021 (本计划已 draft, 见 `Docs/docs/engineering/adr/ADR-021-capture-supervisor-oss-boundary.md`)

**理由**: dedup + status 是基础能力 (UCS §4 canonical), Pro 价值在于
"更多 leg 类型" (L0 / L2), 不在于"运行时仲裁". 把仲裁锁在 Pro 等于让
OSS 用户永远拿不到真冗余.

#### B

保持现状, OSS 用户拿不到运行时冗余 — 等于本计划的 G2 永远在 OSS 不达成.

W3 supervisor 暂落 `pce-pro`; OSS 侧只暴露 status read-only proxy.

### 1.3 Action

owner 选 A 或 B. **本计划假设选 A 继续.** 30 min decision.

如果选 B, Wave 3 的 §4.3 落地清单需要按 R4 fallback 路线重写 (移到
`PCE-pro/pce-pro/` 仓库, OSS 侧只放 read-only proxy).

---

## 2 · 严格 V-GREEN 口径 vs UCS 附录 B 宽松口径的统一

### 2.1 现状

仓库长期同时存在两个口径:

- **严格** (REDUNDANCY-AUDIT §1): 必须 live 验证, V-HERMETIC 不算
- **宽松** (UCS 附录 B): "设计上能抓"即标 ✅/🟢

导致 "62% 形态达标" 等宣称漂移. 用户 / 后续 agent / 项目所有者读到
完全不同的"覆盖率"叙述.

### 2.2 Action

`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md` 附录 B 的
✅/🟢 路由表加一条头部声明:

```markdown
> **口径声明 (2026-05-14 ADR-021 锚定):**
> 本表回答"设计上能抓", 不回答"验证过能抓". 后者请回
> `@Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3.
> 任何"PCE 已覆盖 X 场景"的对外陈述以 §3 V-GREEN 数为准.
```

5 min 编辑工作. 在 ADR-021 通过后同 PR 一起合.

---

## 3 · "依赖隔离规则" 是否需要再松绑

### 3.1 现状

`REDUNDANCY-AUDIT-MATRIX.md` §1 现在的规则:

- 同走 Chromium 网络栈 (L1 MITM + A2 SSLKEYLOGFILE) → 算 **1.5 条**,
  不是 2 条独立
- 都被同一个 Electron Fuse 锁住 (B1 NODE_OPTIONS + `--inspect`) → 算同源
- 都需要 mitmproxy CA trust → 并列但依赖同一前提
- 一条走内存 / 一条走落盘 / 一条走 UI 树 / 一条走协议中间件 → 真正正交

### 3.2 为什么不松绑

如果松绑 "L1 + A2 算 2 条", A2 SSLKEYLOGFILE 落地后 P1 + P2 一夜达 3
条的假象就成立, 但实际是同一 TLS 栈崩了同时崩两条腿, **不是真冗余**.

A2 的真正价值是 **失败模式不重叠**: 当 mitmproxy CA 出问题、L1 解不开
TLS 时, A2 keylog 仍然 work. 但当 Chromium 网络栈挂了 (无网 / 进程崩),
两条都挂.

### 3.3 Action

在 ADR-021 里再次锚定这条规则, 防止后续 agent "为了凑 3 条" 松绑.

---

## 4 · ADR-022 (UIA Accessibility OSS Boundary) draft 启动 ⏳ Wave 7 前置

### 4.1 触发原因

P5.D.1 STRICT MODE 锁 13 P0 全 ≥3 V-GREEN. F4 P2 ChatGPT Desktop 因
ADR-018 §6 标记的"L3g/L3b/L3d 三死锁", 唯一 path-out 是 L4b UIA. 此
轴需要新落地一个 OSS Windows UIA driver.

### 4.2 与 ADR-021 同模板

- UIA driver `pce_core/uia_driver/` 落 OSS (基础能力, 不是 Pro 价值)
- macOS AXUIElement 等价能力推 P5.D.2 (本冲刺只锁 Windows)
- 隐私默认 opt-in: 用户必须主动开

### 4.3 Action

draft `Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md`
进 owner review 队列. Wave 7 开工时 Adopted.

---

## 5 · ADR-023 (gRPC-web protobuf decoder) draft 启动 ⏳ Wave 8 前置

### 5.1 触发原因

P5.D.1 STRICT MODE 锁 13 P0 全 ≥3 V-GREEN. F5 P3 Cursor 因 `api5.cursor.sh`
是 gRPC-web protobuf 私协议, 现 normalizer 出不了 messages 行 (V-PARTIAL).
要把 Cursor 第 1 条腿升 V-GREEN, 必须落 gRPC-web protobuf decoder.

### 5.2 关键边界

- decoder 通用部分 `pce_core/normalizer/protobuf/` 落 OSS (任何 gRPC-web
  应用都能用)
- Cursor-specific schema `pce_core/normalizer/cursor_protobuf/` 也落 OSS
  (.proto 是逆向得来; ADR-019 治理一等公民, 漂移由 nightly 监控)
- 隔离: schema 漂移 → nightly hermetic test 失败 →
  `protobuf-schema-drift` auto-issue

### 5.3 Action

draft `Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md`
进 owner review 队列. Wave 8 开工时 Adopted.

---

## 6 · Pre-flight 验收门 (Day 0 结束时)

```
[ ] §1.2 决策记录: owner 选 A 还是 B (capture_supervisor OSS)
[ ] 如果选 A: ADR-021 进入 owner review 队列
[ ] §2.2 编辑准备好 (待 ADR-021 同 PR 合)
[ ] §3.3 不变式锚定文字写进 ADR-021
[ ] **(STRICT MODE 新增)** ADR-022 (UIA OSS) draft 启动 — Wave 7 前置
[ ] **(STRICT MODE 新增)** ADR-023 (gRPC-web protobuf decoder) draft 启动 — Wave 8 前置
[ ] **(STRICT MODE 新增)** SCOPE-LOCK-2026-05-15.md owner 签字确认 13 P0 范围
[ ] 本冲刺剩余 wave 按选 A 路径展开 + Phase A/B/C 时序 (默认假设)
```

完成后进入 [`01-wave1-live-evidence.md`](01-wave1-live-evidence.md).
