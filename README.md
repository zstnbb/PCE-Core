# PCE Core

本地、私有、可扩展的 AI 交互基础设施。

- 状态：工业化阶段 P0（Foundation 已完成）
- 路线图：`Docs/docs/decisions/2026-04-17-industrialization-roadmap.md`
- 文档入口：`Docs/README.md`

## PCE 是什么

PCE（Personal Cognitive Engine，工作名）是一层**本地**的 AI 基础设施，位于你和各种 AI 工具之间，负责把你在这些工具里的交互**捕获 → 归一化 → 本地持久化 → 可回看**。

它不是一个新 AI 产品，也不替代你现在用的 ChatGPT / Claude / Gemini。它的目标是让这些分散的使用痕迹第一次在本地形成可查询的整体。

产品路径：`记录 → 看见 → 理解 → 干预`，当前实现聚焦于前两步。

## 三大核心能力

| 能力 | 当前状态 | 主要模块 |
|---|---|---|
| 抓 (Capture) | 已有浏览器扩展 + 系统代理 + MCP 三条入口 | `pce_browser_extension/` · `pce_proxy/` · `pce_mcp/` |
| 存 (Storage) | SQLite + 归一化 + reconciler + 脱敏 | `pce_core/db.py` · `pce_core/normalizer/` |
| 渲染 (Render) | 本地 FastAPI + 原生 dashboard，桌面壳在 P3 | `pce_core/server.py` · `pce_core/dashboard/` · `pce_app/` |

后续演进路径见 `Docs/docs/PROJECT.md` 的阶段划分。

## 环境要求

- Python 3.10+
- mitmproxy 10+
- Chrome 或兼容 Chromium 浏览器（用扩展时）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动本地核心服务

```bash
python -m pce_core.server
# 默认监听 127.0.0.1:9800
```

这会同时启动 Ingest API 与本地 dashboard。

### 3. 启动系统代理（可选，Complete Mode）

```bash
mitmdump -s pce_proxy/addon.py -p 8080 --set stream_large_bodies=1m
```

代理监听 `127.0.0.1:8080`。首次启动后证书会生成在 `~/.mitmproxy/`。

配置客户端走代理：

```bash
export HTTP_PROXY=http://127.0.0.1:8080
export HTTPS_PROXY=http://127.0.0.1:8080
```

后续 P2 阶段会提供一键证书安装 + 系统代理开关，届时不再需要手动配置。

### 4. 安装浏览器扩展（Light Mode）

```text
chrome://extensions/
→ 打开"开发者模式"
→ "加载已解压的扩展程序"
→ 选择 pce_browser_extension/
```

扩展会在你访问支持的 AI 网站（ChatGPT / Claude / Gemini / Copilot 等）时自动工作。

### 5. 挂接 AI Agent（可选）

将 `pce_mcp` 注册为 MCP Server，Claude Desktop / Cursor / Windsurf 可以通过它把会话录入 PCE。

```bash
python -m pce_mcp
```

### 6. 查看数据

- 浏览器打开 `http://127.0.0.1:9800/dashboard`
- 或 CLI：`python -m pce_proxy --last 20`

## 数据存储

- 默认路径：`~/.pce/data/pce.db`
- 可通过 `PCE_DATA_DIR` 环境变量覆盖
- 所有敏感 header（Authorization / Cookie / API Key 等）在落库前已被替换为 `REDACTED`
- 数据永远保留在你的本机，PCE 不上传任何内容

## 项目结构

```
PCE Core/
├── Docs/                    # 完整文档体系（决议 / 架构 / ADR / 任务单）
│   └── README.md            # 文档导航
├── pce_core/                # 核心后端
│   ├── server.py            # FastAPI: Ingest + Query API
│   ├── db.py                # SQLite schema 与读写
│   ├── normalizer/          # 归一化 + reconciler + session_manager
│   ├── dashboard/           # 本地 web dashboard
│   └── ...
├── pce_proxy/               # mitmproxy addon
├── pce_browser_extension/   # Chrome MV3 扩展（13+ 站点支持）
├── pce_mcp/                 # MCP Server
├── pce_app/                 # 桌面壳（P3 阶段完整化）
├── tests/                   # 测试集
├── requirements.txt
└── README.md                # 本文件
```

## 设计原则

- **Local-first**：关键数据默认保存在本地，不依赖外部云端服务
- **Habit-preserving**：尽量不要求用户改变已有使用习惯
- **Fail-open**：记录失败可以接受，但绝不阻断用户正常使用 AI 工具
- **Security by default**：敏感认证信息默认脱敏，不以明文落库
- **Capture before interpretation**：先忠实记录，再解释，不提前替用户下结论
- **Open standard alignment**：归一化数据对齐 OpenInference / OTel GenAI，不锁定用户

完整原则见 `Docs/docs/PROJECT.md`。

## 贡献与路线图

工业化阶段共四步：

- **P0 稳定现状**（当前）：健康指标、migration、冒烟测试
- **P1 存层工业化**：OpenInference 对齐、OTLP 导出、导入导出
- **P2 抓层工业化 + UX**：浏览器扩展 WXT 化、证书向导、系统代理开关、SDK 通道
- **P3 渲染层工业化 + UX**：Tauri 桌面壳、首次引导、托盘、自动更新

每个阶段的任务单在 `Docs/tasks/`，可直接交接给本地 agent 执行。

## License

待定。
