# PCE MCP Proxy 安装指引（姿态 B / UCS L3f）

把 `pce_mcp_proxy` 夹在你的 MCP 主机（Claude Desktop / Cursor / Windsurf / Claude Code / Codex CLI / Gemini CLI / Cascade-Windsurf）和真正的 MCP server（filesystem / git / postgres / sequential-thinking / 任何上游）之间，**让所有 stdio JSON-RPC 流量都被 PCE 透明捕获**。

主机和上游都察觉不到中间多了一层。改动只发生在主机的 MCP 配置文件里——把原来指向上游的 `command + args`，包一层 `python -m pce_mcp_proxy -- <原命令>`。

> **背景文档**：
> - 角色定位：[`pce_mcp_proxy/README.md`](../../pce_mcp_proxy/README.md)
> - 与姿态 A 的对照：[`pce_mcp/README.md`](../../pce_mcp/README.md) §M 面的两种姿态
> - 战略框架：[`Docs/research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md`](../research/DESKTOP-CAPTURE-COGNITIVE-FRAMEWORK.md) §4.1
> - UCS 增设决议：[`ADR-015`](../docs/engineering/adr/ADR-015-ucs-l3f-mcp-middleware.md)

---

## 0. 共同前置条件

### 0.1 已经按 [`PCE_MCP_INSTALL.md`](./PCE_MCP_INSTALL.md) §0 跑通过姿态 A

姿态 B 复用 PCE 的同一套 Python 环境、同一个 SQLite 数据库、同一份 normalizer 注册表。如果你**还没装过 `pce_mcp`**，先把 §0.1（Python 依赖）+ §0.3（找到 `<PCE_REPO>`）+ §0.4（数据库位置）跑一遍，再回来。

### 0.2 一次性自测（确认 proxy 自身能起来）

在 PCE 仓库根目录：

```powershell
# Windows
python -m pce_mcp_proxy --help
```

```bash
# macOS / Linux
python3 -m pce_mcp_proxy --help
```

应该看到完整的 flag 帮助文本，含 `--upstream-name / --data-dir / --print-stats / --quiet`。看到帮助即说明包导入正常、ADR-015 对应的 migration 0009 + `SOURCE_MCP_PROXY` 常量都已就位。

### 0.3 跑一次"自身做上游"的烟测

姿态 B 的最小验证：用 proxy 把 `pce_mcp`（姿态 A 的 server）当上游包一层。两个组件都在 PCE 自家代码里，验证不依赖任何第三方 MCP server。

```powershell
# 一个终端：起 proxy + pce_mcp 作为上游
python -m pce_mcp_proxy --upstream-name self --print-stats -- python -m pce_mcp
# 这会阻塞，等 stdin。从另一个终端发一个 JSON-RPC 帧进去？
# 实操中你不会手敲 JSON-RPC，下面每个主机段的 verify 步骤会替你做。
```

或者用 e2e 测试做最严的烟测：

```powershell
python -m pytest tests/e2e_mcp/test_pce_mcp_proxy_stdio.py -v
```

预期 11/11 GREEN（如果中途某 case 红了，先回到 [`pce_mcp_proxy/README.md`](../../pce_mcp_proxy/README.md) 排查，再回来装）。

### 0.4 配置模板（万能形态）

任何 MCP 主机的配置文件里，原本一段是：

```json
{
  "mcpServers": {
    "<friendly-name>": {
      "command": "<bin>",
      "args": ["<arg1>", "<arg2>", "..."]
    }
  }
}
```

包一层之后变成：

```json
{
  "mcpServers": {
    "<friendly-name>": {
      "command": "python",
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "<short-tag>",
        "--",
        "<bin>", "<arg1>", "<arg2>", "..."
      ],
      "env": {
        "PCE_DATA_DIR": "<absolute path to writable dir>"
      }
    }
  }
}
```

注意：

- **`--` 是字面量**（两个连字符），分隔 proxy flags 与上游 argv。漏了它，proxy 会把上游命令当成自己的 flag 解析，立即报错。
- **`--upstream-name` 是友好标签**，仅用作 PCE 数据库里 `provider = "mcp:<short-tag>"` 的命名前缀。漏了也行（proxy 会从上游 argv 启发式推一个），但建议显式写——后面在 dashboard / 查询里更好辨认。
- **`PCE_DATA_DIR`** 不是必需，但强烈建议写。不写就走 OS 默认 user data 目录，多个主机/proxy 副本可能把数据分散到几个数据库里。
- **`command` 一定写 `python`**（macOS / Linux 用 `python3`），不要直接写上游的可执行路径——上游已经在 args 里 `--` 之后了。

下面 8 段每段给一个**具体可粘贴**的样例。其他上游照着 §0.4 模板套用即可。

---

## 1. Claude Desktop（macOS / Windows）

**Tier**：D0（必须工作）· **目标产品 P1**

### 1.1 配置文件位置

- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`

### 1.2 编辑配置 — 包一层 filesystem server

如果原来你的配置是这样：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/projects"]
    }
  }
}
```

改成：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "python",
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "filesystem",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/you/projects"
      ],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "/Users/you/Library/Application Support/PCE"
      }
    }
  }
}
```

> Windows 路径示例：`"PCE_DATA_DIR": "C:\\Users\\<you>\\AppData\\Local\\PCE"`，反斜杠记得转义。

### 1.3 verify

完全重启 Claude Desktop（不是关窗，是退出再开）。打开任意聊天，让 Claude 调一个 filesystem 工具——比如：

> "Read `<PCE_REPO>/README.md` for me."

操作完成后回到终端：

```powershell
python -c "from pce_core.db import get_connection; c=get_connection(); print(c.execute(\"SELECT path, direction, COUNT(*) FROM raw_captures WHERE source_id='mcp-proxy-default' GROUP BY path, direction\").fetchall())"
```

应该看到 `tools/list` / `tools/call` / `initialize` 都出现，每个 method 都有 `request` + `response` 两个方向。

---

## 2. Cursor

**Tier**：D1（应该工作）· **目标产品 P3**

### 2.1 配置文件位置

- 项目级：仓库根目录 `.cursor/mcp.json`
- 全局：
  - macOS：`~/Library/Application Support/Cursor/User/mcp.json`
  - Windows：`%APPDATA%\Cursor\User\mcp.json`

### 2.2 编辑配置 — 包一层 git server

```json
{
  "mcpServers": {
    "git": {
      "command": "python",
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "git",
        "--",
        "uvx", "mcp-server-git", "--repository", "/Users/you/code/pce"
      ],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "/Users/you/Library/Application Support/PCE"
      }
    }
  }
}
```

### 2.3 verify

重启 Cursor。在 chat 面板里让 Cursor 用 git 工具，比如 "show last 3 commits"。然后跑同一段 SQL（§1.3）确认捕获。

---

## 3. Windsurf（Codeium）

**Tier**：D1 · **目标产品 P4**

### 3.1 配置文件位置

- macOS：`~/Library/Application Support/Windsurf/User/mcp.json` 或 `~/.codeium/windsurf/mcp_config.json`
- Windows：`%APPDATA%\Windsurf\User\mcp.json`

### 3.2 编辑配置 — 包一层 sequential-thinking

```json
{
  "mcpServers": {
    "sequential-thinking": {
      "command": "python",
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "sequential-thinking",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-sequential-thinking"
      ],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "<your data dir>"
      }
    }
  }
}
```

### 3.3 verify

重启 Windsurf。让 Cascade（Windsurf 内的 agent）用 sequential-thinking 推理一道复杂问题，然后查 SQL。

> 注意：如果你想让 Cascade 同时观察自己的 `pce_capture` 调用，把 `pce_mcp` 也注册为另一个 MCP server（按 [`PCE_MCP_INSTALL.md`](./PCE_MCP_INSTALL.md) §7 的方式）。两者并行不冲突。

---

## 4. Claude Code（CLI）

**Tier**：D1 · **目标产品 P5**

### 4.1 配置文件位置

`~/.config/claude-code/mcp.json` 或运行时通过 `claude mcp add` 命令注册。

### 4.2 通过 CLI 注册

```bash
claude mcp add filesystem-via-pce \
  python \
  -- \
  -m pce_mcp_proxy \
  --upstream-name filesystem \
  -- \
  npx -y @modelcontextprotocol/server-filesystem /home/you/projects
```

> 第一个 `--` 是 `claude mcp add` 让你停止 flag 解析的分隔符。第二个 `--` 是 `pce_mcp_proxy` 自己的分隔符。**两个 `--` 都是必须的**，不要混淆。

### 4.3 verify

```bash
claude mcp list   # 应该看到 filesystem-via-pce
claude --debug    # 跟踪一次完整会话
```

让 Claude 读一个文件后，跑 §1.3 的 SQL。

---

## 5. Codex CLI（OpenAI）

**Tier**：D2（best-effort）· **目标产品 P6**

### 5.1 配置文件位置

`~/.codex/config.toml`

### 5.2 编辑配置

```toml
[mcp_servers.filesystem]
command = "python"
args = [
  "-m", "pce_mcp_proxy",
  "--upstream-name", "filesystem",
  "--",
  "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/you/projects",
]

[mcp_servers.filesystem.env]
PYTHONPATH = "<PCE_REPO>"
PCE_DATA_DIR = "/Users/you/Library/Application Support/PCE"
```

### 5.3 verify

```bash
codex --help    # 确认 codex 自己能跑
codex           # 启动一次会话，让它用 filesystem 工具
```

跑 §1.3 SQL。

> Codex CLI 对 MCP 支持仍在演进（截至 2026-05），如果上面 TOML 形态在你的版本里不工作，回退到 `claude mcp add` 风格的命令行注册（codex 同样支持）。

---

## 6. Gemini CLI（Google）

**Tier**：D2 · **目标产品 P7**

### 6.1 配置文件位置

`~/.gemini/settings.json` 或 `~/.config/gemini-cli/settings.json`

### 6.2 编辑配置

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "python",
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "filesystem",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/you/projects"
      ],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "/Users/you/Library/Application Support/PCE"
      }
    }
  }
}
```

### 6.3 verify

```bash
gemini mcp ls    # 列出已注册 server
gemini           # 起一次会话
```

跑 §1.3 SQL。

---

## 7. Cascade in Windsurf（PCE 自家 IDE）

**Tier**：D0（必须工作 — 这是 dogfood 路径）· **目标产品 P4**

姿态 B 的特别用法：让 Cascade 把它实际调用的 MCP 工具全部经 proxy。

### 7.1 配置文件位置

Windsurf 内置 MCP 设置，UI 是 Settings → Cascade → MCP Servers，会写到 `~/.codeium/windsurf/mcp_config.json`。

### 7.2 编辑配置 — 同时挂 pce_mcp（姿态 A）和包一层 filesystem（姿态 B）

```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp"],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "<your data dir>"
      }
    },
    "filesystem": {
      "command": "python",
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "filesystem",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem", "<some path>"
      ],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "<same dir as above>"
      }
    }
  }
}
```

### 7.3 verify · self-test

让 Cascade 干点活：

> "Read README.md and summarize what `pce_mcp_proxy` does."

完成后跑：

```powershell
python -c "from pce_core.db import get_connection; c=get_connection(); print(c.execute(\"SELECT source_id, COUNT(*) FROM raw_captures GROUP BY source_id\").fetchall())"
```

应该同时看到：

- `mcp-default`（姿态 A — Cascade 自己调 `pce_capture` 上报）
- `mcp-proxy-default`（姿态 B — Cascade 调 filesystem 工具时被 proxy 截到）

两条线并行工作就是 P5.B v1.1 终极姿态。

---

## 8. 通用主机（任何吃 stdio MCP 的 client）

**Tier**：取决于 client，一般 D2 · 模板 fallback

只要主机能让你写一段 `command + args`（最多再加 `env`），上面 §0.4 的模板就能套：

```jsonc
{
  "mcpServers": {
    "<your-tag>": {
      "command": "python",                    // or python3
      "args": [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "<short-tag>",
        // 其它可选 proxy flags：--print-stats / --quiet / --data-dir / --log-file
        "--",
        // 这里粘你原来直接喂给 host 的命令
        "<bin>", "<arg1>", "<arg2>", "..."
      ],
      "env": {
        "PYTHONPATH": "<PCE_REPO>",
        "PCE_DATA_DIR": "<absolute path>"
      }
    }
  }
}
```

如果主机配置不是 JSON 而是其它格式（TOML / YAML / INI），照原始字段一对一翻译就行；`--` 作为字符串保留，不要被序列化器吃掉。

---

## 9. 同时启用姿态 A 和姿态 B

两者**不冲突，强烈推荐同时开**。互补关系：

| | 姿态 A（`pce_mcp`） | 姿态 B（`pce_mcp_proxy`） |
|---|---|---|
| **位置** | 与 host 平级，自己也是一个 MCP server | 夹在 host 与上游之间 |
| **触发** | agent 主动调 `pce_capture` 等工具 | 透明截每一帧，agent 不感知 |
| **覆盖** | 仅 PCE-aware agent 的协作内容 | 任何被包过 proxy 的上游的全部流量 |
| **`source_id`** | `mcp-default` | `mcp-proxy-default` |
| **是否需要 system prompt 引导** | 是 | 否 |

最佳实践：

1. 在你常用的 MCP 主机（Claude Desktop / Cursor / Cascade-Windsurf）里**同时**注册：
   - `pce`（指向 `python -m pce_mcp`，给 agent 显式 ledger）
   - `<servers-you-care-about>`（指向 `python -m pce_mcp_proxy -- <upstream>`，做透明捕获）
2. 给 agent 写一段 system prompt（参考 [`PCE_MCP_INSTALL.md`](./PCE_MCP_INSTALL.md) §8）让它 *主动* 调 `pce_capture` 描述自己当前在做什么——proxy 拿不到 agent 的"心理状态"，只有 agent 自己能写。

---

## 10. 故障排查

### 10.1 主机 says "MCP server failed to start"

按这个顺序检查：

1. **`python` 在 PATH 上吗？** Windows 上 host 可能用 `py.exe` launcher，写 `"command": "py", "args": ["-3", "-m", "pce_mcp_proxy", ...]`。
2. **`PYTHONPATH` 指对了吗？** 仓库根目录必须能 `import pce_mcp_proxy`。在 PCE 根目录跑 `python -c "import pce_mcp_proxy; print(pce_mcp_proxy.__version__)"` 应该输出 `0.1.0`。
3. **`--` 漏了？** 配置里 proxy flags 和 upstream argv 之间必须有字面量 `"--"`。
4. **上游本身能跑？** 临时把整段 args 改回原来不带 proxy 的，看 host 能不能直接连上游。能 → proxy 配错；不能 → 上游问题，proxy 帮不上。

### 10.2 host 起得来但 PCE 数据库没数据

1. 找 proxy 自己的日志：默认是 `<PCE_DATA_DIR>/mcp_proxy.log`（或加 `--log-file <path>` 显式指定）。
2. 检查 `PCE_DATA_DIR` 路径**对 host 进程**是否可写——host 可能在受限沙箱里跑（macOS 的 Claude Desktop 尤其会）。
3. 跑 `python -c "from pce_core.db import init_db; init_db()"` 看初始化报不报错。
4. 对照 `python -m pytest tests/e2e_mcp/test_pce_mcp_proxy_stdio.py -v` 的输出，自家测试都过的话基本不是包问题。

### 10.3 agent 抱怨某个工具失败但 proxy 自己看起来还活着

姿态 B 的产品承诺是"**proxy 不破坏 host ↔ upstream 会话**"。如果上游真的 panic / segfault / 协议级错误，proxy 只能尽量原样转发——它不会修真错误。

确认方法：

1. 把 args 里的 proxy 包装临时去掉，看主机直连上游有没有同样问题。
2. 若有 → 上游 bug。若无 → 给 PCE 提 issue 并附 proxy 日志（`mcp_proxy.log`）+ `tests/e2e_mcp/` 复现脚本。

### 10.4 数据库膨胀

姿态 B 的捕获密度比姿态 A 高得多（每个 tool 调用 = 至少 2 行 raw_captures）。如果你长期开着多个 proxy，建议：

- 加 `--print-stats` 看每次会话的实际 frame 数
- 周期跑 `pce-cli prune --older-than 30d`（如果你用 Pro 版 CLI）
- 或者自己写 `DELETE FROM raw_captures WHERE source_id='mcp-proxy-default' AND created_at < strftime('%s','now','-30 days')`

---

## 11. 状态总览

P5.B.1（2026-05-09）发布时的覆盖：

| 主机 | Tier | 验证状态 |
|---|---|---|
| Claude Desktop | D0 | ✅ 配置形态确认，待用户实测 |
| Cursor | D1 | ✅ 配置形态确认，待用户实测 |
| Windsurf（Cascade）| D0 | ✅ dogfood 路径，PCE 团队自用 |
| Claude Code | D1 | ✅ 配置形态确认 |
| Codex CLI | D2 | ⚠️ best-effort，依赖 Codex MCP 实现细节 |
| Gemini CLI | D2 | ⚠️ best-effort |
| Cascade-Windsurf（独立配置）| D0 | ✅ 同 Windsurf |
| 通用 stdio MCP host | — | ✅ §0.4 模板兜底 |

P5.B.0（姿态 A）+ P5.B.1（姿态 B）合起来已覆盖目标产品矩阵 P1–P7 的 M 面捕获。剩下 P8 + 类型 5/6/7 留给 P5.B.2 与之后阶段。

---

*End of `PCE_MCP_PROXY_INSTALL.md` · v1.0 · P5.B.1 collateral · 2026-05-09*
