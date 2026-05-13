---
title: "Agent Collaboration Protocol — Cloud Devin × Local Agent"
status: draft v1
date: 2026-05-13
audience: 任何运行在 PCE owner 机器上的 autonomous agent（Claude Code / Cursor agent / 等），以及任何在云端被 owner 发起的 Devin session
authority_chain:
  - Docs/docs/engineering/PCE-STANDARD-WORKFLOW.md  # 6 阶段产品生命周期
  - Docs/docs/engineering/META-PIPELINE-FRAMEWORK.md  # 跨 lane 三件套契约
  - CONTRIBUTING.md  # commit / DCO / 架构红线
---

# PCE 双 agent 协作协议 (Cloud Devin × Local Agent)

> Status: draft v1（2026-05-13）
> Scope: 给本地 agent（Claude Code / Cursor agent / 任何在 owner 电脑上跑的 autonomous agent）和云端 Devin session 各自一份"对方是谁、能干什么、不能干什么、怎么协作"的契约。

---

## 0. 60 秒摘要

PCE 是 local-first AI 抓取项目。两个 agent 分工：

- **Cloud Devin**（远端）：Linux Ubuntu VM，**没有** owner 电脑的访问权限。负责所有不依赖本机的工作：写代码、写测试、跑 CI、写文档、开 PR。
- **Local Agent**（本地，即正在读这段话的 agent）：跑在 owner Windows 电脑上，**有** 系统代理 / 证书 / Cursor.exe / Copilot 历史 / Electron 应用的访问权限。负责所有必须发生在 owner 物理机上的事：live sweep、harvest、安装/卸载根证书、启 desktop 应用、装浏览器扩展、读 `%APPDATA%`。

**通信通道**：GitHub repo `zstnbb/PCE-Core` 的 PR + commit + issue。不走 IM、不走文件传输、不共享 fs。

---

## 1. 谁是谁

### 1.1 Cloud Devin

- **运行环境**: Linux Ubuntu 22.04 VM（cognition.ai 托管）
- **路径**: 项目 clone 在 `/home/ubuntu/repos/PCE-Core`（远端，跟 owner 电脑无关）
- **能干**: 写 .py/.ts/.md/.yaml；跑 pytest / ruff / pnpm；写 migration；开 PR；接 review；跑离线 fixture-based 测试
- **不能干**: 看 owner 电脑屏幕；读 owner 电脑文件；启动 owner 电脑上的应用；安装证书；改注册表；调试 Electron / Windows MSIX 应用；用 owner 的浏览器登录任何账号
- **身份**: Git author `Devin AI <devin-ai-integration[bot]@users.noreply.github.com>`
- **PR 头衔**: 任何由 Cloud Devin 提交的 PR 会自带 `Co-Authored-By: Devin AI` 行
- **找它**: owner 在 https://app.devin.ai 发指令；local agent 通过给 Cloud Devin 的 PR 写 review comment 或 push 到对方分支来交互

### 1.2 Local Agent

- **运行环境**: owner 的 Windows 电脑（推测 Windows 11 + PowerShell 7）
- **路径**: 已把 repo 拉到本地，路径 owner 知道（典型: `F:\INVENTION\You.Inc\PCE Core` 或类似）
- **能干**: 一切只有这台机器能做的事（见 §3.2）
- **不能干**: 不应该 review/merge Cloud Devin 提交的代码改动而不让 owner 看（local agent 不是 reviewer，是合作 agent）
- **身份**: 用 owner 的 git 配置，commit 时建议加 `Co-Authored-By: <你的模型/工具名>` 让 git log 看得清楚是谁干的

---

## 2. 共享物 (single source of truth)

只有一个共享存储：**GitHub repo `zstnbb/PCE-Core`**。

| 共享物 | 用途 | 谁写 |
|---|---|---|
| `master` 分支 | 已审核合入的代码 | 双方 push，但都通过 PR |
| `devin/<ts>-*` 或 `feat/devin/*` 分支 | Cloud Devin 的工作分支 | Cloud Devin |
| `feat/local/*` 分支 | Local Agent 的工作分支 | Local Agent |
| `evidence/*` 分支 | Live sweep / harvest 的实采证据（含 fixture） | Local Agent，由 Cloud Devin 引用 |
| PR description + comments | 异步对话主通道 | 双方 |
| `Docs/handoff/HANDOFF-<topic>.md` | 阶段交接（kickoff / recon / completion） | 双方按 PCE-STANDARD-WORKFLOW §3-8 写 |
| `Docs/research/<date>-<topic>-recon.md` | RECON 阶段产物 | Local Agent（因为只有本机能跑探针） |
| `tests/fixtures/<product>/<date>-<case>/` | 永久测试 fixture | Local Agent push，Cloud Devin 写测试用 |
| Issue tracker (`gh issue`) | 待办 / bug / 决策悬挂 | 双方 |

**不存在的东西**：
- 双方不共享文件系统
- 双方没有 RPC、IM、Slack
- Cloud Devin **不能 ssh 到** owner 电脑，Local Agent **也不应 ssh 到** Cloud Devin VM
- 任何状态都必须在 git repo 里才算"存在"

---

## 3. 分工矩阵

### 3.1 Cloud Devin 负责

- 所有 `pce_core/` `pce_proxy/` `pce_mcp/` 下的 Python 代码（normalizer / launcher / API / migration）
- 浏览器扩展 `pce_browser_extension_wxt/` 的 TypeScript 代码
- 所有 `Docs/docs/` 工程文档、ADR、设计文档
- `pytest` / `pnpm test` 离线测试套
- CI 配置 `.github/workflows/`
- PR 模板、CODEOWNERS、CONTRIBUTING 这类元数据
- **基于现有 fixture 验证 normalizer** —— 这是关键。S1/S2 已经采过的真实数据如果在 repo 里有，Cloud Devin 就能离线闭环

### 3.2 Local Agent 负责 (必须本机)

- 运行 `scripts/harvest/setup_proxy_chain.ps1` 改 HKCU 系统代理
- 运行 `scripts/harvest/teardown_proxy_chain.ps1` 还原
- 启动 mitmdump on 127.0.0.1:8080
- 启动 Cursor.exe / Claude Desktop / ChatGPT Desktop / VS Code + Copilot
- 在 Chrome 上 load unpacked 扩展并手动 smoke
- 读 `%APPDATA%\Cursor\User\globalStorage\state.vscdb`
- 读 `%APPDATA%\Code\User\globalStorage\emptyWindowChatSessions\*.jsonl`
- 装/卸载 PCE 根证书（mitmproxy-ca-cert.pem）
- 跑 `scripts/harvest/harvest_cdp.py --app cursor --duration 600`
- 跑 `scripts/harvest/verify_harvest.py --since "2026-..."`
- Stage 4 LIVE SWEEP（按 PCE-STANDARD-WORKFLOW.md §7）
- 任何需要 elevation（UAC 提权）的操作
- 跟 owner 直接对话拍板"这个版本要不要发"、"这个 cert pinning 接不接"

### 3.3 双方都能干（但要注意冲突）

- Git commit 到非冲突路径（不同子目录）
- 写 `Docs/`（但要在 PR 里 review 对方写的）
- 开 issue 提 bug / 提决策
- 跑 pytest（本地或 CI）

---

## 4. 工作流模式

### 4.1 模式 A: Cloud-driven (Cloud Devin 主导，本地 sweep)

最常用。例子：写新 normalizer。

```
[1] Cloud Devin
    - 写代码 + 单测（用已有 fixture）
    - 开 PR `feat/devin/<topic>`
    - PR description 里明确写: "需要 Local Agent 跑 live sweep 来 confirm"

[2] Local Agent 收到 PR 通知
    - checkout PR 分支
    - 跑 live sweep（具体命令在 PR description 的 "Live verification" 段）
    - 把 sweep 产物 push 到 PR 分支的 `evidence/<date>/` 目录
      或者写在 PR comment 里（小日志）

[3] Cloud Devin 看到 evidence
    - 如果 PASS → 在 PR 写 "live sweep PASS, ready to merge"，请 owner 合
    - 如果 FAIL → 改代码 → push → goto [2]

[4] Owner merge
```

### 4.2 模式 B: Local-driven (本地先采，Cloud Devin 接)

例子：harvest session。

```
[1] Local Agent
    - 跑 harvest 30-90 分钟
    - 把 raw 数据脱敏整理成 fixture
    - 写 `Docs/harvest/HARVEST-SESSION-<id>-RESULTS.md`
    - push 到 `evidence/<date>-harvest-<id>` 分支
    - 开 issue 或 在已有 issue 上 comment: "Cloud Devin 接力 → 用这个 fixture 写 normalizer"

[2] Cloud Devin
    - clone fixture
    - 写 normalizer + 单测
    - 开 PR 引用这个 fixture
    - 进入模式 A 的 [2]
```

### 4.3 模式 C: Parallel (双方独立)

Cloud Devin 做 normalizer 1，Local Agent 做 normalizer 2 的 RECON。**前提：路径不冲突**（不同 site 文件 / 不同子目录）。每次 commit 前先 `git pull --rebase`。

### 4.4 冲突解决

- 谁先 PR merge 的，对面 rebase 自己分支
- 文档冲突：让 owner 拍板，不要双 agent 互相 force-push 同一个文件
- 合并状态争议：以 master 为准；分歧写 ADR 或 issue 不要私下解决

---

## 5. 通信格式

### 5.1 PR description 必填段落

每个 PR description 里必须有：

```markdown
## What
（一句话目的）

## Why
（为什么现在做）

## How it was tested (offline)
（Cloud Devin 自己跑了什么，PASS 多少 / FAIL 多少）

## Live verification needed?
- [ ] Yes — see "Live verification" below
- [x] No — pure offline change

## Live verification (if needed)
1. Checkout this branch: `git fetch && git checkout <branch>`
2. Run: <具体 PowerShell 命令>
3. Expected: <什么结果就 PASS>
4. Push evidence to: `evidence/<date>-<topic>/` 或 PR comment

## Risk / rollback
（如果有问题怎么回滚）
```

### 5.2 PR comment 三种类型

- **`[CLOUD]` 前缀**: Cloud Devin 写的 status update / 答疑
- **`[LOCAL]` 前缀**: Local Agent 写的 sweep result / evidence dump
- **`[OWNER]` 前缀**: owner 决策

### 5.3 Commit message

继续用 Conventional Commits（CONTRIBUTING.md §103-107）。建议加来源 trailer:

```
feat(normalizer/cursor): CursorChatNormalizer + L3g fallback

<body>

Co-Authored-By: Devin AI <devin-ai-integration[bot]@users.noreply.github.com>
Verified-On: Local Agent (Windows 11, 2026-05-13 23:40 UTC+08:00)
```

`Verified-On` 行是约定，本地 agent 把 live sweep evidence 接进去时加。

---

## 6. 文件落点约定

| 内容 | 位置 |
|---|---|
| 协议描述 | `Docs/handoff/AGENT-COLLABORATION-PROTOCOL.md`（即本文件） |
| 阶段交接 | `Docs/handoff/HANDOFF-<phase>-<topic>.md`（按 PCE-STANDARD-WORKFLOW §3-8） |
| RECON 笔记 | `Docs/research/<YYYY-MM-DD>-<topic>-recon-findings.md` |
| 设计文档 | `Docs/docs/engineering/<topic>-DESIGN.md` |
| ADR | `Docs/docs/engineering/adr/ADR-<NNN>-<slug>.md` |
| Harvest fixture | `tests/fixtures/<product>/<YYYY-MM-DD>-<case>/` |
| Live sweep evidence | `evidence/<YYYY-MM-DD>-<topic>/` 分支 |
| 临时探针脚本 | `scripts/probe/`（短期）或 `scripts/harvest/`（要留下的） |

---

## 7. 失败 / 中断 / 兜底

### 7.1 Cloud Devin 离线（session 结束）

- Cloud Devin 的 PR 还在 GitHub 上不会消失
- Local Agent 继续完成 live sweep 部分
- Owner 可以新开一个 Devin session，让新 Devin 接 PR
- 新 Devin 读这份文件 + PR description 就能续上

### 7.2 Local Agent 离线 / owner 不在电脑前

- Cloud Devin 能做的纯 offline 工作可以继续推进（下一个 normalizer、refactor、文档）
- Live verification 必须等
- PR 标为 draft，写明 "blocked on live sweep, owner offline"

### 7.3 数据冲突 / push race

- Force push 任何分支前先 `git pull --rebase`
- 永远不 force push `master`
- 同一个 PR 分支，两边都改之前先看 GitHub PR 页面有没有未读的对方 commit

### 7.4 紧急回滚

- master 出问题：开 revert PR，不直接 reset
- 数据库 migration 出问题：永远只加 forward migration，不删历史 migration

---

## 8. 给 Local Agent 的 onboarding 5 步

第一次跑通这个协议，按顺序做：

1. 把这份文件读完一遍
2. 看 owner 当前在哪个 PCE 阶段：`git log --oneline -10` + 看 `Docs/docs/PROJECT.md` §1 状态行
3. 看最近一份 handoff：`ls Docs/handoff/ | tail -3`
4. 跟 owner 确认任务范围
5. 等 Cloud Devin 的当前 PR 出来（GitHub repo zstnbb/PCE-Core），按其中的 "Live verification" 段跑

---

## 9. 联系 / 升级路径

- Cloud Devin 找不到 / 没回应 → owner 在 https://app.devin.ai 发新 session，让新 Devin 读本文件接班
- Local Agent 找不到 → owner 直接接管 live sweep 部分（手动跑命令）
- 双方对架构有分歧 → 不要互改对方代码，开 issue 让 owner 拍板，必要时升 ADR

---

End of protocol v1.
