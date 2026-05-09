# ADR-013: `pce_mcp/` 与 `pce_mcp_proxy/` 子系统的 Open Core 归属(OSS)

- Status: Accepted
- Date: 2026-05-08
- Related: ADR-009 (Universal Capture Stack), ADR-010 (Open Core module boundary), ADR-012 (P5.B scope reorder)
- Resolves: P5.B.0 启动前必须回答"`pce_mcp/` 装在哪个仓库"的问题

## Context

ADR-010 制定了 Open Core 模块边界，将代码切分为：

- **OSS Edition**(Apache-2.0)：`pce_proxy/` (L1) / `pce_browser_extension_wxt/` (L3a) / `pce_core/cdp/` (L3d) / `pce_core/sdk_capture_litellm.py` (L3e) / `pce_core/otel_exporter.py` (L3f) 等基础捕获能力
- **Pro Edition**(专有)：`pce_agent_kernel/` (L0) / `pce_agent_frida/` (L2) / `pce_agent_electron/` (L3b) / `pce_agent_ax/` (L4b) 等高级 / 防 pinning / 自动化能力

ADR-010 制定时 PCE 只有一个与 MCP 相关的子系统：`pce_core/otel_exporter.py`，且当时把 "L3f" 这个槽位**指代为 OTel 二级通道**。

2026-05-08 起草的桌面端捕获认知框架(`DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`)发现：MCP 应作为**独立捕获面**(M plane)而非 OTel 的子情况，因此提议在 UCS 增设新的 **L3f MCP middleware** 层(将由 ADR-015 在 P5.B.1 期间正式批准)。

与此同步，本仓库已有的 `pce_mcp/` 子系统(姿态 A，PCE 自身充当 MCP server，让 AI agent 调 `pce_capture` 自报告)以及即将在 P5.B.1 实现的 `pce_mcp_proxy/`(姿态 B，透明转发上游 MCP server 的 JSON-RPC 帧)需要明确 Open Core 归属。

## Decision

**`pce_mcp/` 与未来的 `pce_mcp_proxy/` 都归属 OSS Edition (Apache-2.0)，留在 `github.com/zstnbb/pce` 主仓库。**

### 包含范围

| 路径 | 内容 | 状态 |
|---|---|---|
| `pce_mcp/` | 姿态 A — FastMCP 服务，暴露 6 个 PCE 工具(`pce_capture` / `pce_query` / `pce_stats` / `pce_sessions` / `pce_session_messages` / `pce_capture_pair`) | **OSS** — 已实现 |
| `pce_mcp_proxy/` | 姿态 B — 透明 stdio 中继，复制 JSON-RPC 帧入 PCE 数据库 | **OSS** — P5.B.1 期间实现 |
| `pce_core/normalizer/mcp_jsonrpc.py` | 归一化层：JSON-RPC 帧 → 标准 message 行(role: tool / tool_result / tool_call_id 关联) | **OSS** — P5.B.1 期间实现 |
| `Docs/install/PCE_MCP_INSTALL.md` | 用户安装文档(8 个主机位置) | **OSS** — P5.B.0 期间实现 |
| `tests/e2e_mcp/` | stdio subprocess 级 e2e 测试 | **OSS** — P5.B.0 期间实现 |

### 决策依据(对照 ADR-010 §"模块边界"准则)

ADR-010 设定了将模块归入 OSS 的四条标准。MCP 子系统对每一条都满足：

1. **是否信任根基?** ✅ 是。`pce_mcp` 是用户向自己的 LLM agent 显式开放本地数据库的接口，必须可审计。
2. **是否是 CaptureEvent v2 的公开使用者?** ✅ 是。`pce_capture` tool 的输出走的是与浏览器扩展、L1 代理完全相同的 ingest pipeline。透明性是 Open Core 模型的核心。
3. **是否能开源不损失差异化价值?** ✅ 是。MCP 是公开协议(modelcontextprotocol.io / Anthropic 主导)；任何团队都能在协议之上写中继；闭源不会增加 PCE 的护城河。
4. **是否社区生态重要?** ✅ 是。MCP 在 2024-2025 急速爆发，成为 AI 工具协作事实标准。OSS 化能让 PCE 在 MCP 生态早期建立信任与采用。

### 不归 Pro 的明确原因

Pro 仓的设计目标是承载**有商业护城河价值的能力**(L0 内核驱动 / L2 反 pinning / L3b Electron 注入 / L4b AX 桥)。MCP 中继不属于这一类：

- 协议是开放的、规范是公开的(modelcontextprotocol.io)
- 实现复杂度低(stdio 转发 + JSON 解析)
- 闭源会被开源社区在数周内复现，反而损失先发优势

## Consequences

### 正面

- 与 ADR-010 §"OSS Edition" 表中既有 L1 / L3a / L3d / L3e 入仓策略一致 — `pce_mcp` 归属判断无悬念，新人不会困惑
- v1.1 alpha 可以打从主仓库公开 release tag(`v1.1.0-alpha-mcp-server`)，无需提前与 Pro 仓分叉
- 对 MCP 生态早期采用者(`Cursor` / `Windsurf` / `Claude Code` / `Codex CLI` / `Gemini CLI` 用户)产生明确"这是开源工具"的第一印象

### 负面 / 已知代价

- 任何 MCP 兼容客户端都能直接复用 `pce_mcp_proxy/` 的代码而无须支付 PCE Pro 订阅。这一点是 ADR-010 §"开源决策的护城河逻辑"已经接受的代价。
- Pro 不能从 `pce_mcp_proxy/` 中"截留"高级特性：所有 MCP 中继的核心能力必须留在 OSS。如果未来要做 MCP 中继的 *自动调度 / 健康降级 / 多上游聚合*，那部分应该归 `pce_core/capture_supervisor/`(已划入 Pro)。

### 中性 / 不变

- 依赖方向不变：`pce_mcp/` 与 `pce_mcp_proxy/` 都不依赖 `pce_agent_*`，与 ADR-010 §"依赖方向"一致
- CaptureEvent v2 契约不变：`pce_mcp` 与 `pce_mcp_proxy` 都通过 `insert_capture()` 与 `try_normalize_pair()` 走标准 ingest 路径
- License header 保持:`# SPDX-License-Identifier: Apache-2.0`(已在 `pce_mcp/server.py` / `__main__.py` / `__init__.py` 落地)

## 跟进项

- **ADR-015**(P5.B.1 期间落地):UCS 设计文档 v0.3 增设 **L3f MCP middleware** 层,引用本 ADR 作为 Open Core 归属的依据
- **`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`** §"5 层架构"小节将在 ADR-015 中扩成 5+1 层
- **`README.md`** OSS vs Pro 矩阵表(R6 仓库拆分时)需添加 `L3f MCP middleware` 行,标记为 **OSS(Apache-2.0)**
- **CI import-direction 检查**(R4):`pce_mcp_proxy/` 的导入方向规则与 `pce_proxy/` 完全一致 — 不允许 import `pce_agent_*` / `pce_core.capture_supervisor`

## 参考来源

- `Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md` — Open Core 边界判定四条标准
- `Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md` §1.2 + §4 — M 面与 L3f 的提出
- `pce_mcp/server.py` — 已实现的 6 个 tool 与 ingest 路径

---

*本 ADR 于 P5.B.0 阶段(2026-05-08)起草并接受。一旦 ADR-015 在 P5.B.1 期间落地,本 ADR 与 ADR-015 共同构成 L3f 的完整决策基础(本文管"装哪个仓库",ADR-015 管"层在哪里 + 设计")。*
