# TASK-005: P3 渲染层工业化 + UX

- Status: Pending P2
- Priority: P3
- Owner: Local agent
- Date: 2026-04-17
- Phase: Industrialization P3

## 1. 背景

P2 完成之后，PCE 的四条抓取通道都已经可编程、可一键控制。但普通用户依然不会用：

- 要启动 PCE 需要命令行起 Python 服务
- 要看数据要访问 `localhost:9800`
- 没有托盘 / 状态栏入口
- 没有安装包，没有自动更新

P3 的目的是把 PCE 从"开发者可用"变成"可以发给非技术朋友装"的形态。

## 2. 目标

产出一个可分发的 PCE 桌面应用：

1. Tauri 单文件安装包（Windows / macOS / Linux）
2. 首次运行引导向导（证书 / 扩展 / 代理 / 隐私偏好）
3. 系统托盘 / 菜单栏图标 + 快捷开关
4. 自动更新（Tauri updater）
5. 诊断命令 (`pce diagnose`)
6. 可选 Phoenix 开发者面板

## 3. 输入文档

开工前必须阅读：

- `docs/decisions/2026-04-17-industrialization-roadmap.md`
- `docs/engineering/ARCHITECTURE.md`（v0.2 第 8 节）
- `docs/engineering/adr/ADR-005-desktop-shell-tauri-over-electron.md`
- `docs/engineering/adr/ADR-007-otlp-as-optional-secondary-channel.md`
- P2 完成回报

## 4. 范围

### In Scope
- 基于 Tauri v2 的 pce_app 完整化
- Python 后端以 sidecar 形式启动、健康监控、崩溃恢复
- 首次运行引导向导
- 系统托盘 / 菜单栏图标
- 快捷开关（暂停记录 / 切换代理 / 打开 dashboard / 退出）
- 自动更新通道（签名 + 增量）
- `pce diagnose` 命令
- 可选 Phoenix 视图（按需从 app 设置里开启）
- 跨平台打包 CI

### Out of Scope
- Dashboard 从原生 HTML 重构为 React/Svelte（除非明显必要）
- 多用户 / 团队协作
- 云端账号
- 移动端
- "理解层"任何能力

## 5. 建议实现边界

### 5.1 Tauri 工程骨架

在 `pce_app/` 下建立 Tauri v2 项目：

```
pce_app/
├── src-tauri/          # Rust 主进程
│   ├── src/
│   │   ├── main.rs
│   │   ├── sidecar.rs      # 启停 Python 后端
│   │   ├── tray.rs         # 托盘菜单
│   │   ├── wizard.rs       # 引导向导 IPC
│   │   └── capability/     # Tauri capability 声明
│   └── tauri.conf.json
├── src/                    # 前端（建议 Vite + TS，不强制框架）
│   ├── pages/
│   │   ├── wizard/
│   │   ├── home/
│   │   ├── settings/
│   │   └── diagnose/
│   └── ...
├── package.json
└── README.md
```

### 5.2 Python Sidecar

- Tauri 启动时以 sidecar 模式启动 `pce_core.server`
- 绑定到随机空闲端口（避免冲突）
- 前端通过 IPC 获取这个端口，后续走 HTTP
- Tauri 退出时确保 Python 进程被终止
- Python 进程异常退出由 Rust 侧重启（与 P2 的 supervisor 协作）

### 5.3 首次运行向导

分五步：

1. 欢迎 + 隐私原则说明（local-first、数据只在本地）
2. 选择模式（Light / Complete）
3. 安装证书（仅 Complete 模式）→ 调用 P2 的 cert_wizard API
4. 安装扩展（打开 Chrome Web Store 或引导 sideload）
5. 代理开关 + 完成

每一步失败都应可跳过或重试，不能卡死用户。

### 5.4 托盘

菜单项：

- 当前状态（是否在记录 / 今日抓取数）
- 打开 Dashboard
- 切换代理
- 暂停 / 恢复记录
- 设置
- 退出（会提示是否保留后台捕获）

### 5.5 自动更新

- 使用 Tauri 官方 updater
- 更新源指向 GitHub Releases（初期）
- 必须签名（至少 Windows 代码签名、macOS notarization）
- 用户可选"仅下载不安装" / "自动安装"

### 5.6 诊断命令

`pce diagnose`（CLI 子命令或 app 内按钮）：

- 收集：版本信息 / OS / Python 版本 / schema_version / 最近 7 天的日志 / 健康指标 / migration 历史
- 不收集：实际 AI 对话内容、token、个人身份数据
- 打包为 zip 放到 Desktop 并打开所在目录

### 5.7 可选 Phoenix 面板

- 设置页有开关"开启开发者视图 (Phoenix)"
- 打开时：在 Python 后端设 `OTEL_EXPORTER_OTLP_ENDPOINT`，并 `pip install arize-phoenix` 若未装
- 前端提供一个 iframe 或新窗口加载 Phoenix UI

### 5.8 打包与签名

- Windows: MSI + 签名证书（使用 GitHub Actions secrets）
- macOS: DMG + notarization
- Linux: AppImage + deb（deb 可选）
- CI 矩阵：`windows-latest` / `macos-latest` / `ubuntu-latest`

## 6. 验收标准

必须同时满足：

- [ ] 在三个平台都能生成可双击安装的分发物
- [ ] 安装后从双击图标到看到首次引导向导少于 5 秒
- [ ] 引导向导的五步在全新系统上都能走通
- [ ] 托盘图标长期驻留，右键菜单可用
- [ ] 后端 Python 进程崩溃后 5 秒内被 Rust 侧重启，托盘图标显示状态变化
- [ ] 发一个假版本 release 到 GitHub，Tauri updater 能识别并提示更新
- [ ] `pce diagnose` 输出的 zip 不含任何 AI 对话内容
- [ ] 非技术用户（未安装 Python / Rust / Node）在干净机器上从零到抓到第一条捕获少于 10 分钟
- [ ] P0 / P1 / P2 所有测试仍然通过

## 7. 建议交付物

- 新增：`pce_app/` 完整 Tauri 项目
- 修改：`pce_core/server.py`（接受 sidecar 模式、输出 sidecar 端口）
- 新增：`pce_core/diagnose.py`
- 新增：签名与打包的 CI 配置
- 新增：用户安装文档 `Docs/docs/USER_GUIDE.md`
- 完成回报：`Docs/decisions/2026-04-??-P3-completion.md`

## 8. 停止条件

- Tauri v2 在某个平台出现阻塞性 bug 且短期无法修复
- 代码签名证书 / Apple notarization 流程卡住
- 发现 Python 打进 sidecar 的体积过大（需要讨论替代方案）
- 发现 mitmproxy + Tauri sidecar 不兼容（端口 / 权限 / 信号处理）

## 9. 回报格式

同 TASK-002 的第 9 节，额外包含：

- 三平台的 `已知安装阻碍`
- 首次引导的真实成功率（至少 3 人试装）
