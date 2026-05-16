# ADR-023: gRPC-Web Protobuf Decoder — OSS Generic Component

- **Status:** Proposed (awaiting owner approval, P5.D.1 Pre-flight §5)
- **Date:** 2026-05-15
- **Phase:** P5.D.1 Redundancy Sprint, Phase B Wave 8 前置
- **Supersedes:** N/A — first ADR on protobuf normalizer scope
- **Related:** ADR-009 (UCS canonical), ADR-010 (Open Core), ADR-019
  (maintenance — schema drift 治理), ADR-021 (capture_supervisor OSS),
  ADR-022 (UIA OSS, 同期)

---

## 1. 决议 (一句话)

**通用 gRPC-web framing + protobuf decoder `pce_core/normalizer/protobuf/`
落 OSS, 服务 P5.D.1 锁定的 13 P0 场景之 F5 P3 Cursor (`api*.cursor.sh`).
Cursor 专属 schema `pce_core/normalizer/cursor_protobuf/` 也落 OSS,
.proto 是逆向得来 — schema drift 由 nightly hermetic test 监控
(`protobuf-schema-drift` auto-issue), ADR-019 治理一等公民. Cursor
的真实 .proto 不公开, 仓内逆向版本明确标记为"best-effort, 维护成本
来自 schema drift".**

---

## 2. 背景

### 2.1 触发问题

`Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3.5 P3 Cursor 第 1 条腿
(L1 MITM) 当前是 V-PARTIAL — `api5.cursor.sh` 是 gRPC-web protobuf
私协议, ALLOWED_HOSTS 已含, 字节进 raw_captures, 但 normalizer 未出
session/messages 行.

STRICT MODE 锁 13/13 ≥3 V-GREEN, P3 Cursor 必须从 0 V-GREEN 升到 3.
第 1 条腿要做的事必然是 protobuf decoder.

### 2.2 决策选择

- **A (本 ADR)**: decoder 通用部分落 OSS, 任何 gRPC-web 应用复用
- **B (拒绝)**: decoder 专做 Cursor, 但 OSS 任何后续场景 (e.g. 新的
  gRPC-web AI 应用) 都要重写
- **C (拒绝)**: 只落 Pro — 与 STRICT MODE 13/13 冲突, P3 永远凑不齐

---

## 3. 决议内容

### 3.1 OSS 落点

```
pce_core/normalizer/protobuf/                ← 新, 通用 (本 ADR)
├── __init__.py
├── framing.py          — gRPC-web / gRPC-Web-Text / gRPC framing parser
├── decoder.py          — wire-type → field decoder; varint/length-delimited/fixed
└── README.md           — 给后续场景 (新 gRPC-web AI 应用) 接入指南

pce_core/normalizer/cursor_protobuf/          ← 新, Cursor-specific (本 ADR)
├── __init__.py
├── messages.proto      — 逆向得来的 schema (best-effort)
├── normalizer.py       — proto → messages 行 + sessions 行 normalization
└── fixtures/           — RECON 时抓的 10 真实 protobuf frames (lock version)

pce_core/migrations/0018_cursor_source.py     ← 新
tests/test_normalizer_protobuf_framing.py     ← 新, 22 单测
tests/test_normalizer_protobuf_decoder.py     ← 新, 18 单测
tests/test_normalizer_cursor.py               ← 新, 14 单测
```

### 3.2 .proto schema 定位

Cursor 不公开 .proto. `messages.proto` 是 RECON 阶段逆向得来 (用
`protoc --decode_raw` + 字段语义推断). 因此:

- 顶部注释明确标 "REVERSE-ENGINEERED, BEST-EFFORT, MAY DRIFT"
- 字段名为推断, 不是 Cursor 官方
- nightly hermetic test 喂 RECON fixture frames, 验当前 normalizer
  仍能解析

### 3.3 Schema drift 治理 (ADR-019 一致)

- 每夜 nightly:
  1. 跑 hermetic test 喂 fixture frames → normalizer 应输出预期
     messages 行
  2. 失败 → auto-issue 标 `protobuf-schema-drift`
- 真实 Cursor 升级触发 drift → owner 跑新一轮 RECON, 更新
  `messages.proto` + fixtures
- `tools/repair_adapter.py` 兼容: drift 失败时建议 RECON SOP

### 3.4 不变式

- decoder 通用部分 (`pce_core/normalizer/protobuf/`) 不依赖任何应用
  特定逻辑
- Cursor schema 漂移**不**影响 ChatGPT/Claude/etc 其他场景 — 隔离
- raw_captures 永远先全写 (不 dedup 时), 然后 normalizer 解码; 解码
  失败 → 仍保留 raw_captures 行, 但 messages 行不生成
- `independent_basis = "cursor_grpc_protocol"`, 与 Cursor MCP (basis =
  `mcp_jsonrpc`) 不同

### 3.5 Pro 扩展点

如 Pro 想抓更多 gRPC-web 应用 (e.g. 闭源 AI 桌面), 通过 ADR-021
`/api/v1/supervisor/legs/register` 接口注册 leg, 复用 OSS 通用
decoder + 自带 schema. OSS 不锁这条路径.

---

## 4. 后果

### 4.1 OSS 用户得到

- v1.1.6 OSS 用户在 P3 Cursor 拿到 L1 V-GREEN
- 通用 decoder 可服务后续任意 gRPC-web 应用 (ROI 复用)
- nightly drift detection 在 release 渠道一致

### 4.2 维护成本

- Cursor 升级可能改 schema → drift → RECON 再做一次
- 通用 decoder 部分稳定, 不受 Cursor 升级影响
- ADR-019 治理一等公民, schema drift 是已计入预算的维护项

### 4.3 风险接受

- Cursor 加 cert pinning → mitmproxy CA install 流程已就位, 但 pinning
  需 patch + re-sign, 工时翻倍 — STRICT MODE 不允许妥协, 必须做
- Cursor 升级 protobuf framing 协议 (e.g. 改用 Connect-Web) → ADR
  补丁 (ADR-023.1) 加新 framing 模式

---

## 5. 实施 (P5.D.1 Wave 8)

落地清单见
`@Docs/stability/redundancy-sprint/08-wave8-cursor-protobuf.md` §3.

时间预算: 200h, 4-6 周 wall-clock 单人专职 — 是 Phase B longest pole,
决定 v1.1.6 release 日.

---

## 6. 决策依据

| 维度 | A: 通用 decoder + Cursor schema 都落 OSS (本 ADR) | B: 都留 Pro |
|---|---|---|
| OSS P3 Cursor ≥3 V-GREEN | ✅ | ❌ |
| 后续 gRPC-web 场景复用 | ✅ (decoder 通用) | ❌ (每场景重写) |
| Schema drift 治理 | ✅ ADR-019 一致 | (Pro 内部, 看不到) |
| ADR-010 Open Core 一致 | ✅ (decoder 是基础能力) | 与 §3 冲突 |
| 标杆性 (STRICT MODE) | ✅ (13/13 不破) | ❌ |

---

## 7. 不做的事

- ❌ 不在 OSS 公开 Cursor 完整反向工程 (.proto 标 best-effort, 不
  claim 官方)
- ❌ 不让 Pro fork 通用 decoder
- ❌ 不让 Cursor schema 漂移影响其他场景 (隔离硬约束)

---

## 8. 验收 (P5.D.1 Wave 8 同步)

- 60+ 单测 GREEN (framing 22 + decoder 18 + cursor 14 + L3b/L4a 6-8)
- live handoff: 真 Cursor session → ≥ 1 messages 行 + model_name 提取
  正确
- /api/v1/supervisor/scenario/f5_p3_cursor status=redundant
- nightly schema drift detection 跑过 1 轮
- ADR status: Proposed → Adopted (owner sign-off)

---

## 9. 一句话锚点

**Cursor 的私 protobuf 是 PCE 在 LLM IDE 场景必须翻译的桥. 把通用
decoder 锁在 Pro 等于 STRICT MODE 13/13 永远凑不齐, 而且后续每个
gRPC-web 场景都要重写. 通用部分归 OSS, 应用 schema 也归 OSS (但标
best-effort), schema drift 由 nightly 治理 — 这与 PCE 的 local-first +
open-core + maintenance-as-first-class 原则一致.**
