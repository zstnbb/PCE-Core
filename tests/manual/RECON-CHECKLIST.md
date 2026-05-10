# Phase 4.0 · Claude Desktop super-app surface 真机侦察操作清单

**目标**：60 分钟内点完 Claude Desktop 8 个一级 surface，让 recon
工具记录每个 surface 实际产生的 HTTP / WebSocket 流量，事后由
analyzer 产出 scope 决定表。

**前置条件**：
- 已通过 `python -m pce_app_launcher detect` 确认 Claude Desktop 已安装
- 你已登录 Claude Desktop（cookie / token 健康）
- 当前 Claude Desktop **完全关闭**（recon 工具会重新启动它，避免端口冲突）
- 已 `pip install playwright` 且 `playwright install chromium`（capture_bridge 同前提）

**运行**：

```powershell
# 在仓库根目录
python -m tests.manual.recon_claude_desktop --duration 3600
```

工具会：
1. 用 `pce_app_launcher` 启动 Claude Desktop 带 `--remote-debugging-port=9222`
2. 通过 Playwright `connect_over_cdp` 监听**所有** HTTP 响应 + **所有** WebSocket 帧
3. 写到 `tests/manual/recon_<时间戳>/events.jsonl`（不写 PCE Core，零污染）
4. 在终端开 REPL 接收 marker 命令

**重要**：每完成一个 surface 的操作前，**先在 REPL 输入 `mark <label>`**，
然后再点击。这样 analyzer 能把流量按 surface 归类。

---

## 顺序点击清单

按编号逐项执行。每项**先打 marker，再操作**。预计每项 3-5 分钟。

### 1. `mark chat-vanilla` — 普通聊天

- 点 "+ New chat"
- 输入: `Reply with exactly 'PONG' and the number 42.`
- 等回复结束
- 等 5 秒（让 SSE / 后续 metadata POST 完成）

### 2. `mark chat-thinking` — 显式 thinking 模式（如果模型支持）

- 在 model picker 选 Sonnet 4 / Opus 4.5（任何带 reasoning trace 的）
- 输入: `Solve 17 × 19 step by step. Show your reasoning.`
- 等待回复 + thinking block 出现
- 等 5 秒

### 3. `mark chat-artifact` — Artifact / 代码生成

- New chat
- 输入: `Write a small HTML page that displays "Hello PCE recon"
        with a blue background. Use the artifact panel.`
- 等待 artifact 面板出现并加载完成
- 等 5 秒

### 4. `mark cowork-open` — Cowork tab（关键未知 surface）

- 点顶部 "Cowork" tab
- 等界面加载完成（可能是空白 / 引导页）
- 如果有 "New cowork session" 之类按钮，点一下
- 输入一句简单 prompt（如 `Hello cowork`）
- 等回复或界面响应
- 等 10 秒（cowork 可能走 WebSocket，多等观察 framing）

### 5. `mark code-tab` — Code tab（关键未知 surface）

- 点顶部 "Code" tab
- 等界面加载
- 如果是 Claude Code 嵌入：输入 `pwd` 或 `echo hello`
- 等回复
- 等 5 秒

### 6. `mark projects-list` — Projects 列表

- 点左侧 "Projects"
- 等列表加载完成（即便为空）
- 如果有 project，点开一个
- 等 5 秒

### 7. `mark projects-chat` — Project 内聊天（继 6）

- 在已打开的 project 内（如果上一步有）输入 `What context do you have?`
- 等回复
- 等 5 秒
- *如果没有 project*: `mark projects-skipped` 后跳到 8

### 8. `mark artifacts-page` — Artifacts 列表页

- 点左侧 "Artifacts"
- 等列表加载
- 如果有 artifact 缩略图，点开一个
- 等 5 秒

### 9. `mark customize-page` — Customize 页

- 点左侧 "Customize"
- 等界面完全加载
- 不要修改任何设置
- 等 5 秒

### 10. `mark voice-input` — 语音输入（如果支持）

- 回到 New chat
- 点输入框右侧的 mic 图标
- 说一句话（如 "test recon hello"），等转写完成
- 不要发送，让转写结果停在输入框
- 等 5 秒

### 11. Quick actions — 5 个快捷入口

每个 quick action 都开一次 New chat，用模板默认 prompt：

- `mark quick-learn`        点 "Learn"，发送默认 prompt
- `mark quick-write`        点 "Write"，发送默认 prompt
- `mark quick-code`         点 "Code"，发送默认 prompt
- `mark quick-life-stuff`   点 "Life stuff"，发送默认 prompt
- `mark quick-claudes-choice` 点 "Claude's choice"，发送默认 prompt

每个等回复 + 5 秒缓冲。

### 12. `mark file-upload` — 文件上传

- New chat
- 点输入框 + 号 / paperclip
- 上传**任意一个**小 PDF 或图片（< 1 MB）
- 输入: `Summarize this in one sentence.`
- 等回复 + 5 秒

### 13. 收尾

```
> stats
> stop
```

工具会写出 `summary.json` 并打印最终事件计数。

---

## 分析

```powershell
python -m tests.manual.analyze_recon tests/manual/recon_<时间戳>/
```

会打印 markdown 报告并写到 `tests/manual/recon_<时间戳>/report.md`。
报告包含：

- **Gap vs current capture_bridge** —— 我们当前过滤命中多少 / 漏多少
- **HTTP endpoint 清单** —— 按 (host, method, path-template) 聚合，含 ✅/❌ 标注
- **WebSocket 清单** —— 当前 capture_bridge 完全不监听的部分
- **每 marker 流量分布** —— 哪个 surface 用哪些 endpoint
- **Top 3 漏抓 endpoint 的样本 body** —— 直接用来设计 normalizer 扩展

---

## 故障排查

| 现象 | 原因 | 处置 |
|---|---|---|
| `playwright_import_failed` | 没装 playwright | `pip install playwright && playwright install chromium` |
| Claude Desktop 没启动 | exe 路径未 detect 到 | 先 `python -m pce_app_launcher detect` 确认 |
| CDP 连接 timeout 15s | 9222 已被其它 Chrome 占 | 关掉所有 Chrome / 用 `--no-launch --cdp-endpoint http://...` 指定其它端口 |
| `events.jsonl` 空文件 | bridge 启动失败 | 看 `meta.json` + 终端 ERROR 日志 |
| WebSocket 全为 0 但前端肯定有 | 协议级别异常 | 检查 Claude Desktop 是否全功能可用；可能 fallback 到 long-polling |

---

## 输出存放与 gitignore

`tests/manual/recon_*/` 默认走 `.gitignore`（recon 数据含会话内容，
不入仓）。如需把某次 report.md 留档，单独 cherry-pick 到
`Docs/research/<日期>-claude-desktop-recon.md` 之类位置。
