# Wave 8 — Cursor protobuf decoder + 第二/三条腿 (Phase B Track-3, ~200h, 4-6 周)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.2
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §3.2
> + `@Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.3 (P3 Cursor 详情)
>
> **目标**: F5 P3 Cursor 0 → 3 V-GREEN. 通过 gRPC-web protobuf
> normalizer (Leg 1 L1) + L3f MCP (Leg 2) + L3b preload OR L4a
> clipboard (Leg 3) 凑足 3 条独立腿.
>
> **Track owner**: TBD (单人专职). Wall-clock 4-6 周, 是 Phase B 的
> longest pole — 它的关闭日决定 v1.1.6 release 日.

---

## 1 · 现状

`REDUNDANCY-AUDIT-MATRIX.md` §3.5 P3 Cursor:

| # | Path | 当前 | 缺口 |
|---|---|---|---|
| 1 | L1 MITM | V-PARTIAL — 9 cursor 域已 allowlist, host 字节进 raw_captures, 但 `api5.cursor.sh` 是 **gRPC-web protobuf**, normalizer 未出 session/messages | protobuf decoder + live |
| 2 | L3f MCP | V-HERMETIC (Cursor 支持 MCP, 无 live handoff) | live handoff |
| 3 | L3b Electron preload | V-DOC-ONLY (未实施) | 全实施 (备用 leg 3) |
| 4 | L4a 剪贴板 | 未配置 | 实施 (备用 leg 3) |

**0 V-GREEN, 3 条腿全从 0 起**. Cursor 是 Tier-D0, D0 release gate
强制达标, 没有妥协余地.

---

## 2 · ADR-023 (本 Wave 新增 ADR, 落地依据)

`Docs/docs/engineering/adr/ADR-023-grpc-web-protobuf-decoder.md` 草稿
要点:

- gRPC-web protobuf decoder 落 OSS `pce_core/normalizer/protobuf/`
- 不绑死 Cursor — decoder 本身是通用 .proto + framing 解析器, Cursor
  专属 .proto schema 落 `pce_core/normalizer/cursor_protobuf/`
- .proto schema **逆向**得来 (Cursor 不公开), 维护成本主项: 每次 Cursor
  升级需要 RECON 验证 schema 兼容
- ADR-019 治理一等公民: schema 漂移在 nightly 里有 hermetic test 保护

---

## 3 · 落地清单 (200h breakdown)

### 3.1 Phase 8.A — RECON Cursor 协议 (~24h, 3 工日)

| 任务 | 内容 | 工时 |
|---|---|:---:|
| Cursor 9 host 全量 endpoint 抓包 | mitmproxy 拦 1h 真实 Cursor 用 → 200+ rows 分析 | 4h |
| `api5.cursor.sh` 全量 endpoint 列表 | 区分 Chat / Composer / Tab Completion / Indexer | 4h |
| protobuf framing 协议判定 | gRPC-web (POST + protobuf header `application/grpc-web+proto`) vs gRPC-Web-Text (base64) | 4h |
| message types 逆向 | 用 `proto.parser.proto` 或 `protoc --decode_raw` 逆向出 ChatRequest / ChatResponse / TabCompletion 等核心 message | 8h |
| 文档化 RECON 结果 | `Docs/research/2026-05-21-cursor-grpc-web-recon.md` | 4h |

### 3.2 Phase 8.B — protobuf decoder 落地 (~80h, 10 工日)

| 文件 | 内容 | LOC |
|---|---|:---:|
| `pce_core/normalizer/protobuf/__init__.py` (新) | 通用 framing parser (gRPC-web, gRPC-Web-Text, gRPC) | 50 |
| `pce_core/normalizer/protobuf/framing.py` (新) | 帧解析: 5-byte length-prefix, compressed flag, base64-text mode | 200 |
| `pce_core/normalizer/protobuf/decoder.py` (新) | wire-type → field decoder; varint / length-delimited / fixed64 / fixed32 | 300 |
| `pce_core/normalizer/cursor_protobuf/__init__.py` (新) | Cursor-specific `.proto` schema (逆向得来) | 30 |
| `pce_core/normalizer/cursor_protobuf/messages.proto` (新) | 包括 ChatRequest / ChatResponse / TabCompletion / Composer 等 | (.proto file) |
| `pce_core/normalizer/cursor_protobuf/normalizer.py` (新) | proto → messages 行 + sessions 行 normalization | 250 |
| `pce_core/migrations/0018_cursor_source.py` (新) | 注册 `source_id='proxy-cursor-default'` (区别于通用 proxy) | 80 |
| `tests/test_normalizer_protobuf_framing.py` (新) | 22 单测: framing parse / compressed / multi-frame / malformed | 400 |
| `tests/test_normalizer_protobuf_decoder.py` (新) | 18 单测: wire-type / varint edge / nested / repeated | 350 |
| `tests/test_normalizer_cursor.py` (新) | 14 单测 + fixture (10 真实 Cursor proto frames captured RECON time) | 350 |

总 LOC: ~2010 (含 proto file 不计 LOC).

### 3.3 Phase 8.C — Leg 2 L3f MCP live (~20h, 3 工日)

Cursor MCP 支持已存在 (`~/.cursor/mcp.json`); `pce_mcp_proxy` 已就绪
(P5.B.1). 缺真 Cursor 触发 1 个 MCP tool call → live handoff.

| 任务 | 内容 | 工时 |
|---|---|:---:|
| Cursor MCP 配置 | 把 `pce_mcp_proxy` 加到 `~/.cursor/mcp.json` 作为 server | 2h |
| 触发 MCP tool call | 在 Cursor Composer 用 `/` 选 PCE 注册的 tool | 2h |
| 验证 raw_captures | `source_id='mcp-proxy-default'` + meta_json.subsystem=`cursor` | 4h |
| handoff | `HANDOFF-W8-T2-CURSOR-MCP-LIVE-2026-06-15.md` | 4h |
| Cursor MCP discovery 异常处理 (Cursor 0.4x+) | RECON + driver fix | 8h |

### 3.4 Phase 8.D — Leg 3 选型 + 落地 (~50h, 6-7 工日)

#### 选 L3b Electron preload OR L4a 剪贴板

**首选 L3b Electron preload** — 与 L1/L3f basis 真不同 (NODE_OPTIONS
preload script 跑在 Cursor 进程内, 直接 hook 渲染层). Cursor 不像
Claude Desktop 锁 Fuse, 可能可行 (Phase 8.D 第 1 步 RECON 验)

**备选 L4a 剪贴板** — Cursor 的 chat / composer 输出剪贴板复制有 AI 特征,
basis = user_action, 与 L1/L3f 不同.

| 子任务 | 内容 | 工时 |
|---|---|:---:|
| 8.D.1 RECON Electron Fuse 状态 | 模仿 ADR-018 §3 H4 lockdown probe 流程, 看 Cursor 是否锁 NODE_OPTIONS | 8h |
| 8.D.2-A L3b preload (若 Fuse 未锁) | 写 Cursor 专属 preload, 与现有 `pce_app_launcher/` 整合 | 30h |
| 8.D.2-B L4a 剪贴板 (若 Fuse 锁) | 复用 Wave 4 W4-T3 clipboard_monitor 框架, 加 Cursor-specific AI 特征识别 | 8h |
| 8.D.3 live handoff | `HANDOFF-W8-T3-CURSOR-{L3B,L4A}-LIVE-2026-06-25.md` | 4h |
| 8.D.4 测试 | 8 单测 | 8h |

### 3.5 Phase 8.E — ADR-023 起草 + Adopted (~6h)

`ADR-023-grpc-web-protobuf-decoder.md` — 走 ADR-021 同模板.

### 3.6 Phase 8.F — Dedup + supervisor 集成 (~10h)

- scenarios.yaml `f5_p3_cursor` 已含 (Wave 3 §3.2)
- dedup fingerprint 适配 protobuf body (canonicalize 到同 pair_id)
- supervisor /status 显示 P3 三 leg

### 3.7 Phase 8.G — Live 验收冲刺 (~10h)

24h 内连续跑 5 个真 Cursor session (chat / composer / tab completion /
codebase indexing / MCP), 验三 leg 都报 + dedup 起作用.

---

## 4 · 关键设计点

### 4.1 protobuf schema 维护

Cursor 不公开 .proto. 我们逆向出来的 schema 会随 Cursor 升级漂移.
治理策略 (ADR-019 一致):

- nightly hermetic test 喂 RECON 时抓的 fixture frames, 验 normalizer
  仍能解析
- schema 漂移 → nightly 失败 → auto-issue 标 `protobuf-schema-drift`
- `tools/repair_adapter.py` 兼容: 失败时建议人工 RECON 新版本

### 4.2 与 L3f MCP 的 dedup

Cursor 一次 Composer 调用 MCP tool 时, L1 (gRPC-web POST) 和 L3f (MCP
JSON-RPC stdio) 都会捕到同一对话. fingerprint 必须 canonicalize:

- L1: prompt 文本前 1024 字节 hash
- L3f: MCP tools/call params.arguments 字符串前 1024 字节 hash
- 必须 normalize 出"相同的 prompt 文本" → fingerprint 同

策略: 都先 extract `prompt_canonical` 字段进 raw_captures.meta_json,
fingerprint 用此字段, 不直接用 body.

### 4.3 ALLOWED_HOSTS 不变

9 cursor 域已 allowlist (`api*.cursor.sh` etc), 不需扩.

---

## 5 · 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W8-R1 | Cursor 在 Wave 进行中升级, 改 protobuf schema | RECON fixture 锁版本 (e.g. Cursor 0.42); release 前再 RECON 一次最新版; ADR-023 §4 维护策略 |
| W8-R2 | Cursor 加 cert pinning | mitmproxy CA install 流程已就位, 但 pinning 需 patch + re-sign Cursor binary, 工时翻倍; fallback: 仅靠 L3f + L4a 凑 2 条, 第 3 条立 ADR 暂记缺口 — **STRICT MODE 不接受**, 必须 patch |
| W8-R3 | gRPC-web framing 在某 endpoint 不一致 (text mode vs binary mode 混用) | framing.py 双模式 dispatcher; per-endpoint config |
| W8-R4 | Cursor MCP 配置点变化 (0.4x → 0.5x) | RECON 阶段确认; 锁版本 fallback |
| W8-R5 | Wall-clock 超 6 周 → release 顺移 | 5 周 mid-check → 8.A/B 必须完成; 如未完成, 提早预警, 但不允许妥协 leg 数 (STRICT) |

---

## 6 · 测试矩阵

- protobuf framing: 22 单测
- protobuf decoder: 18 单测
- cursor normalizer: 14 单测 + 10 fixture frames
- L3b preload (if 选 A): 8 单测
- L4a clipboard (if 选 B): 6 单测
- e2e cursor live: 3 handoff (T1/T2/T3)

总: 60+ 新单测 + 3 handoff.

---

## 7 · 验收门 (Wave 8 关闭)

```
[ ] ADR-023 Adopted
[ ] HANDOFF-W8-T1-CURSOR-PROTOBUF-LIVE PASS
    + raw_captures 有 ≥ 1 行带 normalize 完毕的 messages
    + model_name 提取正确 (Cursor 默认 claude-3.7-sonnet 或 gpt-4o)
[ ] HANDOFF-W8-T2-CURSOR-MCP-LIVE PASS
[ ] HANDOFF-W8-T3-CURSOR-{L3B,L4A}-LIVE PASS
[ ] REDUNDANCY-AUDIT §3 P3 Cursor 3 行 V-GREEN
[ ] /api/v1/supervisor/scenario/f5_p3_cursor status=redundant
[ ] migration 0018 通过 + 60+ 新单测 GREEN
[ ] dedup 实战: 三 leg 同发 → 1 主行 + deduped_by 数组
[ ] HANDOFF-W8-CLOSING-2026-06-25.md
```

完成后并入 [`09-wave9-final-closure.md`](09-wave9-final-closure.md).
**Wave 8 是 Phase B longest pole, 它关闭即触发 Phase C kickoff.**
