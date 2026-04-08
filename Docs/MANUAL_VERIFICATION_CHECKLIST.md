# PCE 手动验证清单

> 每个验证项都是独立的，按顺序逐个通过。
> 每项标注了：**前置条件**、**操作步骤**、**预期结果**、**排障方法**。
> 通过打 ✅，失败打 ❌ 并记录现象，我们一起排障。

---

## 第〇关：环境准备

### V0.1 — Python 环境可用
- **操作**：打开终端，运行：
  ```
  python --version
  ```
- **预期**：显示 Python 3.10+
- **排障**：如果不是，需要安装 Python 3.10 或更高版本

### V0.2 — 依赖已安装
- **操作**：
  ```
  cd "F:\INVENTION\You.Inc\PCE Core"
  pip install -r requirements.txt
  ```
- **预期**：无报错，fastapi / uvicorn / pydantic 等已安装
- **排障**：如果报错，贴错误信息

### V0.3 — 浏览器扩展已加载
- **操作**：
  1. 打开 Chrome，地址栏输入 `chrome://extensions/`
  2. 打开右上角「开发者模式」
  3. 点「加载已解压的扩展程序」，选择 `F:\INVENTION\You.Inc\PCE Core\pce_browser_extension`
  4. 如果已经加载过，点扩展卡片上的 🔄 刷新按钮
- **预期**：扩展出现在列表中，无错误标红
- **排障**：如果有红色错误，点「错误」按钮截图给我

---

## 第一关：PCE Core 服务器

### V1.1 — 服务器能启动
- **前置**：V0.1, V0.2 通过
- **操作**：
  ```
  cd "F:\INVENTION\You.Inc\PCE Core"
  python -m pce_core.server
  ```
- **预期**：终端显示类似：
  ```
  PCE Core server v0.1.0 ready on 127.0.0.1:9800
  Uvicorn running on http://127.0.0.1:9800
  ```
- **排障**：如果端口被占用，先关掉上一个实例或运行 `netstat -ano | findstr 9800`

### V1.2 — 健康检查通过
- **前置**：V1.1 通过，服务器保持运行
- **操作**：打开浏览器访问 http://127.0.0.1:9800/api/v1/health
- **预期**：看到 JSON：
  ```json
  {"status": "ok", "version": "0.1.0", "db_path": "...", "total_captures": ...}
  ```
- **排障**：如果无法访问，检查防火墙是否拦截了 localhost:9800

### V1.3 — 扩展能连上服务器
- **前置**：V1.1, V0.3 通过
- **操作**：点击 Chrome 工具栏的 PCE 扩展图标，打开 popup
- **预期**：状态显示 **Online**（绿色）
- **排障**：如果显示 Offline：
  1. 确认 V1.2 通过（服务器真的在跑）
  2. 在 `chrome://extensions/` 页面点扩展的「Service Worker」链接，打开 DevTools → Console，看有无错误
  3. 截图 Console 内容给我

---

## 第二关：浏览器扩展 — DOM 提取（DeepSeek）

### V2.1 — 内容脚本加载成功
- **前置**：V1.3 通过
- **操作**：
  1. 打开 https://chat.deepseek.com
  2. 按 F12 打开 DevTools → Console
  3. 在 Console 过滤框输入 `PCE`
- **预期**：看到类似日志：
  ```
  [PCE] DeepSeek extractor loaded
  ```
  如果同时看到 `[PCE] bridge.js: injecting network interceptor` 更好
- **排障**：
  - 如果没有任何 `[PCE]` 日志 → 扩展没有注入，检查 V0.3
  - 如果看到 `[PCE] generic.js loaded` 而不是 DeepSeek → 告诉我，说明 deepseek.js 没正确注入

### V2.2 — DOM 提取能捕获对话
- **前置**：V2.1 通过
- **操作**：
  1. 在 DeepSeek 输入一条消息，例如「你好，请用一句话介绍你自己」
  2. 等 AI 回复完成
  3. 观察 Console 中的 `[PCE]` 日志
- **预期**：看到类似：
  ```
  [PCE] DeepSeek: sending 2 new msgs (0→2)
  [PCE] Captured: deepseek via conversation from chat.deepseek.com (xxxxxxxx)
  ```
- **排障**：
  - 如果没有 sending 日志 → DOM 选择器可能失效，截图页面结构给我
  - 如果有 sending 但 Captured 失败 → 服务器连接问题，看有无 fetch 报错

### V2.3 — 捕获的对话出现在 Sessions 里
- **前置**：V2.2 通过
- **操作**：浏览器访问 http://127.0.0.1:9800/api/v1/sessions?last=5
- **预期**：看到 JSON 数组，其中一个 session 的 `provider` 为 `"deepseek"`，`title_hint` 包含你刚才发的消息
- **排障**：
  - 如果有 capture 但没有 session → 归一化失败，运行以下命令查看原始捕获：
    ```
    浏览器访问 http://127.0.0.1:9800/api/v1/captures?last=5&provider=deepseek
    ```
    截图结果给我

### V2.4 — Messages 内容完整
- **前置**：V2.3 通过
- **操作**：从 V2.3 的结果中复制 session 的 `id`，访问：
  ```
  http://127.0.0.1:9800/api/v1/sessions/{session_id}/messages
  ```
- **预期**：
  - 至少 2 条 message
  - 一条 `role: "user"`，内容是你发的消息
  - 一条 `role: "assistant"`，内容是 AI 的回复
- **排障**：如果 messages 为空或只有 user → 截图给我

---

## 第三关：浏览器扩展 — 网络拦截 L3（核心验证）

> 这是最重要的验证。网络拦截是我们加固的核心路径。

### V3.1 — 网络拦截脚本注入成功
- **前置**：V1.3 通过
- **操作**：
  1. 打开 https://chat.deepseek.com （如果已打开，刷新页面）
  2. F12 → Console，过滤 `PCE`
- **预期**：看到：
  ```
  [PCE] bridge.js: injecting network interceptor
  [PCE] Network interceptor loaded, monitoring: fetch, xhr, ...
  ```
- **排障**：如果没看到 bridge.js 日志 → 截图 Console 给我

### V3.2 — 网络拦截能捕获 API 请求
- **前置**：V3.1 通过
- **操作**：
  1. 在 DeepSeek 发送一条新消息，例如「1+1等于多少？」
  2. 观察 Console 日志
- **预期**：看到类似：
  ```
  [PCE-Intercept] Captured fetch: POST https://chat.deepseek.com/api/v0/chat/completions
  ```
  或类似的 `[PCE-Intercept]` / `[PCE] Network capture` 日志
- **排障**：
  - 如果没有任何 intercept 日志 → 拦截器可能没有匹配到 DeepSeek 的 API URL
  - 打开 DevTools → Network 面板，找到 DeepSeek 发出的 API 请求，记录它的完整 URL，告诉我
  - 同时查看 Console 有无 JS 错误（红色报错）

### V3.3 — 网络拦截数据到达服务器
- **前置**：V3.2 通过
- **操作**：查看运行 PCE 服务器的终端窗口
- **预期**：看到类似：
  ```
  ingested network_intercept chat.deepseek.com from browser_extension (xxxxxxxx)
  ```
- **排障**：
  - 如果没有日志 → 数据没发到服务器。回到 Chrome Console 看有无 fetch 报错
  - 如果有 `Capture failed` 日志 → 截图给我

### V3.4 — 网络拦截的 SSE 流式响应正确组装
- **前置**：V3.3 通过
- **操作**：浏览器访问：
  ```
  http://127.0.0.1:9800/api/v1/captures?last=5&provider=deepseek
  ```
  找到 `direction: "network_intercept"` 的条目，看 `body_text_or_json` 字段
- **预期**：body 是一个 JSON wrapper，里面的 `response_body` 字段包含 SSE 文本（以 `data:` 开头的多行文本），或者已经是组装好的 JSON
- **排障**：如果 `response_body` 为空或很短 → 拦截器没有正确读取流式响应，截图给我

### V3.5 — 网络拦截产生了 Session
- **前置**：V3.4 通过
- **操作**：
  ```
  http://127.0.0.1:9800/api/v1/sessions?last=10
  ```
  寻找 `created_via: "browser_extension_network"` 的 session
- **预期**：存在这样的 session，且 messages 中有完整的 user + assistant 内容
- **排障**：
  - 如果有 capture 但没有 session → SSE 组装或归一化失败。访问：
    ```
    http://127.0.0.1:9800/api/v1/captures?last=10
    ```
    把最近的 network_intercept 类型的 capture 截图给我，我来分析 body 格式

---

## 第四关：ChatGPT 验证

### V4.1 — ChatGPT 内容脚本 + 网络拦截
- **前置**：V1.3 通过
- **操作**：
  1. 打开 https://chatgpt.com
  2. F12 → Console，过滤 `PCE`
- **预期**：看到 chatgpt.js 加载 + bridge.js 注入日志

### V4.2 — ChatGPT 对话捕获
- **操作**：发一条消息，等回复完成
- **预期**：Console 有 capture 日志 + 服务器终端有 ingest 日志
- **验证**：访问 `http://127.0.0.1:9800/api/v1/sessions?last=5&provider=openai` 确认 session 存在

---

## 第五关：Claude 验证

### V5.1 — Claude 内容脚本 + 网络拦截
- **前置**：V1.3 通过
- **操作**：
  1. 打开 https://claude.ai
  2. F12 → Console，过滤 `PCE`
- **预期**：看到 claude.js 加载 + bridge.js 注入日志

### V5.2 — Claude 对话捕获
- **操作**：发一条消息，等回复完成
- **预期**：Console 有 capture 日志 + 服务器终端有 ingest 日志
- **验证**：访问 `http://127.0.0.1:9800/api/v1/sessions?last=5&provider=anthropic` 确认 session 存在

---

## 第六关：Dashboard UI

### V6.1 — Dashboard 能打开
- **前置**：V1.1 通过
- **操作**：浏览器访问 http://127.0.0.1:9800
- **预期**：看到 PCE Dashboard 页面（如果有前端的话），或者 API 文档页面
- **排障**：如果 404，访问 http://127.0.0.1:9800/docs 查看 FastAPI 自动文档

### V6.2 — Dashboard 能看到 Sessions
- **操作**：在 Dashboard 中查看 Sessions 列表（或通过 API 文档的 Try it out）
- **预期**：之前捕获的 DeepSeek / ChatGPT / Claude 的 sessions 都在列表中

---

## 验证记录模板

完成每项后，请用以下格式记录：

```
V1.1 ✅ 服务器启动正常
V1.2 ✅ 健康检查通过
V1.3 ❌ 扩展显示 Offline（截图：...）
...
```

这样我可以精确定位问题在哪个环节，快速帮你修复。

---

## 优先级说明

| 优先级 | 验证项 | 为什么重要 |
|:---:|--------|-----------|
| 🔴 | V1.1–V1.3 | 基础设施，后面全依赖它 |
| 🔴 | V3.1–V3.5 | **核心路径**：网络拦截是我们刚加固的统一捕获策略 |
| 🟡 | V2.1–V2.4 | DOM 提取是备选路径，DeepSeek 是最新适配的 |
| 🟡 | V4, V5 | 多平台覆盖验证 |
| 🟢 | V6 | UI 层面，不影响捕获功能 |

**建议顺序**：先把 V1（服务器）通过 → 然后直接跳到 V3（网络拦截）→ 再回来做 V2（DOM）→ 最后 V4/V5/V6。
