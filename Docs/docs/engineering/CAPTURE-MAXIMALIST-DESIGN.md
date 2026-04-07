# 极致捕获架构设计 (Capture Maximalist Design)

> **核心哲学：先抓再说。** 
> 
> 用户与 AI 的每一次交互都是认知资产。能抓就抓，能多抓就多抓，
> 去重和理解是后面的事。抓不到 = 永久丢失。

**修订历史:**
- v1 (2026-04-07): 初版三层捕获网架构
- v2 (2026-04-07): 纳入评审反馈 — 权限双模式、WebSocket 覆盖、SSE 简化策略、
  Ingest 层短时去重、行为元数据采集

---

## 1. 现状差距分析

### 当前覆盖

| 捕获层 | 覆盖范围 | 盲区 |
|---|---|---|
| Proxy (mitmproxy) | 34 个硬编码 AI 域名 | 新服务、SaaS 内嵌 AI、未知域名 |
| Browser Extension | 7 个硬编码网站 (DOM 抓取) | 其他 AI 网站、任何网页内嵌 AI |
| Local Model Hook | 单端口反代 (默认 Ollama) | 多模型并行、非 HTTP 本地模型 |
| MCP Server | 需要工具主动调用 | 无法被动捕获 |
| 桌面应用 | 无 | ChatGPT/Claude 桌面版、IDE AI |
| 剪贴板/屏幕 | 无 | 复制粘贴的 AI 对话 |

### 目标：三层捕获网，无死角

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Layer 1: 浏览器内                             │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────────┐ │
│  │ DOM 智能抓取  │  │ fetch/XHR 拦截   │  │ 已知站点专用提取器    │ │
│  │ (任意页面)    │  │ (任意页面网络流量)│  │ (ChatGPT/Claude/...) │ │
│  └──────┬───────┘  └────────┬─────────┘  └───────────┬───────────┘ │
│         └──────────────┬────┘────────────────────────┘             │
│                   Service Worker → PCE Ingest API                   │
├─────────────────────────────────────────────────────────────────────┤
│                        Layer 2: 系统网络                             │
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────────┐ │
│  │ mitmproxy 代理    │  │ 启发式 AI 检测  │  │ 全量捕获模式       │ │
│  │ (扩展允许列表)    │  │ (自动识别 AI)   │  │ (可选/可开关)      │ │
│  └────────┬─────────┘  └───────┬────────┘  └─────────┬──────────┘ │
│           └──────────────┬─────┘─────────────────────┘             │
│                     PCE Proxy → PCE Core DB                         │
├─────────────────────────────────────────────────────────────────────┤
│                        Layer 3: 本地应用                             │
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────────┐ │
│  │ 多端口本地模型    │  │ 系统代理自动配  │  │ 剪贴板监听         │ │
│  │ Hook (扫描+反代)  │  │ (PAC/全局代理)  │  │ (AI 对话检测)      │ │
│  └────────┬─────────┘  └───────┬────────┘  └─────────┬──────────┘ │
│           └──────────────┬─────┘─────────────────────┘             │
│                     PCE Core Ingest API                              │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
                    raw_captures (Tier 0: 先存)
                              ↓
                    normalizer (Tier 1: 再理解)
                              ↓
                    sessions + messages (结构化)
```

---

## 2. Layer 1: 浏览器内极致捕获 (优先级最高)

浏览器是用户与 AI 交互最多的场所。这一层改造投入产出比最高。

### 2.1 Manifest 权限策略

**现状:** manifest.json `matches` 硬编码 7 个域名。
**目标:** 在**所有页面**注入轻量检测脚本。

> ⚠️ **Chrome Web Store 审核风险:** `<all_urls>` 权限在 Google 审核中阻力越来越大。
> 需要 justification 文档，且可能被拒。必须设计双模式适配。

**双模式权限方案:**

```
模式 A: 侧载/自用模式 (sideload) — 当前阶段默认
  - host_permissions: ["<all_urls>"]
  - permissions: ["webRequest", "storage", "activeTab", "scripting"]
  - content_scripts matches: ["<all_urls>"]
  → 全域注入 detector.js, 全域网络拦截
  → 无审核限制, 功能完整

模式 B: 商店模式 (webstore) — 未来上架时切换
  - host_permissions: [已知 AI 域名列表, "http://127.0.0.1:9800/*"]
  - permissions: ["activeTab", "storage", "scripting", "contextMenus"]
  - content_scripts matches: [已知 AI 域名列表]
  → 已知站点自动注入, 未知站点靠 activeTab + 用户主动触发
  → 右键菜单 / Popup 按钮触发 → chrome.scripting.executeScript()
  → 逐步申请更多域名权限 (optional_host_permissions)
```

**过渡策略:** 代码层完全兼容两种模式。detector.js 和拦截器通过
`chrome.scripting.executeScript()` 动态注入，manifest 权限只决定
"自动注入" vs "需要用户点击激活"。核心逻辑零改动。

**安全与性能考量:**
- 全域注入的是一个 **极轻量的探测脚本** (~2KB)，不是完整提取器
- 探测脚本只做两件事：① 检测页面是否有 AI 特征 ② 如果有，动态注入完整提取器
- 对非 AI 页面性能影响约 0.1ms

### 2.2 AI 页面探测器 (detector.js)

全域注入的轻量脚本，负责判断当前页面是否值得深度捕获：

```
检测信号 (任一命中即激活):

A. URL 匹配 (快速路径)
   - 已知 AI 域名列表 (chatgpt.com, claude.ai, gemini.google.com, ...)
   - URL 路径含 /chat/, /conversation/, /c/, /ask/, /generate/

B. DOM 特征 (中等开销，延迟执行)
   - 页面含 [data-message-author-role], [data-testid*="turn"], [class*="chat-message"]
   - 页面含 textarea + 类 chat 的消息列表容器
   - 页面含 "Send message", "Ask AI", "Generate" 等按钮

C. Meta 信号 (低开销)
   - <meta name="description"> 含 AI/chat/assistant 关键词
   - document.title 含 AI/ChatGPT/Claude/Copilot 等

D. 网络流量信号 (由 fetch/XHR 拦截器触发)
   - 页面向已知 AI API 域名发请求
   - 请求体含 {"model":..., "messages":...} 模式
```

**激活分级:**

| 级别 | 条件 | 动作 |
|---|---|---|
| **Level 0** | 无 AI 信号 | 仅保持 detector.js (2KB)，不做任何捕获 |
| **Level 1** | URL/Meta 匹配 | 注入通用 DOM 提取器 |
| **Level 2** | DOM 特征命中 | 注入通用 DOM 提取器 + 启动 MutationObserver |
| **Level 3** | 已知站点 | 注入专用提取器 (chatgpt.js / claude.js) |
| **Level N** | 网络流量命中 | 捕获实际 API 请求/响应 (最高价值数据) |

### 2.3 网络拦截器 (network_interceptor.js)

**这是最高价值的新增能力。**

当前浏览器扩展只抓 DOM 文本，但网页发出的实际 API 请求才是最完整的数据。
例如：ChatGPT 网页调用 `https://chatgpt.com/backend-api/conversation`，
请求体和响应体包含完整的 messages 数组、token 用量、模型参数等。

**原理:** 通过 content script 向页面注入代码，monkey-patch 四种网络 API，
拦截所有网络请求。在浏览器内完成，无需系统代理。

```
注入方式:
  content_script → 创建 <script src="network_interceptor.js"> → 注入页面上下文 (page context)

拦截链路:
  页面代码调用 fetch(url, options)
    → 我们的 patched fetch 先记录 request
    → 调用原始 fetch
    → 拿到 response 后 clone 一份读取 body
    → 通过 window.postMessage 发回 content script
    → content script 转发给 service worker
    → service worker POST 到 PCE Ingest API

AI 流量识别规则:
  1. URL 命中已知 AI API 域名
  2. 请求 Content-Type 为 JSON 且 body 含 "model"/"messages"/"prompt"
  3. 响应为 SSE (text/event-stream) 且含 "choices"/"content"/"delta"
  4. URL 路径含 /v1/chat/, /v1/completions/, /api/conversation/ 等
```

> ⚠️ **monkey-patch 被检测风险:** ChatGPT/Claude 等站点的前端代码可能校验
> `fetch` 是否被篡改（比较 `fetch.toString()` 是否为 native code），或者
> 使用 monkey-patch 无法覆盖的底层 API。必须逐站点测试验证。

**四种网络 API 全覆盖:**

```
必须 patch 的四种 API:

1. window.fetch          — 现代 Web 主流，大多数 AI 站点使用
2. XMLHttpRequest        — 旧式但仍有站点使用
3. WebSocket             — Claude.ai 等可能用 WS 做实时通信
4. EventSource           — SSE 的原生 API，部分站点直接用

patch 策略:
  - 保存原始引用: const _origFetch = window.fetch.bind(window)
  - 在脚本注入前保存 (在页面自身 JS 执行前)
  - 使用 Object.defineProperty 设置 toString() 返回 native code 字符串
    (降低被检测概率，但不保证对所有站点有效)

WebSocket 拦截:
  - patch WebSocket constructor
  - 监听 onmessage / addEventListener('message') 事件
  - 不干预发送/接收，只读取并转发
  - WS 消息通常是 JSON，可直接识别 AI 内容

EventSource 拦截:
  - patch EventSource constructor
  - 监听 onmessage 事件，累积 SSE 数据
  - 与 fetch SSE 共用同一个累积器逻辑
```

**捕获优先级 (重要):**

```
已知站点专用提取器 > 网络拦截器 > 通用 DOM 提取器

原因:
  - 专用提取器是逐站点测试验证过的，最可靠
  - 网络拦截器可能被站点检测/阻断，是"锦上添花"
  - 通用 DOM 提取器是最后兜底
  
三者同时运行，互为备份。如果拦截器在某站点失效，
专用提取器依然能保证该站点的完整捕获。
```

**SSE 流式响应处理 (v1: 粗糙但稳定)**

> 实际场景中 SSE 存在连接中断重连、多并发流、非数据事件夹杂等复杂情况。
> v1 只做最简单的事：**把 chunk 拼成完整响应**。边界情况后续迭代。

```
v1 策略 (Sprint 1.1):
  - 每个 fetch/EventSource 连接维护一个 chunks[] 数组
  - 收到 chunk → push 到数组
  - 流结束 (response.body reader done / EventSource close) → 拼接 → 发送
  - 超时保护: 30 秒无新 chunk → 强制发送已有内容并清空
  - 不处理: 重连、并发流合并、非 data 事件过滤
  - 不处理: 部分 JSON 解析 (整体发送原始文本，让 normalizer 处理)

v2 (后续迭代):
  - 识别 SSE 重连 (event: retry) 并正确续接
  - 多并发流追踪 (通过 URL + 时间戳区分)
  - 增量发送 (每 10 秒发送一次中间状态)
```

**与 DOM 抓取的关系: 互补，不替代**

| 场景 | 网络拦截 | DOM 抓取 |
|---|---|---|
| 网页直接调 AI API | ✅ 完整 request/response | ❌ 看不到 API 数据 |
| 网页通过自己后端调 AI | ❌ 只能看到到自己后端的请求 | ✅ 能看到渲染后的对话 |
| 私有部署 / 企业 AI | ❌ 域名不在列表 | ✅ 能抓到 DOM 内容 |
| 多模态 (图片/文件) | ✅ 完整请求数据 | ⚠️ 只能拿到文本 |
| 站点检测到 patch 并阻断 | ❌ 失效 | ✅ 不受影响 (DOM 不依赖 patch) |
| WebSocket 通信 | ✅ WS 拦截 | ⚠️ 只能看最终渲染结果 |

**结论: 三种方式 (专用提取器 + 网络拦截 + 通用 DOM) 同时运行，互为备份。**

### 2.4 已知站点专用提取器 (保留并扩展)

现有的 chatgpt.js、claude.js、generic.js 保留。它们的专用逻辑（增量发送、
streaming 检测、thinking 提取）是 DOM 层的精细化工作。

扩展计划：
```
新增专用提取器:
  - gemini.js      → Google Gemini (gemini.google.com)
  - deepseek.js    → DeepSeek Chat (chat.deepseek.com)
  - perplexity.js  → Perplexity AI (www.perplexity.ai)
  - copilot.js     → Microsoft Copilot (copilot.microsoft.com)
  - github_copilot_chat.js → GitHub Copilot Chat (github.com)
  - poe.js         → Poe (poe.com)
  - huggingface.js → HuggingFace Chat (huggingface.co/chat)
  - you.js         → You.com
  - phind.js       → Phind (phind.com)
  - kimi.js        → Kimi / Moonshot (kimi.moonshot.cn)

已知站点 → 用专用提取器 (精确)
未知站点 → detector.js 检测 → 通用提取器 (启发式)
所有站点 → fetch/XHR 拦截 (网络层)
```

### 2.5 行为元数据采集 (低成本高价值)

除了内容本身，用户与 AI 交互的**行为模式**对后续分析极有价值。
这些数据在浏览器层几乎零成本就能采集，应在 Sprint 1.1 就纳入。

```
采集的行为事件:

1. request_sent_at      — 用户点击发送的时刻 (ms 精度)
2. first_token_at       — 收到第一个响应 token 的时刻
3. stream_complete_at   — 流式响应完成的时刻
4. user_read_start_at   — 响应开始渲染、用户可见的时刻
5. next_message_at      — 用户发出下一条消息的时刻
6. copy_events[]        — 用户复制了 AI 输出 (时刻 + 选区长度)
7. scroll_depth         — 用户阅读响应时滚动到的最深位置 (%)

可衍生的分析指标:
  - 首 token 延迟 = first_token_at - request_sent_at (服务质量)
  - 生成耗时 = stream_complete_at - first_token_at (模型速度)
  - 阅读时间 = next_message_at - stream_complete_at (用户理解深度)
  - 思考间隔 = request_sent_at[n] - stream_complete_at[n-1] (任务复杂度)
  - 输出价值 = copy_events 数量 + 选区长度 (输出实用性)
```

**实现位置:**
```
- DOM 提取器 (chatgpt.js 等): 监听发送按钮点击 → request_sent_at
- 网络拦截器: 记录 first_token_at, stream_complete_at
- content script: 监听 document 'copy' 事件, 记录 copy_events
- content script: IntersectionObserver 监听滚动深度
- 所有时间戳存入 meta 字段，随 capture payload 一起发送
```

**数据格式 (附在 meta 中):**
```json
{
  "behavior": {
    "request_sent_at": 1712505600123,
    "first_token_at": 1712505601456,
    "stream_complete_at": 1712505608789,
    "think_interval_ms": 15000,
    "copy_events": [{"at": 1712505612000, "length": 350}],
    "scroll_depth_pct": 85
  }
}
```

### 2.6 用户手动标记能力

即使自动检测失败，用户也应该能手动告诉扩展"这个页面有 AI 对话，抓它"：

```
方式:
  - 扩展 Popup 上增加 "Capture This Page" 按钮
  - 右键菜单: "PCE: Capture AI Conversation"
  - 快捷键: Ctrl+Shift+P → 捕获当前页面
  
触发后:
  - 立即注入通用 DOM 提取器
  - 抓取当前页面全文 + 结构化尝试
  - 标记为 manual_capture
  - 后续该域名自动加入监控列表
```

### 2.7 新的扩展架构总览

```
pce_browser_extension/
├── manifest.json                   # 侧载: <all_urls> / 商店: 精确域名列表
├── background/
│   └── service_worker.js           # 消息路由 + API调用 + 动态注入 + 短时去重
├── interceptor/
│   ├── network_interceptor.js      # fetch/XHR/WebSocket/EventSource patch
│   │                               # (注入页面上下文, web_accessible_resources)
│   └── ai_patterns.js              # AI 流量识别规则 (域名 + body 模式)
├── content_scripts/
│   ├── detector.js                 # 全域注入的轻量 AI 检测器 (~2KB)
│   ├── bridge.js                   # interceptor ↔ content script 消息桥接
│   ├── behavior_tracker.js         # 行为元数据采集 (copy/scroll/timing)
│   ├── universal_extractor.js      # 通用 DOM 提取器 (启发式)
│   ├── chatgpt.js                  # ChatGPT 专用 (保留增强)
│   ├── claude.js                   # Claude 专用 (保留增强)
│   ├── gemini.js                   # Gemini 专用 (新增)
│   ├── deepseek.js                 # DeepSeek 专用 (新增)
│   └── ...                         # 更多专用提取器
├── popup/
│   ├── popup.html                  # 状态面板 + "Capture This Page" 按钮
│   ├── popup.js
│   └── popup.css
├── config/
│   ├── known_sites.json            # 已知 AI 站点 → 专用提取器映射
│   └── ai_domains.json             # AI API 域名列表 (网络拦截用)
└── icons/
```

---

## 3. Layer 2: 系统网络级捕获

### 3.1 Proxy 启发式 AI 检测

**现状:** `_resolve_host()` 检查白名单，不在白名单内的直接跳过。
**目标:** 白名单之外，增加启发式检测。

```python
# 检测规则 (按优先级):

Rule 1: 域名白名单 (当前 34 个，直接通过)
Rule 2: 域名模式匹配
  - *api*.ai, *api*.ml, *chat*.*
  - *.openai.com, *.anthropic.com (子域名)
Rule 3: 请求体特征 (JSON POST)
  - 含 "model" + "messages" 字段 → 高置信度 AI API 调用
  - 含 "prompt" + "max_tokens" 字段 → 高置信度
  - 含 "model" + "input" 字段 → 可能是 AI
Rule 4: 响应特征
  - Content-Type: text/event-stream + body 含 "choices" → AI 流式响应
  - JSON 响应含 "choices"/"completion"/"generated_text" → AI 响应
```

**实现方式:**
```python
class CaptureMode(Enum):
    ALLOWLIST = "allowlist"      # 当前模式: 只白名单
    SMART = "smart"              # 白名单 + 启发式
    ALL = "all"                  # 全量捕获

# 通过环境变量/配置切换:
# PCE_CAPTURE_MODE=smart
```

### 3.2 动态允许列表

```
来源:
  1. 浏览器扩展上报: 扩展发现新 AI 域名 → 通知 Core API → 加入列表
  2. 用户手动添加: Dashboard 设置页面 → 添加/移除域名
  3. 社区列表: 可选从远程 URL 同步 AI 域名列表
  
存储:
  - config.py 硬编码 → 基础列表 (不可删除)
  - DB 表 custom_domains → 用户自定义列表
  - 启动时合并两个列表
```

### 3.3 全量捕获模式

对于高级用户/特殊场景（如企业内部 AI 系统用了非标域名），
提供全量捕获模式：

```
PCE_CAPTURE_MODE=all

行为:
  - 所有 HTTPS 流量都存入 raw_captures
  - 但使用更粗粒度的存储 (只存 headers + body 前 N 字节)
  - 后台异步扫描，对符合 AI 特征的记录做完整存储
  - 非 AI 流量在 N 天后自动清理 (可配置)
```

---

## 4. Layer 3: 本地应用捕获

### 4.1 多端口本地模型 Hook

**现状:** 只代理一个端口 (默认 11434 → 11435)。
**目标:** 自动扫描并代理所有运行中的本地模型。

```
启动流程:
  1. 扫描已知端口列表 (11434, 1234, 8000, 8080, 5000, 3000)
  2. 对每个端口发送探测请求 (GET /api/tags, GET /v1/models, etc.)
  3. 发现活跃的本地模型 → 自动启动反代
  4. 反代端口 = 原始端口 + 1000 (e.g., 11434 → 12434)
  5. 定期重新扫描 (每 60 秒)

在 ServiceManager 中管理多个 hook 实例。
```

### 4.2 系统代理自动配置

为了捕获桌面应用的 AI 流量：

```
方案 A: PAC 文件 (推荐)
  - PCE 生成 proxy.pac 文件:
    function FindProxyForURL(url, host) {
      if (isInAIDomainList(host)) return "PROXY 127.0.0.1:8080";
      return "DIRECT";
    }
  - 配置系统使用此 PAC 文件
  - 只有 AI 流量走代理，其他不受影响

方案 B: 全局代理
  - 设置系统 HTTP_PROXY=127.0.0.1:8080
  - 更激进，所有流量都走代理
  - 可选开关

方案 C: 透明代理 (高级)
  - 使用 iptables/nftables/WFP 重定向特定端口流量
  - 无需应用配置
  - 复杂度高，暂不实现
```

### 4.3 剪贴板监听 (低优先级但有价值)

```
监听系统剪贴板变化:
  - 检测粘贴的文本是否像 AI 对话
  - 启发式: 包含 "User:", "Assistant:", "Human:", "AI:" 等角色标记
  - 或: 包含 markdown 格式的长文本 + 代码块
  
触发时:
  - 存入 raw_captures, direction="clipboard"
  - 后续由 normalizer 尝试解析
```

---

## 5. 存储层适配

### 5.1 扩展 direction 类型

```sql
-- 当前: 'request', 'response', 'conversation'
-- 扩展为:
CHECK (direction IN (
  'request',           -- API 请求
  'response',          -- API 响应
  'conversation',      -- DOM 抓取的对话
  'network_intercept', -- 浏览器内 fetch/XHR 拦截
  'clipboard',         -- 剪贴板捕获
  'manual_capture'     -- 用户手动触发
))
```

### 5.2 去重策略 (两层)

多层捕获网必然导致同一交互被多次捕获。这是设计期望——**宁多勿漏**。
但网络拦截和 DOM 抓取几乎同时触发，不做任何前置过滤会导致短时存储量翻倍，
给 normalizer 增加不必要的负担。

**两层去重设计:**

```
第一层: Ingest 前 — service_worker.js 短时窗口去重 (新增)
  位置: 浏览器扩展 service worker, 在 POST 到 Ingest API 之前
  规则: 同一 session_hint 内, 5 秒窗口内 content_hash 相同 → 直接丢弃
  目的: 过滤 fetch 拦截 + DOM 抓取的即时重复
  
  实现:
    const recentHashes = new Map();  // hash → timestamp
    function isDuplicate(sessionHint, contentHash) {
      const key = `${sessionHint || 'global'}:${contentHash}`;
      const prev = recentHashes.get(key);
      const now = Date.now();
      if (prev && (now - prev) < 5000) return true;  // 5秒窗口
      recentHashes.set(key, now);
      // 定期清理过期条目 (每 60 秒清一次)
      return false;
    }

  content_hash 计算:
    对 conversation.messages 做简单指纹:
    messages.map(m => m.role + ':' + m.content.slice(0, 100)).join('|')
    然后取 hash (可以用简单的字符串 hash, 不需要 crypto)

第二层: Normalizer — Tier 1 message 级去重 (已有)
  位置: pce_core/normalizer/pipeline.py _persist_result()
  规则: 同一 session 内, _message_hash(role, content[:200]) 相同 → 跳过插入
  目的: 跨时间窗口的最终去重保障

原则:
  - Tier 0 (raw_captures): 通过第一层已过滤掉 ~50% 的即时重复
  - Tier 1 (messages): 第二层保证零重复
  - 第一层是性能优化, 不保证 100% 去重 (宁漏过不误杀)
  - 第二层是正确性保障, 保证 100% 去重
```

### 5.3 存储空间管理

全量捕获会显著增加存储:

```
策略:
  - hot tier (< 30天): 保留全部 raw_captures
  - warm tier (30-90天): 保留 Tier 1 (sessions/messages) + 压缩 Tier 0
  - cold tier (> 90天): 只保留 Tier 1, 可选归档 Tier 0 到 zip
  - 用户可在 Dashboard 配置保留策略
  - 默认不自动清理 (本地存储用户数据，尊重用户选择)
```

---

## 6. 实施路线图

### Phase 1: 浏览器扩展极致化 (最高优先级)

```
Sprint 1.1: 网络拦截器 + 行为元数据 (最高价值)
  ├── 实现 network_interceptor.js (fetch + XHR + WebSocket + EventSource)
  ├── 实现 ai_patterns.js (AI 流量识别)
  ├── SSE 流式响应: v1 粗糙但稳定 (chunk 累积 + 超时刷新)
  ├── 实现 behavior_tracker.js (时间戳 + copy 事件 + 滚动深度)
  ├── service_worker.js: 网络拦截消息路由 + 短时去重 (5秒窗口)
  ├── Ingest API 增加 direction="network_intercept" 支持
  └── 逐站点验证: ChatGPT / Claude / Gemini (拦截器是否被检测)

Sprint 1.2: 全域检测 + 通用提取
  ├── 实现 detector.js (轻量 AI 页面检测器)
  ├── 实现 universal_extractor.js (通用 DOM 提取)
  ├── manifest.json: 侧载版 <all_urls> + 商店版精确列表
  ├── service_worker.js 增加动态脚本注入逻辑
  └── 测试: 在 10+ 个 AI 网站验证

Sprint 1.3: 用户控制
  ├── Popup 增加 "Capture This Page" 按钮
  ├── 右键菜单集成
  ├── Popup 显示当前页面检测状态
  └── 站点黑名单 (用户可排除不想捕获的站点)

Sprint 1.4: 更多专用提取器
  ├── gemini.js, deepseek.js, copilot.js
  ├── perplexity.js, poe.js, huggingface.js
  └── 每个提取器都有增量发送 + streaming 检测
```

### Phase 2: Proxy 智能化

```
Sprint 2.1: 启发式 AI 检测
  ├── 实现 CaptureMode.SMART
  ├── 请求体/响应体模式匹配
  └── 自动标记新发现的 AI 域名

Sprint 2.2: 动态允许列表
  ├── DB 表 + API 端点管理自定义域名
  ├── 浏览器扩展 → Core API 上报新域名
  └── Dashboard 域名管理页面
```

### Phase 3: 本地应用

```
Sprint 3.1: 多端口本地模型 Hook
Sprint 3.2: 系统代理自动配置 (PAC 文件)
Sprint 3.3: 剪贴板监听 (实验性)
```

---

## 7. 安全与隐私红线

```
绝对不做:
  ✗ 不捕获密码/银行/支付页面 (通过域名黑名单排除)
  ✗ 不上传任何数据到远程服务器 (local-first)
  ✗ 不存储 Authorization/Cookie/API Key (已有 redact 机制)
  ✗ 不在用户不知情的情况下开启全量捕获

用户必须知道:
  ✓ 扩展 Popup 永远显示当前捕获状态
  ✓ Dashboard 显示总捕获量和来源分布
  ✓ 所有新捕获模式默认关闭，用户主动开启
  ✓ 提供一键 "暂停所有捕获" 功能
```

---

## 8. 预期效果

| 场景 | 当前 | Phase 1 后 | Phase 2 后 | Phase 3 后 |
|---|---|---|---|---|
| ChatGPT 网页 | ✅ DOM 抓取 | ✅ DOM + API 拦截 | ✅ + Proxy | ✅ |
| Claude 网页 | ✅ DOM 抓取 | ✅ DOM + API 拦截 | ✅ + Proxy | ✅ |
| Gemini/DeepSeek/Poe 等 | ⚠️ 通用抓取 | ✅ 专用提取器 | ✅ | ✅ |
| Notion AI / Canva AI | ❌ | ✅ 网络拦截 + DOM 检测 | ✅ | ✅ |
| 未知新 AI 网站 | ❌ | ✅ 自动检测 + 抓取 | ✅ | ✅ |
| ChatGPT 桌面版 | ❌ | ❌ | ✅ Proxy 捕获 | ✅ |
| IDE AI (Cursor/Copilot) | ❌ | ❌ | ✅ Proxy 捕获 | ✅ |
| Ollama 等本地模型 | ✅ 单端口 | ✅ | ✅ | ✅ 多端口 |
| API 调用 (curl/Python) | ✅ Proxy | ✅ | ✅ | ✅ |
| 复制粘贴的 AI 对话 | ❌ | ❌ | ❌ | ✅ 剪贴板 |
| **预估覆盖率** | **~40%** | **~75%** | **~88%** | **~95%** |
