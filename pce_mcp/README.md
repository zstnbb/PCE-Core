# `pce_mcp` — PCE 的 MCP 服务

> **状态**：OSS（Apache-2.0），由 [ADR-013](../Docs/docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md) 正式归属
>
> **角色**：在 PCE 桌面端捕获认知框架（[`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`](../Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md)）的 **M 面**（MCP 捕获面）上扮演 **姿态 A — PCE 作为 MCP 服务**

## 一句话定位

`pce_mcp` 让 PCE 出现在任何兼容 MCP 的 AI 工具旁边（Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI / Gemini CLI / Cascade-Windsurf 等），AI agent 通过调用 `pce_capture` 等工具把"自己刚才做了什么"主动报告进 PCE 的本地数据库。

```
    AI agent (Claude / GPT / Gemini ...)
            │
            │  "我刚回答了用户什么、调用了什么工具"
            ▼
    pce_mcp (this package)         ← 你在这里
            │
            ▼
    pce_core ingest pipeline       ← 与浏览器扩展、L1 代理走同一条路
            │
            ▼
       本地 SQLite
```

## M 面的两种姿态

`pce_mcp` 只承担姿态 A。完整的 M 面捕获能力还包括姿态 B 与姿态 C（后两者在 P5.B.1 / P5.B.2 实现）：

| 姿态 | 作用 | 实现 | 状态 |
|---|---|---|---|
| **A — PCE 作为 MCP 服务** | agent 主动调 `pce_capture` 自报告 | **本包 `pce_mcp/`** | ✅ v1.0 已上线 |
| **B — PCE 作为 MCP 中继** | 透明拦截 host ↔ upstream 之间的 JSON-RPC 帧 | `pce_mcp_proxy/`（待建） | ⏳ P5.B.1 实现 |
| **C — Electron preload + MCP 子进程注入** | 既听 chat 也跟踪 child_process MCP spawn | `pce_preload/` 内 | ⏳ P5.B.2 实现 |

## 暴露的工具（6 个）

实现在 [`server.py`](./server.py)。

| Tool | 用途 | 主要场景 |
|---|---|---|
| `pce_capture` | 把一次 AI 交互（请求/响应对 或 完整对话）写入 PCE | agent 主动记录 |
| `pce_query` | 查最近 N 条 capture（可按 provider / host / direction 过滤） | agent 自查"我之前抓到了啥" |
| `pce_stats` | 数据库摘要：总条数、按 provider、按 direction、按 source | agent 一句话回报状态 |
| `pce_sessions` | 列出归一化后的对话 session | agent 找以前的会话 |
| `pce_session_messages` | 拉某个 session 的所有 message 记录 | agent 复盘上下文 |
| `pce_capture_pair` | 通过 `pair_id` 拉一对 request/response 原文 | agent 调试某次失败抓取 |

所有写入均经 `pce_core/normalizer/pipeline.py` 自动归一化为 `sessions` + `messages` 行，与浏览器扩展、L1 代理产生的数据完全同形。

## 快速启动

### 本机自测（不接 host）

```powershell
# 项目根目录
python -m pce_mcp                  # stdio 传输（默认）
python -m pce_mcp --sse            # SSE 传输, 0.0.0.0:9801
```

stdio 模式启动后会等待 newline-delimited JSON-RPC 2.0 帧从 stdin 进来。

### 接入 Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI / Gemini CLI / Cascade-Windsurf 等

完整安装步骤见 [**`Docs/install/PCE_MCP_INSTALL.md`**](../Docs/install/PCE_MCP_INSTALL.md)，覆盖 8 种主流 MCP 主机，含验证与卸载。

## 数据库位置

`pce_mcp` 写入由 [`pce_core/config.py`](../pce_core/config.py) 的 `DB_PATH` 决定，默认是：

- Windows：`%LOCALAPPDATA%\PCE\pce.db`
- macOS：`~/Library/Application Support/PCE/pce.db`
- Linux：`~/.local/share/PCE/pce.db`

通过环境变量 `PCE_DATA_DIR` 可重定向到自定义目录（测试用例就是这么用的）。

## 测试

```powershell
# 进程内单测（直接 import 函数）
python -m pytest tests/test_mcp.py -v

# stdio subprocess 级 e2e（真 MCP 协议握手）
python -m pytest tests/e2e_mcp/ -v
```

## 与 UCS 的关系

`pce_mcp` 实现的是 UCS（[`Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md`](../Docs/docs/engineering/UNIVERSAL-CAPTURE-STACK-DESIGN.md)）即将增设的 **L3f MCP middleware** 层（详见 ADR-015 拟）。在 UCS 五层视角下：

- L0 内核驱动 — Pro
- L1 TLS MITM — OSS（与 `pce_mcp` 兄弟）
- L2 Frida SSL hook — Pro
- L3 应用 hook：3a 浏览器扩展 / 3b Electron preload / 3c IDE 扩展 / 3d CDP / 3e LiteLLM / **3f MCP middleware ← `pce_mcp` 在这里**
- L4 UI / 无障碍

依赖方向（[ADR-010](../Docs/docs/engineering/adr/ADR-010-open-core-module-boundary.md)）：`pce_mcp/` **不能** import `pce_agent_*` 或 `pce_core.capture_supervisor`。CI import-direction 检查会硬拦截。

## License

Apache-2.0。每个源文件含 `# SPDX-License-Identifier: Apache-2.0` 头。

## 进一步阅读

- 战略：[`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`](../Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md) §1.2 + §4
- 验证矩阵：[`Docs/stability/DESKTOP-PRODUCT-MATRIX.md`](../Docs/stability/DESKTOP-PRODUCT-MATRIX.md) §4.1（P1 Claude Desktop 主路径就是 M / L3f）
- 执行计划：[`Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md`](../Docs/handoff/HANDOFF-IDE-DESKTOP-KICKOFF.md) §4.1（P5.B.0 — MCP asset formalisation）
- ADR-013（开源归属）：[`Docs/docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md`](../Docs/docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md)
