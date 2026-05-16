# ADR-022: UIA Accessibility — OSS Boundary

- **Status:** Proposed (awaiting owner approval, P5.D.1 Pre-flight §4)
- **Date:** 2026-05-15
- **Phase:** P5.D.1 Redundancy Sprint, Phase B Wave 7 前置
- **Supersedes (partial):** N/A — first ADR on UIA driver scope
- **Related:** ADR-009 (UCS canonical), ADR-010 (Open Core), ADR-018
  (MSIX strategy — §6 标记 P2 三死锁), ADR-019 (maintenance), ADR-021
  (capture_supervisor OSS boundary — 本 ADR 复用边界模板),
  ADR-023 (gRPC-web protobuf decoder, 本冲刺并行新增)

---

## 1. 决议 (一句话)

**Windows UI Automation (UIA) accessibility driver `pce_core/uia_driver/`
落 OSS, 作为 P5.D.1 锁定的 13 P0 场景之 F4 P2 ChatGPT Desktop 的第 3 条
独立 leg (basis = `ui_automation_tree`). macOS AXUIElement 等价能力
推 P5.D.2 不在本 ADR. UIA 默认 opt-in 关闭, 用户必须主动开启;
redact mandatory.**

---

## 2. 背景

### 2.1 触发问题

`Docs/stability/REDUNDANCY-AUDIT-MATRIX.md` §3.4 P2 ChatGPT Desktop
按 ADR-018 §6 经验证锁定:

- L3g 结构死 (IndexedDB 空, 会话内容在服务端)
- L3b NODE_OPTIONS 死 (Electron Fuse 锁)
- L3d CDP 死 (MSIX 6 实验全败)
- L2 Frida 永久 Pro 渠道

可用的只有 L1 + A2 (corroboration, 算 1.5 条). STRICT MODE 锁
≥3 V-GREEN, 唯一 path-out 是 L4b UI Automation.

### 2.2 与 ADR-021 同模板

ADR-021 的 §6 决策依据 (基础能力归 OSS, 差异化能力归 Pro) 在 UIA 同样
适用:

- UIA 是 Windows 系统提供的 accessibility API, 不是 PCE 独家专利
- 任何 OSS 桌面自动化项目都可以用 UIA 抓 prompt + response
- 把 UIA driver 锁在 Pro 等于 OSS 用户拿不到 P2 的第 3 条腿 — 与
  STRICT MODE 13/13 标杆冲突

---

## 3. 决议内容

### 3.1 OSS 落点

```
pce_core/uia_driver/                        ← 新, OSS (本 ADR)
├── __init__.py
├── uia.py              — IUIAutomation COM 包装; element finder
├── chatgpt_desktop.py  — ChatGPT Desktop 具体的 prompt/response/send element 定位
├── capture.py          — 把 UIA 抓到的 prompt+response → insert_capture
├── config.py           — 默认 opt-in 关闭; 用户主动启用
└── redact.py           — mandatory redaction (复用 OSS redact 规则)

pce_core/migrations/0017_uia_source.py       ← 新
tests/test_uia_driver_unit.py                ← 新, 18 单测
tests/test_uia_redact.py                     ← 新, 6 单测
tests/e2e_uia/test_chatgpt_desktop_uia.py    ← 新, 4 e2e
```

### 3.2 Pro 注册点 (与 ADR-021 接口一致)

```http
POST /api/v1/supervisor/legs/register     ← ADR-021 §3.2 复用
{
  "source": "L4b_uia_accessibility_pro",
  "scenario_ids": ["f4_p2_chatgpt_desktop_macos"],   ← Pro 加 macOS 等
  "independent_basis": "ax_ui_element",              ← macOS 等价
  "agent_pid": 12345,
  "agent_version": "pro-1.0.0"
}
```

OSS 端先做 Windows; Pro 通过此接口加 macOS / Linux AT-SPI 等独立 leg.

### 3.3 不变式

- 默认 `enabled=False`. 用户主动开启 + 看到 explainer
- redact mandatory: 只采**目标 ChatGPT 窗口内**的 prompt input +
  response output element; 兄弟窗 / 子窗 / 通知 / 剪贴板一律不采
- raw_captures.meta_json 不存 UIA 全 tree, 只存 element type +
  prompt/response 文本经 redact 后的字符串
- `independent_basis = "ui_automation_tree"`, 与 L1 / A2 不同

### 3.4 隔离: Pro 不可改 OSS 仲裁

- Pro 不能 fork UIA element finder
- Pro 不能旁路 capture (raw_captures 必须经 ingest API + dedup)
- Pro 可以扩展 leg 类型 (macOS / Linux), 不能扩展 status 状态机

---

## 4. 后果

### 4.1 OSS 用户得到

- v1.1.6 OSS 用户在 P2 ChatGPT Desktop 拿到 3 条 V-GREEN
- UIA driver 在所有 release 渠道一致
- 任何"L1 / A2 都挂了"的极端场景, UIA 仍能抓 (basis 真不同)

### 4.2 Pro 渠道

- macOS AXUIElement 等价能力推 P5.D.2 (不在本 ADR 范围)
- Linux AT-SPI 等可未来 Pro 接入

### 4.3 维护

- UIA tree 抓取受 ChatGPT Desktop UI 升级影响 — RECON 阶段固化
  automation_id, ADR-019 nightly hermetic test 监控漂移
- 隐私敏感性高, opt-in + redact 是硬约束

---

## 5. 实施 (P5.D.1 Wave 7)

落地清单见
`@Docs/stability/redundancy-sprint/07-wave7-chatgpt-desktop-uia.md` §3.

时间预算: 140h, 3-4 周 wall-clock 单人专职.

---

## 6. 决策依据

| 维度 | A: UIA driver 落 OSS (本 ADR) | B: 全部留 Pro |
|---|---|---|
| OSS P2 ≥3 V-GREEN 达成 | ✅ | ❌ |
| Pro 差异化 | ✅ macOS / Linux 是 Pro 独家 | (无所谓) |
| ADR-010 Open Core 一致 | ✅ (UIA 是系统能力, 不是 PCE 价值) | 与 ADR-010 §3 冲突 |
| ADR-019 maintenance 一致 | ✅ (维护分散) | ❌ |
| 实施复杂度 | 低 (单 PR) | 高 (跨仓库) |
| 标杆性 | ✅ (13/13 STRICT MODE 不破) | ❌ (P2 永远凑不到 3 条) |

---

## 7. 不做的事

- ❌ 不在 UIA driver 里加 OCR (出范围, 推 P6 if needed)
- ❌ 不在 UIA driver 里加全屏抓取 (隐私无法约束)
- ❌ 不让 Pro fork UIA element finder
- ❌ 不允许默认 opt-in 开启

---

## 8. 验收 (P5.D.1 Wave 7 同步)

- 28 单测 GREEN (uia_driver_unit 18 + uia_redact 6 + e2e_uia 4)
- /api/v1/supervisor/scenario/f4_p2_chatgpt_desktop status=redundant
- dedup 实战: UIA 抓的 prompt 与 L1 抓的 POST body
  成功 join 到同 pair_id (W7 live test 验)
- ADR status: Proposed → Adopted (owner sign-off)

---

## 9. 一句话锚点

**P2 ChatGPT Desktop 的第 3 条腿不能锁在 Pro — 锁在 Pro 等于 STRICT
MODE 13/13 永远凑不齐. UIA 是系统能力, 应该 OSS 用户共享; Pro 的差
异化在 macOS / Linux 等价能力上.**
