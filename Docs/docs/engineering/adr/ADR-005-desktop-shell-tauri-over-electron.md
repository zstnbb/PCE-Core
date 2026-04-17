# ADR-005: 桌面壳选型采用 Tauri，不采用 Electron

- Status: Accepted
- Date: 2026-04-17
- Related: 2026-04-17-industrialization-roadmap.md

## Context

PCE 第一版 PoC 阶段没有桌面壳，用户需要自行启动 Python 服务、安装浏览器扩展、装 mitmproxy 证书。这套流程对非技术用户几乎不可用。

P3 阶段需要提供一个桌面应用，统一承载：

- 安装向导
- 系统托盘 / 菜单栏图标
- 状态显示与快捷开关
- 证书安装 / 代理启停
- 本地 dashboard 入口
- 自动更新

在这一步选型上，市面上两条主流路径是 Tauri 和 Electron。2026-04-05 的 Foundation Session 已记录此问题悬而未决（PROJECT 文档的"待决策问题"第 5 条）。本 ADR 收口。

## Decision

桌面壳采用 **Tauri**，不采用 Electron。

## Rationale

| 维度 | Tauri | Electron |
|---|---|---|
| 安装包体积 | 约 5-20 MB（系统 WebView） | 约 80-150 MB（自带 Chromium） |
| 内存占用 | 低，共享系统 WebView | 高，每进程独立 Chromium |
| 后端语言 | Rust，性能好、内存安全 | Node.js，生态熟悉但攻击面大 |
| 安全模型 | 默认限制严，IPC 显式声明 | 默认宽松，历史上多次 RCE 漏洞 |
| 自动更新 | 官方内建 | 官方内建 |
| 系统 API 访问 | 官方封装（托盘、通知、文件、shell） | 官方封装（更全） |
| 生态成熟度 | v2 已稳定，生产可用 | 久经考验 |
| 与 Python 后端共存 | sidecar 模式内建 | 需自行管理子进程 |
| 体积对"一次安装后台运行"的影响 | 用户几乎感知不到 | 每个 Electron 应用都占一块资源 |

关键判断：

1. PCE 的定位是"后台长期运行的本地基础设施"，体积和常驻内存比一次性启动速度更重要。
2. anything-analyzer 的 Electron 壳是我们观察到的反面教材：用户装的是一个独立 Chromium，不能继承真实浏览器 profile，攻击面大。
3. Tauri 的 sidecar 模式天然适合包装我们的 Python 后端。
4. Rust 生态对"一键装 CA 证书、切换系统代理"这类系统操作提供了可靠封装。
5. 社区中已有多款 Tauri + Python 的成功先例。

## Consequences

### Positive

- 发行物体积比同类 Electron 应用小一个数量级
- 本地资源占用低，用户几乎感知不到 PCE 在后台跑
- 默认安全模型更严格，符合"用户数据只在本地"的承诺
- IPC 必须显式声明，减少"意外暴露 API"的风险
- 官方 updater / tray / notification 封装充分

### Negative

- 需要 Rust 工具链构建
- 跨平台构建矩阵相比 Electron 稍繁琐
- 团队需要理解 Tauri 的 command / capability 模型
- 某些 Electron 插件无直接对应版本

## Guardrails

- 所有桌面壳与 Python 后端的通信通过 Tauri sidecar 模式进行
- 不允许在 Tauri 前端直接读写数据库，必须走 Python 后端的 HTTP API
- Tauri capability 配置采用最小化原则，只声明当前阶段实际用到的 API
- CI 必须覆盖 Windows / macOS / Linux 三平台打包

## Review Trigger

以下情况重新评估本 ADR：

- Tauri v2 稳定性出现不可接受的退化
- 我们需要深度定制渲染进程（例如内嵌 CDP 浏览器），而 Tauri 无法满足
- 某个核心平台 API 在 Tauri 上长期缺失
