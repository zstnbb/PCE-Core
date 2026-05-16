# Wave 7 — ChatGPT Desktop L4b UIA (Phase B Track-2, ~140h, 3-4 周)

> 上承: `@Docs/stability/REDUNDANCY-IMPLEMENTATION-PLAN-2026-05-14.md` §1.2
> + `@Docs/stability/redundancy-sprint/SCOPE-LOCK-2026-05-15.md` §3.2
> + `@Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §4.2 (P2 详情)
> + `@Docs/docs/engineering/adr/ADR-018-msix-store-app-capture-strategy.md` §6
>
> **目标**: F4 P2 ChatGPT Desktop 1 → 3 V-GREEN. 通过 L4b
> Accessibility (UIA) 兜底, 解 ADR-018 §6 列出的"L3g 结构性死 +
> L3b/L3d 死锁"困局.
>
> **Track owner**: TBD (单人专职). Wall-clock 3-4 周.

---

## 1 · 现状

`REDUNDANCY-AUDIT-MATRIX.md` §3.4 P2:

| # | Path | 当前 | 备注 |
|---|---|---|---|
| 1 | L1 MITM | V-GREEN (2026-05-12 sweep) | 1 条已锁 |
| 2 | A2 SSLKEYLOGFILE | V-PARTIAL → V-GREEN (Wave 2 完成) | 但与 L1 同 net stack, 算 1.5 条 corroboration, **不计独立 leg** |
| 3 | L3g | V-DEAD (IndexedDB 空, 会话内容在服务端) | 结构性 |
| 4 | L3b NODE_OPTIONS / L3d CDP | V-DEAD (H4 LOCKED + MSIX 6 死) | 结构性 |
| 5 | L4b UIA | 未实施 | **本 Wave 落地** |

唯一的 path-out 是 L4b (Windows UIA 子系统). L3g/L3b/L3d 全 V-DEAD,
不能通过它们补腿.

---

## 2 · ADR-022 (本 Wave 新增 ADR, 落地依据)

`Docs/docs/engineering/adr/ADR-022-uia-accessibility-oss-boundary.md`
草稿见 §10. 关键决策:

- UIA driver `pce_core/uia_driver/` 落 OSS (与 capture_supervisor 同
  ADR-021 边界 — 仲裁 + 基础能力归 OSS, Pro 通过注册接口加 leg)
- UIA driver 不替代 chat-region L1, 只补 P2 第 3 条腿
- 隐私: UIA 读屏可能含 PII, redact 必须 mandatory; 默认采 prompt /
  response 文本, 不采 user 系统其它窗口

---

## 3 · 落地清单 (140h breakdown)

### 3.1 RECON (~16h, 2 工日)

| 任务 | 内容 | 工时 |
|---|---|:---:|
| UIA tree dump on ChatGPT Desktop | `tests/e2e_desktop_ui/scripts/dump_uia.py --target=chatgpt` 全量 dump 各窗口 / popup / role 标记 | 4h |
| 找 prompt input + response stream + send button + new chat | UIA `automation_id` / control_type / name 标记 | 6h |
| Wallclock 测试 stream 完成事件 | UIA event subscription on Text changed of response area | 4h |
| 文档化 RECON 结果 | `Docs/research/2026-05-21-chatgpt-desktop-uia-recon.md` | 2h |

### 3.2 UIA Driver 实施 (~60h, 7-8 工日)

| 文件 | 内容 | LOC |
|---|---|:---:|
| `pce_core/uia_driver/__init__.py` (新) | 包入口 | 30 |
| `pce_core/uia_driver/uia.py` (新) | IUIAutomation COM 包装; element finder; subtree walker | 250 |
| `pce_core/uia_driver/chatgpt_desktop.py` (新) | ChatGPT Desktop 具体的 prompt/response/send element 定位 + stream listener | 300 |
| `pce_core/uia_driver/capture.py` (新) | 把 UIA 抓到的 prompt+response → `insert_capture(source_id='uia-chatgpt-default', ...)` + redact + ai_signal_score | 200 |
| `pce_core/migrations/0017_uia_source.py` (新) | 注册 `source_id='uia-chatgpt-default'` | 80 |
| `pce_core/db.py` (改) | 加 `SOURCE_UIA_CHATGPT` 常量 | 5 |

总 LOC: ~865.

### 3.3 测试 (~30h, 3-4 工日)

| 测试套件 | 单测数 | LOC |
|---|:---:|:---:|
| `tests/test_uia_driver_unit.py` (UIA tree fixtures) | 18 | 400 |
| `tests/e2e_uia/test_chatgpt_desktop_uia.py` (real ChatGPT Desktop, smoke) | 4 | 250 |
| `tests/test_uia_redact.py` (redaction unit) | 6 | 150 |

总: 28 新单测.

### 3.4 Live + handoff (~16h, 2 工日)

- 真 ChatGPT Desktop session — 跑 5 个不同 prompt 验 UIA 抓全
- `HANDOFF-W7-T1-CHATGPT-DESKTOP-UIA-RECON-2026-05-21.md`
- `HANDOFF-W7-T2-CHATGPT-DESKTOP-UIA-LIVE-2026-06-05.md`

### 3.5 ADR-022 起草 + Adopted (~8h)

`ADR-022-uia-accessibility-oss-boundary.md` — 走 ADR-021 同模板.

### 3.6 Dedup 与 supervisor 集成 (~10h)

- scenarios.yaml `f4_p2_chatgpt_desktop` 新增 L4b leg, basis=`ui_automation_tree`
- dedup fingerprint 适配 (UIA 抓的 prompt 文本 vs L1 抓的 HTTP body
  → fingerprint canonicalization 对齐)
- supervisor /status 把 L4b leg 显示成 third leg

---

## 4 · 关键设计点

### 4.1 UIA stream 完成判定

ChatGPT Desktop 的 response area 是逐 token 增长的 Text. 完成判定:

- **Method A**: monitor Send button 重新可点击 (UIA `IsEnabled=True`)
- **Method B**: monitor response area 文本 200ms 不变化
- **Method C**: monitor "Stop generating" button 消失

ADR-018 §6 推断 Method A 最稳, RECON 阶段验.

### 4.2 与 L1 的 dedup

L4b UIA 抓的 prompt 是用户键入的明文; L1 抓的是 `wss://...` 帧或
`/v1/chat/completions` POST body. 两条 leg 的 fingerprint 必须能
canonicalize 到同 pair_id, 否则 dedup 不工作 (假阴 → 重复行).

策略: pair_id 仍由 L1 (主腿) 生成; L4b 抓到的 prompt 用 timestamp
window (5min bucket) + prompt 文本前 1024 字节 hash 与 L1 的请求
match. 如果 match 失败, L4b 自己开 pair_id (deduped_by=null).

### 4.3 隐私

- UIA reads 桌面 accessibility tree, 可见 PII (附近窗口 / 通知 /
  剪贴板)
- redact policy: 只采**目标 ChatGPT 窗口内**的 prompt input +
  response output element; 其它子窗 / 兄弟窗一律不采
- 加 user opt-in: `pce_core/uia_driver/config.py` 默认 `enabled=False`,
  用户必须主动开 (UI 弹窗 + Docs/install/PCE_UIA_INSTALL.md)

---

## 5 · 风险

| ID | 风险 | 缓解 |
|---|---|---|
| W7-R1 | ChatGPT Desktop UIA tree 不暴露 prompt input 的 Text content (Chromium-Electron 可能屏蔽) | RECON 阶段第 1 件事; fallback: 用 OCR (pillow + tesseract) 截屏读 — 工时翻倍, 但 path-out 仍存在 |
| W7-R2 | UIA tree 性能差 (大窗口 dump 很慢) | 只 subscribe 事件, 不 poll; 响应区改用 incremental subtree walker |
| W7-R3 | OpenAI 改 Desktop 渲染方式 (e.g. canvas-rendered text) | UIA + OCR 双 fallback; 长期看 ADR-018 §6.5 单独跟进 |
| W7-R4 | 隐私担忧 — 用户拒绝 UIA 读屏 | 默认关 + opt-in + 安装时 explainer; 用户不开就保持 1 V-GREEN (P2 不达标 — 这是 STRICT MODE 接受的代价, owner 决策) |
| W7-R5 | 与 macOS 适配冲突 (UIA 是 Windows-only) | 范围声明: P2 macOS 推 P5.D.2 用 AXUIElement; 本 Wave 只锁 P2 Windows |

---

## 6 · scenarios.yaml 已含 (Wave 3 §3.2)

```yaml
- id: f4_p2_chatgpt_desktop
  legs:
    - source: L1_mitm
      independent_basis: chromium_net_stack
    - source: A2_keylog
      independent_basis: chromium_keylog   # corroboration
    - source: L4b_uia_accessibility
      independent_basis: ui_automation_tree
  redundancy_target: 3
```

注: A2 仍按 §1.3 隔离规则算 0.5 条; L4b 是真第 3 条独立 leg.

---

## 7 · 验收门 (Wave 7 关闭)

```
[ ] ADR-022 Adopted (owner sign-off)
[ ] HANDOFF-W7-T1-CHATGPT-DESKTOP-UIA-RECON PASS
[ ] HANDOFF-W7-T2-CHATGPT-DESKTOP-UIA-LIVE PASS
[ ] REDUNDANCY-AUDIT §3 P2 row L4b 加 V-GREEN
[ ] /api/v1/supervisor/scenario/f4_p2_chatgpt_desktop status=redundant
[ ] migration 0017 通过 + 28 新单测 GREEN
[ ] dedup 实战: UIA 抓的 prompt 与 L1 抓的 POST body
    成功 join 到同 pair_id (W7 live test 验)
[ ] HANDOFF-W7-CLOSING-2026-06-05.md (或并入 W9)
```

完成后并入 [`09-wave9-final-closure.md`](09-wave9-final-closure.md) 全量关闭.
