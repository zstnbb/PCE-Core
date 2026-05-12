# Desktop D-Case Execution Standard

> **Version**: pce-desktop-d-case-standard-2026-05-12
>
> **Status**: canonical — desktop lane chat-region 的 acceptance bar.
>
> **Adopted**: 2026-05-12 (P5.C.0 契约冻结)
>
> **Sibling**: `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md` (browser lane T-case standard, 已存在). 本文件做的是 Desktop chat-region 等价物.
>
> **Scope**:
> - 本文件仅覆盖 **chat-region D00–D22** (23 D-cases, P5.B chat-region 已落地).
> - **cowork-region C00–C16** (17 C-cases) 定义在 `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.B, 本文件**不重复**, 仅引用.
> - **code-region E00–E25** (26 E-cases) 定义在 `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.C, 本文件**不重复**, 仅引用.
> - 三个 region 共同构成 desktop lane 的 verification-as-data 契约 (per `META-PIPELINE-FRAMEWORK.md` §2.2).
>
> **Authority**: 与 browser lane 平行, 但适配桌面端 UIA + L1 + L3g 多通道捕获模型. 任何 D-case verdict 由 `tests/e2e_desktop_ui/` 框架产出, 经 PCE Core HTTP closed-loop 验证.
>
> **Audience**: Cascade、Test Conductor 实现者、桌面端 driver 维护者、外部贡献者.

---

## 1. Global Pass Bar (与 PCE-PROBE-E2E-EXECUTION-STANDARD §"Global Pass Bar" 同形)

每个 PASS 必须证明三段链路都过, 即 capture-storage-render 三件套:

- **Capture**: UIA driver / 网络拦截 / L3g 持久化 watcher 至少一条通道观察到 D-case 触发的产物 (token-bearing prompt + assistant 状态 / 文件 attachment / 工具调用 frame).
- **Storage**: PCE Core 通过 `/api/v1/sessions` + `/api/v1/sessions/{id}/messages` + `/api/v1/captures` 暴露归一化后的状态.
- **Render**: 已存储状态包含 dashboard 不依赖 raw payload 即可呈现的字段 (含 `content_text` / `content_json.rich_content` / `attachments` / `branch_id` / `interaction_kind` 等).

**SKIP 允许**仅当外部条件不可控:
- product UI 不暴露该功能 (e.g. ChatGPT Desktop 没有 cowork)
- 模型不暴露该 surface (e.g. Haiku 没有 thinking)
- 账号 tier 不开放 (e.g. project scope 在 free tier 不可用)
- pinning / 网络层 block (D-case 整体跳过, 不算 capture-pipeline FAIL)

**SKIP 不允许**:
- selector 漂移 / UIA tree 找不到元素
- 网络抓到了但 normalizer 漏处理
- 三段链路其中一段缺失但其他段过

**多通道允许**: D-case 可以跨 N 面 (L1) + L3g 持久化 + L3f MCP middleware + UIA 多个证据源同时取证. 单证据不足以 PASS, 多证据冗余反而是设计意图.

---

## 2. 三个核心结构化案例的升级口径

来自 chat-region 实测 (HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP/SKIP-CONVERSION/WEB-PARITY 的发现). D08 / D14 / D15 / D16 是结构化深度案例, 不能只用 token / content delta 当 PASS.

### 2.1 D08 MCP tool call

完整生命周期才算 PASS:

- **Capture**: MCP middleware (`pce_mcp_proxy`) 抓到 `tools/call` 请求 + `tools/result` 响应 frame, 或者 `pce_mcp` server (姿态 A) 接到 PCE 自身工具调用. 二者**至少一条**满足.
- **Storage**: PCE Core `messages` 至少 2 行 (role=tool_call + role=tool_result), 或者 1 行 assistant message 的 `content_json` 含 `attachments[].type=='tool_call'` + `attachments[].type=='tool_result'`, 且 `tool_call_id` 关联.
- **Render**: dashboard 能 reconstruct tool 调用 + 结果对应关系, 不只是平铺消息.

仅 raw_captures 行数增加不是 PASS.

### 2.2 D14 Edit user message + branch fork

- **Capture**: edit 操作触发新 capture, 旧 user turn 仍存在.
- **Storage**: 新 user message 与原 user message 共享 `branch_parent_id` (per migration 0008 + `2026-04-26-regenerate-edit-branch-semantics.md`); 至少 2 个 branch 在 `branches` 表; collapsed view 默认返回 latest branch.
- **Render**: dashboard expand/collapse toggle 能切换显示模式.

仅 user content 改变 + 新 message 不是 PASS.

### 2.3 D15 Regenerate

- **Capture**: regenerate 点击触发新 assistant capture.
- **Storage**: 新 assistant message 与原 assistant 共享 `branch_parent_id` (该 parent_id 指向用户 turn); `variant_index` ≥ 2 在 user turn.
- **Render**: dashboard 能识别 variant group + 当前 variant.

仅 assistant content 不同不是 PASS.

### 2.4 D16 Branch flip

- **Capture**: branch 切换后, capture 反映 currently-visible branch, 不是隐藏 branch DOM.
- **Storage**: `branch_id` + `branch_parent_id` + `branch_choices` 字段都齐.
- **Render**: dashboard 能重建 branch tree.

仅 latest user message has token A 不是 PASS.

### 2.5 D20/D21 Artifact (text/interactive)

来自 chat-region web-parity 实测的 headline win:

- **Capture**: `tool_use.input_json_delta` SSE events 完整收集 (chat-region L1 已验证).
- **Storage**: assistant `content_json` 含完整 artifact body + `content_type` 区分 (markdown / SVG / HTML / JSX).
- **Render**: dashboard 能渲染 artifact 卡片 (即使 P5.C 不做 user dashboard, 渲染契约也要写在 case 里).

仅 chat-side text mention artifact 不是 PASS.

---

## 3. Case Matrix (D00–D22)

每条 D-case 的 capture/storage/render/pass_gate 四元组:

| Case | Scenario | Capture bar | Storage bar | Render bar | Pass gate |
|---|---|---|---|---|---|
| **D00** | product detection | Driver attached + L1/L3g/L3f at least one source identifies product | `sessions` 第一行 `provider/tool_family/created_via` 与产品对齐 | dashboard "适配健康"显示该产品 ID + plane 列表 | 三段都过 |
| **D01** | single-turn chat | UIA send prompt → driver observes assistant final state | `messages` 2 行 (user + assistant), 同 `session_id` | text renders in dashboard | 三段都过, no missing role |
| **D02** | streaming complete | 流结束后驱动器观察终态 | 存储的 assistant text 完整, 不是 partial prefix | renders 不带流式 artifacts | 终答案在 PCE Core |
| **D03** | multi-turn (5+) | 5 turn 连续发, 每 turn 都被捕获 | 1 session, 10 messages, `turn_index` 严格单调 | 时间线呈现 5 turn 顺序 | 不允许 session 拆分 |
| **D04** | stop / cancel | cancel 操作 + visible 部分被捕获 | request 行存在; partial body 若有则 `interaction_kind="cancelled"` | dashboard 区分 cancel vs error | request-only normalize 路径必须存在 (deferred to P5.C.5) |
| **D05** | model switch | switch 后下一 turn 的 model_name 反映新值 | `messages.model_name` 与产品 UI 切换匹配 | session detail 显示 model 切换历史 | 模型切换被显式记录 |
| **D06** | file attachment (generic) | 上传 csv/txt/docx 后 driver 拦截 | user `content_json.attachments[]` 含 `file_uuid` + `mime` + `file_kind` | dashboard 渲染文件卡 | attachment metadata, not only filename text |
| **D07** | code block | fenced code 在 assistant 流式中出现 | code 存在 `content_json.rich_content.blocks` 或同等结构, language tag 保留 | dashboard 渲染为 code block | code-specific storage, not plain text |
| **D08** | MCP tool call | (见 §2.1) | (见 §2.1) | (见 §2.1) | tool_call_id 关联 + 三段同时过; 仅 raw_captures 增加不是 PASS |
| **D09** | inline completion (IDE only) | 接受 ghost-text 触发 capture | message `interaction_kind="completion"` (或同等) | dashboard 区分 completion vs chat | applicable only to IDE-class (Cursor / Windsurf / Copilot) |
| **D10** | error mid-stream | upstream 4xx/5xx 或 proxy kill | error stored as `status_code + error text`, no phantom assistant | dashboard renders error distinctly | pipeline 不崩 + 下次请求 clean |
| **D11** | long-context (≥8K) | 50 turn 连续无 drop | 1 session, 100 messages, cumulative `token_estimate` ≥ 8000 | dashboard 不卡 | zero message loss, no session split |
| **D12** | silent on idle | 5min 无 user activity | 0 chat-relevant message/session 写入 | dashboard 不变化 (除诊断) | heartbeat noise OK in raw_captures, NOT in messages |
| **D13** | extended thinking | thinking_delta SSE 或同形 thinking text track 被捕获 | thinking 存在 `content_json.thinking` 或 separate track, 不污染 final content_text | dashboard 能切换显示 thinking | 即使无 toggle (Haiku tier), 文本形式 thinking 也算 PASS (per chat-region SKIP-conversion run) |
| **D14** | edit user message | (见 §2.2) | (见 §2.2) | (见 §2.2) | branch_id + branch_parent_id 齐; 仅 user text 改不是 PASS |
| **D15** | regenerate | (见 §2.3) | (见 §2.3) | (见 §2.3) | variant 关系存在; 仅 content delta 不是 PASS |
| **D16** | branch flip | (见 §2.4) | (见 §2.4) | (见 §2.4) | 显式 branch identity; 仅 latest 不是 PASS |
| **D17** | image / vision | image upload via clipboard or file dialog 被捕获 | user `attachments[]` 含 `file_kind="image"` + dimensions | dashboard 渲染图片卡 | attachment metadata required |
| **D18** | PDF document | PDF upload + summary prompt | user `attachments[]` 含 `file_kind="document"` + page count where surfaced | dashboard 渲染 document 卡 | summary references PDF contents |
| **D19** | project scope | project 内 chat 发送 | `messages.session_key` extract from chat UUID (NOT `/project/` URL); `layer_meta.project_id` populated | dashboard 显示 project context | architectural finding: Claude Desktop projects use `/chat_conversations/` not `/project/` |
| **D20** | artifact (text) | (见 §2.5) | (见 §2.5) | (见 §2.5) | full artifact body in content_json |
| **D21** | artifact (interactive) | (见 §2.5) | (见 §2.5) | (见 §2.5) | full artifact source code in content_json |
| **D22** | writing style | style switch BEFORE send 触发 metadata capture | `sessions.layer_meta.style` populated; assistant `content_text` clean (no style boilerplate) | dashboard 显示 active style | style metadata on session |

### 3.1 SKIP allowed keywords (与 PCE-PROBE-E2E-EXECUTION-STANDARD 对齐)

```python
_EXTERNAL_SKIP_KEYWORDS = (
    "not logged in", "quota", "rate limit", "rate-limit",
    "no native surface", "not available", "feature unavailable",
    "account", "model does not", "provider did not",
    "ui targeting failure",   # e.g. driver 无法定位元素
    "popup not in tree",      # UIA tree 不包含弹窗 (per chat-region SKIP-conversion run)
)

_STRICT_EXTERNAL_SKIP_KEYWORDS = (  # for strict_gap_on_skip cases
    "not logged in", "quota", "rate limit", "rate-limit",
    "no native surface", "feature unavailable",
)
```

D14 / D15 / D16 / D20 / D21 / D22 默认 `strict_gap_on_skip=True`: 这些是结构化案例, "popup not in tree" 不算外部 SKIP, 算 GAP.

---

## 4. Machine-Readable (P5.C.0 待落, 用于 Test Conductor 接入)

仿 `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_probe\execution_standard.py` 形式, 在 `tests/e2e_desktop_ui/execution_standard.py` 落地:

```python
# SPDX-License-Identifier: Apache-2.0
"""Executable completion standard for the desktop chat-region D-case matrix."""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Iterable

STANDARD_VERSION = "pce-desktop-d-case-standard-2026-05-12"


@dataclass(frozen=True)
class DCaseStandard:
    case_id: str
    name: str
    capture: str
    storage: str
    render: str
    pass_gate: str
    allowed_skip: tuple[str, ...] = ()
    strict_gap_on_skip: bool = False
    region: str = "chat"     # "chat" / "cowork" / "code"


CASE_STANDARDS: dict[str, DCaseStandard] = {
    "D00": DCaseStandard(
        "D00", "product_detection",
        "Driver attached AND ≥1 capture source (L1/L3g/L3f) identifies the product.",
        "First sessions row provider/tool_family/created_via aligns with product manifest.",
        "Dashboard 'health' page displays product ID + plane list.",
        "All three layers required.",
    ),
    "D01": DCaseStandard(
        "D01", "single_turn",
        "UIA driver sends prompt; assistant final state observed by ≥1 source.",
        "messages table has 2 rows (user + assistant), same session_id.",
        "Dashboard renders both texts in time-line order.",
        "Pass requires capture + storage + renderable text.",
    ),
    # ... D02–D22 同样 7 列
}


def standard_for_case(case_id: str) -> DCaseStandard | None:
    return CASE_STANDARDS.get(case_id.upper())


def is_external_skip(case_id: str, summary: str) -> bool:
    """与 PCE-PROBE-E2E-EXECUTION-STANDARD `is_external_skip` 形式相同."""
    ...
```

P5.C.0 落地完整 dataclass; P5.C.2 Test Conductor 通过 `case_standard_module: tests.e2e_desktop_ui.execution_standard` 接入此 dataclass.

---

## 5. 与 cowork-region (C-case) 和 code-region (E-case) 的关系

三者一起构成 desktop lane 的 verification-as-data:

```
desktop lane case set:
├── D-case (chat-region):     D00–D22, this document
├── C-case (cowork-region):   C00–C16, see DESKTOP-PRODUCT-MATRIX.md §5.B
└── E-case (code-region):     E00–E25, see DESKTOP-PRODUCT-MATRIX.md §5.C
```

总计 66 个 case, 但 per-product applicability 不同 (per `DESKTOP-PRODUCT-MATRIX.md` §5.1 + §5.B.1 + §5.C.1):

| Product | D-case applicable | C-case applicable | E-case applicable | Total |
|---|---|---|---|---|
| P1 Claude Desktop | 22 | 16 | 26 | **64** |
| P2 ChatGPT Desktop | 21 | 1 | 0 | 22 |
| P3 Cursor | 17 | 0 | 0 | 17 |
| P4 Windsurf | 17 | 0 | 0 | 17 |
| P5 Copilot | 9 | 0 | 0 | 9 |
| P6 Claude Code | 12 | 7 (CLI mirror) | 18 (CLI mirror) | 37 |
| P7 Codex CLI | 12 | 1 (best-effort) | 5 (best-effort) | 18 |
| P8 Gemini CLI | 12 | 1 (best-effort) | 5 (best-effort) | 18 |

P1 Claude Desktop 是唯一 64 case 全适用的 D0 product, 也是 P5.B 已经实测的 reference 实现.

---

## 6. Per-product release gate (与 DESKTOP-PRODUCT-MATRIX §3 Tier-D 一致)

| Tier | release gate | failure handling |
|---|---|---|
| **D0** (Claude Desktop / Cursor / Claude Code) | ≥85% applicable cases PASS **AND** ≥2 plane both PASS for D-case + C-case (where applicable) | block release |
| **D1** (Windsurf / Codex CLI / Gemini CLI) | ≥70% applicable cases PASS | ship with documented gap |
| **D2** (ChatGPT Desktop / Copilot) | ≥50% applicable cases PASS OR documented why not | ship with explicit "best-effort" label |

P5.B chat-region 已经把 P1 Claude Desktop 推过 D0 释放门 (19/22 = 86%). C-region + E-region 释放门由 P5.B.5 / P5.B.6 余项轨道完成.

---

## 7. Reference & cross-doc

### 7.1 同形姊妹文档
- Browser lane: `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PROBE-E2E-EXECUTION-STANDARD.md`
- Cowork-region: `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.B
- Code-region: `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\DESKTOP-PRODUCT-MATRIX.md` §5.C
- Health beacon: `@f:\INVENTION\You.Inc\PCE Core\Docs\stability\PCE-PIPELINE-HEALTH-MATRIX.md`

### 7.2 上游策略 / 决策
- `@f:\INVENTION\You.Inc\PCE Core\Docs\research\DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` (4 plane)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\META-PIPELINE-FRAMEWORK.md` (三件套契约)
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-019-maintenance-as-first-class-concern.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\docs\engineering\adr\ADR-018-msix-store-app-capture-strategy.md` (三主轴 / H2/H3/H4)

### 7.3 实施雏形
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\drivers\base.py` — DesktopDriver ABC
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\drivers\claude_desktop.py` — Claude Desktop 已落地 driver
- `@f:\INVENTION\You.Inc\PCE Core\tests\e2e_desktop_ui\cases\` — chat-region case 实现 (P5.B chat-region)

### 7.4 chat-region 实测 evidence
- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-WEB-PARITY-2026-05-10.md`
- `@f:\INVENTION\You.Inc\PCE Core\Docs\handoff\HANDOFF-P1-CLAUDE-DESKTOP-SKIP-CONVERSION-2026-05-10.md`

---

## 8. Known issues (carry-forward to P5.C.5 cleanup)

来自 chat-region 5 个 sub-run 的总结:

- **D04 cancel** — `pipeline.try_normalize_pair` requires both sides; cancel-mid-stream means response never arrives. 1-2 day fix scoped (request-only normalization path). Tracked: `HANDOFF-P1-CLAUDE-DESKTOP-CHAT-FULL-SWEEP-2026-05-10.md` §3. P5.C.5 修或显式延后 P6.
- **D15 driver gap** — Retry 按钮 UIA 定位需要 `automation_id` 锁定; 当前用 visible name fallback 在 multi-turn 场景下偶发 mistarget. 中等优先级.
- **D17/D18 Chromium-rendered popup** — 上传按钮的 popup 对 UIA 不透明, `{DOWN}{ENTER}` 不 dispatch. 需要不同 attach surface (drag-drop / CF_DIB / direct Chromium IPC). P6 范围.
- **Inspector scripts 散根目录** — `_inspect_window_*.py` 等 P5.B 残留. P5.C.5 迁到 `tests/e2e_desktop_ui/inspectors/`.

---

## 9. 一句话锚点

**Desktop D-case 是 chat-region 的 capture-storage-render 三段保险, 与 C-case (cowork) + E-case (code) 共同构成 desktop lane 的 verification-as-data 契约. 单证据不算 PASS, 多通道冗余是设计意图.**
