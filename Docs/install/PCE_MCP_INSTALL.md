# PCE MCP 安装指引

把 PCE 注册为 MCP 服务后，任何兼容 MCP 的 AI 工具（Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI / Gemini CLI / Cascade in Windsurf 等）的 agent 都可以主动调用 `pce_capture` 等 6 个工具，把交互悄悄记录进你本地的 PCE 数据库。

> **背景文档**：
> - 角色定位：[`pce_mcp/README.md`](../../pce_mcp/README.md)
> - 战略框架：[`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`](../research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md) §1.2（M 面）+ §4（L3f）
> - Open Core 归属：[`ADR-013`](../docs/engineering/adr/ADR-013-pce-mcp-oss-classification.md)（OSS / Apache-2.0）

---

## 0. 共同前置条件

无论装到哪个 MCP 主机，都需要满足：

### 0.1 Python 与依赖

```powershell
# Windows
python --version          # 3.10+
pip install -r requirements.txt
```

```bash
# macOS / Linux
python3 --version         # 3.10+
pip install -r requirements.txt
```

### 0.2 一次性自测（确认 `pce_mcp` 启动正常）

在 PCE 仓库根目录：

```powershell
python -m pce_mcp
# 预期：进程启动后阻塞，没有报错；按 Ctrl+C 退出
```

如果看到 `ModuleNotFoundError: No module named 'mcp'`，回到 0.1 跑 `pip install -r requirements.txt`。

### 0.3 找到 PCE 仓库的绝对路径

每个主机的配置都需要 `python -m pce_mcp` 的执行位置。

- Windows：`F:\INVENTION\You.Inc\PCE Core`（示例；以你的实际路径为准）
- macOS：`/Users/<you>/code/pce`
- Linux：`/home/<you>/code/pce`

下面文档统一用占位符 `<PCE_REPO>` 表示这个绝对路径。

### 0.4 数据库位置

默认存到操作系统的 user data 目录：

- Windows：`%LOCALAPPDATA%\PCE\pce.db`
- macOS：`~/Library/Application Support/PCE/pce.db`
- Linux：`~/.local/share/PCE/pce.db`

要想用自定义目录，在配置里增加环境变量 `PCE_DATA_DIR`。

---

## 1. Claude Desktop（macOS / Windows）

**Tier**：D0（必须工作）· **目标产品 P1**

### 1.1 找到配置文件

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

如果文件不存在，新建之。

### 1.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>"
    }
  }
}
```

如果系统上 `python` 指向 Python 2，请用 `python3`（macOS / Linux）或绝对路径（如 `C:\Python311\python.exe`）。

### 1.3 重启 Claude Desktop

完全退出 Claude Desktop（菜单栏图标 → Quit / 任务栏 → 退出），重新打开。

### 1.4 验证

在 Claude Desktop 里跟它说一句：

> 请调用 `pce_stats` 工具，告诉我数据库现在有多少条 capture。

如果 PCE 已正确注册，Claude 会调用工具并返回 `Total captures: N` 这类结果。

第一次跑会看到工具调用确认气泡。

---

## 2. Cursor

**Tier**：D0（必须工作）· **目标产品 P3**

### 2.1 找到配置文件

`~/.cursor/mcp.json`（Win / Mac / Linux 通用）

### 2.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>"
    }
  }
}
```

### 2.3 重启 Cursor

`Cursor → Settings → MCP` 里能看到 "pce" 状态变绿。

### 2.4 验证

在 Cursor 的 chat 里：

> 用 `pce_query` 工具列出最近 5 条 capture。

---

## 3. Windsurf

**Tier**：D1 · **目标产品 P4**

### 3.1 找到配置文件

`~/.codeium/windsurf/mcp_config.json`（Win / Mac / Linux 通用）

如果用的是 Windsurf 较老版本，可能需要在 Settings → Cascade → MCP 面板里手动添加。

### 3.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>"
    }
  }
}
```

### 3.3 在 Windsurf 内 reload MCP

不需要完整重启 Windsurf；点击 Cascade 工具面板的 "Refresh MCP servers" 即可。

### 3.4 验证

在 Cascade 里：

> 用 `pce_capture_pair` 查 pair_id `aaaaaaaa` 是否存在。

应该收到 `No captures found for pair_id=aaaaaaaa.`（这就证明工具被调到了）。

---

## 4. Claude Code（Anthropic 官方 CLI）

**Tier**：D0（必须工作）· **目标产品 P6**

### 4.1 找到配置文件

`~/.claude/mcp_config.json`

如果文件不存在，运行 `claude mcp add pce ...`（Claude Code v1.x+）会自动创建：

```bash
claude mcp add pce --command python --args -m pce_mcp --cwd <PCE_REPO>
```

或手动编辑：

### 4.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>"
    }
  }
}
```

### 4.3 验证

```bash
claude
> 调用 pce_stats 看一下 PCE 数据库
```

或在交互前用 `claude mcp list` 验证 pce 已注册。

---

## 5. Codex CLI（OpenAI 官方）

**Tier**：D1 · **目标产品 P7**

### 5.1 找到配置文件

OpenAI Codex CLI 自 2025 后续版本起支持 MCP。配置文件位置（以官方文档为准，可能随版本变动）：

- 默认：`~/.openai/codex/mcp_config.json` 或 `~/.config/openai-codex/mcp.json`

如果当前版本不支持 MCP，回退方案是 `python -m pce_mcp --sse` 起 SSE 模式 + 在 CLI 里 `OPENAI_BASE_URL` 配合 PCE 的 L3e CLI gateway（P5.B.4-B 期间实现）。

### 5.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>"
    }
  }
}
```

### 5.3 验证

```bash
codex
> 用 pce_query 看一下最近的 capture
```

---

## 6. Gemini CLI（Google 官方）

**Tier**：D1 · **目标产品 P8**

### 6.1 找到配置文件

Gemini CLI 自 2025 起加 MCP 支持。配置位置（以官方文档为准）：

- 默认：`~/.config/gemini/mcp_config.json` 或类似

### 6.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>"
    }
  }
}
```

### 6.3 验证

```bash
gemini
> Use the pce_stats tool to show PCE database stats.
```

---

## 7. Cascade in Windsurf（PCE 自检验路径）

**特殊用途**：把 PCE MCP 装到 Cascade 自身——你正在使用的这个 IDE 助手，也是 MCP host。这创造了"PCE 用 PCE 验证 PCE"的自闭环测试场景。

> **意义**：在 P5.B.0 期间，这是验证 PCE MCP 资产端到端可工作的**零外部依赖**方式。无需启 Claude Desktop、无需登录、无需 GUI。

### 7.1 找到配置文件

同 §3.1：`~/.codeium/windsurf/mcp_config.json`。

### 7.2 编辑配置

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "cwd": "<PCE_REPO>",
      "env": {
        "PCE_DATA_DIR": "<PCE_REPO>/_pce_self_test_data"
      }
    }
  }
}
```

### 7.3 验证

回到 Cascade，**让 Cascade 调 `pce_capture` 写一条测试数据**：

> 帮我用 `pce_capture` 工具写一条测试 capture，provider=test, conversation_json='{"messages":[{"role":"user","content":"hello PCE"}]}'，然后用 `pce_query` 验证它能查到。

如果两步都成功，PCE MCP 链路完全打通。

### 7.4 隔离开发数据

`PCE_DATA_DIR` 指向独立目录可以避免污染主数据库。验证完成后删除该目录即可清理。

---

## 8. 通用 stdio MCP host

任何符合 [MCP 2024-11-05 规范](https://modelcontextprotocol.io/specification/2024-11-05) 的 stdio host 都可以用同样的配置 schema：

```json
{
  "command": "python",
  "args": ["-m", "pce_mcp"],
  "cwd": "<PCE_REPO>",
  "env": {
    "PCE_DATA_DIR": "<optional override>"
  }
}
```

如果 host 用 SSE/HTTP 而非 stdio，启动 `python -m pce_mcp --sse` 后服务监听 `http://127.0.0.1:9801/sse`。

---

## 9. 故障排查

### 9.1 工具 list 出来但调用 timeout

通常是 `cwd` 路径错误，PCE 的 `pce_core` 包不在 `sys.path` 上。

**修法**：用绝对路径，并验证：

```powershell
cd <PCE_REPO>
python -c "from pce_mcp.server import mcp; print('OK')"
```

返回 `OK` 才能继续。

### 9.2 host 看不到 pce 服务

- 确认 `command` / `args` 写法符合 host 的 schema（不同 host 在键名上略有差异，请回到对应章节）
- 确认 host 进程已重启（部分 host 不会热加载 MCP config）

### 9.3 调用 `pce_capture` 后 dashboard 看不到

- 检查 host 用的 `cwd` 与 PCE Core dashboard 服务用的 `cwd` 一致；不同 cwd 会写到不同 SQLite
- 用 `python -m pce_core.cli inspect` 直接看 SQLite 内容验证

### 9.4 Windows: PowerShell 报 `python` 无效

- 装 Python 时勾选 "Add Python to PATH"
- 或用 `where.exe python` 找到绝对路径填入 `command` 字段

### 9.5 macOS: 系统 Python vs Homebrew Python

如果 PCE 用 Homebrew Python 安装依赖，但 host 调用了系统自带 `/usr/bin/python3`，依赖找不到。**修法**：在 `command` 字段填 Homebrew Python 绝对路径（如 `/opt/homebrew/bin/python3`）。

---

## 10. 卸载

### 10.1 从 host 移除

按各章节定位配置文件，删除 `mcpServers.pce` 对应条目即可。

### 10.2 删除 PCE 数据库（可选）

- Windows：`Remove-Item $env:LOCALAPPDATA\PCE\pce.db -Force`
- macOS：`rm ~/Library/Application\ Support/PCE/pce.db`
- Linux：`rm ~/.local/share/PCE/pce.db`

⚠️ 这会删除所有 PCE 捕获历史（包括浏览器扩展抓的）。仅在确实想"全擦干净"时执行。

---

## 11. 验收 (acceptance — P5.B.0 sign-off)

每个 MCP host 至少完成下列三步：

- [ ] 配置文件已添加 `pce` 条目
- [ ] host 重启后能在 MCP servers 列表里看到 pce
- [ ] 让 host 上的 agent 调一次 `pce_stats` / `pce_query`，返回正常

完成 §1（Claude Desktop） + §2（Cursor） + §4（Claude Code） + §7（Cascade-Windsurf 自检） 即视为 P5.B.0 验证完毕（D0/D1 重点产品 + 自检路径）。其余 host 按需补齐。

---

*版本：v1.0 · 起草于 P5.B.0 (2026-05-08) · 维护人：PCE 维护者 · 反馈渠道：项目 issue tracker*
