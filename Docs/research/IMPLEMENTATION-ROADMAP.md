# PCE 工程实施路径

> 基于 ARCHITECTURE.md 设计约束 + AI 产品全景调研结论，推导出的具体工程实施路径。

---

## 核心架构原则（已确立）

> "差别只在'怎么进来'，不在'进来之后怎么处理'。"
> — ARCHITECTURE.md §4.3

```
                    ┌─────────────────┐
                    │  Capture Ingest │  ← 统一入口 API
                    │     (HTTP)      │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ┌────┴────┐         ┌────┴────┐         ┌────┴────┐
   │ Proxy   │         │Browser  │         │  MCP    │
   │ Addon   │         │Extension│         │ Server  │
   └─────────┘         └─────────┘         └─────────┘
   mitmproxy            Chrome              stdio/SSE
   网络层拦截            DOM+fetch           工具调用记录
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                    ┌────────┴────────┐
                    │   Normalizer    │  ← 多格式 → 统一 session/message
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │  SQLite Storage │  ← raw_captures + sessions + messages
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │   Query API     │  ← FastAPI
                    └────────┬────────┘
                             │
                    ┌────────┴────────┐
                    │   Dashboard     │  ← Web UI (后续)
                    └─────────────────┘
```

**关键洞察**：不管有多少种捕获前端，它们都往同一个入口写数据。所以第一步不是急着做更多的捕获前端，而是先把"入口 → 存储 → 查询"这条共享管道做扎实。

---

## 实施路径总览

```
STEP 0  ✅ Proxy PoC (已完成)
STEP 1  → Capture Ingest API：统一入口服务
STEP 2  → Normalizer：raw → session/message 归一化
STEP 3  → Browser Extension：覆盖 Web AI 产品
STEP 4  → MCP Server：覆盖 IDE/Agent 工具调用
STEP 5  → Query API + Dashboard：可视化回看
STEP 6  → 扩展域名 + 本地模型 Hook
```

---

## STEP 1：Capture Ingest API

### 为什么先做这个

当前 Proxy PoC 的 `insert_capture()` 是在 mitmproxy addon 进程内直接调用的。如果将来浏览器插件、MCP Server 也需要写数据，它们不可能共用 mitmproxy 进程。

**需要一个独立的 HTTP 服务作为统一写入入口**，所有捕获前端都往这个入口 POST 数据。

### 具体产出

```
pce_core/
├── server.py          ← FastAPI 服务（端口 9800）
├── ingest.py          ← POST /api/v1/captures  接收数据
├── config.py          ← 统一配置（从 pce_proxy/config.py 提升）
├── db.py              ← 统一 DB 层（从 pce_proxy/db.py 提升）
└── models.py          ← Pydantic schema 定义
```

### 核心 API

```
POST /api/v1/captures
{
  "source_type": "proxy" | "browser_extension" | "mcp" | "ide_plugin" | "local_model",
  "source_name": "mitmproxy" | "chrome-ext" | "pce-mcp-server" | ...,
  "direction": "request" | "response" | "conversation",
  "pair_id": "optional, for req/resp pairing",
  "provider": "openai" | "anthropic" | "google" | ...,
  "host": "api.openai.com",
  "path": "/v1/chat/completions",
  "method": "POST",
  "model_name": "gpt-4",
  "status_code": 200,
  "latency_ms": 342.5,
  "headers_json": "{}",           ← 已脱敏
  "body_json": "{}",
  "meta": {}                      ← 来源特有的元数据
}

GET /api/v1/captures?last=20&provider=openai&source_type=browser_extension
GET /api/v1/captures/{pair_id}
GET /api/v1/stats
```

### 改造 Proxy Addon

当前 `pce_proxy/addon.py` 直接调用 `insert_capture()` 写 SQLite。改造后：

- **方案 A（推荐）**：addon 仍直接写 SQLite（同进程，零网络开销），但写入逻辑统一到 `pce_core/db.py`
- **方案 B**：addon 通过 HTTP POST 到 Ingest API（增加一跳网络，但完全解耦）

推荐先 A 后 B：开发阶段用 A 保持简单，产品化时切 B。

### 工作量估计
- **2-3 天**（FastAPI 服务 + Pydantic schema + 迁移 DB 层 + 测试）

---

## STEP 2：Normalizer（raw → session/message）

### 为什么在捕获扩展之前做

ARCHITECTURE.md §5.3 明确说了：

> "如果没有统一结构，后面的搜索、时间线和分析都会被来源格式绑死"

如果先做浏览器插件再做归一化，就会出现两种完全不同的数据格式混在 raw_captures 里，后面补归一化更痛苦。

### 具体产出

```
pce_core/
├── normalizer/
│   ├── __init__.py
│   ├── base.py            ← 归一化接口定义
│   ├── openai.py          ← OpenAI chat completions → session/message
│   ├── anthropic.py       ← Anthropic messages → session/message
│   └── dom_conversation.py ← 浏览器插件 DOM 抓取 → session/message（预留）
```

### 新增 DB Schema（Tier 1）

根据 ARCHITECTURE.md §7 已定义好的结构：

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    provider        TEXT,
    tool_family     TEXT,      -- "chatgpt-web" / "cursor" / "cline" / ...
    session_key     TEXT,      -- 用于去重/归并
    message_count   INTEGER DEFAULT 0,
    title_hint      TEXT,
    created_via     TEXT,      -- "proxy" / "browser_extension" / "mcp"
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    capture_pair_id TEXT,      -- 关联 raw_captures.pair_id
    ts              REAL NOT NULL,
    role            TEXT NOT NULL,  -- "user" / "assistant" / "system" / "tool"
    content_text    TEXT,
    content_json    TEXT,
    model_name      TEXT,
    token_estimate  INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
```

### 归一化逻辑（以 OpenAI 为例）

```
raw_captures (pair_id=abc)
├── request:  {"model":"gpt-4", "messages":[{"role":"user","content":"hello"}]}
└── response: {"choices":[{"message":{"role":"assistant","content":"Hi!"}}]}

          ↓ normalizer.openai

sessions
└── id=sess_001, provider=openai, tool_family=api-direct, message_count=2

messages
├── id=msg_001, session_id=sess_001, role=user,      content_text="hello"
└── id=msg_002, session_id=sess_001, role=assistant,  content_text="Hi!"
```

### 工作量估计
- **3-4 天**（schema + OpenAI normalizer + Anthropic normalizer + 测试 + 会话归并逻辑）

---

## STEP 3：Browser Extension（Chrome）

### 为什么是第一个新增的捕获前端

根据市场调研：
- **ROI 最高**：一个插件覆盖所有 Web AI 产品
- **实现最简单**：Chrome Manifest V3 Content Script，无需系统权限
- **覆盖增量最大**：+20-25%

### 具体产出

```
pce_browser_extension/
├── manifest.json          ← Manifest V3
├── content_scripts/
│   ├── chatgpt.js         ← ChatGPT Web DOM 抓取
│   ├── claude.js          ← Claude.ai DOM 抓取
│   └── generic.js         ← 通用对话 DOM 监听
├── background.js          ← Service Worker，负责 POST 到 Ingest API
├── popup.html             ← 状态面板
└── popup.js
```

### 工作原理

```
1. Content Script 注入目标页面（chatgpt.com, claude.ai, gemini.google.com, ...）
2. 通过 MutationObserver 监听 DOM 变化，捕获对话内容
3. 同时通过 chrome.webRequest.onCompleted 监听 API 请求（可选）
4. 将捕获的数据 POST 到本地 http://127.0.0.1:9800/api/v1/captures
5. Ingest API 写入 SQLite，Normalizer 生成 session/message
```

### 覆盖的产品

| 产品 | URL Pattern | 抓取方式 |
|------|-------------|---------|
| ChatGPT Web | `chatgpt.com/*` | DOM + fetch 拦截 |
| Claude.ai | `claude.ai/*` | DOM |
| Gemini | `gemini.google.com/*` | DOM |
| Perplexity | `www.perplexity.ai/*` | DOM |
| DeepSeek | `chat.deepseek.com/*` | DOM |
| Grok | `grok.com/*` | DOM |
| Poe | `poe.com/*` | DOM |
| Notion AI | `www.notion.so/*` | DOM（AI 对话框） |
| 各类 Web AI | `*` | 通用对话模式匹配 |

### 工作量估计
- **5-7 天**（Manifest V3 + ChatGPT/Claude content script + background + Ingest 对接 + 测试）
- 先做 ChatGPT + Claude 两个最常用的，其他迭代添加

---

## STEP 4：MCP Server

### 为什么排在浏览器插件之后

- MCP 主要覆盖 IDE/Agent 场景，而 IDE 场景的一部分已被网络代理覆盖
- 浏览器插件的覆盖增量更大（Web AI 产品完全没有被代理覆盖）
- 但 MCP 是**标准化协议**，一次实现覆盖 50+ 客户端

### 具体产出

```
pce_mcp/
├── __init__.py
├── server.py              ← MCP Server 主逻辑
├── tools/
│   ├── capture_tool.py    ← 提供 capture/log tool 让 LLM 调用
│   └── query_tool.py      ← 提供查询 tool 让 LLM 查看历史
├── resources/
│   └── history.py         ← 暴露历史记录为 MCP resource
└── config.json            ← MCP server 配置（供各客户端引用）
```

### 两种 MCP 策略

**策略 A：PCE 作为 MCP Tool Provider**
- PCE 注册为 MCP Server，提供 `pce_log` / `pce_query` tools
- AI 客户端在调用其他 tool 时，PCE 可以通过 MCP 的 Sampling 回调记录对话
- 适用于：Claude Desktop、Cursor、Cline、Continue、Windsurf 等

**策略 B：PCE 作为 MCP Proxy（中间人）**
- PCE 注册为 MCP Server，同时作为 MCP Client 连接真正的 tool server
- 所有 MCP tool 调用经过 PCE 中转，实现完整记录
- 类似于网络代理在 MCP 层面的等价物

推荐先做 A（简单），再探索 B（高覆盖但复杂）。

### 客户端配置示例

用户在 Claude Desktop 的 `claude_desktop_config.json` 中添加：
```json
{
  "mcpServers": {
    "pce": {
      "command": "python",
      "args": ["-m", "pce_mcp.server"],
      "env": {
        "PCE_DATA_DIR": "~/.pce/data"
      }
    }
  }
}
```

### 工作量估计
- **策略 A：3-4 天**（MCP SDK + tool 定义 + 数据写入 + 测试）
- **策略 B：额外 3-5 天**

---

## STEP 5：Query API + Dashboard

### 具体产出

```
pce_core/
├── api/
│   ├── routes_captures.py  ← GET /captures, /captures/{id}
│   ├── routes_sessions.py  ← GET /sessions, /sessions/{id}/messages
│   ├── routes_stats.py     ← GET /stats/overview, /stats/by-provider
│   └── routes_sources.py   ← GET /sources

pce_dashboard/
├── package.json
├── src/
│   ├── App.tsx
│   ├── pages/
│   │   ├── Timeline.tsx     ← 时间线视图
│   │   ├── SessionDetail.tsx ← 会话详情
│   │   └── Settings.tsx     ← 配置页
│   └── components/
│       ├── CaptureCard.tsx
│       ├── SearchBar.tsx
│       └── SourceFilter.tsx
```

### 工作量估计
- **Query API：2-3 天**（大部分逻辑在 STEP 1 已建立）
- **Dashboard：5-7 天**（React + TailwindCSS + 基础页面）

---

## STEP 6：扩展域名 + 本地模型 Hook

### 扩展 Allowlist

将调研发现的所有 AI 域名加入默认配置：

```python
ALLOWED_HOSTS = {
    # 直连 Provider
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.x.ai",
    "api.deepseek.com",
    "api.mistral.ai",
    "api.cohere.com",
    "api.groq.com",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.perplexity.ai",
    # 厂商中继
    "api2.cursor.sh",
    "api.codeium.com",
    "copilot-proxy.githubusercontent.com",
    # 聚合服务
    "openrouter.ai",
    # 本地模型
    "localhost",
    "127.0.0.1",
}
```

### 本地模型 Hook

对 Ollama/LM Studio 的拦截极其简单：

```
方案：PCE 代理监听 localhost:9801
    用户设置 OPENAI_API_BASE=http://127.0.0.1:9801/v1
    PCE 收到请求 → 记录 → 转发到 localhost:11434（Ollama）→ 记录响应 → 返回
```

本质就是把已有的 mitmproxy 在 localhost 上再跑一遍，几乎零开发量。

### 工作量估计
- **1-2 天**

---

## 总时间线

```
Week 1-2:  STEP 1 (Ingest API) + STEP 2 (Normalizer)
           → 统一后端就绪，所有捕获前端可以接入

Week 3-4:  STEP 3 (Browser Extension)
           → 覆盖率从 ~35% 跳到 ~60%

Week 5:    STEP 4 (MCP Server)
           → 覆盖率到 ~75%

Week 6-7:  STEP 5 (Query API + Dashboard)
           → "看见" 能力完成

Week 8:    STEP 6 (扩展域名 + 本地模型)
           → 覆盖率到 ~85%
```

**8 周左右可以完成"记录 → 看见"的完整工程基础**，覆盖约 85% 的桌面端 AI 交互场景。

---

## 当前仓库需要的结构调整

从 STEP 1 开始，仓库结构需要从"单一 proxy 包"演进为"多模块 monorepo"：

```
PCE Core/
├── pce_core/              ← 共享核心（DB, config, models, normalizer）
│   ├── db.py
│   ├── config.py
│   ├── models.py
│   ├── server.py          ← FastAPI Ingest + Query
│   └── normalizer/
├── pce_proxy/             ← mitmproxy addon（已有，重构为调用 pce_core）
├── pce_browser_extension/ ← Chrome 插件
├── pce_mcp/               ← MCP Server
├── pce_dashboard/         ← Web UI
├── tests/
├── Docs/
└── pyproject.toml         ← 统一 Python 项目管理
```

---

## 下一步建议

**立即可以开始的是 STEP 1：Capture Ingest API**

具体行动：
1. 创建 `pce_core/` 包
2. 将 `pce_proxy/db.py` 和 `pce_proxy/config.py` 中的通用部分提升到 `pce_core/`
3. 用 FastAPI 实现 `POST /api/v1/captures`
4. 让 `pce_proxy/addon.py` 改为调用 `pce_core.db`
5. 写测试验证数据流通

是否开始实施？
