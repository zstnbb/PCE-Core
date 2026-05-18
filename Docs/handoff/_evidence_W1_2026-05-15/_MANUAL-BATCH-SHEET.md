# W1 Manual Batch Sheet — 2026-05-15

> 你的活儿清单。完成每一步后告诉我"OK / 已完成 / done"我就接着干。
> 5 个 CLI 任务我已经做完了 (T3 / T10 / T11)。剩 6 个真需要你手动。
>
> 建议总时长 ≈ 45 分钟，分 4 个 batch。

---

## Batch 1 — Codex CLI 登录 (1 分钟，只一次)

打开任意 PowerShell：

```powershell
codex login
```

它会弹出浏览器走 OAuth 流程。登完关掉浏览器就行。
然后告诉我"codex 登好了"，我接着自动跑 T8 + T9。

---

## Batch 2 — 浏览器 3 站 (10 分钟，T4 + T5 + T6)

**前置**: 你常用的浏览器（Chrome / Edge）应已登录这 3 站。
如果没登录，请先在普通浏览器（不开代理）登录后再继续。

**Step 2.1 — 启动代理链**:

```powershell
cd "F:\INVENTION\You.Inc\PCE Core"
.\scripts\harvest\setup_proxy_chain.ps1
```

预期看到: "Original ProxyServer: '127.0.0.1:7890'" (Clash) → 切到 ":8080"。

**Step 2.2 — 重启浏览器** (不重启 cookies / session 会绑在老代理):

把所有 Chrome / Edge 窗口全关掉再打开。

**Step 2.3 — 3 站发 prompt**:

打开浏览器 (任何 tab) ，每个站发同样这一句话:

1. https://gemini.google.com — 发 `What is 2+2?` → 等 Gemini 回复
2. https://aistudio.google.com — 进入任意 model → 发 `What is 2+2?` → 等回复
3. https://grok.com — 发 `What is 2+2?` → 等回复

完成后告诉我 "浏览器 3 站完成"。**先不要 teardown 代理**，我会先验证数据落库。

---

## Batch 3 — Claude Desktop (15 分钟，T1 + T2)

> 这一步用 MCP，不用网络代理，可以跟 Batch 2 完全独立。

**Step 3.1 — 安装 PCE MCP server (T2)**:

到这个文件 → 双击它:
```
F:\INVENTION\You.Inc\PCE Core\pce_mcp\mcpb\pack-output\pce-mcp-0.1.0.mcpb
```

Claude Desktop 会弹出"安装 MCP server"对话框，点确认。

**Step 3.2 — 验证安装**:

打开 Claude Desktop → 右下角设置图标 → Settings → Extensions → 应该看到 "pce-mcp"。

**Step 3.3 — 触发 1 次 tool call (T1 + T2)**:

在 Claude Desktop 任意聊天里输入:
```
请用 pce 工具查一下我最近的 sessions
```

让 Claude 调用 `pce_sessions` 或 `pce_stats` 工具一次。

完成后告诉我 "Claude Desktop 完成"。

---

## Batch 4 — Windsurf MCP (10 分钟，T7)

**Step 4.1 — 我会先帮你生成 Windsurf MCP 配置**:

等我先把配置文件准备好（下一步骤生成），到时告诉你具体编辑哪个文件。

---

完成后，最后再:

```powershell
cd "F:\INVENTION\You.Inc\PCE Core"
.\scripts\harvest\teardown_proxy_chain.ps1
```

把系统代理还原回 Clash。

