# TASK-004: P2 抓层工业化 + UX

- Status: Pending P1
- Priority: P2
- Owner: Local agent
- Date: 2026-04-17
- Phase: Industrialization P2

## 1. 背景

P1 完成之后，PCE 的存层已经对齐了开源标准，数据可进可出。但抓层对非技术用户仍然不可用：

- 浏览器扩展需要开发者模式侧载
- mitmproxy 证书需要用户自己去 `~/.mitmproxy/` 翻文件手动安装
- 系统代理需要用户自己去系统设置里改
- 用户写代码调 OpenAI 没有统一入口可以让 PCE 抓到

P2 的目的是把抓层的**工程质量**与**用户体验**同时提升到工业级。

## 2. 目标

让 PCE 的四条抓取入口同时具备：

1. 浏览器扩展用 WXT 构建链（TS + HMR + 跨浏览器）
2. 跨平台 CA 证书向导（一键安装 / 卸载 / 重签 / 导出）
3. 系统代理一键开关（Windows / macOS / Linux）
4. SDK 捕获通道（基于 LiteLLM Proxy）
5. 代理与归一化管道的健康守护进程

## 3. 输入文档

开工前必须阅读：

- `docs/decisions/2026-04-17-industrialization-roadmap.md`
- `docs/engineering/ARCHITECTURE.md`（v0.2 第 4、6 节）
- `docs/engineering/adr/ADR-002-local-proxy-as-core.md`
- `docs/engineering/adr/ADR-003-dual-install-modes.md`
- `docs/engineering/adr/ADR-006-browser-extension-framework-wxt.md`
- P1 完成回报

## 4. 范围

### In Scope
- 浏览器扩展的 WXT 化（构建链替换，业务逻辑原样迁移）
- 跨平台 CA 证书管理模块（Python + 可被 Tauri 调用）
- 系统代理开关模块（Windows 注册表 / macOS networksetup / Linux gsettings）
- LiteLLM 作为 SDK 捕获通道的集成
- 代理子进程健康守护
- 这些模块的单元测试与集成测试

### Out of Scope
- Tauri 桌面壳本身（P3 做）
- 首次运行引导（P3 做）
- 扩展商店上架（可并行但不卡住 P2）
- 移动端抓包引导（留给 P4）
- CDP 内嵌浏览器（留给 P4）

## 5. 建议实现边界

### 5.1 浏览器扩展迁 WXT

执行顺序：

1. 在 `pce_browser_extension_wxt/` 新建 WXT 项目骨架（不覆盖原目录）
2. 把 manifest 通过 WXT 定义式配置生成，合并现有两份 manifest（sideload vs webstore）为一份带构建开关的配置
3. 移植 `background/` → `entrypoints/background.ts`
4. 移植 `content_scripts/detector.js` / `universal_extractor.js` / `network_interceptor.js` 等 → `entrypoints/content/*.ts`
5. 每移植一个 content script，加 TypeScript 类型但不改行为
6. 验证与现有 e2e 测试兼容
7. 原目录保留到本轮结束后再删，以便回滚

禁止在本轮做任何业务逻辑改动。

### 5.2 CA 证书向导

新建 `pce_core/cert_wizard/`：

- `install_ca(cert_path) -> Result`（Windows: `certutil -addstore Root`；macOS: `security add-trusted-cert`；Linux: 写 `/usr/local/share/ca-certificates` 后 `update-ca-certificates`）
- `uninstall_ca(cert_thumbprint) -> Result`
- `regenerate_ca() -> new_cert_path`
- `export_ca(dest_path) -> Result`
- `list_ca() -> list[CertInfo]`
- 所有需要提权的动作必须显式返回"需要提权"状态，不能隐式起 sudo / UAC
- 提供对应 HTTP API 以便 pce_app 调用

### 5.3 系统代理开关

新建 `pce_core/proxy_toggle/`：

- `enable_system_proxy(host, port, bypass_list)`：
  - Windows: 写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`
  - macOS: 调 `networksetup -setwebproxy` / `-setsecurewebproxy`
  - Linux: 调 `gsettings set org.gnome.system.proxy`（其他 DE 尽力而为）
- `disable_system_proxy()`
- `get_current_proxy_state()`

提供对应 HTTP API。

### 5.4 LiteLLM SDK 通道

新增 `pce_core/sdk_capture_litellm/`：

- 启动一个配置好的 LiteLLM Proxy 子进程（端口可配置）
- LiteLLM 的配置文件由 PCE 管理（`litellm_config.yaml`）
- 通过 LiteLLM 的 HTTP 回调或自定义 logger 把每次调用写进 PCE 的 Ingest API
- Source 标记为 `sdk`
- 提供启停 HTTP API

用户使用方式：

```python
# 在用户代码中
client = OpenAI(
    api_key=PASSTHROUGH,
    base_url="http://127.0.0.1:9900/v1"  # PCE-LiteLLM proxy
)
```

### 5.5 代理健康守护

新建 `pce_core/supervisor/`：

- 启动并监控 `mitmdump` / `litellm` / `pce_core.server` 三个子进程
- 任一进程异常退出时自动重启（指数退避）
- 暴露 `/api/v1/supervisor` 查看各子进程状态
- 可被 Tauri 主进程托管或独立运行

## 6. 验收标准

必须同时满足：

- [ ] `pnpm dev` 能启动 WXT 开发模式，修改代码自动热重载
- [ ] `pnpm build` 产出可加载的 Chrome / Firefox 构建物
- [ ] 旧扩展的 e2e 抓取测试在新 WXT 构建物上同样通过
- [ ] `POST /api/v1/cert/install` 能在 Windows / macOS / Linux 任一平台成功安装 mitmproxy CA，`list_ca` 能看到
- [ ] `POST /api/v1/proxy/enable` 能让系统浏览器走 127.0.0.1:8080，`disable` 能复原
- [ ] 启动 `pce_core.sdk_capture_litellm` 后，用 OpenAI SDK 指向它发一次请求，PCE 数据库新增一条 `source=sdk` 的记录
- [ ] supervisor 杀死子进程后 5 秒内自动重启
- [ ] 针对证书 / 代理开关 / LiteLLM 桥接 / supervisor 的测试在 CI 通过（跨平台至少在 Windows + Linux 验证）
- [ ] P0 / P1 所有测试不被破坏

## 7. 建议交付物

- 新增：`pce_browser_extension_wxt/`
- 新增：`pce_core/cert_wizard/` / `pce_core/proxy_toggle/` / `pce_core/sdk_capture_litellm/` / `pce_core/supervisor/`
- 修改：`pce_core/server.py`（新增对应 API 路由）
- 新增：对应测试
- 更新：`requirements.txt`（LiteLLM）与 WXT 的 package.json
- 完成回报：`Docs/decisions/2026-04-??-P2-completion.md`

## 8. 停止条件

- WXT 迁移过程中发现现有业务逻辑有隐含 bug 且无法在不改语义的前提下复现
- 某平台的系统 API 要求比预期严重的依赖（例如 Windows 需要签名驱动）
- LiteLLM 的回调机制与当前 Ingest API 无法无损对接
- 发现证书管理要求绕过的系统安全机制（必须暂停并向上报告）

## 9. 回报格式

同 TASK-002 的第 9 节。
